import sys
import asyncio
import os
import json
import re
import logging
import aiomqtt

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [DAEMON] %(message)s", datefmt="%H:%M:%S")

# --- DEBUG TOGGLES ---
DEBUG_NLP = True

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- CACHE & MEMORY ---
COMPILED_TOPICS = {} 
COMPILED_ACTIONS = {}
COMPILED_COLORS = []
ROUTING_MAP = {}
LAST_KNOWN_TOPIC = None
AWAITING_DISCOVERY_CHOICE = False
AWAITING_SPOTIFY_CHOICE = False # <-- ADDED

WORD_TO_NUMBER = {
    "cem": "100", "hundred": "100", "noventa": "90", "oitenta": "80", 
    "setenta": "70", "sessenta": "60", "cinquenta": "50", "quarenta": "40", 
    "trinta": "30", "vinte": "20", "dez": "10",
    "nove": "9", "oito": "8", "sete": "7", "seis": "6", "cinco": "5",
    "quatro": "4", "três": "3", "dois": "2", "um": "1", "zero": "0",
    "nine": "9", "eight": "8", "seven": "7", "six": "6", "five": "5",
    "four": "4", "three": "3", "two": "2", "one": "1"
}

# --- LOAD CONFIGS ---
def load_configs():
    global COMPILED_TOPICS, COMPILED_ACTIONS, COMPILED_COLORS, ROUTING_MAP
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    def safe_load(filename):
        try:
            with open(os.path.join(base_dir, filename), 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logging.warning(f"File '{filename}' not found.")
        except json.JSONDecodeError as e:
            logging.critical(f"Syntax error in '{filename}': {e}")
            sys.exit(1)
        return None
    
    ROUTING_MAP = safe_load("routing.json") or {}
    topics_data = safe_load("topics.json")
    if topics_data:
        for topic, words in topics_data.items():
            COMPILED_TOPICS[topic] = re.compile(r'\b(' + '|'.join(words) + r')\b')

    actions_data = safe_load("actions.json")
    if actions_data:
        for act, words in actions_data.items():
            COMPILED_ACTIONS[act] = re.compile(r'\b(' + '|'.join(words) + r')\b')

    colors_data = safe_load("colors.json")
    if colors_data:
        for name in sorted(colors_data.keys(), key=len, reverse=True):
            regex_str = r'\b' + re.sub(r'o\b', r'[oa]s?', name) + r'\b'
            COMPILED_COLORS.append((re.compile(regex_str), colors_data[name], name))


# --- STT TEXT PROCESSING ---
def process_voice_command(text):
    global LAST_KNOWN_TOPIC, AWAITING_DISCOVERY_CHOICE, AWAITING_SPOTIFY_CHOICE
    text = text.lower()
    
    # --- ADDED: KNOWN TYPO AUTOCORRECT ---
    AUTOCORRECT = {
        "martin monish": "martim moniz",
        "martin moniche": "martim moniz",
        "marty moines": "martim moniz",
        "eric grant": "harry grande",
        "erie grant": "harry grande",
        "harry grant": "harry grande"
    }
    
    for bad, good in AUTOCORRECT.items():
        text = text.replace(bad, good)
        
    text = re.sub(r'[.,!?]', '', text)
    
    # --- ADDED: DEBUG PRINTOUT ---
    if DEBUG_NLP:
        logging.info(f"[DEBUG NLP] Cleaned & Autocorrected Text: '{text}'")
    
    for word, digit in WORD_TO_NUMBER.items():
        text = re.sub(rf'\b{word}\b', digit, text)

    # --- ADDED: SPOTIFY CHOICE INTERCEPTION ---
    if AWAITING_SPOTIFY_CHOICE:
        AWAITING_SPOTIFY_CHOICE = False
        match = re.search(r'\b(\d+)\b', text)
        if match:
            choice = int(match.group(1))
            logging.info(f"User selected Spotify option [{choice}]. Routing directly.")
            return [({"action": "play_choice", "choice_index": choice}, "pc/spotify/control")]
        else:
            logging.warning("Expected a number for Spotify choice, but none was found. Canceling selection.")
            return []

    if AWAITING_DISCOVERY_CHOICE:
        AWAITING_DISCOVERY_CHOICE = False
        match = re.search(r'\d+', text)
        if match:
            return [({"action": "save_discovery", "index": int(match.group())}, "system/discovery")]

    chunks = re.split(r'\b(?:e|and|depois|then|also)\b', text)
    intents = []

    for chunk in chunks:
        chunk = chunk.strip()
        if len(chunk) < 2: continue

        payload = {}
        target_topic = None

        # 1: Action Extraction (Longest match wins)
        best_action = None
        action_word_used = None 
        longest_match = 0
        for act, regex_obj in COMPILED_ACTIONS.items():
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
                AWAITING_DISCOVERY_CHOICE = True 

        # 2: Entity Extraction (Slot Filling for Music)
        if payload.get("action") == "play":

            topic_keywords = [word for words in [v.pattern for v in COMPILED_TOPICS.values()] for word in re.findall(r'\w+', words)]
            topic_pattern = r'\b(?:' + '|'.join(topic_keywords) + r')\b'

            stop_boundaries = rf'(?:\s+(?:on|no|em|by|artist|artista|de|do|da|playlist|lista|song|música|musica|track|som|{topic_pattern})|$)'

            # --- Slot 1: Playlist ---
            playlist_match = re.search(rf'\b(?:playlist|lista)\s+(.+?){stop_boundaries}', chunk, re.IGNORECASE)
            if playlist_match:
                payload["playlist_name"] = playlist_match.group(1).strip()
                logging.info(f"Explicit playlist detected: {payload['playlist_name']}")

            # --- Slot 2: Artist ---
            artist_match = re.search(rf'\b(?:by|artist|artista|de|do|da)\s+(.+?){stop_boundaries}', chunk, re.IGNORECASE)
            if artist_match:
                payload["artist_name"] = artist_match.group(1).strip()
                logging.info(f"Explicit artist detected: {payload['artist_name']}")

            # --- Slot 3: Track ---
            track_match = re.search(rf'\b(?:song|música|musica|track|som)\s+(.+?){stop_boundaries}', chunk, re.IGNORECASE)
            if track_match:
                payload["track_name"] = track_match.group(1).strip()
                logging.info(f"Explicit track detected: {payload['track_name']}")

            # --- Slot 4 (Greedy Positional Fallback) ---
            if not any(k in payload for k in ["playlist_name", "artist_name", "track_name"]):
                if action_word_used:
                    fallback_match = re.search(rf'\b{action_word_used}\b\s+(.+?){stop_boundaries}', chunk, re.IGNORECASE)
                    if fallback_match:
                        payload["search_query"] = fallback_match.group(1).strip()
                        logging.info(f"Greedy fallback captured query: '{payload['search_query']}'")
                    else:
                        logging.info("Generic play command detected (no explicit parameters found).")

        # 3: Topic Routing (User explicitly mentioned a topic)
        if not target_topic:
            for topic, regex_obj in COMPILED_TOPICS.items():
                if regex_obj.search(chunk):
                    target_topic = topic
                    break

        # 4: Lookup Routing Map by Action
        if not target_topic and best_action:
            for topic, actions in ROUTING_MAP.items():
                if best_action in actions:
                    target_topic = topic
                    break

        # 5: Fallback to Context Memory
        if not target_topic and LAST_KNOWN_TOPIC:
            target_topic = LAST_KNOWN_TOPIC

        if target_topic:
            LAST_KNOWN_TOPIC = target_topic

        # 6: Attributes & Colors
        temp_match = re.search(r'(\d+)\s*(porcento|percent|%).*(temperatura|temp|temps|temperature|calor|frio|hot|cold)', chunk)
        if temp_match: payload["temp"] = int(temp_match.group(1))
        else:
            pct_match = re.search(r'(\d+)\s*(porcento|percent|%)', chunk)
            if pct_match:
                valor = int(pct_match.group(1))
                if target_topic and "spotify" in target_topic:
                    payload["volume"] = valor
                    if "action" not in payload: payload["action"] = "volume"
                else: payload["lum"] = valor

        for regex_obj, color_value, _ in COMPILED_COLORS:
            if regex_obj.search(chunk):
                payload["color"] = color_value
                break

        if payload:
            intents.append((payload, target_topic))
            
            # --- ADDED: DEBUG PRINTOUT ---
            if DEBUG_NLP:
                logging.info(f"[DEBUG NLP] Extracted Payload: {json.dumps(payload, indent=2)}")

    return intents

async def run_daemon():
    logging.info("Booting Central Brain...")
    load_configs()
    logging.info("Configurations loaded. Connecting to MQTT broker...")
    
    try:
        async with aiomqtt.Client("localhost") as client:
            await client.subscribe("jarvis/sensor/voice")
            await client.subscribe("jarvis/feedback")
            logging.info("Online. Listening on topics 'jarvis/sensor/voice' and 'jarvis/feedback'...")
            
            async for message in client.messages:
                topic = message.topic.value
                payload_data = message.payload.decode('utf-8')
                
                # --- SENSOR INGESTION ROUTING ---
                if topic == "jarvis/sensor/voice":
                    logging.info(f"Transcript Received: '{payload_data}'")
                    intents = process_voice_command(payload_data)
                    
                    if intents:
                        for command, target_topic in intents:
                            if target_topic:
                                logging.info(f"Intent Decoded: {command} -> Routing to [{target_topic}]")
                                await client.publish(target_topic, json.dumps(command))
                            else:
                                logging.warning(f"Intent decoded ({command}), but no target topic known. Ignoring.")
                            await asyncio.sleep(0.1)
                    else:
                        logging.warning("Could not extract a valid command.")
                
                # --- FEEDBACK OBSERVABILITY LOGGING ---
                elif topic == "jarvis/feedback":
                    try:
                        fb = json.loads(payload_data)
                        msg = fb.get('message', '')
                        
                        # --- ADDED: TRIGGER SPOTIFY CHOICE STATE MACHINE ---
                        if "CONFIDENCE_LOW|" in msg:
                            global AWAITING_SPOTIFY_CHOICE
                            AWAITING_SPOTIFY_CHOICE = True
                            clean_msg = msg.replace("CONFIDENCE_LOW|", "Low Confidence. Please say the number of your choice:\n")
                            logging.warning(f"Spotify requires user input:\n{clean_msg}")
                        else:
                            status_icon = "V" if fb.get('status') == "success" else "X"
                            logging.info(f"Feedback [{fb.get('device', 'unknown')}]: {status_icon} {msg}")
                            
                    except json.JSONDecodeError:
                        logging.error("Received malformed feedback packet.")
                        
    except aiomqtt.MqttError as e:
        logging.error(f"MQTT Connection Error: {e}")
    except asyncio.CancelledError:
        logging.info("Shutting down Central Brain.")

if __name__ == "__main__":
    asyncio.run(run_daemon())