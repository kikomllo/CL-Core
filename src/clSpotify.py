# --- IMPORTS ---
import os
import re
import sys
import argparse
import logging
import asyncio
import json
import jellyfish
import threading
from typing import Tuple, Optional, Dict, Any, List
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import aiomqtt

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="\r\033[K[%(asctime)s] [SPOTIFY] %(message)s", datefmt="%H:%M:%S")

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class SpotifyManager:
    """Enterprise state controller for Spotify API interaction and playback logic."""
    
    def __init__(self, debug_math: bool = True):
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        self.env_path: str = os.path.abspath(os.path.join(self.base_dir, "..", ".env"))
        load_dotenv(self.env_path)

        self.client_id: str = os.getenv("SPOTIPY_CLIENT_ID", "")
        self.client_secret: str = os.getenv("SPOTIPY_CLIENT_SECRET", "")
        self.redirect_uri: str = os.getenv("SPOTIPY_REDIRECT_URI", "")
        
        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            logging.critical("Spotify credentials missing in .env file!")
            sys.exit(1)

        self.scope: str = "user-read-playback-state user-modify-playback-state playlist-read-private playlist-read-collaborative"
        self.sp: spotipy.Spotify = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
            scope=self.scope
        ))

        # Isolated State Memory
        self.search_cache: Dict[int, str] = {}
        self.cache_lock = threading.Lock()
        self.pre_duck_volume: Optional[int] = None
        
        # Configuration
        self.confidence_threshold: float = 0.60
        self.perfect_match_threshold: float = 0.85
        self.debug_math: bool = debug_math

    # --- STATUS ENGINE ---
    def get_status(self) -> None:
        """Reads the API and prints the currently playing track."""
        try:
            playback = self.sp.current_playback()
            if not playback or not playback.get('is_playing'):
                print("\n--- Spotify Status ---\nStatus:\t\tPaused / Inactive\n" + "-" * 22 + "\n")
                return

            item = playback.get('item', {})
            track_name = item.get('name', 'Unknown')
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

    # --- MATHEMATICS & LOGIC ENGINES ---
    def _calculate_confidence(self, track_query: str, artist_query: str, actual_name: str, actual_artist: str, popularity: int) -> float:
        """Calculates independent track and artist phonetic scores with strict penalties."""
        t_q = track_query.lower() if track_query else ""
        a_q = artist_query.lower() if artist_query else ""
        a_n = actual_name.lower()
        a_a = actual_artist.lower()
        
        # 1. Track Match
        track_score = 1.0
        if t_q:
            track_phonetic = jellyfish.jaro_winkler_similarity(jellyfish.metaphone(t_q), jellyfish.metaphone(a_n))
            track_text = jellyfish.jaro_winkler_similarity(t_q, a_n)
            track_score = max(track_phonetic, track_text)

        # 2. Artist Match
        artist_score = 1.0
        if a_q:
            artist_phonetic = jellyfish.jaro_winkler_similarity(jellyfish.metaphone(a_q), jellyfish.metaphone(a_a))
            artist_text = jellyfish.jaro_winkler_similarity(a_q, a_a)
            artist_score = max(artist_phonetic, artist_text)

        # 3. Base Match: Strict Minimum Requirements
        if t_q and a_q:
            base_match = (track_score * 0.5) + (artist_score * 0.5)
            if track_score < 0.75 or artist_score < 0.75:
                base_match *= 0.3  # False positive nuke
        elif t_q:
            base_match = track_score
        else:
            base_match = artist_score

        # 4. Popularity Weight 
        pop_weight = popularity / 100.0
        composite = (base_match * 0.85) + (pop_weight * 0.15)
        
        if self.debug_math:
            print(f"\n[DEBUG] Evaluating: '{actual_name}' by '{actual_artist}' (Pop: {popularity})")
            print(f"  ├─ Track Score:  {track_score:.2f}")
            print(f"  ├─ Artist Score: {artist_score:.2f}")
            print(f"  └─ Final Math:   (Base {base_match:.2f} * 0.85) + (Pop {pop_weight:.2f} * 0.15) = {composite:.0%}")
            print("-" * 50)
            
        return composite

    # --- PLAYBACK ROUTERS ---
    def _play_playlist(self, playlist_name: str) -> Tuple[bool, str]:
        compressed_query = re.sub(r'\W+', '', playlist_name).lower()
        query_tokens = [re.sub(r'\W+', '', t) for t in playlist_name.lower().replace('-', ' ').split() if t]
        
        user_playlists = self.sp.current_user_playlists(limit=50)
        target_uri, target_actual_name = None, None
        
        # 1: Compressed Match
        for item in user_playlists.get('items', []):
            if item and compressed_query == re.sub(r'\W+', '', item['name']).lower():
                target_uri, target_actual_name = item['uri'], item['name']
                break
        
        # 2: Tokenized Substring Match
        if not target_uri and query_tokens:
            best_score = 0
            for item in user_playlists.get('items', []):
                if not item: continue
                compressed_p_name = re.sub(r'\W+', '', item['name']).lower()
                current_score = sum(1 for token in query_tokens if token in compressed_p_name)
                
                if current_score > best_score:
                    best_score = current_score
                    target_uri, target_actual_name = item['uri'], item['name']
                    
        if target_uri:
            self.sp.start_playback(context_uri=target_uri)
            return True, f"Playing your personal playlist: {target_actual_name}"
        
        # 3: Global Fallback
        results = self.sp.search(q=playlist_name, type='playlist', limit=1)
        if results['playlists']['items']:
            self.sp.start_playback(context_uri=results['playlists']['items'][0]['uri'])
            return True, f"Playing global playlist: {playlist_name}"
            
        return False, f"Playlist '{playlist_name}' not found anywhere."

    def _play_track_fuzzy(self, track_name: Optional[str] = None, artist_name: Optional[str] = None, search_query: Optional[str] = None) -> Tuple[bool, str]:
        self.search_cache.clear()
        
        queries_to_try = []
        if track_name and artist_name: queries_to_try.extend([f"{track_name} {artist_name}", track_name])
        elif track_name: queries_to_try.append(track_name)
        elif search_query: queries_to_try.append(search_query)
        else: return False, "No valid track query provided."

        best_items_list, best_confidence = [], 0
        top_item_uri, top_actual_name, top_actual_artist = "", "", ""
        
        if self.debug_math: logging.info(f"[DEBUG] Cascade Queue: {queries_to_try}")

        for q in queries_to_try:
            logging.info(f"Searching Spotify for: '{q}'")
            results = self.sp.search(q=q, type='track', limit=5)
            
            if not results['tracks']['items']: continue 
                
            for item in results['tracks']['items']:
                actual_name = item['name']
                actual_artist = item['artists'][0]['name']
                popularity = item.get('popularity', 50)
                
                confidence = self._calculate_confidence(track_name, artist_name, actual_name, actual_artist, popularity)
                
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_items_list = results['tracks']['items']
                    top_item_uri, top_actual_name, top_actual_artist = item['uri'], actual_name, actual_artist

            if best_confidence >= self.perfect_match_threshold:
                logging.info("Found a perfect match. Halting cascade early.")
                break 

        if not best_items_list:
            return False, f"Could not find any results for '{queries_to_try[0]}'."
        
        logging.info(f"-> Math Engine Selected: '{top_actual_name} by {top_actual_artist}' | Score: {best_confidence:.0%}")
        
        if best_confidence >= self.confidence_threshold:
            self.sp.start_playback(uris=[top_item_uri])
            return True, f"Playing: {top_actual_name} by {top_actual_artist}"
        
        # Low confidence fallback cache
        msg = "CONFIDENCE_LOW|"
        with self.cache_lock:
            for idx, item in enumerate(best_items_list): 
                choice_num = idx + 1
                self.search_cache[choice_num] = item['uri']
                msg += f"\n[{choice_num}] {item['name']} by {item['artists'][0]['name']}"
            
        return True, msg
    def _play_artist_fuzzy(self, artist_name: str) -> Tuple[bool, str]:
        logging.info(f"Fuzzy searching Spotify for artist: '{artist_name}'")
        results = self.sp.search(q=artist_name, type='artist', limit=1)
        
        if results['artists']['items']:
            item = results['artists']['items'][0]
            self.sp.start_playback(context_uri=item['uri'])
            return True, f"Playing artist radio: {item['name']}"
            
        return False, f"Artist '{artist_name}' not found."

    def _handle_ducking(self, action: str) -> Tuple[bool, str]:
        if action == "duck":
            if self.pre_duck_volume is not None:
                return True, "Volume is already ducked."
                
            playback = self.sp.current_playback()
            if playback and playback.get('is_playing') and playback.get('device'):
                self.pre_duck_volume = playback['device']['volume_percent']
                new_vol = max(0, self.pre_duck_volume - 20)
                self.sp.volume(new_vol)
                return True, f"Listening... Volume dipped to {new_vol}%."
            return False, "Nothing is playing, no need to duck."

        elif action == "unduck":
            if self.pre_duck_volume is not None:
                self.sp.volume(self.pre_duck_volume)
                restored = self.pre_duck_volume
                self.pre_duck_volume = None
                return True, f"Done listening. Restored volume to {restored}%."
            return False, "No original volume to restore."
        
        return False, "Invalid ducking command."

    # --- MAIN EXECUTION ENGINE ---
    def execute_command(self, action: str, volume: Optional[int] = None, track_name: Optional[str] = None, 
                        artist_name: Optional[str] = None, playlist_name: Optional[str] = None, 
                        search_query: Optional[str] = None, choice_index: Optional[int] = None) -> Tuple[bool, str]:
        """Clean router that delegates actions to the appropriate class helper methods."""
        try:
            if action in ["duck", "unduck"]:
                return self._handle_ducking(action)
            
            if action == "play_choice" and choice_index is not None:
                with self.cache_lock:
                    uri = self.search_cache.get(choice_index)
                    if uri:
                        self.search_cache.clear()
                        
                if uri:
                    self.sp.start_playback(uris=[uri])
                    return True, f"Playing option {choice_index}."
                return False, f"Option {choice_index} is invalid or expired."
                
            if action == "play":
                if playlist_name: return self._play_playlist(playlist_name)
                elif track_name or search_query: return self._play_track_fuzzy(track_name, artist_name, search_query)
                elif artist_name: return self._play_artist_fuzzy(artist_name)
                else:
                    self.sp.start_playback()
                    return True, "Resuming playback."
                
            if action == "pause":
                self.sp.pause_playback()
                return True, "Music paused."
                
            if action == "next":
                self.sp.next_track()
                return True, "Skipped to next track."
                
            if action == "prev":
                self.sp.previous_track()
                return True, "Returned to previous track."
                
            if action == "volume" and volume is not None:
                clean_vol = max(0, min(100, volume))
                self.sp.volume(clean_vol)
                return True, f"Volume changed to {clean_vol}%."
            
            return False, f"Action '{action}' is not recognized."
                
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 403: 
                if action == "play" and not any([track_name, artist_name, playlist_name, search_query, choice_index]):
                    return False, "Music is already playing."
                return False, "Action refused. Premium account required or playback restriction."
            elif e.http_status == 404: 
                return False, "No active device found. Open Spotify first."
            return False, f"Spotify API Error: {e}"
        except Exception as e:
            return False, f"Internal Error: {str(e)}"


