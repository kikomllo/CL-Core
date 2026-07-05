import os
import re
import sys
import argparse
import logging
import asyncio
import json
import jellyfish
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import aiomqtt

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [SPOTIFY] %(message)s", datefmt="%H:%M:%S")

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- CREDENTIALS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", ".env"))
load_dotenv(ENV_PATH)

CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")

# --- DEBUG TOGGLES ---
DEBUG_MATH = True

# --- CACHE ---
SEARCH_CACHE = {}
CONFIDENCE_THRESHOLD = 0.60
PRE_DUCK_VOLUME = None

if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI]):
    logging.critical("Spotify credentials missing in .env file!")
    sys.exit(1)

SCOPE = "user-read-playback-state user-modify-playback-state playlist-read-private playlist-read-collaborative"

def get_status(sp):
    """Reads the API and prints the currently playing track."""
    try:
        playback = sp.current_playback()
        if playback is None or not playback.get('is_playing'):
            print("\n--- Spotify Status ---")
            print("Status:\t\tPaused / Inactive")
            return

        item = playback.get('item')
        if item:
            track_name = item.get('name')
            artists = ", ".join([artist['name'] for artist in item.get('artists', [])])
            device_name = playback.get('device', {}).get('name', 'Unknown')
            volume = playback.get('device', {}).get('volume_percent', 'N/A')
            
            print("\n--- Spotify Status ---")
            print(f"Status:\t\tPlaying")
            print(f"Track:\t\t{track_name}")
            print(f"Artist(s):\t{artists}")
            print(f"Device:\t\t{device_name}")
            print(f"Volume:\t\t{volume}%")
            print("-" * 22 + "\n")
    except Exception as e:
        logging.error(f"Error getting Spotify status: {e}")

# --- PLAYBACK HELPER ENGINES ---

def _play_playlist(sp, playlist_name):
    """Handles the scoring for finding and playing a playlist."""
    target_uri = None
    target_actual_name = None
    
    compressed_query = re.sub(r'\W+', '', playlist_name).lower()
    
    spaced_query = playlist_name.lower().replace('-', ' ')
    raw_tokens = spaced_query.split()
    query_tokens = [re.sub(r'\W+', '', token) for token in raw_tokens]
    query_tokens = [t for t in query_tokens if t] 
    
    user_playlists = sp.current_user_playlists(limit=50)
    
    # 1: Compressed Match
    for item in user_playlists['items']:
        if item:
            compressed_p_name = re.sub(r'\W+', '', item['name']).lower()
            if compressed_query == compressed_p_name:
                target_uri = item['uri']
                target_actual_name = item['name']
                break
    
    # 2: Tokenized Substring Match
    if not target_uri and query_tokens:
        best_score = 0
        for item in user_playlists['items']:
            if item:
                compressed_p_name = re.sub(r'\W+', '', item['name']).lower()
                current_score = 0
                for token in query_tokens:
                    if token in compressed_p_name:
                        current_score += 1
                if current_score > best_score:
                    best_score = current_score
                    target_uri = item['uri']
                    target_actual_name = item['name']
                    
    if target_uri:
        sp.start_playback(context_uri=target_uri)
        return True, f"Playing your personal playlist: {target_actual_name}"
    
    # 3: Global Fallback
    results = sp.search(q=playlist_name, type='playlist', limit=1)
    if results['playlists']['items']:
        uri = results['playlists']['items'][0]['uri']
        sp.start_playback(context_uri=uri)
        return True, f"Playing global playlist: {playlist_name}"
        
    return False, f"Playlist '{playlist_name}' not found anywhere."

