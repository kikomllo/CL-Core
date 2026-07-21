# --- IMPORTS ---
import sys
import asyncio
import os
import json
import logging
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
        
        self.awaiting_discovery_choice = False
        self.awaiting_spotify_choice = False
        
        self.nlp = IntentEngine(intents_data, word_to_number, abort_keywords)

    def route_voice_command(self, payload_data: str) -> List[Tuple[Dict[str, Any], str]]:
        clean_text = self.nlp.normalize_text(payload_data)

        if self.nlp.is_abort_command(clean_text):
            self.awaiting_spotify_choice = False
            self.awaiting_discovery_choice = False
            return [({"action": "abort"}, "jarvis/sys/control")]

        if self.awaiting_spotify_choice and clean_text.isdigit():
            self.awaiting_spotify_choice = False
            return [({"action": "play_choice", "choice_index": int(clean_text)}, "pc/spotify/control")]

        if self.awaiting_discovery_choice and clean_text.isdigit():
            self.awaiting_discovery_choice = False
            return [({"action": "save_discovery", "index": int(clean_text)}, "system/discovery")]

        return self.nlp.parse(clean_text)

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
                                        
                                        if self.awaiting_discovery_choice or self.awaiting_spotify_choice:
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
                                    self.awaiting_spotify_choice = True
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": "Please select an option from the terminal.", "request_reply": True}))
                                    await client.publish("jarvis/sys/mic_control", json.dumps({"action": "request_reply"}))

                                elif device == 'smart_lights' and fb.get('action') == 'awaiting_selection':
                                    self.awaiting_discovery_choice = True
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