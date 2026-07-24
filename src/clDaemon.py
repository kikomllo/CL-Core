# --- IMPORTS ---
import sys
import asyncio
import os
import json
import logging
import time
import aiomqtt
from typing import Dict, List, Tuple, Any

# --- CUSTOM MODULES ---
from utils.clConfigLoader import ConfigLoader
from nlp.clIntentEngine import IntentEngine

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="\r\033[K[%(asctime)s] [DAEMON] %(message)s", datefmt="%H:%M:%S")

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

        # 4. Standard NLP Parsing & Shadowing Execution
        raw_intents = self.nlp.parse(clean_text)
        return self._optimize_intent_queue(raw_intents)

    async def run(self) -> None:
        logging.info("Central Daemon Online. Connecting to MQTT broker...")
        while True:
            try:
                async with aiomqtt.Client("localhost") as client:
                    await client.subscribe("jarvis/sensor/voice")
                    await client.subscribe("jarvis/feedback")
                    
                    async for message in client.messages:
                        topic = message.topic.value
                        payload_data = message.payload.decode('utf-8')
                        
                        if topic == "jarvis/sensor/voice":
                            intents = self.route_voice_command(payload_data)
                            
                            if intents:
                                final_mic_state = "open_window"
                                
                                for command, target_topic in intents:
                                    if target_topic == "jarvis/sys/control" and command.get("action") == "abort":
                                        logging.info("[SYSTEM] Abort sequence initiated.")
                                        await client.publish("jarvis/sys/tts_stop", "1")
                                        final_mic_state = None  
                                        break
                                        
                                    elif target_topic:
                                        await client.publish(target_topic, json.dumps(command))
                                        
                                        action = command.get("action", "")
                                        is_spotify_status = (target_topic == "pc/spotify/control" and action.startswith("status_"))
                                        
                                        if not is_spotify_status:
                                            # Send a blind TTS request to the dedicated TTS service
                                            await client.publish("jarvis/sys/tts_request", json.dumps({
                                                "target_topic": target_topic, 
                                                "command": command
                                            }))
                                        
                                        if self.active_context["type"] is not None:
                                            final_mic_state = "request_reply"
                                            
                                if final_mic_state:
                                    await client.publish("jarvis/sys/mic_control", json.dumps({"action": final_mic_state}))
                        
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
                                    await client.publish("jarvis/sys/mic_control", json.dumps({"action": "request_reply"}))

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
                                    
                                    if msg:
                                        await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))
                                        await client.publish("jarvis/sys/mic_control", json.dumps({"action": "request_reply"}))
                                        
                            except json.JSONDecodeError:
                                pass
                                
            except aiomqtt.MqttError as e:
                logging.error(f"MQTT Error: {e}. Retrying in 5s...")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break

if __name__ == "__main__":
    daemon = CentralDaemon()
    asyncio.run(daemon.run())