def calculate_confidence(track_query, artist_query, actual_name, actual_artist, popularity):
    """Calculates independent track and artist phonetic scores with brutal penalties."""
    track_query = track_query.lower() if track_query else ""
    artist_query = artist_query.lower() if artist_query else ""
    actual_name = actual_name.lower()
    actual_artist = actual_artist.lower()
    
    # 1. Track Match
    t_q_phone = jellyfish.metaphone(track_query)
    t_a_phone = jellyfish.metaphone(actual_name)
    track_phonetic = jellyfish.jaro_winkler_similarity(t_q_phone, t_a_phone)
    track_text = jellyfish.jaro_winkler_similarity(track_query, actual_name)
    track_score = max(track_phonetic, track_text) if track_query else 1.0

    # 2. Artist Match
    a_q_phone = jellyfish.metaphone(artist_query)
    a_a_phone = jellyfish.metaphone(actual_artist)
    artist_phonetic = jellyfish.jaro_winkler_similarity(a_q_phone, a_a_phone)
    artist_text = jellyfish.jaro_winkler_similarity(artist_query, actual_artist)
    artist_score = max(artist_phonetic, artist_text) if artist_query else 1.0

    # 3. Base Match: Strict Minimum Requirements
    if track_query and artist_query:
        base_match = (track_score * 0.5) + (artist_score * 0.5)
        
        # GLOBAL FIX: If either slot is below 75%, it is considered a false positive.
        # Nuke the score completely to force a fallback or user choice.
        if track_score < 0.75 or artist_score < 0.75:
            base_match *= 0.3  
            
    elif track_query:
        base_match = track_score
    else:
        base_match = artist_score

    # 4. Popularity Weight 
    pop_weight = popularity / 100.0
    composite = (base_match * 0.85) + (pop_weight * 0.15)
    
    if DEBUG_MATH:
        print(f"\n[DEBUG] Evaluating: '{actual_name}' by '{actual_artist}' (Pop: {popularity})")
        print(f"   ├─ Track Sounds:  {t_q_phone} vs {t_a_phone} | Score: {track_score:.2f}")
        print(f"   ├─ Artist Sounds: {a_q_phone} vs {a_a_phone} | Score: {artist_score:.2f}")
        print(f"   └─ Final Math:    (Base {base_match:.2f} * 0.85) + (Pop {pop_weight:.2f} * 0.15) = {composite:.0%}")
        print("-" * 50)
        
    return composite

def _play_track_fuzzy(sp, track_name=None, artist_name=None, search_query=None):
    """Handles fuzzy search with an exhaustive, global best-match cascade."""
    global SEARCH_CACHE
    SEARCH_CACHE.clear()
    
    PERFECT_MATCH_THRESHOLD = 0.85 # Only skip fallbacks if we find a near-perfect 85%+ score
    
    queries_to_try = []
    if track_name and artist_name:
        queries_to_try.append(f"{track_name} {artist_name}") 
        queries_to_try.append(track_name)                    
    elif track_name:
        queries_to_try.append(track_name)
    elif search_query:
        queries_to_try.append(search_query)
    else:
        return False, "No valid track query provided."

    best_items_list = []
    best_confidence = 0
    top_item_uri = ""
    top_actual_name = ""
    top_actual_artist = ""
    
    if DEBUG_MATH:
        logging.info(f"[DEBUG] Cascade Queue: Will try searching these strings in order: {queries_to_try}")

    for q in queries_to_try:
        logging.info(f"Searching Spotify for: '{q}'")
        results = sp.search(q=q, type='track', limit=5)
        
        if not results['tracks']['items']:
            continue 
            
        for item in results['tracks']['items']:
            actual_name = item['name']
            actual_artist = item['artists'][0]['name']
            popularity = item.get('popularity', 50)
            
            # Pass original track_name/artist_name for pure math evaluation
            confidence = calculate_confidence(track_name, artist_name, actual_name, actual_artist, popularity)
            
            if confidence > best_confidence:
                best_confidence = confidence
                best_items_list = results['tracks']['items']
                top_item_uri = item['uri']
                top_actual_name = actual_name
                top_actual_artist = actual_artist

        # GLOBAL FIX: Do not break the cascade on a "meh" score. 
        # Only stop searching early if we found an undeniable, perfect match (>85%).
        # Otherwise, keep searching the fallbacks and let the highest score win!
        if best_confidence >= PERFECT_MATCH_THRESHOLD:
            logging.info("Found a perfect match. Halting cascade early.")
            break 

    if not best_items_list:
        return False, f"Could not find any results for '{queries_to_try[0]}'."
    
    logging.info(f"-> Math Engine Selected: '{top_actual_name} by {top_actual_artist}' | Score: {best_confidence:.0%}")
    
    # Auto-play if the WINNING score from ALL cascade searches is above 60%
    if best_confidence >= CONFIDENCE_THRESHOLD:
        sp.start_playback(uris=[top_item_uri])
        return True, f"Playing: {top_actual_name} by {top_actual_artist}"
    
    # Low confidence fallback
    msg = "CONFIDENCE_LOW|"
    for idx, item in enumerate(best_items_list): 
        choice_num = idx + 1
        SEARCH_CACHE[choice_num] = item['uri']
        msg += f"\n[{choice_num}] {item['name']} by {item['artists'][0]['name']}"
        
    return True, msg

