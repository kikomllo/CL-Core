# --- IMPORTS ---
import sys
import asyncio
import os
import json
import re
import logging
import random
import aiomqtt
from rapidfuzz import process, fuzz
from typing import Dict, List, Tuple, Any, Optional

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="\r\033[K[%(asctime)s] [DAEMON] %(message)s", datefmt="%H:%M:%S")

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class CentralDaemon:
    """CPU-Optimized Fuzzy Intent Engine"""
    
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_dir = os.path.abspath(os.path.join(self.base_dir, "..", "config"))
        
        self.tts_responses = self._safe_load("tts_responses.json") or {}
        self.intents_data = self._safe_load("intents.json") or {}
        
        # State Context
        self.awaiting_discovery_choice = False
        self.awaiting_spotify_choice = False
        
        # Build the flat template list for RapidFuzz
        self.flat_templates = []
        self.template_to_intent = {}
        self._build_fuzzy_corpus()

    def _safe_load(self, filename: str) -> Optional[Any]:
        try:
            with open(os.path.join(self.config_dir, filename), 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load {filename}: {e}")
            return None

    def _build_fuzzy_corpus(self) -> None:
        """Flattens intents.json into a corpus RapidFuzz can search against."""
        for intent_name, config in self.intents_data.items():
            for template in config.get("templates", []):
                clean_template = template
                if "{" in template and "}" in template:
                    clean_template = template.split("{")[0].strip()
                
                if clean_template:
                    if clean_template not in self.flat_templates:
                        self.flat_templates.append(clean_template)
                    
                    if clean_template not in self.template_to_intent:
                        self.template_to_intent[clean_template] = []
                        
                    self.template_to_intent[clean_template].append({
                        "intent_name": intent_name,
                        "target_topic": config.get("target_topic"),
                        "action_override": config.get("action_override"),
                        "original_template": template
                    })
        logging.info(f"Fuzzy Engine initialized with {len(self.flat_templates)} mapping targets.")

    def extract_variables(self, chunk: str, intent_match: Dict) -> Dict[str, Any]:
        """Fuzzy-friendly slot extraction using Typo-Forgiving Edge-Stripping."""
        from rapidfuzz import fuzz
        import re
        
        payload = {"action": intent_match["action_override"]}
        template = intent_match["original_template"]
        
        if "{" in template and "}" in template:
            var_start = template.find("{") + 1
            var_end = template.find("}")
            var_name = template[var_start:var_end]
            
            clean_template = template.replace(f"{{{var_name}}}", "").strip()
            
            chunk_clean = re.sub(r'[^\w\s]', '', chunk)
            chunk_words = chunk_clean.split()
            
            template_words = set(clean_template.split())
            expanded_template_words = set()
            for w in template_words:
                expanded_template_words.add(w)
                if w.endswith('s'): 
                    expanded_template_words.add(w[:-1])
                else: 
                    expanded_template_words.add(w + 's')
                
            stop_words = {"please", "it", "a", "some", "my", "the", "can", "you", "could", "to", "for", "track", "song"}
            words_to_remove = expanded_template_words.union(stop_words)
            
            # --- NEW: Typo-forgiving strip logic ---
            def should_strip(word: str) -> bool:
                if word in words_to_remove:
                    return True
                # If word is a typo of a template word (e.g., 'playy' vs 'play')
                if len(word) > 3:
                    for w in words_to_remove:
                        if len(w) > 3 and fuzz.ratio(word, w) >= 80:
                            return True
                return False

            # Strip from left
            while chunk_words and should_strip(chunk_words[0]):
                chunk_words.pop(0)
                
            # Strip from right
            while chunk_words and should_strip(chunk_words[-1]):
                chunk_words.pop()
            
            variable_value = " ".join(chunk_words)
            
            if variable_value:
                if variable_value.isdigit():
                    payload[var_name] = int(variable_value)
                elif var_name in ["lum", "volume", "choice_index", "index"]:
                    nums = re.findall(r'\d+', variable_value)
                    if nums:
                        payload[var_name] = int(nums[0])
                    else:
                        payload[var_name] = variable_value
                else:
                    payload[var_name] = variable_value
                    
        return payload

    def process_voice_command(self, text: str) -> List[Tuple[Dict[str, Any], str]]:
        """Parses audio transcripts against the Fuzzy Engine with Specificity Tie-Breaking."""
        import re
        text = text.lower().strip()
        
        if self.awaiting_spotify_choice and text.isdigit():
            self.awaiting_spotify_choice = False
            return [({"action": "play_choice", "choice_index": int(text)}, "pc/spotify/control")]
            
        if self.awaiting_discovery_choice and text.isdigit():
            self.awaiting_discovery_choice = False
            return [({"action": "save_discovery", "index": int(text)}, "system/discovery")]

        if any(abort_word in text for abort_word in ["abort", "cancel", "nevermind"]):
            self.awaiting_spotify_choice = False
            self.awaiting_discovery_choice = False
            return [({"action": "abort"}, "jarvis/sys/control")]

        text = text.replace(",", "")
        
        text = text.replace("playlists", "playlist")
        text = text.replace("lights", "light")
        text = text.replace("songs", "song")

        chunks = re.split(r'\b(?:and|then)\b', text)
        executed_intents = []
        
        for chunk in chunks:
            chunk = chunk.strip()
            if len(chunk) < 3: 
                continue
                
            matches = process.extract(
                chunk, 
                self.flat_templates, 
                scorer=fuzz.token_set_ratio,
                limit=10
            )
            
            if matches:
                # Lowered from 75 to 70 to allow for heavy text typos like "playy"
                valid_matches = [m for m in matches if m[1] > 70]
                
                if valid_matches:
                    valid_matches.sort(key=lambda x: (x[1], len(x[0])), reverse=True)
                    
                    best_match = valid_matches[0]
                    matched_phrase = best_match[0]
                    score = best_match[1]
                    
                    possible_intents = self.template_to_intent[matched_phrase]
                    intent_info = possible_intents[0] 
                    target_topic = intent_info["target_topic"]
                    
                    payload = self.extract_variables(chunk, intent_info)
                    
                    logging.info(f"[FUZZY MATCH] {intent_info['intent_name']} (Confidence: {score}%) | Payload: {payload}")
                    executed_intents.append((payload, target_topic))
                else:
                    logging.warning(f"Low confidence fuzzy match for chunk: '{chunk}'")

        return executed_intents

    async def dispatch_tts_response(self, client: aiomqtt.Client, command: Dict[str, Any], target_topic: str) -> None:
        """Handles dynamic voice feedback post-routing."""
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
            except KeyError:
                phrase = raw_phrase # Fallback if variables are missing

            await client.publish("jarvis/sys/speak", json.dumps({"text": phrase}))
            
    async def run(self) -> None:
        """Main non-blocking MQTT loop."""
        logging.info("Template Intent Engine Online. Connecting to MQTT broker...")
        while True:
            try:
                async with aiomqtt.Client("localhost") as client:
                    await client.subscribe("jarvis/sensor/voice")
                    await client.subscribe("jarvis/feedback")
                    
                    async for message in client.messages:
                        topic = message.topic.value
                        payload_data = message.payload.decode('utf-8')
                        
                        if topic == "jarvis/sensor/voice":
                            intents = self.process_voice_command(payload_data)
                            
                            if intents:
                                final_mic_state = "open_window"
                                
                                for command, target_topic in intents:
                                    if target_topic == "jarvis/sys/control" and command.get("action") == "abort":
                                        logging.info("[SYSTEM] Abort sequence initiated.")
                                        await client.publish("jarvis/sys/tts_stop", "1")
                                        await client.publish("jarvis/sys/speak", json.dumps({"text": "Aborted.", "request_reply": False}))
                                        final_mic_state = None  
                                        break
                                        
                                    elif target_topic:
                                        await client.publish(target_topic, json.dumps(command))
                                        await self.dispatch_tts_response(client, command, target_topic)
                                        
                                        if self.awaiting_discovery_choice or self.awaiting_spotify_choice:
                                            final_mic_state = "request_reply"
                                            
                                if final_mic_state:
                                    await client.publish("jarvis/sys/mic_control", json.dumps({"action": final_mic_state}))
                        
                        # Handle feedback loops
                        elif topic == "jarvis/feedback":
                            try:
                                fb = json.loads(payload_data)
                                msg = fb.get('message', '')
                                
                                if "CONFIDENCE_LOW|" in msg:
                                    self.awaiting_spotify_choice = True
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": "Please select an option from the terminal.", "request_reply": True}))
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