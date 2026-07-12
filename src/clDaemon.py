# --- IMPORTS ---
import sys
import asyncio
import os
import json
import re
import logging
import random
import aiomqtt
from typing import Dict, List, Tuple, Any, Optional, Pattern

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="\r\033[K[%(asctime)s] [DAEMON] %(message)s", datefmt="%H:%M:%S")

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class CentralDaemon:
    """Enterprise class for Natural Language Processing, intent extraction, and ecosystem routing."""
    
    def __init__(self, debug_nlp: bool = True):
        self.debug_nlp: bool = debug_nlp
        
        # Pathing
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        self.config_dir: str = os.path.abspath(os.path.join(self.base_dir, "..", "config"))
        
        # NLP Memory & Configuration
        self.topics: Dict[str, Pattern] = {}
        self.actions: Dict[str, Pattern] = {}
        self.colors: List[Tuple[Pattern, str, str]] = []
        self.routing_map: Dict[str, List[str]] = {}
        self.nlp_rules: Dict[str, Any] = {}
        self.personality_eggs: Dict[str, str] = {}
        self.module_lookup: Dict[str, str] = {}
        self.tts_responses: Dict[str, Dict[str, List[str]]] = {} 
        
        # Compiled Regexes
        self.abort_regex: Optional[Pattern] = None
        self.restart_regex: Optional[Pattern] = None
        
        # Contextual State Memory
        self.last_known_topic: Optional[str] = None
        self.awaiting_discovery_choice: bool = False
        self.awaiting_spotify_choice: bool = False

        # Boot Sequence
        self._load_configs()

    def _safe_load(self, filename: str) -> Optional[Any]:
        """Safely parses JSON configs from the config directory."""
        try:
            with open(os.path.join(self.config_dir, filename), 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logging.warning(f"File '{filename}' not found.")
        except json.JSONDecodeError as e:
            logging.critical(f"Syntax error in '{filename}': {e}")
            sys.exit(1)
        return None

    def _load_configs(self) -> None:
        """Hydrates the Daemon's brain with vocabulary, rules, and system maps."""
        self.routing_map = self._safe_load("routing.json") or {}
        
        topics_data = self._safe_load("topics.json") or {}
        for topic, words in topics_data.items():
            self.topics[topic] = re.compile(r'\b(' + '|'.join(words) + r')\b')

        actions_data = self._safe_load("actions.json") or {}
        for act, words in actions_data.items():
            self.actions[act] = re.compile(r'\b(' + '|'.join(words) + r')\b')
            
        self.nlp_rules = self._safe_load("nlp_rules.json") or {}
        abort_words = self.nlp_rules.get("abort_keywords", [])
        if abort_words:
            self.abort_regex = re.compile(r'\b(?:' + '|'.join(abort_words) + r')\b', re.IGNORECASE)

        colors_data = self._safe_load("colors.json") or {}
        for name in sorted(colors_data.keys(), key=len, reverse=True):
            regex_str = r'\b' + re.sub(r'o\b', r'[oa]s?', name) + r'\b'
            self.colors.append((re.compile(regex_str), colors_data[name], name))
            
        self.personality_eggs = self._safe_load("easter_eggs.json") or {}

        microservices_data = self._safe_load("microservices.json") or {}
        aliases = []
        for filename, trigger_words in microservices_data.items():
            for word in trigger_words:
                clean_word = word.lower().strip()
                self.module_lookup[clean_word] = filename
                aliases.append(re.escape(clean_word))
                
        if aliases:
            regex_str = r'\b(?:restart|reboot|reload)\s+module\s+(' + '|'.join(aliases) + r')\b'
            self.restart_regex = re.compile(regex_str, re.IGNORECASE)
            
        self.tts_responses = self._safe_load("tts_responses.json") or {}

    # --- TEXT-TO-SPEECH DISPATCHER ---
    async def dispatch_tts_response(self, client: aiomqtt.Client, command: Dict[str, Any], target_topic: str) -> None:
        action = command.get("action")
        response_key = action

        if target_topic == "home/room/desk_light/set":
            if "color" in command: response_key = "color"
            elif "lum" in command: response_key = "lum"
            elif "temp" in command: response_key = "temp"
            
        elif target_topic == "pc/spotify/control" and action == "play":
            if "track_name" in command and "artist_name" in command: response_key = "play_track_artist"
            elif "track_name" in command: response_key = "play_track"
            elif "artist_name" in command: response_key = "play_artist"
            elif "playlist_name" in command: response_key = "play_playlist"

        topic_responses = self.tts_responses.get(target_topic, {})
        phrases = topic_responses.get(response_key, [])

        if phrases:
            raw_phrase = random.choice(phrases)
            try:
                phrase = raw_phrase.format(**command)
            except KeyError as e:
                logging.warning(f"Missing variable for TTS string formatting: {e}")
                phrase = raw_phrase.replace(f"{{{e.args[0]}}}", "unknown")

            await client.publish("jarvis/sys/speak", json.dumps({"text": phrase}))
            
    # --- NLP EXTRACTION ENGINE ---
    def process_voice_command(self, text: str) -> List[Tuple[Dict[str, Any], Optional[str]]]:
        text = text.lower()
        
        for trigger, response in self.personality_eggs.items():
            if trigger in text:
                return [({"action": "personality", "response": response}, "jarvis/sys/speak")]
                
        global_restart_match = re.search(r'\b(?:(?:restart|reboot|reload)\s+all\s+modules?|(?:full|complete)\s+system\s+(?:reboot|restart|reload))\b', text, re.IGNORECASE)
        if global_restart_match:
            logging.info("Global microservice ecosystem restart requested.")
            return [({"action": "restart_all_modules"}, "jarvis/sys/manager")]

        if self.restart_regex:
            module_match = self.restart_regex.search(text)
            if module_match:
                trigger_word = module_match.group(1).lower()
                target_script = self.module_lookup.get(trigger_word)
                if target_script:
                    logging.info(f"Microservice restart requested for: {target_script}")
                    return [({"action": "restart_module", "target": target_script}, "jarvis/sys/manager")]
        
        if self.abort_regex and self.abort_regex.search(text):
            logging.info("User explicitly cancelled the command. Resetting states.")
            self.awaiting_discovery_choice = False
            self.awaiting_spotify_choice = False
            return [({"action": "abort"}, "jarvis/sys/control")]

        for bad, good in self.nlp_rules.get("autocorrect", {}).items():
            text = text.replace(bad, good)
            
        text = re.sub(r'[,!?]', '', text)
        text = re.sub(r'\.(?!\w)', '', text)
        
        if self.debug_nlp:
            logging.info(f"[DEBUG NLP] Cleaned Text: '{text}'")
        
        for word, digit in self.nlp_rules.get("word_to_number", {}).items():
            text = re.sub(rf'\b{word}\b', digit, text)

        if self.awaiting_spotify_choice:
            self.awaiting_spotify_choice = False
            match = re.search(r'\b(\d+)\b', text)
            if match:
                choice = int(match.group(1))
                logging.info(f"User selected Spotify option [{choice}].")
                return [({"action": "play_choice", "choice_index": choice}, "pc/spotify/control")]
            return []

        if self.awaiting_discovery_choice:
            self.awaiting_discovery_choice = False
            match = re.search(r'\d+', text)
            if match:
                return [({"action": "save_discovery", "index": int(match.group())}, "system/discovery")]

        chunks = re.split(r'\b(?:e|and|depois|then|also)\b', text)
        intents = []

        for chunk in chunks:
            chunk = chunk.strip()
            if len(chunk) < 2: continue

            payload: Dict[str, Any] = {}
            target_topic = None
            best_action = None
            action_word_used = None 
            longest_match = 0
            
            for act, regex_obj in self.actions.items():
                match = regex_obj.search(chunk)
                if match:
                    matched_word = match.group(1)
                    if len(matched_word) > longest_match:
                        longest_match = len(matched_word)
                        best_action = act
                        action_word_used = matched_word 
            
            if best_action:
                payload["action"] = best_action
                if best_action == "discover":
                    target_topic = "system/discovery"
                    self.awaiting_discovery_choice = True 

            if payload.get("action") == "play" or re.search(r'\b(?:song|track|music|música|musica)\b', chunk):
                if payload.get("action") in ["on", "off", "toggle"]:
                    payload["action"] = "play"

                topic_keywords = [word for words in [v.pattern for v in self.topics.values()] for word in re.findall(r'\w+', words)]
                topic_pattern = r'\b(?:' + '|'.join(topic_keywords) + r')\b'
                stop_boundaries = rf'(?:\s+(?:on|no|em|by|my|artist|artista|de|do|da|playlist|lista|song|música|musica|track|som|{topic_pattern})|$)'

                playlist_match = re.search(rf'\b(?:playlists?|listas?)\s+(.+?){stop_boundaries}', chunk, re.IGNORECASE)
                if playlist_match: payload["playlist_name"] = playlist_match.group(1).strip()

                artist_match = re.search(rf'\b(?:by|my|artists?|artistas?|de|do|da)\s+(.+?){stop_boundaries}', chunk, re.IGNORECASE)
                if artist_match: payload["artist_name"] = artist_match.group(1).strip()

                track_match = re.search(rf'\b(?:songs?|músicas?|musicas?|tracks?|sons?)\s+(.+?){stop_boundaries}', chunk, re.IGNORECASE)
                if track_match: payload["track_name"] = track_match.group(1).strip()
                    
                if "track_name" not in payload and "artist_name" in payload:
                    implicit_match = re.search(rf'\b(?:play|tocar)\s+(.+?)\s+(?:by|my|de|do|da)\b', chunk, re.IGNORECASE)
                    if implicit_match: payload["track_name"] = implicit_match.group(1).strip()

            elif payload.get("action") in ["open", "close", "search", "open_site"] and action_word_used:
                if payload.get("action") == "search":
                    sys_match = re.search(rf'\b{action_word_used}\b\s+(?:(?:online|for|sobre|por|na internet|the web for)\s+)*(.+)', chunk, re.IGNORECASE)
                elif payload.get("action") == "open_site":
                    sys_match = re.search(rf'\b{action_word_used}\b\s+(?:(?:the site|o site|online|website)\s+)*(.+)', chunk, re.IGNORECASE)
                else:
                    sys_match = re.search(rf'\b{action_word_used}\b\s+(?:(?:to|para|the|o|a|pasta|folder|dir|directory|app|aplicativo)\s+)*(.+)', chunk, re.IGNORECASE)
                
                if sys_match: payload["target"] = sys_match.group(1).strip()

            if not target_topic:
                for topic, regex_obj in self.topics.items():
                    if regex_obj.search(chunk):
                        target_topic = topic
                        break

            if not target_topic and best_action:
                for topic, actions in self.routing_map.items():
                    if best_action in actions:
                        target_topic = topic
                        break

            if not target_topic and self.last_known_topic:
                target_topic = self.last_known_topic

            if target_topic:
                self.last_known_topic = target_topic

            temp_match = re.search(r'(\d+)\s*(porcento|percent|%).*(temperatura|temp|temps|temperature|calor|frio|hot|cold)', chunk)
            if temp_match: 
                payload["temp"] = int(temp_match.group(1))
            else:
                pct_match = re.search(r'(\d+)\s*(porcento|percent|%)', chunk)
                if pct_match:
                    valor = int(pct_match.group(1))
                    if target_topic and "spotify" in target_topic:
                        payload["volume"] = valor
                        if "action" not in payload: payload["action"] = "volume"
                    else: payload["lum"] = valor

            for regex_obj, color_value, _ in self.colors:
                if regex_obj.search(chunk):
                    payload["color"] = color_value
                    break

            if payload:
                intents.append((payload, target_topic))
                if self.debug_nlp:
                    logging.info(f"[DEBUG NLP] Extracted Payload: {json.dumps(payload, indent=2)}")

        return intents

    # --- ASYNC SUPERVISOR ---
    async def run(self) -> None:
        """Main non-blocking MQTT loop for the Daemon."""
        logging.info("Configurations loaded. Connecting to MQTT broker...")
        
        try:
            async with aiomqtt.Client("localhost") as client:
                await client.subscribe("jarvis/sensor/voice")
                await client.subscribe("jarvis/feedback")
                logging.info("Online. Listening on topics...")
                
                async for message in client.messages:
                    topic = message.topic.value
                    payload_data = message.payload.decode('utf-8')
                    
                    if topic == "jarvis/sensor/voice":
                        logging.info(f"Transcript Received: '{payload_data}'")
                        intents = self.process_voice_command(payload_data)
                        
                        if intents:
                            for command, target_topic in intents:
                                if target_topic == "jarvis/sys/control" and command.get("action") == "abort":
                                    await client.publish("jarvis/sys/tts_stop", "1")
                                    await client.publish("jarvis/sys/speak", json.dumps({
                                        "text": "Command aborted, sir.", "request_reply": False
                                    }))
                                elif command.get("action") == "personality":
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": command["response"]}))
                                elif target_topic:
                                    logging.info(f"Intent Decoded: {command} -> Routing to [{target_topic}]")
                                    await client.publish(target_topic, json.dumps(command))
                                    asyncio.create_task(self.dispatch_tts_response(client, command, target_topic))
                                    
                                    # --- CONTINUOUS CONVERSATION TRIGGER ---
                                    # Keeps the mic hot after execution. Ignored for mic_control 
                                    # so saying "go to sleep" actually allows the mic to close.
                                    if target_topic != "jarvis/sys/mic_control":
                                        await client.publish("jarvis/sys/mic_control", json.dumps({"action": "open_window"}))
                                        
                                else:
                                    logging.warning(f"Intent decoded ({command}), but no target topic known.")
                                await asyncio.sleep(0.1)
                        else:
                            # --- SILENT REJECTION ---
                            logging.info(f"[SILENT REJECTION] Ignored background conversation: '{payload_data}'")
                    
                    elif topic == "jarvis/feedback":
                        try:
                            fb = json.loads(payload_data)
                            msg = fb.get('message', '')
                            
                            if "CONFIDENCE_LOW|" in msg:
                                self.awaiting_spotify_choice = True
                                options_text = msg.split("CONFIDENCE_LOW|")[1] if "CONFIDENCE_LOW|" in msg else msg
                                
                                print("\n" + "="*50)
                                print("SPOTIFY MULTIPLE MATCHES FOUND. AWAITING YOUR SELECTION:")
                                print(options_text.strip())
                                print("="*50 + "\n")
                                
                                await client.publish("jarvis/sys/speak", json.dumps({
                                    "text": "My apologies, sir. The audio match was ambiguous. Could you select an option from the terminal?",
                                    "request_reply": True 
                                }))
                                await client.publish("jarvis/sys/mic_control", json.dumps({"action": "request_reply"}))
                            else:
                                status_icon = "V" if fb.get('status') == "success" else "X"
                                logging.info(f"Feedback [{fb.get('device', 'unknown')}]: {status_icon} {msg}")
                                
                        except json.JSONDecodeError:
                            logging.error("Received malformed feedback packet.")
                            
        except aiomqtt.MqttError as e:
            logging.error(f"MQTT Connection Error: {e}")
        except asyncio.CancelledError:
            logging.info("Shutting down Central Brain.")

# --- MAIN ---
if __name__ == "__main__":
    logging.info("Booting Central Brain...")
    daemon = CentralDaemon(debug_nlp=True)
    asyncio.run(daemon.run())