def _play_artist_fuzzy(sp, artist_name):
    """Handles querying and playing an artist using broad search."""
    logging.info(f"Fuzzy searching Spotify for artist: '{artist_name}'")
    results = sp.search(q=artist_name, type='artist', limit=1)
    
    if results['artists']['items']:
        item = results['artists']['items'][0]
        uri = item['uri']
        actual_artist = item['name']
        
        sp.start_playback(context_uri=uri)
        return True, f"Playing artist radio: {actual_artist}"
        
    return False, f"Artist '{artist_name}' not found."

# --- MAIN CONTROL ROUTER ---
def control_music(sp, action, volume=None, track_name=None, artist_name=None, playlist_name=None, search_query=None, choice_index=None):
    """Clean router that delegates actions to the appropriate helper engine."""
    global PRE_DUCK_VOLUME

    try:
        # --- AUDIO DUCKING ---
        if action == "duck":
            if PRE_DUCK_VOLUME is not None:
                return True, "Volume is already ducked." # Prevent double-ducking
                
            playback = sp.current_playback()
            if playback and playback.get('is_playing') and playback.get('device'):
                current_vol = playback['device']['volume_percent']
                PRE_DUCK_VOLUME = current_vol
                new_vol = max(0, current_vol - 20)
                sp.volume(new_vol)
                return True, f"Listening... Volume dipped to {new_vol}%."
            return False, "Nothing is playing, no need to duck."

        elif action == "unduck":
            if PRE_DUCK_VOLUME is not None:
                sp.volume(PRE_DUCK_VOLUME)
                restored_vol = PRE_DUCK_VOLUME
                PRE_DUCK_VOLUME = None # Reset the cache
                return True, f"Done listening. Restored volume to {restored_vol}%."
            return False, "No original volume to restore."
        
        # --- Play from cache ---
        if action == "play_choice" and choice_index is not None:
            uri = SEARCH_CACHE.get(choice_index)
            if uri:
                sp.start_playback(uris=[uri])
                SEARCH_CACHE.clear()
                return True, f"Playing option {choice_index}."
            return False, f"Option {choice_index} is invalid or expired."
            
        if action == "play":
            if playlist_name:
                return _play_playlist(sp, playlist_name)
            elif track_name or search_query:
                return _play_track_fuzzy(sp, track_name, artist_name, search_query)
            elif artist_name:
                return _play_artist_fuzzy(sp, artist_name)
            else:
                sp.start_playback()
                return True, "Resuming playback."
            
        elif action == "pause":
            sp.pause_playback()
            return True, "Music paused."
            
        elif action == "next":
            sp.next_track()
            return True, "Skipped to next track."
            
        elif action == "prev":
            sp.previous_track()
            return True, "Returned to previous track."
            
        elif action == "volume" and volume is not None:
            clean_vol = max(0, min(100, volume))
            sp.volume(clean_vol)
            return True, f"Volume changed to {clean_vol}%."
        
        return False, f"Action '{action}' is not recognized."
            
    except spotipy.exceptions.SpotifyException as e:
        if e.http_status == 403:
            return False, "Action refused. Premium account required or restriction violated."
        elif e.http_status == 404:
            return False, "No active device found. Open Spotify and press Play first."
        return False, f"Spotify API Error: {e}"
    except Exception as e:
        return False, f"Internal Error: {str(e)}"

