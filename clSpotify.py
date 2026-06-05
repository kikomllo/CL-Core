import os
import re
import sys
import argparse
import logging
import asyncio
import json
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
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)

CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")

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

def _play_track(sp, track_name, artist_name=None):
    """Handles querying and playing a specific track."""
    query = f"track:{track_name}"
    if artist_name:
        query += f" artist:{artist_name}"
        
    results = sp.search(q=query, type='track', limit=1)
    if results['tracks']['items']:
        uri = results['tracks']['items'][0]['uri']
        sp.start_playback(uris=[uri])
        return True, f"Playing track: {track_name}" + (f" by {artist_name}" if artist_name else "")
    return False, f"Track '{track_name}' not found."

def _play_artist(sp, artist_name):
    """Handles querying and playing an artist's radio/top tracks."""
    results = sp.search(q=f"artist:{artist_name}", type='artist', limit=1)
    if results['artists']['items']:
        uri = results['artists']['items'][0]['uri']
        sp.start_playback(context_uri=uri)
        return True, f"Playing artist: {artist_name}"
    return False, f"Artist '{artist_name}' not found."

# --- MAIN CONTROL ROUTER ---
def control_music(sp, action, volume=None, track_name=None, artist_name=None, playlist_name=None):
    """Clean router that delegates actions to the appropriate helper engine."""
    try:
        if action == "play":
            if playlist_name:
                return _play_playlist(sp, playlist_name)
            elif track_name:
                return _play_track(sp, track_name, artist_name)
            elif artist_name:
                return _play_artist(sp, artist_name)
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
                    
                    success, msg = await asyncio.to_thread(
                        control_music, sp, action, volume, track_name, artist_name, playlist_name
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