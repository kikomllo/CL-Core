import sys
import asyncio
import os
import json
import re
import logging
import aiomqtt
import random

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [DAEMON] %(message)s", datefmt="%H:%M:%S")

DEBUG_NLP = True

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

COMPILED_TOPICS = {} 
COMPILED_ACTIONS = {}
COMPILED_COLORS = []
ROUTING_MAP = {}
LAST_KNOWN_TOPIC = None
AWAITING_DISCOVERY_CHOICE = False
AWAITING_SPOTIFY_CHOICE = False

EASTER_EGGS = {}

NLP_RULES = {}
COMPILED_ABORT_REGEX = None

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
            
    global NLP_RULES, COMPILED_ABORT_REGEX
    NLP_RULES = safe_load("nlp_rules.json") or {}
    
    abort_words = NLP_RULES.get("abort_keywords", [])
    if abort_words:
        COMPILED_ABORT_REGEX = re.compile(r'\b(?:' + '|'.join(abort_words) + r')\b', re.IGNORECASE)

    colors_data = safe_load("colors.json")
    if colors_data:
        for name in sorted(colors_data.keys(), key=len, reverse=True):
            regex_str = r'\b' + re.sub(r'o\b', r'[oa]s?', name) + r'\b'
            COMPILED_COLORS.append((re.compile(regex_str), colors_data[name], name))
            
    global PERSONALITY_EGGS
    PERSONALITY_EGGS = safe_load("easter_eggs.json") or {}

async def dispatch_tts_response(client, command, target_topic):
    action = command.get("action")
    phrase = ""

    # 1. Handle Light commands (clControl)
    if target_topic == "home/room/desk_light/set":
        if action == "on": 
            phrase = random.choice([
                "At your service, sir. Lights on.",
                "Powering them up.",
                "As always, sir. Turning them on.",
                "Let there be light!",
            ])
        elif action == "off": 
            phrase = random.choice([
                "Bravo going dark!",
                "Preparing to power down. Have a good night, sir.",
                "Extinguishing the light arrays. Let me know if you require anything else.",
                "Going dark, sir. House protocols are on standby."
            ])
        elif action == "toggle": 
            phrase = random.choice([
                "Reconfiguring the ambient illumination, sir.",
                "Switching the light state as requested.",
                "Of course sir, changing light state.",
                "Changing light state.",
            ])
        
        if "color" in command:
            phrase = random.choice([
                "Rendering the requested color profile, sir. A little ostentatious, don't you think?",
                "Adjusting the room's color spectrum to your preference.",
                f"Changing color to {command['color']}",
                f"As you wish sir. Color to {command['color']}"
            ])
        elif "lum" in command:
            phrase = random.choice([
                f"Calibrating brightness levels to {command['lum']} percent.",
                f"Duly noted, sir. Adjusting light intensity to {command['lum']} percent."
                f"Adjusting light intensity to {command['lum']} percent."
            ])
        elif "temp" in command:
            phrase = random.choice([
                f"Reconfiguring the light temperature spectrum.",
                f"Calibrating temperature spectrum. Current Value: {command['temp']}.",
            ])

    # 2. Handle Music commands (clSpotify)
    elif target_topic == "pc/spotify/control":
        if action == "play":
            track = command.get("track_name")
            artist = command.get("artist_name")
            playlist = command.get("playlist_name")
            
            if track and artist: 
                phrase = random.choice([
                    f"Playing {track} by {artist}.",
                    f"Currently playing {track} from {artist}.",
                    f"As you wish, sir. Accessing {track} from {artist}'s catalog.",
                    f"Sourcing the track {track}. Enjoy the music sir."
                ])
            elif track: 
                phrase = random.choice([
                    f"Sourcing the track {track}. Enjoy.",
                    f"Playing track: {track}.",
                    f"Currently playing {track}"
                ])
            elif artist: 
                phrase = ([
                    f"Compiling an audio queue for {artist}.",
                    f"Playing artist: {artist}.",
                    f"Enjoy listening to {artist}."
                ])
            elif playlist: 
                phrase = random.choice([
                    f"Retrieving your playlist: {playlist}. A very astute selection, sir.",
                    f"Playing {playlist}."
                ])
            
        elif action in ["pause", "stop"]: 
            phrase = random.choice([
                "Halting the media streams.",
                "Audio paused. I shall keep it on standby.",
                "Media disabled."
            ])
        elif action == "next": 
            phrase = "Skipping to the subsequent track."
        elif action == "prev": 
            phrase = "Reverting to the previous track."
        elif action == "volume": 
            phrase = random.choice([
                f"Adjusting audio to {command.get('volume')} percent.",
                f"Reconfiguring volume to {command.get('volume')} percent."
            ])

    # 3. Handle System discovery routing
    elif target_topic == "system/discovery":
        if action == "discover": 
            phrase = "Running diagnostics and scanning local networks for hardware. Standby."
        elif action == "save_discovery": 
            phrase = "Query complete, sir. Committing device hardware addresses to the central database."

    if phrase:
        await client.publish("jarvis/sys/speak", json.dumps({"text": phrase}))
        