# --- MQTT SERVICE LISTENER ---
async def mqtt_service_listener(sp):
    logging.info("Service Mode initialized. Listening on topic 'pc/spotify/control'...")
    try:
        async with aiomqtt.Client("localhost") as mqtt_client:
            await mqtt_client.subscribe("pc/spotify/control")
            
            async for message in mqtt_client.messages:
                try:
                    payload = json.loads(message.payload.decode('utf-8'))
                    logging.info(f"Command Received: {payload}")
                    
                    action = payload.get("action")
                    volume = payload.get("volume")
                    track_name = payload.get("track_name")
                    artist_name = payload.get("artist_name")
                    playlist_name = payload.get("playlist_name")
                    search_query = payload.get("search_query")
                    choice_index = payload.get("choice_index") # <-- Added retrieval
                    
                    success, msg = await asyncio.to_thread(
                        control_music, sp, action, volume, track_name, artist_name, playlist_name, search_query, choice_index
                    )
                    
                    feedback = {
                        "device": "spotify",
                        "status": "success" if success else "error",
                        "message": msg
                    }
                    await mqtt_client.publish("jarvis/feedback", json.dumps(feedback))
                    
                except json.JSONDecodeError:
                    logging.error("Received malformed JSON data.")
    except aiomqtt.MqttError as e:
        logging.error(f"MQTT Connection Error: {e} (Is Mosquitto running?)")
    except asyncio.CancelledError:
        logging.info("Spotify service shutting down.")

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="Microservice Control for Spotify")
    
    parser.add_argument("--status", action="store_true", help="Show current track")
    parser.add_argument("--play", action="store_true", help="Resume playback")
    parser.add_argument("--pause", action="store_true", help="Pause playback")
    parser.add_argument("--next", action="store_true", help="Next track")
    parser.add_argument("--prev", action="store_true", help="Previous track")
    parser.add_argument("--vol", type=int, help="Set volume (0-100)")
    parser.add_argument("--track", type=str, help="Play a specific track (can be combined with --artist)")
    parser.add_argument("--artist", type=str, help="Play a specific artist")
    parser.add_argument("--playlist", type=str, help="Play a specific playlist")
    
    args = parser.parse_args()

    # 1: Initialize Client
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scope=SCOPE
        ))
    except Exception as e:
        logging.critical(f"Failed to initialize Spotify client: {e}")
        sys.exit(1)

    # 2: Status Command
    if args.status:
        get_status(sp)
        return

    # 3: Control Commands
    has_direct_command = any([
        args.play, args.pause, args.next, args.prev, 
        args.vol is not None, args.track, args.artist, args.playlist
    ])

    if has_direct_command:
        logging.info("Executing manual override command directly...")
        
        if args.playlist:
            control_music(sp, "play", playlist_name=args.playlist)
        elif args.track:
            control_music(sp, "play", track_name=args.track, artist_name=args.artist)
        elif args.artist:
            control_music(sp, "play", artist_name=args.artist)
        elif args.play:
            control_music(sp, "play")
            
        if args.pause: control_music(sp, "pause")
        if args.next: control_music(sp, "next")
        if args.prev: control_music(sp, "prev")
        if args.vol is not None: control_music(sp, "volume", args.vol)
        
    else:
        # 4: Boot into Microservice Mode if no arguments are passed
        try:
            asyncio.run(mqtt_service_listener(sp))
        except KeyboardInterrupt:
            logging.info("Exiting Service Mode.")

if __name__ == "__main__":
    main()