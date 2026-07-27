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
        
        nlp_rules = core_data.get("nlp_rules", {})
        word_to_number = nlp_rules.get("word_to_number", {})
        abort_keywords = nlp_rules.get("abort_keywords", ["abort", "cancel", "nevermind"])
        
        # --- UNIFIED STATE MACHINE ---
        # Replaces isolated boolean flags with a scalable, time-aware context tracker
        self.active_context = {
            "type": None, 
            "expires_at": 0.0
        }
        
        self.nlp = IntentEngine(intents_data, word_to_number, abort_keywords)
        self.intents_file_path = os.path.join(os.path.dirname(__file__), "..", "config", "intents.json")
        self.last_intents_mtime = os.stat(self.intents_file_path).st_mtime if os.path.exists(self.intents_file_path) else 0
        self.followups_enabled = core_data.get("settings", {}).get("enable_followup", True)

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

    def route_voice_command(self, payload_data: str) -> List[Tuple[Dict[str, Any], str]]:
        clean_text = self.nlp.normalize_text(payload_data)

        # 1. Global Abort Check
        if self.nlp.is_abort_command(clean_text):
            self.active_context["type"] = None  # Purge active state
            return [({"action": "abort"}, "jarvis/sys/control")]

        # 2. State Timeout Check
        if self.active_context["type"] and time.time() > self.active_context["expires_at"]:
            logging.info(f"[STATE] Active context '{self.active_context['type']}' expired.")
            self.active_context["type"] = None

        # 3. Contextual Routing
        if self.active_context["type"] == "spotify_choice" and clean_text.isdigit():
            self.active_context["type"] = None
            return [({"action": "play_choice", "choice_index": int(clean_text)}, "pc/spotify/control")]

        if self.active_context["type"] == "discovery_choice" and clean_text.isdigit():
            self.active_context["type"] = None
            return [({"action": "save_discovery", "index": int(clean_text)}, "system/discovery")]

        if self.active_context["type"] == "discovery_name":
            temp_name = self.active_context.get("temp_name", "unknown")
            self.active_context["type"] = None
            if clean_text.lower() == "skip":
                return []
            return [({"action": "intent_rename_light", "target_str": f"{temp_name} to {clean_text}"}, "home/room/all/set")]

        if self.active_context["type"] == "light_remove_target":
            self.active_context["type"] = None
            if clean_text.lower() == "cancel": return []
            return [({"action": "intent_remove_light", "target_str": clean_text}, "home/room/all/set")]

        if self.active_context["type"] == "light_default_target":
            self.active_context["type"] = None
            if clean_text.lower() == "cancel": return []
            return [({"action": "intent_set_default_light", "target_str": clean_text}, "home/room/all/set")]

        if self.active_context["type"] == "light_rename_target":
            self.active_context["type"] = None
            if clean_text.lower() == "cancel": return []
            return [({"action": "intent_rename_light", "target_str": clean_text}, "home/room/all/set")]

        # 4. Standard NLP Parsing & Shadowing Execution
        raw_intents = self.nlp.parse(clean_text)
        return self._optimize_intent_queue(raw_intents)

    async def monitor_timeouts(self, client: aiomqtt.Client):
        while True:
            await asyncio.sleep(1)
            if self.active_context["type"] and time.time() > self.active_context["expires_at"]:
                logging.info(f"[STATE] Active context '{self.active_context['type']}' expired passively.")
                self.active_context = {"type": None, "expires_at": 0.0}
                await client.publish("jarvis/sys/ui_options", json.dumps({"options": []}))
                await client.publish("jarvis/sys/mic_control", json.dumps({"action": "cancel"}))

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
                    logging.info("--- DAEMON READY: Listening for commands ---")
                    
                    async for message in client.messages:
                        topic = message.topic.value
                        payload_data = message.payload.decode('utf-8')
                        
                        if topic == "jarvis/sensor/voice":
                            logging.info(f"Voice command received: '{payload_data}'")
                            intents = self.route_voice_command(payload_data)
                            
                            if intents:
                                final_mic_state = "open_window"
                                
                                for command, target_topic in intents:
                                    if target_topic == "jarvis/sys/control" and command.get("action") == "abort":
                                        logging.info("[SYSTEM] Abort sequence initiated.")
                                        await client.publish("jarvis/sys/tts_stop", "1")
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
                                        await client.publish("jarvis/sys/speak", json.dumps({"text": f"Follow ups are now {state_str}."}))
                                        final_mic_state = None
                                        continue

                                    elif target_topic:
                                        logging.info(f"Routing intent -> {target_topic}: {command}")
                                        await client.publish(target_topic, json.dumps(command))
                                        
                                        action = command.get("action", "")
                                        is_spotify_status = (target_topic == "pc/spotify/control" and action.startswith("status_"))
                                        
                                        if not is_spotify_status:
                                            # Send a blind TTS request to the dedicated TTS service
                                            await client.publish("jarvis/sys/tts_request", json.dumps({
                                                "target_topic": target_topic,
                                                "command": command,
                                                "append_followup": self.followups_enabled and action != "discover"
                                            }))
                                        
                                        if self.active_context["type"] is not None:
                                            final_mic_state = "request_reply"

                                if final_mic_state and final_mic_state != "request_reply":
                                    await client.publish("jarvis/sys/mic_control", json.dumps({"action": final_mic_state}))
                            else:
                                logging.warning(f"No intent matched for: '{payload_data}'")
                            
                            await client.publish("jarvis/sys/audio_process", json.dumps({"state": "idle"}))
                        
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

                                else:
                                    if msg:
                                        await client.publish("jarvis/sys/speak", json.dumps({"text": msg}))
                                        
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