def process_voice_command(text):
    global LAST_KNOWN_TOPIC, AWAITING_DISCOVERY_CHOICE, AWAITING_SPOTIFY_CHOICE
    text = text.lower()
    
    # --- EASTER EGG ENGINE ---
    for trigger, response in PERSONALITY_EGGS.items():
        if trigger in text:
            return [({"action": "personality", "response": response}, "jarvis/sys/speak")]
    
    if COMPILED_ABORT_REGEX and COMPILED_ABORT_REGEX.search(text):
        logging.info("User explicitly cancelled the command. Resetting states.")
        AWAITING_DISCOVERY_CHOICE = False
        AWAITING_SPOTIFY_CHOICE = False
        return [({"action": "abort"}, "jarvis/sys/control")]

    autocorrect_dict = NLP_RULES.get("autocorrect", {})
    for bad, good in autocorrect_dict.items():
        text = text.replace(bad, good)
        
    text = re.sub(r'[.,!?]', '', text)
    
    if DEBUG_NLP:
        logging.info(f"[DEBUG NLP] Cleaned & Autocorrected Text: '{text}'")
    
    word_to_num = NLP_RULES.get("word_to_number", {})
    for word, digit in word_to_num.items():
        text = re.sub(rf'\b{word}\b', digit, text)

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

        # --- REWRITTEN ENTITY EXTRACTION ---
        if payload.get("action") == "play" or re.search(r'\b(?:song|track|music|música|musica)\b', chunk):
            
            if payload.get("action") in ["on", "off", "toggle"]:
                payload["action"] = "play"

            topic_keywords = [word for words in [v.pattern for v in COMPILED_TOPICS.values()] for word in re.findall(r'\w+', words)]
            topic_pattern = r'\b(?:' + '|'.join(topic_keywords) + r')\b'
            
            # Added phonetic fallbacks like 'my' to handle Whisper inaccuracies
            stop_boundaries = rf'(?:\s+(?:on|no|em|by|my|artist|artista|de|do|da|playlist|lista|song|música|musica|track|som|{topic_pattern})|$)'

            playlist_match = re.search(rf'\b(?:playlists?|listas?)\s+(.+?){stop_boundaries}', chunk, re.IGNORECASE)
            if playlist_match:
                payload["playlist_name"] = playlist_match.group(1).strip()
                logging.info(f"Explicit playlist detected: {payload['playlist_name']}")

            # Included 'my' here as well
            artist_match = re.search(rf'\b(?:by|my|artists?|artistas?|de|do|da)\s+(.+?){stop_boundaries}', chunk, re.IGNORECASE)
            if artist_match:
                payload["artist_name"] = artist_match.group(1).strip()
                logging.info(f"Explicit artist detected: {payload['artist_name']}")

            track_match = re.search(rf'\b(?:songs?|músicas?|musicas?|tracks?|sons?)\s+(.+?){stop_boundaries}', chunk, re.IGNORECASE)
            if track_match:
                payload["track_name"] = track_match.group(1).strip()
                logging.info(f"Explicit track detected: {payload['track_name']}")
                
            # Implicit Track Fallback: If said "Play [Track] by [Artist]" without the word "song"
            if "track_name" not in payload and "artist_name" in payload:
                implicit_match = re.search(rf'\b(?:play|tocar)\s+(.+?)\s+(?:by|my|de|do|da)\b', chunk, re.IGNORECASE)
                if implicit_match:
                    payload["track_name"] = implicit_match.group(1).strip()
                    logging.info(f"Implicit track detected: {payload['track_name']}")

        elif payload.get("action") in ["open", "close"]:
            if action_word_used:
                sys_match = re.search(rf'\b{action_word_used}\b\s+(?:(?:to|para|the|o|a|pasta|folder|dir|directory|app|aplicativo)\s+)*(.+)', chunk, re.IGNORECASE)
                if sys_match:
                    payload["target"] = sys_match.group(1).strip()
                    logging.info(f"System target detected: {payload['target']}")

        if not target_topic:
            for topic, regex_obj in COMPILED_TOPICS.items():
                if regex_obj.search(chunk):
                    target_topic = topic
                    break

        if not target_topic and best_action:
            for topic, actions in ROUTING_MAP.items():
                if best_action in actions:
                    target_topic = topic
                    break

        if not target_topic and LAST_KNOWN_TOPIC:
            target_topic = LAST_KNOWN_TOPIC

        if target_topic:
            LAST_KNOWN_TOPIC = target_topic

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
            logging.info("Online. Listening on topics...")
            
            async for message in client.messages:
                topic = message.topic.value
                payload_data = message.payload.decode('utf-8')
                
                if topic == "jarvis/sensor/voice":
                    logging.info(f"Transcript Received: '{payload_data}'")
                    intents = process_voice_command(payload_data)
                    
                    if intents:
                        for command, target_topic in intents:
                            # --- INTERCEPT PERSONALITY ---
                            if command.get("action") == "personality":
                                await client.publish("jarvis/sys/speak", json.dumps({"text": command["response"]}))
                            
                            # --- STANDARD ROUTING ---
                            elif target_topic:
                                logging.info(f"Intent Decoded: {command} -> Routing to [{target_topic}]")
                                await client.publish(target_topic, json.dumps(command))
                                asyncio.create_task(dispatch_tts_response(client, command, target_topic))
                            
                            else:
                                logging.warning(f"Intent decoded ({command}), but no target topic known.")
                            await asyncio.sleep(0.1)
                    else:
                        logging.warning("Could not extract a valid command.")
                
                elif topic == "jarvis/feedback":
                    try:
                        fb = json.loads(payload_data)
                        msg = fb.get('message', '')
                        
                        if "CONFIDENCE_LOW|" in msg:
                            global AWAITING_SPOTIFY_CHOICE
                            AWAITING_SPOTIFY_CHOICE = True
                            
                            # 1. Cleanly format and print the choices to the terminal
                            options_text = msg.split("CONFIDENCE_LOW|")[1] if "CONFIDENCE_LOW|" in msg else msg
                            print("\n" + "="*50)
                            print("SPOTIFY MULTIPLE MATCHES FOUND. AWAITING YOUR SELECTION:")
                            print(options_text.strip())
                            print("="*50 + "\n")
                            
                            # 2. Command TTS to speak and flag it to request a reply
                            await client.publish("jarvis/sys/speak", json.dumps({
                                "text": "My apologies, sir. The audio match was ambiguous. Could you select an option from the terminal?",
                                "request_reply": True  # <-- FIX: Forces mic to hold until this specific phrase completes
                            }))
                            
                            # 3. Trigger the microphone to open remotely
                            await client.publish("jarvis/sys/mic_open", "1")
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