# --- MQTT SERVICE LISTENER ---
async def mqtt_service_listener(manager: SpotifyManager) -> None:
    logging.info("Service Mode initialized. Listening on topic 'pc/spotify/control'...")
    while True:
        try:
            async with aiomqtt.Client("localhost") as mqtt_client:
                await mqtt_client.subscribe("pc/spotify/control")
                
                async for message in mqtt_client.messages:
                    try:
                        payload = json.loads(message.payload.decode('utf-8'))
                        logging.info(f"Command Received: {payload}")
                        
                        # Offload strictly synchronous Spotipy requests to a background thread
                        success, msg = await asyncio.to_thread(
                            manager.execute_command, 
                            action=payload.get("action"), 
                            volume=payload.get("volume"), 
                            track_name=payload.get("track_name"), 
                            artist_name=payload.get("artist_name"), 
                            playlist_name=payload.get("playlist_name"), 
                            search_query=payload.get("search_query"), 
                            choice_index=payload.get("choice_index")
                        )
                        
                        await mqtt_client.publish("jarvis/feedback", json.dumps({
                            "device": "spotify",
                            "status": "success" if success else "error",
                            "message": msg
                        }))
                        
                    except json.JSONDecodeError:
                        logging.error("Received malformed JSON data.")
        except aiomqtt.MqttError as e:
            await asyncio.sleep(5)
            logging.error(f"MQTT Connection Error: {e} (Is Mosquitto running?)")
        except asyncio.CancelledError:
            logging.info("Spotify service shutting down.")
            break


# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="Microservice Control for Spotify")
    parser.add_argument("--status", action="store_true", help="Show current track")
    parser.add_argument("--play", action="store_true", help="Resume playback")
    parser.add_argument("--pause", action="store_true", help="Pause playback")
    parser.add_argument("--next", action="store_true", help="Next track")
    parser.add_argument("--prev", action="store_true", help="Previous track")
    parser.add_argument("--vol", type=int, help="Set volume (0-100)")
    parser.add_argument("--track", type=str, help="Play a specific track")
    parser.add_argument("--artist", type=str, help="Play a specific artist")
    parser.add_argument("--playlist", type=str, help="Play a specific playlist")
    args = parser.parse_args()

    try:
        manager = SpotifyManager()
    except Exception as e:
        logging.critical(f"Failed to initialize Manager: {e}")
        sys.exit(1)

    if args.status:
        manager.get_status()
        return

    has_direct_command = any([args.play, args.pause, args.next, args.prev, args.vol is not None, args.track, args.artist, args.playlist])

    if has_direct_command:
        logging.info("Executing manual override command directly...")
        if args.playlist: manager.execute_command("play", playlist_name=args.playlist)
        elif args.track: manager.execute_command("play", track_name=args.track, artist_name=args.artist)
        elif args.artist: manager.execute_command("play", artist_name=args.artist)
        elif args.play: manager.execute_command("play")
            
        if args.pause: manager.execute_command("pause")
        if args.next: manager.execute_command("next")
        if args.prev: manager.execute_command("prev")
        if args.vol is not None: manager.execute_command("volume", volume=args.vol)
    else:
        try:
            asyncio.run(mqtt_service_listener(manager))
        except KeyboardInterrupt:
            logging.info("Exiting Service Mode.")

if __name__ == "__main__":
    main()