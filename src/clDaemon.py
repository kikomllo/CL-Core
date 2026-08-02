# --- IMPORTS ---
import sys
import asyncio
import os
import json
import logging
import time
import datetime
import aiomqtt
from typing import Dict, List, Tuple, Any

# --- CUSTOM MODULES ---
from utils.clConfigLoader import ConfigLoader
from nlp.clIntentEngine import IntentEngine

# --- LOGGING SETUP ---
import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('DAEMON')

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class CentralDaemon:
    """MQTT Orchestrator & State Machine"""
    
    def __init__(self):
        self.loader = ConfigLoader()
        
        intents_data = self.loader.load_and_validate("intents.json", "intents_schema.json")
        core_data = self.loader.load_json("core.json")
        
        responses_data = self.loader.load_json("responses.json")
        
        nlp_rules = core_data.get("nlp_rules", {})
        word_to_number = nlp_rules.get("word_to_number", {})
        abort_keywords = nlp_rules.get("abort_keywords", ["abort", "cancel", "nevermind"])
        
        self.stt_corrections = nlp_rules.get("stt_corrections", {})
        
        # --- UNIFIED STATE MACHINE ---
        self.active_context = {
            "type": None, 
            "expires_at": 0.0
        }
        
        self.tts_state: str = "idle"
        self.mic_state: str = "idle"
        self.is_ducked: bool = False
        self.attention_mode: bool = False
        self.pending_mic_request: bool = False
        
        conversational_data = responses_data.get("conversational", {})
        self.nlp = IntentEngine(intents_data, word_to_number, abort_keywords, conversational_data)
        
        self.intents_file_path = os.path.join(os.path.dirname(__file__), "..", "config", "intents.json")
        self.last_intents_mtime = os.stat(self.intents_file_path).st_mtime if os.path.exists(self.intents_file_path) else 0
        self.followups_enabled = core_data.get("settings", {}).get("enable_followup", True)
        self.silent_mode = core_data.get("settings", {}).get("silent_mode", False)

    def _optimize_intent_queue(self, intents: List[Tuple[Dict[str, Any], str]]) -> List[Tuple[Dict[str, Any], str]]:
        """
        INTENT SHADOWING: Resolves temporal conflicts (self-corrections) 
        by accumulating unique attributes and overwriting conflicting ones.
        """
        optimized_map = {}
        for command, target_topic in intents:
            if target_topic in optimized_map:
                optimized_map[target_topic].update(command)
            else:
                optimized_map[target_topic] = command.copy()
            
        return [(cmd, topic) for topic, cmd in optimized_map.items()]
    
    def sanitize_transcription(self, text: str) -> str:
        """
        MIDDLEWARE: Intercepts known STT phonetic hallucinations and corrects them
        using exact core.json mappings and rapidfuzz phonetic matching before intent processing.
        """
        cleaned_text = text.lower().strip()
        import re
        from rapidfuzz import fuzz
        
        # 1. Configured dictionary replacements
        for hallucination, correction in self.stt_corrections.items():
            if hallucination in cleaned_text:
                cleaned_text = re.sub(rf'\b{re.escape(hallucination)}\b', correction, cleaned_text, flags=re.IGNORECASE)
                logging.info(f"[STT INTERCEPTOR] Replaced hallucination '{hallucination}' -> '{correction}'")
                
        # 2. Phonetic fuzzy replacement for wake words
        words = cleaned_text.split()
        for i, w in enumerate(words[:3]):
            clean_w = re.sub(r'[^\w]', '', w)
            if len(clean_w) >= 4 and fuzz.ratio(clean_w, "jarvis") >= 75:
                words[i] = w.replace(clean_w, "jarvis")
                
        return " ".join(words)

    def route_voice_command(self, payload_data: str) -> List[Tuple[Dict[str, Any], str]]:
        import re
        
        sanitized_payload = self.sanitize_transcription(payload_data)
        clean_text = self.nlp.normalize_text(sanitized_payload)

        # 1. Global Abort Check
        if self.nlp.is_abort_command(clean_text):
            self.active_context["type"] = None  # Purge active state
            return [({"action": "abort"}, "jarvis/sys/control")]

        # 2. State Timeout Check
        if self.active_context["type"] and time.time() > self.active_context["expires_at"]:
            logging.info(f"[STATE] Active context '{self.active_context['type']}' expired.")
            self.active_context["type"] = None

        # --- EXTRACT FIRST DIGIT FOR INDEX ROUTING ---
        extracted_digits = re.findall(r'\d+', clean_text)
        choice_num = int(extracted_digits[0]) if extracted_digits else None

        # 3. Contextual Routing
        if self.active_context["type"] == "spotify_choice" and choice_num is not None:
            self.active_context["type"] = None
            return [({"action": "play_choice", "choice_index": choice_num}, "pc/spotify/control")]

        if self.active_context["type"] == "discovery_choice" and choice_num is not None:
            self.active_context["type"] = None
            return [({"action": "save_discovery", "index": choice_num}, "system/discovery")]

        if self.active_context["type"] == "discovery_name":
            temp_name = self.active_context.get("temp_name", "unknown")
            self.active_context["type"] = None
            if clean_text.lower() == "skip" or self.nlp.is_abort_command(clean_text):
                return [({"action": "abort"}, "jarvis/sys/control")]
            return [({"action": "intent_rename_light", "target_str": f"{temp_name} to {clean_text}"}, "home/room/all/set")]

        if self.active_context["type"] == "light_remove_target":
            self.active_context["type"] = None
            if self.nlp.is_abort_command(clean_text): return [({"action": "abort"}, "jarvis/sys/control")]
            return [({"action": "intent_remove_light", "target_str": clean_text}, "home/room/all/set")]

        if self.active_context["type"] == "light_default_target":
            self.active_context["type"] = None
            if self.nlp.is_abort_command(clean_text): return [({"action": "abort"}, "jarvis/sys/control")]
            return [({"action": "intent_set_default_light", "target_str": clean_text}, "home/room/all/set")]

        if self.active_context["type"] == "todo_add_target":
            self.active_context["type"] = None
            if self.nlp.is_abort_command(clean_text): return [({"action": "abort"}, "jarvis/sys/control")]
            return [({"action": "create", "task": clean_text}, "jarvis/sys/todo/create")]

        if self.active_context["type"] == "calendar_add_target":
            self.active_context["type"] = None
            if self.nlp.is_abort_command(clean_text): return [({"action": "abort"}, "jarvis/sys/control")]
            return [({"action": "create", "event": clean_text}, "jarvis/sys/calendar/create")]

        if self.active_context["type"] == "calendar_time_target":
            event_name = self.active_context.get("event", "unknown")
            self.active_context["type"] = None
            if self.nlp.is_abort_command(clean_text): return [({"action": "abort"}, "jarvis/sys/control")]
            return [({"action": "create", "event": event_name, "time_str": clean_text}, "jarvis/sys/calendar/create")]

        if self.active_context["type"] == "alarm_delete":
            alarms = self.active_context.get("alarms", [])
            self.active_context = {"type": None, "expires_at": 0.0}
            if clean_text.lower() in ["all", "everything", "all alarms"]:
                return [({"action": "delete", "id": "all"}, "jarvis/sys/alarm/control")]
            elif choice_num is not None and 0 <= choice_num < len(alarms):
                selected_id = alarms[choice_num].get("id")
                return [({"action": "delete", "id": selected_id}, "jarvis/sys/alarm/control")]
            elif self.nlp.is_abort_command(clean_text):
                return [({"action": "abort"}, "jarvis/sys/control")]

        if self.active_context["type"] == "light_rename_target":
            self.active_context["type"] = None
            if self.nlp.is_abort_command(clean_text): return [({"action": "abort"}, "jarvis/sys/control")]
        if self.active_context["type"] == "alarm_ringing":
            alarm_id = self.active_context.get("alarm_id")
            expected = str(self.active_context.get("expected_answer", "turn off alarm")).lower().strip()
            
            raw_intents = self.nlp.parse(clean_text)
            is_deactivate_intent = any(topic == "jarvis/sys/alarm/deactivate" for _, topic in raw_intents)
            
            if expected in clean_text or is_deactivate_intent or any(w in clean_text for w in ["stop", "off", "awake", "dismiss", "disable", "cancel"]):
                logging.info(f"[ALARM] Deactivation challenge passed for alarm {alarm_id}.")
                self.active_context = {"type": None, "expires_at": 0.0}
                return [
                    ({"id": alarm_id, "action": "deactivate"}, "jarvis/sys/alarm/deactivate"),
                    ({"text": "Alarm deactivated, sir.", "skip_ducking": True}, "jarvis/sys/speak")
                ]
            elif not clean_text or clean_text in ["you", "a", "it", "hey jarvis"]:
                logging.info(f"[ALARM] Standalone wake word received during alarm {alarm_id}. Opening microphone...")
                return [({"text": "Yes, sir? Say disable alarm.", "request_reply": True, "skip_ducking": True}, "jarvis/sys/speak")]
            else:
                logging.warning(f"[ALARM] Deactivation challenge failed for text '{clean_text}'.")
                return [({"text": "Say disable alarm to turn it off.", "request_reply": True, "skip_ducking": True}, "jarvis/sys/speak")]

        # 4. Standard NLP Parsing & Shadowing Execution
        raw_intents = self.nlp.parse(clean_text)
        return self._optimize_intent_queue(raw_intents)
    
    async def evaluate_ducking(self, client: aiomqtt.Client):
        """Single source of truth for volume ducking logic."""
        should_duck = False
        
        if self.tts_state == "active":
            should_duck = True
            
        elif self.mic_state in ["recording", "listening"] and not self.attention_mode:
            should_duck = True
            
        if should_duck and not self.is_ducked:
            await client.publish("pc/spotify/control", json.dumps({"action": "duck", "silent": True}))
            self.is_ducked = True
        elif not should_duck and self.is_ducked:
            await client.publish("pc/spotify/control", json.dumps({"action": "unduck", "silent": True}))
            self.is_ducked = False

    async def monitor_timeouts(self, client: aiomqtt.Client):
        while True:
            await asyncio.sleep(1)
            if self.active_context["type"] and time.time() > self.active_context["expires_at"]:
                logging.info(f"[STATE] Active context '{self.active_context['type']}' expired passively.")
                self.active_context = {"type": None, "expires_at": 0.0}
                await client.publish("jarvis/sys/ui_options", json.dumps({"options": []}))
                await client.publish("jarvis/sys/mic_control", json.dumps({"action": "cancel"}))
                
                await self.evaluate_ducking(client)
                    
    async def monitor_config(self):
        while True:
            await asyncio.sleep(5)
            try:
                if os.path.exists(self.intents_file_path):
                    current_mtime = os.stat(self.intents_file_path).st_mtime
                    if current_mtime > self.last_intents_mtime:
                        logging.info("[CONFIG] intents.json modified. Reloading...")
                        new_data = self.loader.load_and_validate("intents.json", "intents_schema.json")
                        self.nlp.reload_intents(new_data)
                        self.last_intents_mtime = current_mtime
            except Exception as e:
                logging.error(f"[CONFIG] Error reloading intents: {e}")

    async def run(self) -> None:
        logging.info("Central Daemon Online. Connecting to MQTT broker...")
        
        attempt = 0
        while True:
            try:
                async with aiomqtt.Client("localhost") as client:
                    attempt = 0
                    logging.info("MQTT broker connected. Subscribing to topics...")
                    monitor_task = asyncio.create_task(self.monitor_timeouts(client))
                    config_task = asyncio.create_task(self.monitor_config())
                    await client.subscribe("jarvis/sensor/voice")
                    await client.subscribe("jarvis/feedback")
                    await client.subscribe("jarvis/sys/speak")
                    
                    await client.subscribe("jarvis/sys/tts_state")
                    await client.subscribe("jarvis/sys/mic_state")
                    await client.subscribe("jarvis/sys/mic_control")
                    await client.subscribe("jarvis/sys/alarm/ring")
                    await client.subscribe("jarvis/sys/alarm/deactivate")
                    
                    logging.info("--- DAEMON READY: Listening for commands ---")
                    
                    async for message in client.messages:
                        topic = message.topic.value
                        payload_data = message.payload.decode('utf-8')
                        
                        if topic == "jarvis/sys/alarm/ring":
                            try:
                                payload = json.loads(payload_data)
                                self.active_context = {
                                    "type": "alarm_ringing",
                                    "alarm_id": payload.get("id"),
                                    "expected_answer": payload.get("expected_answer", "turn off alarm"),
                                    "expires_at": time.time() + 300.0
                                }
                                logging.info(f"[DAEMON] Alarm {payload.get('id')} ringing! Locking interface into alarm_ringing context.")
                            except json.JSONDecodeError:
                                pass

                        elif topic == "jarvis/sys/alarm/deactivate":
                            if self.active_context["type"] == "alarm_ringing":
                                logging.info("[DAEMON] Alarm deactivated. Releasing interface context.")
                                self.active_context = {"type": None, "expires_at": 0.0}
                                # Request Daily Briefing automatically after disabling morning alarm
                                await client.publish("jarvis/sys/calendar/request", json.dumps({"action": "daily_briefing"}))

                        elif topic == "jarvis/sys/speak":
                            try:
                                payload = json.loads(payload_data)
                                if payload.get("request_reply", False):
                                    self.pending_mic_request = True
                            except json.JSONDecodeError:
                                pass

                        elif topic == "jarvis/sys/tts_state":
                            try:
                                payload = json.loads(payload_data)
                                self.tts_state = payload.get("state", "idle")
                                if self.tts_state == "idle" and self.pending_mic_request:
                                    self.pending_mic_request = False
                                    await client.publish("jarvis/sys/mic_control", json.dumps({"action": "request_reply"}))
                                await self.evaluate_ducking(client)
                            except json.JSONDecodeError:
                                pass

                        elif topic == "jarvis/sys/mic_state":
                            try:
                                payload = json.loads(payload_data)
                                self.mic_state = payload.get("state", "idle")
                                await self.evaluate_ducking(client)
                            except json.JSONDecodeError:
                                pass
                                
                        elif topic == "jarvis/sys/mic_control":
                            try:
                                payload = json.loads(payload_data)
                                action = payload.get("action")
                                if action == "attention_on":
                                    self.attention_mode = True
                                elif action in ["attention_off", "cancel"]:
                                    self.attention_mode = False
                                await self.evaluate_ducking(client)
                            except json.JSONDecodeError:
                                pass
                            
                        elif topic == "jarvis/sensor/voice":
                            # --- 1. DAEMON-LEVEL WAKE WORD EXTRACTION ---
                            raw_payload = self.sanitize_transcription(payload_data)
                            
                            import re
                            ww_pattern = r'^(?:hey\s+|hi\s+|ok\s+|a\s+|uh\s+|ha\s+|eh\s+)?jarvis\b[.,!?]*\s*'
                            is_wakeword_present = bool(re.search(ww_pattern, raw_payload, flags=re.IGNORECASE))
                            if is_wakeword_present:
                                raw_payload = re.sub(ww_pattern, '', raw_payload, flags=re.IGNORECASE).strip()
                                    
                            # --- 2. NORMALIZE THE REMAINDER ---
                            clean_text = self.nlp.normalize_text(raw_payload)
                            logging.info(f"[VOICE INPUT] Raw: '{payload_data}' | Extracted: '{raw_payload}' | Normalized: '{clean_text}'")
                            # Pass only the extracted command to the intent router
                            intents = self.route_voice_command(raw_payload)
                            
                            if intents:
                                final_mic_state = "open_window" if (self.followups_enabled and not self.silent_mode) else None
                                
                                for command, target_topic in intents:
                                    if target_topic == "jarvis/sys/control" and command.get("action") == "abort":
                                        logging.info("[SYSTEM] Abort sequence initiated.")
                                        await client.publish("jarvis/sys/tts_stop", "1")
                                        
                                        await self.evaluate_ducking(client)
                                        
                                        final_mic_state = None  
                                        break
                                        
                                    elif target_topic in ["jarvis/sys/daemon_control", "jarvis/sys/control"] and command.get("action") in ["toggle_followup", "followup_on", "followup_off"]:
                                        action_cmd = command.get("action")
                                        if action_cmd == "followup_on":
                                            self.followups_enabled = True
                                        elif action_cmd == "followup_off":
                                            self.followups_enabled = False
                                        else:
                                            self.followups_enabled = not self.followups_enabled
                                        
                                        # Save to core.json
                                        try:
                                            with open(os.path.join(os.path.dirname(__file__), "..", "config", "core.json"), "r") as f:
                                                core = json.load(f)
                                            if "settings" not in core: core["settings"] = {}
                                            core["settings"]["enable_followup"] = self.followups_enabled
                                            with open(os.path.join(os.path.dirname(__file__), "..", "config", "core.json"), "w") as f:
                                                json.dump(core, f, indent=4)
                                        except Exception as e:
                                            logging.error(f"Failed to persist followup setting: {e}")
                                            
                                        state_str = "enabled" if self.followups_enabled else "disabled"
                                        if not self.silent_mode:
                                            await client.publish("jarvis/sys/speak", json.dumps({"text": f"Follow ups are now {state_str}."}))
                                        final_mic_state = None
                                        continue

                                    elif target_topic in ["jarvis/sys/daemon_control", "jarvis/sys/control"] and command.get("action") in ["toggle_silent_mode", "silent_mode_on", "silent_mode_off"]:
                                        action_cmd = command.get("action")
                                        if action_cmd == "silent_mode_on":
                                            self.silent_mode = True
                                        elif action_cmd == "silent_mode_off":
                                            self.silent_mode = False
                                        else:
                                            self.silent_mode = not self.silent_mode
                                        
                                        # Save to core.json
                                        try:
                                            with open(os.path.join(os.path.dirname(__file__), "..", "config", "core.json"), "r") as f:
                                                core = json.load(f)
                                            if "settings" not in core: core["settings"] = {}
                                            core["settings"]["silent_mode"] = self.silent_mode
                                            with open(os.path.join(os.path.dirname(__file__), "..", "config", "core.json"), "w") as f:
                                                json.dump(core, f, indent=4)
                                        except Exception as e:
                                            logging.error(f"Failed to persist silent_mode setting: {e}")
                                            
                                        state_str = "enabled" if self.silent_mode else "disabled"
                                        logging.info(f"[DAEMON] Silent mode is now {state_str}.")
                                        await client.publish("jarvis/sys/silent_mode", json.dumps({"silent_mode": self.silent_mode}))
                                        if not self.silent_mode:
                                            await client.publish("jarvis/sys/speak", json.dumps({"text": "Silent mode disabled."}))
                                        final_mic_state = None
                                        continue
                                    
                                    # --- Intercept and route conversational intents ---
                                    elif target_topic == "jarvis/sys/control" and command.get("action") == "conversational":
                                        if not self.silent_mode:
                                            logging.info("[DAEMON] Routing conversational intent to TTS.")
                                            await client.publish("jarvis/sys/speak", json.dumps({
                                                "text": command.get("text"),
                                                "request_reply": False
                                            }))
                                        
                                        final_mic_state = None 
                                        continue

                                    elif target_topic:
                                        action = command.get("action", "")
                                        is_silent = command.get("silent", False)
                                        is_spotify_status = (target_topic == "pc/spotify/control" and action.startswith("status_"))
                                        
                                        logging.info(f"Routing intent -> {target_topic}: {command}")
                                        await client.publish(target_topic, json.dumps(command))
                                        
                                        if not is_spotify_status and not is_silent and not self.silent_mode:
                                            await client.publish("jarvis/sys/tts_request", json.dumps({
                                                "target_topic": target_topic,
                                                "command": command,
                                                "append_followup": self.followups_enabled and not self.silent_mode and action != "discover"
                                            }))
                                        
                                        if self.active_context["type"] is not None and not self.silent_mode:
                                            final_mic_state = "request_reply"

                                if final_mic_state and final_mic_state != "request_reply":
                                    await client.publish("jarvis/sys/mic_control", json.dumps({"action": final_mic_state}))
                            else:
                                # --- 3. STANDALONE WAKE WORD FALLBACK ---
                                hallucinations = ["thank you", "thanks", "thanks for watching", "you", "a", "it"]
                                is_clean_empty = not clean_text or clean_text in hallucinations
                                
                                if is_wakeword_present and is_clean_empty and not self.silent_mode:
                                    logging.info("[DAEMON] Standalone wake word detected. Prompting user...")
                                    greeting = "Hello Sir, what can I do?" if not getattr(self, 'already_spoke', False) else "Yes sir?"
                                    self.already_spoke = True
                                    
                                    await client.publish("jarvis/sys/speak", json.dumps({
                                        "text": greeting,
                                        "request_reply": True
                                    }))
                                else:
                                    logging.warning(f"[UNMATCHED STT] Could not resolve any intent for: '{payload_data}' | Clean text: '{clean_text}'")
                                    await self.evaluate_ducking(client)
                                        
                            await client.publish("jarvis/sys/audio_process", json.dumps({"state": "idle"}))
                            await client.publish("jarvis/sys/mic_state", json.dumps({"state": "idle"}))
                        
                        elif topic == "jarvis/feedback":
                            try:
                                fb = json.loads(payload_data)
                                device = fb.get('device')
                                msg = fb.get('message', '')
                                
                                if device == 'spotify' and isinstance(msg, dict) and msg.get('query_action'):
                                    query_action = msg.get('query_action')
                                    if msg.get('status') == 'success':
                                        print("\n" + "="*55)
                                        print(f"NOW PLAYING: {msg.get('track')} - {msg.get('artist')}")
                                        print(f"CONTEXT:     {msg.get('context')}")
                                        print(f"UP NEXT:     {msg.get('next_in_queue')}")
                                        print(f"VOLUME:      {msg.get('volume')}%")
                                        print("="*55 + "\n")
                                        pseudo_cmd = {"action": query_action, **msg}
                                    else:
                                        pseudo_cmd = {"action": "status_idle"}
                                        
                                    if not fb.get('silent', False):
                                        await client.publish("jarvis/sys/tts_request", json.dumps({
                                            "target_topic": "pc/spotify/control", 
                                            "command": pseudo_cmd
                                        }))
                                
                                elif isinstance(msg, str) and "CONFIDENCE_LOW|" in msg:
                                    self.active_context = {"type": "spotify_choice", "expires_at": time.time() + 20.0}
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": "Please select an option from the terminal.", "request_reply": True}))

                                elif device == 'smart_lights' and fb.get('action') == 'awaiting_selection':
                                    self.active_context = {"type": "discovery_choice", "expires_at": time.time() + 30.0}
                                    devices = fb.get('devices', [])
                                    
                                    print("\n" + "=" * 60)
                                    print("{:^60}".format("DISCOVERED HARDWARE TARGETS"))
                                    print("=" * 60)
                                    table_str = "  {:<5} {:<8} {:<15} {:<15}\n".format("IDX", "TYPE", "MODEL", "IP")
                                    table_str += "  " + "-" * 54 + "\n"
                                    for i, dev in enumerate(devices):
                                        table_str += "  {:<5} {:<8} {:<15} {:<15}\n".format(f"[{i}]", dev['type'].upper(), dev['model'], dev['ip'])
                                    print(table_str)
                                    print("=" * 60 + "\n")
                                    
                                    ui_options = []
                                    for i, dev in enumerate(devices):
                                        if dev.get('saved_name'):
                                            ui_options.append(f"[{i}] {dev['saved_name'].replace('_', ' ').title()}")
                                        else:
                                            ui_options.append(f"[{i}] {dev['type'].upper()} {dev['model']}")
                                    await client.publish("jarvis/sys/ui_options", json.dumps({
                                        "title": "Discovered Devices",
                                        "options": ui_options if ui_options else ["No devices found"]
                                    }))

                                    if msg:
                                        await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'alarms' and fb.get('action') == 'request_delete_selection':
                                    alarms = fb.get('alarms', [])
                                    self.active_context = {"type": "alarm_delete", "expires_at": time.time() + 30.0, "alarms": alarms}
                                    if alarms:
                                        msg = f"Found {len(alarms)} active alarms. Which one would you like to delete?"
                                    else:
                                        msg = "You have no active alarms scheduled."
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True if alarms else False}))

                                elif device == 'smart_lights' and fb.get('action') == 'request_naming':
                                    self.active_context = {"type": "discovery_name", "expires_at": time.time() + 30.0, "temp_name": fb.get('temp_name')}
                                    msg = fb.get('message', '') + " What would you like to call this light?"
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'smart_lights' and fb.get('action') == 'request_light_action':
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'smart_lights' and fb.get('action') == 'request_light_remove':
                                    self.active_context = {"type": "light_remove_target", "expires_at": time.time() + 30.0}
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'smart_lights' and fb.get('action') == 'request_light_default':
                                    self.active_context = {"type": "light_default_target", "expires_at": time.time() + 30.0}
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'smart_lights' and fb.get('action') == 'request_light_rename':
                                    self.active_context = {"type": "light_rename_target", "expires_at": time.time() + 30.0}
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'utilities' and fb.get('action') == 'request_todo_add':
                                    self.active_context = {"type": "todo_add_target", "expires_at": time.time() + 30.0}
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'utilities' and fb.get('action') == 'request_calendar_add':
                                    self.active_context = {"type": "calendar_add_target", "expires_at": time.time() + 30.0}
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'utilities' and fb.get('action') == 'request_calendar_time':
                                    self.active_context = {"type": "calendar_time_target", "expires_at": time.time() + 30.0, "event": fb.get('event', '')}
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                else:
                                    is_spotify = (device == 'spotify')
                                    is_success = (fb.get('status') == 'success')
                                    is_silent = fb.get('silent', False)
                                    
                                    if msg and not is_silent:
                                        if is_spotify and is_success:
                                            pass 
                                        else:
                                            text_to_speak = msg if isinstance(msg, str) else str(msg)
                                            
                                            await client.publish("jarvis/sys/mic_control", json.dumps({"action": "cancel"}))
                                            
                                            await client.publish("jarvis/sys/speak", json.dumps({
                                                "text": text_to_speak,
                                                "request_reply": self.followups_enabled
                                            }))
                                            
                            except json.JSONDecodeError:
                                pass
                                
            except (aiomqtt.MqttError, OSError, ConnectionRefusedError) as e:
                delay = min(30, 2 ** attempt)
                logging.error(f"MQTT Connection Error: {e}. Retrying in {delay}s...")
                if 'monitor_task' in locals() and not monitor_task.done():
                    monitor_task.cancel()
                if 'config_task' in locals() and not config_task.done():
                    config_task.cancel()
                attempt += 1
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                if 'monitor_task' in locals() and not monitor_task.done():
                    monitor_task.cancel()
                if 'config_task' in locals() and not config_task.done():
                    config_task.cancel()
                break
            except Exception as e:
                delay = min(30, 2 ** attempt)
                logging.error(f"Unexpected daemon error: {type(e).__name__}: {e}. Retrying in {delay}s...")
                attempt += 1
                await asyncio.sleep(delay)

if __name__ == "__main__":
    daemon = CentralDaemon()
    asyncio.run(daemon.run())