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
import time
from typing import Tuple, Optional, Dict, Any, List
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import aiomqtt

# NEW: Import your centralized env loader
from utils.clEnvLoader import EnvLoader

# --- LOGGING SETUP ---
import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('SPOTIFY')

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class SpotifyManager:
    """Enterprise state controller for Spotify API interaction and playback logic."""
    
    def __init__(self, debug_math: bool = True):
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        self.env = EnvLoader()

        self.client_id: str = self.env.get("SPOTIPY_CLIENT_ID", "")
        self.client_secret: str = self.env.get("SPOTIPY_CLIENT_SECRET", "")
        self.redirect_uri: str = self.env.get("SPOTIPY_REDIRECT_URI", "")
        
        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            logging.critical("Spotify credentials missing in .env file!")
            sys.exit(1)

        self.scope: str = "user-read-playback-state user-modify-playback-state playlist-read-private playlist-read-collaborative"
        self.sp: spotipy.Spotify = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=self.redirect_uri,
                scope=self.scope
            ), 
            requests_timeout=5
        )

        self.search_cache: Dict[int, str] = {}
        self.cache_lock = threading.Lock()
        self.pre_duck_volume: Optional[int] = None
        self.ducked_device_id: Optional[str] = None
        self.last_known_normal_volume: int = 80
        
        self.confidence_threshold: float = 0.60
        self.perfect_match_threshold: float = 0.85
        self.debug_math: bool = debug_math

        # --- NEW: API Lag Compensators ---
        self._local_playing_state: Optional[bool] = None
        self._last_state_change_time: float = 0.0
        
        # --- NEW: UI State Tracking ---
        self.ui_is_fullscreen: bool = False
        self.last_ui_poll_time: float = 0.0

    # --- DEVICE WAKEUP ENGINE ---
    def _wake_up_spotify(self) -> None:
        """Simulate a media 'play' command on the host computer to wake up Spotify."""
        logging.info("No active device found. Attempting to wake up local client...")
        try:
            if sys.platform.startswith('linux'):
                # Native Linux headless D-Bus command for Spotify (MPRIS)
                import subprocess
                subprocess.run([
                    "dbus-send", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify", 
                    "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.PlayPause"
                ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # Windows / Mac fallback
                import pyautogui
                pyautogui.press('playpause')
                
            time.sleep(2)  # Wait for the OS and Spotify app to connect to the Spotify network
        except ImportError:
            logging.error("Failed to wake up Spotify: pyautogui is not installed on Windows/Mac.")
        except Exception as e:
            logging.error(f"Failed to wake up Spotify: {e}")

    def _get_active_device(self) -> Optional[str]:
        """Retrieve the active device ID."""
        try:
            devices = self.sp.devices()
            for device in devices.get('devices', []):
                if device.get('is_active'):
                    return device.get('id')
            return None
        except Exception as e:
            logging.error(f"Failed to get Spotify devices: {e}")
            return None

    def _ensure_active_device(self) -> Optional[str]:
        """Ensure we have an active device before executing a command."""
        device = self._get_active_device()
        if not device:
            self._wake_up_spotify()
            device = self._get_active_device()
        return device

    # --- STATUS ENGINE ---
    def status(self, lightweight: bool = False) -> Dict[str, Any]:
        """
        Retrieve comprehensive status of the current playback.
        If lightweight=True, skips secondary API calls for context/queue.
        """
        if not self._get_active_device():
             return {"status": "error", "message": "No active Spotify session found."}
             
        try:
            playback = self.sp.current_playback()
            if not playback or not playback.get('item'):
                return {"status": "idle", "message": "Nothing is currently playing."}
                
            track_name = playback['item'].get('name', 'Unknown Track')
            artists = ", ".join([artist['name'] for artist in playback['item'].get('artists', [])])
            volume = playback.get('device', {}).get('volume_percent', 'Unknown')
            
            # --- API Lag Compensation ---
            is_playing = playback.get('is_playing', False)
            if time.time() - self._last_state_change_time < 3.0:
                if self._local_playing_state is not None:
                    is_playing = self._local_playing_state
            else:
                self._local_playing_state = is_playing

            # --- Early return for UI poller ---
            if lightweight:
                return {
                    "status": "success",
                    "is_playing": is_playing,
                    "volume": volume,
                    "track": track_name,
                    "artist": artists,
                    "context": "Unknown",
                    "next_in_queue": "Unknown",
                    "progress_ms": playback.get("progress_ms", 0),
                    "duration_ms": playback['item'].get("duration_ms", 0)
                }
            
            context_name = "None"
            context = playback.get('context')
            if context and context.get('type') == 'playlist':
                playlist_id = context['uri'].split(":")[-1]
                playlist_data = self.sp.playlist(playlist_id, fields="name")
                context_name = playlist_data.get('name', 'Unknown Playlist')
            elif context and context.get('type') == 'album':
                context_name = playback['item'].get('album', {}).get('name', 'Unknown Album')

            next_in_queue = "None"
            queue_data = self.sp.queue()
            if queue_data and queue_data.get('queue') and len(queue_data['queue']) > 0:
                next_track = queue_data['queue'][0]
                next_in_queue = next_track.get('name', 'Unknown Track')

            return {
                "status": "success",
                "is_playing": is_playing,
                "volume": volume,
                "track": track_name,
                "artist": artists,
                "context": context_name,
                "next_in_queue": next_in_queue,
                "progress_ms": playback.get("progress_ms", 0),
                "duration_ms": playback['item'].get("duration_ms", 0)
            }

        except spotipy.SpotifyException as e:
            return {"status": "error", "message": f"API Error: {str(e)}"}
        except Exception as e:
             return {"status": "error", "message": f"Parsing Error: {str(e)}"}

    def get_status(self) -> None:
        """Reads the API and prints the currently playing track and queue information."""
        status_data = self.status()
        print("\n--- Spotify Status ---")
        if status_data.get("status") == "success":
            print(f"Status:\t\t{'Playing' if status_data.get('is_playing') else 'Paused'}")
            print(f"Track:\t\t{status_data.get('track')}")
            print(f"Artist(s):\t{status_data.get('artist')}")
            print(f"Context:\t{status_data.get('context')}")
            print(f"Volume:\t\t{status_data.get('volume')}%")
            print(f"Next in Queue:\t{status_data.get('next_in_queue')}")
        else:
            print(f"Status:\t\t{status_data.get('message', 'Inactive')}")
        print("-" * 22 + "\n")

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
        device = self._ensure_active_device()
        if not device: return False, "Spotify is not active on any device."

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
            self.sp.start_playback(device_id=device, context_uri=target_uri)
            self._local_playing_state = True
            self._last_state_change_time = time.time()
            return True, f"Playing your personal playlist: {target_actual_name}"
        
        # 3: Global Fallback
        results = self.sp.search(q=playlist_name, type='playlist', limit=1)
        if results['playlists']['items']:
            self.sp.start_playback(device_id=device, context_uri=results['playlists']['items'][0]['uri'])
            self._local_playing_state = True
            self._last_state_change_time = time.time()
            return True, f"Playing global playlist: {playlist_name}"
            
        return False, f"Playlist '{playlist_name}' not found anywhere."

    def _play_track_fuzzy(self, track_name: Optional[str] = None, artist_name: Optional[str] = None, search_query: Optional[str] = None) -> Tuple[bool, str]:
        device = self._ensure_active_device()
        if not device: return False, "Spotify is not active on any device."

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
            self.sp.start_playback(device_id=device, uris=[top_item_uri])
            self._local_playing_state = True
            self._last_state_change_time = time.time()
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
        device = self._ensure_active_device()
        if not device: return False, "Spotify is not active on any device."

        logging.info(f"Fuzzy searching Spotify for artist: '{artist_name}'")
        results = self.sp.search(q=artist_name, type='artist', limit=1)
        
        if results['artists']['items']:
            item = results['artists']['items'][0]
            self.sp.start_playback(device_id=device, context_uri=item['uri'])
            self._local_playing_state = True
            self._last_state_change_time = time.time()
            return True, f"Playing artist radio: {item['name']}"
            
        return False, f"Artist '{artist_name}' not found."

    def _handle_ducking(self, action: str) -> Tuple[bool, str]:
        if action == "duck":
            # Prevent ducking on top of an existing duck state
            if self.pre_duck_volume is not None:
                logging.info("[DUCK] System already ducked. Ignoring duplicate request.")
                return True, "Already ducked"

            device = self._get_active_device()
            if not device:
                logging.warning("[DUCK] No active Spotify device found to duck.")
                return False, "No active device"

            try:
                current_playback = self.sp.current_playback()
                if not current_playback or not current_playback.get('is_playing'):
                    logging.info("[DUCK] Spotify is not currently playing. Skipping ducking.")
                    return False, "Not currently playing"
                
                raw_vol = current_playback.get('device', {}).get('volume_percent')
                current_vol = raw_vol if raw_vol is not None else 50
                
                # If current volume is normal (>50%), record it as the true un-ducked volume.
                # If current volume is <=50% (already ducked or mid-restore), preserve last_known_normal_volume.
                if current_vol > 50:
                    self.last_known_normal_volume = current_vol
                    self.pre_duck_volume = current_vol
                else:
                    self.pre_duck_volume = getattr(self, 'last_known_normal_volume', 80)
                    
                self.ducked_device_id = device
                new_vol = max(0, int(self.pre_duck_volume * 0.8))
                
                logging.info(f"[DUCK] Ducking volume from {self.pre_duck_volume}% to {new_vol}% on device '{device}'")
                self.sp.volume(new_vol, device_id=device)
                return True, f"Ducked to {new_vol}%"
            except Exception as e:
                logging.error(f"[DUCK] Failed to duck Spotify volume: {e}")
                self.pre_duck_volume = None
                self.ducked_device_id = None
                return False, f"Duck error: {e}"

        elif action == "unduck":
            if self.pre_duck_volume is None:
                logging.info("[UNDUCK] Unduck called but no saved volume exists. No-op.")
                return True, "Not ducked"

            target_vol = self.pre_duck_volume
            target_device = self.ducked_device_id or self._get_active_device()

            self.pre_duck_volume = None
            self.ducked_device_id = None

            try:
                logging.info(f"[UNDUCK] Restoring volume to {target_vol}% on device '{target_device or 'default'}'")
                self.sp.volume(target_vol, device_id=target_device)
                return True, f"Restored volume to {target_vol}%"
            except Exception as e:
                logging.warning(f"[UNDUCK] Target device volume restore failed: {e}. Attempting default account restore...")
                try:
                    # Fallback attempt without device_id constraint
                    self.sp.volume(target_vol)
                    return True, f"Restored volume to {target_vol}% (fallback)"
                except Exception as fallback_e:
                    logging.error(f"[UNDUCK] Primary and fallback volume restore failed: {fallback_e}")
                    return False, f"Unduck error: {fallback_e}"

        return False, f"Unknown duck action: {action}"

    # --- MAIN EXECUTION ENGINE ---
    def execute_command(self, action: str, volume: Optional[int] = None, track_name: Optional[str] = None, 
                        artist_name: Optional[str] = None, playlist_name: Optional[str] = None, 
                        search_query: Optional[str] = None, choice_index: Optional[int] = None) -> Tuple[bool, Any]:
        """Clean router that delegates actions to the appropriate class helper methods."""
        try:
            if action in ["duck", "unduck"]:
                return self._handle_ducking(action)
            
            if action and action.startswith("status"):
                s = self.status()
                s["query_action"] = action
                if s.get("status") == "idle":
                    return False, s
                return (s.get("status") == "success"), s

            if action == "play_choice" and choice_index is not None:
                device = self._ensure_active_device()
                if not device: return False, "Spotify is not active on any device."

                with self.cache_lock:
                    uri = self.search_cache.get(choice_index)
                    if uri:
                        self.search_cache.clear()
                        
                if uri:
                    self.sp.start_playback(device_id=device, uris=[uri])
                    self._local_playing_state = True
                    self._last_state_change_time = time.time()
                    return True, f"Playing option {choice_index}."
                return False, f"Option {choice_index} is invalid or expired."
                
            if action == "play":
                if playlist_name: return self._play_playlist(playlist_name)
                elif track_name or search_query: return self._play_track_fuzzy(track_name, artist_name, search_query)
                elif artist_name: return self._play_artist_fuzzy(artist_name)
                else:
                    device = self._get_active_device()
                    if not device:
                        logging.info("API indicates no active device. Triggering hardware wakeup...")
                        self._wake_up_spotify()
                        
                        status_data = self.status(lightweight=True)
                        if status_data.get("status") == "success" and status_data.get("is_playing"):
                            self._local_playing_state = True
                            self._last_state_change_time = time.time()
                            return True, "Resumed playback via host hardware controls."
                            
                        device = self._get_active_device()
                        if not device: 
                            return False, "Spotify is not active on any device. Open it manually."
                            
                    self.sp.start_playback(device_id=device)
                    self._local_playing_state = True
                    self._last_state_change_time = time.time()
                    return True, "Resuming playback via API."
                
            if action == "pause":
                device = self._get_active_device()
                if not device: return False, "Spotify is not active."
                self.sp.pause_playback(device_id=device)
                
                self._local_playing_state = False
                self._last_state_change_time = time.time()
                
                return True, "Music paused."
            
            if action == "toggle":
                is_playing = self._local_playing_state
                if time.time() - self._last_state_change_time >= 3.0 or is_playing is None:
                    playback = self.sp.current_playback()
                    is_playing = playback.get("is_playing", False) if playback else False

                if is_playing:
                    device = self._get_active_device()
                    if device:
                        self.sp.pause_playback(device_id=device)
                        self._local_playing_state = False
                        self._last_state_change_time = time.time()
                        return True, "Music paused."
                else:
                    device = self._ensure_active_device()
                    if device:
                        self.sp.start_playback(device_id=device)
                        self._local_playing_state = True
                        self._last_state_change_time = time.time()
                        return True, "Resuming playback."
                return False, "Spotify is not active."
                
            if action == "next":
                device = self._ensure_active_device()
                if not device: return False, "Spotify is not active."
                self.sp.next_track(device_id=device)
                self._local_playing_state = True
                self._last_state_change_time = time.time()
                return True, "Skipped to next track."
                
            if action == "prev":
                device = self._ensure_active_device()
                if not device: return False, "Spotify is not active."
                self.sp.previous_track(device_id=device)
                self._local_playing_state = True
                self._last_state_change_time = time.time()
                return True, "Returned to previous track."
                
            if action == "volume" and volume is not None:
                device = self._ensure_active_device()
                if not device: return False, "Spotify is not active."
                clean_vol = max(0, min(100, volume))
                self.sp.volume(clean_vol, device_id=device)
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
                await mqtt_client.subscribe("jarvis/sys/ui_control")
                
                async for message in mqtt_client.messages:
                    try:
                        topic = message.topic.value
                        payload = json.loads(message.payload.decode('utf-8'))
                        
                        # --- Track Fullscreen UI State ---
                        if topic == "jarvis/sys/ui_control":
                            if payload.get("action") == "set_fullscreen":
                                manager.ui_is_fullscreen = True
                            elif payload.get("action") == "set_overlay":
                                manager.ui_is_fullscreen = False
                            continue
                            
                        if topic == "pc/spotify/control":
                            if not isinstance(payload, dict):
                                logging.error(f"Invalid payload type: Expected dict, got {type(payload)}")
                                continue
                                
                            logging.info(f"Command Received: {payload}")
                            
                            # --- Handle Direct Status Polls from UI ---
                            if payload.get("action") == "status":
                                manager.last_ui_poll_time = time.time()
                                status_data = await asyncio.to_thread(manager.status, lightweight=True)
                                if status_data and status_data.get("status") == "success":
                                    refresh_payload = {
                                        "title": status_data.get("track", "Unknown"),
                                        "artist": status_data.get("artist", "Unknown"),
                                        "position": status_data.get("progress_ms", 0) / 1000.0,
                                        "duration": status_data.get("duration_ms", 0) / 1000.0,
                                        "status": "Playing" if status_data.get("is_playing") else "Paused"
                                    }
                                    await mqtt_client.publish("jarvis/sys/media_status", json.dumps(refresh_payload))
                                continue 
                            
                            # --- Execution Layer ---
                            await mqtt_client.publish("jarvis/feedback", json.dumps({
                                "device": "spotify",
                                "status": "info",
                                "message": f"Acknowledged: {payload.get('action')}",
                                "silent": True
                            }))
                            
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
                                "message": msg,
                                "silent": payload.get("silent", False)
                            }))
                            
                            # --- Conditional Background Refresh ---
                            if success:
                                action_cmd = payload.get("action")
                                
                                # 1. Duck and Unduck don't change track metadata. Skip refresh completely.
                                if action_cmd in ["duck", "unduck"]:
                                    continue
                                
                                # 2. For all other playback commands, only refresh if the widget is open in fullscreen.
                                #    (We know it's open if it's been sending "status" heartbeats in the last 15s)
                                widget_is_active = manager.ui_is_fullscreen and (time.time() - manager.last_ui_poll_time <= 15.0)
                                
                                if widget_is_active:
                                    async def background_refresh():
                                        await asyncio.sleep(1.0)
                                        try:
                                            status_data = await asyncio.to_thread(manager.status, lightweight=True)
                                            if status_data and status_data.get("status") == "success":
                                                refresh_payload = {
                                                    "title": status_data.get("track", "Unknown"),
                                                    "artist": status_data.get("artist", "Unknown"),
                                                    "position": status_data.get("progress_ms", 0) / 1000.0,
                                                    "duration": status_data.get("duration_ms", 0) / 1000.0,
                                                    "status": "Playing" if status_data.get("is_playing") else "Paused"
                                                }
                                                async with aiomqtt.Client("localhost") as bg_client:
                                                    await bg_client.publish("jarvis/sys/media_status", json.dumps(refresh_payload))
                                        except Exception as e:
                                            logging.error(f"Post-command status refresh failed: {e}")
                                    
                                    asyncio.create_task(background_refresh())
                                    
                    except json.JSONDecodeError:
                        logging.error("Received malformed JSON data.")
                    except Exception as e:
                        logging.error(f"Critical error processing MQTT message: {e}")
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