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
import aiohttp

# NEW: Import your centralized env loader
from utils.clEnvLoader import EnvLoader
from utils.clLocalMedia import send_spotify_command, set_spotify_shuffle, set_spotify_repeat, seek_spotify, REPEAT_MODES
from utils.clQueueTracker import QueueTracker, ADVANCED, DIVERGED
from utils.clYtMusicLyrics import YtMusicLyrics
from utils.clLyrics import LyricsCache, cache_key, clean_title, estimate_line_times, first_artist, pick_search_result, title_variants

# Per-user runtime state (gitignored), like the rest of data/.
LYRICS_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "lyrics_cache")

# --- LOGGING SETUP ---
import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('SPOTIFY')

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

def _add_playback_modes(payload: Dict[str, Any], status_data: Dict[str, Any]) -> None:
    """Carries shuffle/repeat from a status() result into a media_status payload when known."""
    for key in ("shuffle", "repeat"):
        if status_data.get(key) is not None:
            payload[key] = status_data[key]


_LRC_LINE_RE = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\](.*)$")

def parse_lrc(lrc_text: str) -> List[Dict[str, Any]]:
    """Parses standard LRC-format synced lyrics ("[mm:ss.xx]line text" per
    line, as returned by lrclib.net's syncedLyrics field) into a list of
    {"time": seconds, "text": ...} entries sorted by time. Lines with no
    timestamp (or empty after stripping it) are skipped -- e.g. lrclib's
    own [ar:]/[ti:]/[length:] metadata header lines."""
    entries = []
    for raw_line in lrc_text.splitlines():
        match = _LRC_LINE_RE.match(raw_line.strip())
        if not match:
            continue
        minutes, seconds, text = match.groups()
        text = text.strip()
        if not text:
            continue
        entries.append({"time": int(minutes) * 60 + float(seconds), "text": text})
    entries.sort(key=lambda e: e["time"])
    return entries

class SpotifyManager:
    """Enterprise state controller for Spotify API interaction and playback logic."""

    # Generous margin over clTerminal.py's ~10s local-session poll interval.
    LOCAL_STATUS_FRESHNESS_SECONDS = 15.0
    LOCAL_FAILURE_LIMIT = 3
    LOCAL_SKIP_SECONDS = 60.0
    QUEUE_REFILL_AT = 2             # refetch when this few tracks remain (the API returns ~20)
    QUEUE_REFRESH_BACKOFF_SECONDS = 30.0

    def __init__(self, debug_math: bool = True):
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        self.env = EnvLoader()

        self.client_id: str = self.env.get("SPOTIPY_CLIENT_ID", "")
        self.client_secret: str = self.env.get("SPOTIPY_CLIENT_SECRET", "")
        self.redirect_uri: str = self.env.get("SPOTIPY_REDIRECT_URI", "")
        
        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            logging.critical("Spotify credentials missing in .env file!")
            # Block forever so the supervisor doesn't infinitely resurrect an unconfigured service
            while True:
                time.sleep(3600)

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
        self.last_known_normal_volume: Optional[int] = None
        self._last_duck_cycle_time: float = 0.0

        # Free, local track/artist/position/state from clTerminal.py's
        # Spotify-filtered OS media session (see status()'s lightweight
        # fast path) -- kept fresh via the jarvis/sys/spotify_local_status
        # subscription in mqtt_service_listener.
        self._local_status: Optional[Dict[str, Any]] = None
        self._local_status_time: float = 0.0
        self._last_pushed_local: Optional[Tuple[tuple, float, float]] = None  # (signature, position, time) last sent to the UI

        self.confidence_threshold: float = 0.60
        self.perfect_match_threshold: float = 0.85
        self.debug_math: bool = debug_math

        # --- NEW: API Lag Compensators ---
        self._local_playing_state: Optional[bool] = None
        self._last_state_change_time: float = 0.0
        # True when the last execute_command went through the local client's
        # buttons (no API) -- the listener then skips its API status refresh,
        # since clTerminal.py's media listeners push the change themselves.
        self.last_command_local: bool = False
        # Circuit breaker for the local path: after LOCAL_FAILURE_LIMIT
        # failures in a row (Spotify not running, etc.) skip straight to the
        # API for LOCAL_SKIP_SECONDS instead of paying the local attempt each time.
        self._local_failures: Dict[str, int] = {}
        self._local_skip_until: Dict[str, float] = {}

        # Local copy of the up-next queue; fetched from the API on first
        # request, then followed through the free local media session.
        self.queue = QueueTracker()
        self._last_shuffle: Optional[bool] = None
        self._queue_refresh_blocked_until: float = 0.0

        # --- NEW: UI State Tracking ---
        self.ui_is_fullscreen: bool = False
        self.last_ui_poll_time: float = 0.0

        # --- NEW: API Rate Limit & Caching State ---
        self.api_cooldown_until: float = 0.0
        self.rolling_request_count: int = 0
        self.rolling_window_start: float = time.time()
        self.rolling_window_seconds: float = 30.0
        self.known_limit: Optional[int] = None
        self._cached_playback_data: Optional[Dict[str, Any]] = None
        self._cached_playback_time: float = 0.0

        # Lyric providers (chainable) and their on-disk cache.
        self.lyrics_cache = LyricsCache(LYRICS_CACHE_DIR)
        # Lyrics without timestamps are shown with estimated ones (see
        # clLyrics.estimate_line_times); LYRICS_ESTIMATE_UNSYNCED=0 in .env
        # turns that off and shows nothing for them instead.
        self.estimate_unsynced_lyrics: bool = self.env.get("LYRICS_ESTIMATE_UNSYNCED", "1") != "0"
        # Tried in order until one returns synced lyrics; each is
        # async (title, artist, duration) -> (synced LRC | None, plain | None, errored).
        self.lyrics_providers = [("lrclib", self._lookup_lrclib)]
        # YouTube Music (no login, no cookie): synced lyrics for much that
        # lrclib lacks. LYRICS_YTMUSIC_ENABLED=0 in .env turns it off.
        self._ytmusic: Optional[YtMusicLyrics] = None
        if self.env.get("LYRICS_YTMUSIC_ENABLED", "1") != "0":
            try:
                import ytmusicapi  # noqa: F401 -- availability check only
            except ImportError:
                logging.warning("ytmusicapi is not installed; YouTube Music lyrics are off. Run: pip install ytmusicapi")
            else:
                self._ytmusic = YtMusicLyrics()
                self.lyrics_providers.append(("ytmusic", self._lookup_ytmusic))
        # --- NEW: Lyrics (lrclib.net) ---
        # Only re-fetched when the (title, artist) pair actually changes --
        # keyed on that rather than a Spotify track id, since that's all
        # this codebase's media_status payload already carries.
        self._last_lyrics_track: Optional[Tuple[str, str]] = None
        self._last_lyrics_duration: float = 0.0
        self._last_lyrics_found: bool = False
        self._lyrics_settling: Optional[Tuple[str, str]] = None
        self._background_tasks: set = set()

    # --- RATE LIMIT & CACHE ENGINES ---
    def _check_rate_limit(self) -> bool:
        """Pre-flight check to enforce API cooldowns and track rolling limits."""
        now = time.time()
        if now < self.api_cooldown_until:
            logging.warning(f"Spotify API is on cooldown for {self.api_cooldown_until - now:.1f} more seconds.")
            return False

        if now - self.rolling_window_start > self.rolling_window_seconds:
            self.rolling_window_start = now
            self.rolling_request_count = 0

        if self.known_limit is not None and self.rolling_request_count >= (self.known_limit - 1):
            logging.warning("Approaching known Spotify API rate limit. Blocking request to avoid 429.")
            self.api_cooldown_until = self.rolling_window_start + self.rolling_window_seconds
            return False

        self.rolling_request_count += 1
        return True

    def _get_current_playback(self, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """Fetch current playback with a 3-second cache and rate limit protection."""
        now = time.time()
        if not force_refresh and now - self._cached_playback_time < 3.0:
            return self._cached_playback_data

        if not self._check_rate_limit():
            return self._cached_playback_data  # Return stale cache if blocked

        try:
            playback = self.sp.current_playback()
            self._cached_playback_data = playback
            self._cached_playback_time = time.time()
            return playback
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get("Retry-After", 30)) if hasattr(e, 'headers') and e.headers else 30
                self.api_cooldown_until = time.time() + retry_after
                self.known_limit = max(1, self.rolling_request_count)
                logging.error(f"HTTP 429 Too Many Requests! Cooldown set for {retry_after}s. Known limit recorded as {self.known_limit}.")
            raise

    # --- DEVICE WAKEUP ENGINE ---
    def _wake_up_spotify(self) -> None:
        """Wakes up Spotify, launching it first via _launch_spotify_app if it isn't running."""
        logging.info("No active device found. Attempting to wake up local client...")
        try:
            if sys.platform.startswith('linux'):
                # Targets Spotify's D-Bus name directly (MPRIS).
                import subprocess
                result = subprocess.run([
                    "dbus-send", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify",
                    "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.PlayPause"
                ], check=False, capture_output=True, text=True)
                if result.returncode != 0:
                    # Spotify isn't running -- launch it and retry.
                    if self._launch_spotify_app():
                        time.sleep(4)
                        subprocess.run([
                            "dbus-send", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify",
                            "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.PlayPause"
                        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform == 'win32':
                if not self._wake_up_spotify_windows():
                    # No SMTC session -- Spotify likely isn't running at all.
                    if self._launch_spotify_app():
                        time.sleep(4)
                        self._wake_up_spotify_windows()
                    else:
                        import pyautogui
                        pyautogui.press('playpause')
            else:
                # Mac fallback
                import pyautogui
                pyautogui.press('playpause')

            time.sleep(2)  # Wait for the OS and Spotify app to connect to the Spotify network
        except ImportError:
            logging.error("Failed to wake up Spotify: pyautogui is not installed.")
        except Exception as e:
            logging.error(f"Failed to wake up Spotify: {e}")

    def _launch_spotify_app(self) -> bool:
        """Locates and launches the Spotify desktop client. Returns True if a launch was attempted."""
        import subprocess
        import shutil

        if sys.platform == 'win32':
            candidates = []
            appdata = os.environ.get('APPDATA', '')
            if appdata:
                candidates.append(os.path.join(appdata, "Spotify", "Spotify.exe"))

            # Non-default installs record their location in the uninstall registry.
            try:
                import winreg
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Spotify") as key:
                            install_location = winreg.QueryValueEx(key, "InstallLocation")[0]
                            if install_location:
                                candidates.append(os.path.join(install_location, "Spotify.exe"))
                    except FileNotFoundError:
                        pass
            except Exception:
                pass

            which_path = shutil.which("spotify") or shutil.which("Spotify.exe")
            if which_path:
                candidates.append(which_path)

            for path in candidates:
                if path and os.path.exists(path):
                    logging.info(f"Launching Spotify from detected install path: {path}")
                    subprocess.Popen([path])
                    return True

            # Microsoft Store package -- no .exe on disk, only an AppX package.
            try:
                pkg_result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-AppxPackage -Name SpotifyAB.SpotifyMusic).PackageFamilyName"],
                    capture_output=True, text=True, check=False
                )
                family_name = pkg_result.stdout.strip()
                if family_name:
                    aumid = f"{family_name}!Spotify"
                    logging.info(f"Launching Spotify Store app via AUMID: {aumid}")
                    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"])
                    return True
            except Exception:
                pass

            logging.warning("Could not locate a Spotify installation to launch.")
            return False

        # Linux
        which_path = shutil.which("spotify")
        if which_path:
            logging.info(f"Launching Spotify from PATH: {which_path}")
            subprocess.Popen([which_path])
            return True

        if shutil.which("flatpak"):
            try:
                installed = subprocess.run(
                    ["flatpak", "list", "--app", "--columns=application"],
                    capture_output=True, text=True, check=False
                ).stdout
                if "com.spotify.Client" in installed:
                    logging.info("Launching Spotify via Flatpak.")
                    subprocess.Popen(["flatpak", "run", "com.spotify.Client"])
                    return True
            except Exception:
                pass

        logging.warning("Could not locate a Spotify installation to launch.")
        return False

    def _wake_up_spotify_windows(self) -> bool:
        """Sends Play directly to Spotify's SMTC session via winsdk. Returns True if found and commanded."""
        try:
            from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SMTC
        except ImportError:
            logging.warning("winsdk is not installed; cannot target Spotify directly on Windows. Run: pip install winsdk")
            return False

        async def _play_spotify_session() -> bool:
            manager = await SMTC.request_async()
            for session in manager.get_sessions():
                if "spotify" in session.source_app_user_model_id.lower():
                    await session.try_play_async()
                    return True
            return False

        try:
            return asyncio.run(_play_spotify_session())
        except Exception as e:
            logging.error(f"Failed to target Spotify session via SMTC: {e}")
            return False

    def _get_active_device(self) -> Optional[str]:
        """Retrieve the active device ID."""
        # 1. Zero-cost fast path: Check if we already have the device in the playback cache!
        playback = self._get_current_playback(force_refresh=False)
        if playback and playback.get('device'):
            return playback['device'].get('id')
            
        # 2. Fallback: Hit the API if no active playback was cached
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

    def _get_any_device(self) -> Optional[str]:
        """Like _get_active_device, but falls back to any connected (not
        necessarily 'active'/playing) device instead of waking playback --
        for commands like volume that shouldn't start music just to run."""
        device = self._get_active_device()
        if device:
            return device
        try:
            devices = self.sp.devices().get('devices', [])
            return devices[0].get('id') if devices else None
        except Exception as e:
            logging.error(f"Failed to get Spotify devices: {e}")
            return None

    # --- STATUS ENGINE ---
    def _lightweight_status_from_local_session(self) -> Optional[Dict[str, Any]]:
        """Builds a lightweight status() response from clTerminal.py's free,
        local Spotify-filtered OS media session instead of a real Web API
        call -- the fast path for the media widget's regular polling.
        Volume isn't part of that OS session data at all, so it falls back
        to our own best-known cached value rather than a real reading.
        Returns None (falling through to the real API) whenever that local
        data is missing, stale, or reports nothing found -- including
        whenever local Spotify isn't the active playback device, e.g. the
        user is listening on a different Spotify Connect device."""
        if self._local_status is None:
            return None
        if time.time() - self._local_status_time > self.LOCAL_STATUS_FRESHNESS_SECONDS:
            return None
        if not self._local_status.get("found"):
            return None

        volume = self.last_known_normal_volume if self.last_known_normal_volume is not None else "Unknown"
        return {
            "status": "success",
            "is_playing": self._local_status.get("status") == "Playing",
            "volume": volume,
            "track": self._local_status.get("title") or "Unknown",
            "artist": self._local_status.get("artist") or "Unknown",
            "context": "Unknown",
            "next_in_queue": "Unknown",
            "progress_ms": int(self._local_status.get("position", 0.0) * 1000),
            "duration_ms": int(self._local_status.get("duration", 0.0) * 1000),
            "shuffle": self._local_status.get("shuffle"),
            "repeat": self._local_status.get("repeat"),
        }

    def status(self, lightweight: bool = False, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Retrieve comprehensive status of the current playback.
        If lightweight=True, skips secondary API calls for context/queue.
        """
        if lightweight and not force_refresh:
            local = self._lightweight_status_from_local_session()
            if local is not None:
                return local

        try:
            playback = self._get_current_playback(force_refresh=force_refresh)
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
                    "duration_ms": playback['item'].get("duration_ms", 0),
                    "shuffle": playback.get("shuffle_state"),
                    "repeat": playback.get("repeat_state"),
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
        if status_data.get("status") == "success":
            status_text = (
                f"\n--- Spotify Status ---\n"
                f"Status:\t\t{'Playing' if status_data.get('is_playing') else 'Paused'}\n"
                f"Track:\t\t{status_data.get('track')}\n"
                f"Artist(s):\t{status_data.get('artist')}\n"
                f"Context:\t{status_data.get('context')}\n"
                f"Volume:\t\t{status_data.get('volume')}%\n"
                f"Next in Queue:\t{status_data.get('next_in_queue')}\n"
                f"----------------------\n"
            )
            logging.info(status_text)
        else:
            logging.info(f"\n--- Spotify Status ---\nStatus:\t\t{status_data.get('message', 'Inactive')}\n----------------------\n")

    # --- LYRICS (lrclib.net) ---
    LRCLIB_API = "https://lrclib.net/api"
    LRCLIB_MAX_GETS = 3  # title/artist variants tried before falling back to /search

    async def _lrclib_request(self, session, endpoint: str, params: Dict[str, Any]) -> Tuple[Any, bool]:
        """(json | None, errored). A 404 is a clean "not there"; anything else
        that isn't a 200 (rate limit, server error, network failure) is an
        error, so the caller doesn't cache it as a miss."""
        try:
            async with session.get(f"{self.LRCLIB_API}/{endpoint}", params=params) as resp:
                if resp.status == 404:
                    return None, False
                if resp.status != 200:
                    return None, True
                return await resp.json(), False
        except Exception as e:
            logging.warning(f"lrclib {endpoint} failed for '{params.get('track_name')}': {e}")
            return None, True

    async def _lookup_lrclib(self, title: str, artist: str, duration: float) -> Tuple[Optional[str], Optional[str], bool]:
        """(synced LRC, plain text, errored). Exact lookups first (the title
        as Spotify has it, then without "(feat. ...)"-style tags), then one
        fuzzy /search; stops at the first synced hit and remembers the first
        plain-only one as a fallback."""
        plain: Optional[str] = None
        errored = False
        duration_param = int(round(duration))
        pairs = [(t, a) for t in title_variants(title) for a in dict.fromkeys([artist, first_artist(artist)])][:self.LRCLIB_MAX_GETS]
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            for track_name, artist_name in pairs:
                data, failed = await self._lrclib_request(session, "get", {"track_name": track_name, "artist_name": artist_name, "duration": duration_param})
                errored = errored or failed
                if isinstance(data, dict):
                    if data.get("syncedLyrics"):
                        return data["syncedLyrics"], data.get("plainLyrics") or plain, errored
                    plain = plain or data.get("plainLyrics")

            results, failed = await self._lrclib_request(session, "search", {"track_name": clean_title(title), "artist_name": first_artist(artist)})
            errored = errored or failed
            best = pick_search_result(results if isinstance(results, list) else [], duration)
            if best.get("syncedLyrics"):
                return best["syncedLyrics"], best.get("plainLyrics") or plain, errored
            plain = plain or best.get("plainLyrics")
        return None, plain, errored

    YTMUSIC_TIMEOUT_SECONDS = 15

    async def _lookup_ytmusic(self, title: str, artist: str, duration: float) -> Tuple[Optional[str], Optional[str], bool]:
        # ytmusicapi is blocking: a worker thread, and a deadline so a hung
        # request can't stall the lookup (the thread finishes on its own).
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._ytmusic.find, title, artist, duration), timeout=self.YTMUSIC_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logging.warning(f"YouTube Music lyrics timed out for '{title}'")
            return None, None, True

    async def _lookup_lyrics(self, title: str, artist: str, duration: float) -> Tuple[Optional[str], Optional[str], str, bool]:
        """Runs the providers in order until one has synced lyrics. Returns
        (synced, plain, source, errored); plain is the first plain text any
        of them had."""
        plain, source, errored = None, "none", False
        for name, provider in self.lyrics_providers:
            try:
                synced, provider_plain, failed = await provider(title, artist, duration)
            except Exception as e:
                logging.warning(f"Lyrics provider '{name}' failed for '{title}': {e}")
                errored = True
                continue
            errored = errored or failed
            if synced:
                return synced, provider_plain or plain, name, errored
            if provider_plain and plain is None:
                plain, source = provider_plain, name
        return None, plain, source, errored

    def _lyrics_payload(self, entry: Dict[str, Any], duration: float) -> Dict[str, Any]:
        if entry.get("synced"):
            lines = parse_lrc(entry["synced"])
            if lines:
                return {"found": True, "synced": True, "lines": lines}
        if entry.get("plain") and self.estimate_unsynced_lyrics:
            lines = estimate_line_times(entry["plain"], duration)
            if lines:
                return {"found": True, "synced": False, "lines": lines}
        return {"found": False}

    async def fetch_lyrics(self, title: str, artist: str, duration_seconds: float) -> Dict[str, Any]:
        """Synced lyrics if any provider has them (lrclib first), otherwise
        plain lyrics with estimated line times, otherwise {"found": False}.
        Results -- including "nothing found" -- are cached on disk so a track
        is looked up once, not on every play; a lookup that failed on the
        network is never cached as a miss. Never raises: a missing lyric is
        an entirely normal outcome, not an error condition."""
        provider_names = [name for name, _ in self.lyrics_providers]
        key = cache_key(title, artist, duration_seconds)
        entry = self.lyrics_cache.get(key, provider_names)
        if entry is None:
            synced, plain, source, errored = await self._lookup_lyrics(title, artist, duration_seconds)
            if errored and not synced:
                # A provider couldn't be asked (down, paused, rate limited):
                # use what we have for now but let the next play try again.
                return self._lyrics_payload({"synced": None, "plain": plain}, duration_seconds)
            entry = self.lyrics_cache.put(key, synced, plain, source, provider_names)
        return self._lyrics_payload(entry, duration_seconds)

    async def maybe_publish_lyrics(self, mqtt_client, title: str, artist: str, duration_seconds: float) -> None:
        """Re-fetches and publishes the full synced lyric sheet only when
        the (title, artist) pair actually changes. The UI tracks position
        locally to pick the current/next/previous line by index -- the
        same way its progress bar already advances between the
        infrequent status polls (MediaWidget's own timer only re-checks
        with a real request near a track's end or after an explicit
        command, interpolating position client-side the rest of the
        time) -- so there's no need to resolve or re-send anything per
        line change; one publish per track covers the whole song."""
        track_key = (title, artist)
        if track_key == self._last_lyrics_track:
            # Same track: only worth another lookup if the first came up empty
            # and the duration it used turned out wrong (the local timeline
            # can lag a track change, so the first reading may be the previous
            # track's length) -- lrclib matches on duration.
            corrected = abs(duration_seconds - self._last_lyrics_duration) > self.LYRICS_DURATION_CORRECTION_SECONDS
            if self._last_lyrics_found or not corrected:
                return
        self._last_lyrics_track = track_key
        self._last_lyrics_duration = duration_seconds
        result = await self.fetch_lyrics(title, artist, duration_seconds)
        self._last_lyrics_found = bool(result.get("found"))
        await mqtt_client.publish("jarvis/sys/spotify_lyrics", json.dumps({
            "title": title,
            "artist": artist,
            **result,
        }))

    LYRICS_SETTLE_SECONDS = 1.5
    LYRICS_DURATION_CORRECTION_SECONDS = 2.0

    async def _request_lyrics(self, mqtt_client, title: str, artist: str, duration_seconds: float) -> None:
        """Called for every changed local status. For a *new* track it doesn't
        look up lyrics straight away: the OS reports the new title a moment
        before the new length (live: GEIZA arrived with the previous track's
        226s instead of its own 136s), and a lookup with the wrong duration
        finds nothing -- and, once made, is never repeated. It waits briefly
        and uses whatever duration the local status holds by then."""
        track_key = (title, artist)
        if track_key == self._last_lyrics_track:
            await self.maybe_publish_lyrics(mqtt_client, title, artist, duration_seconds)  # acts only on a late correction
        elif track_key != self._lyrics_settling:
            self._lyrics_settling = track_key
            task = asyncio.create_task(self._publish_lyrics_when_settled(mqtt_client, title, artist))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _publish_lyrics_when_settled(self, mqtt_client, title: str, artist: str) -> None:
        try:
            await asyncio.sleep(self.LYRICS_SETTLE_SECONDS)
            local = self._local_status or {}
            if (local.get("title") or "Unknown", local.get("artist") or "Unknown") != (title, artist):
                return  # a different track is playing now; its own change scheduled its own lookup
            await self.maybe_publish_lyrics(mqtt_client, title, artist, local.get("duration", 0.0))
        except Exception as e:
            logging.error(f"Lyrics lookup for '{title}' failed: {e}")
        finally:
            if self._lyrics_settling == (title, artist):
                self._lyrics_settling = None

    LOCAL_SEEK_JUMP_SECONDS = 1.5

    def _local_status_changed(self, payload: Dict[str, Any]) -> bool:
        """True when the local session differs from what was last pushed to
        the UI: any visible field, or a position that jumped off the
        interpolated course (a seek -- plain drift is the UI's own job)."""
        now = time.time()
        signature = tuple(payload.get(key) for key in (
            "title", "artist", "status", "duration", "shuffle", "repeat", "can_seek", "can_next", "can_prev",
        )) + (self.last_known_normal_volume,)
        last = self._last_pushed_local
        position = payload.get("position", 0.0)
        self._last_pushed_local = (signature, position, now)
        if last is None or last[0] != signature:
            return True
        expected = last[1] + ((now - last[2]) if payload.get("status") == "Playing" else 0.0)
        return abs(position - expected) > self.LOCAL_SEEK_JUMP_SECONDS

    async def handle_local_status_update(self, mqtt_client, payload: Dict[str, Any]) -> None:
        """Reacts to clTerminal.py's free, local Spotify-filtered OS media
        session by also pushing a fresh media_status (and re-checking
        lyrics) directly, rather than only ever refreshing on an explicit
        UI poll or command. poll_spotify_local_status still publishes on
        its ~10s heartbeat (it doubles as the freshness signal for status()),
        but the UI only hears about it when something actually changed --
        the OS-event listeners wake that loop immediately on a track change,
        so a change reaches the UI in well under a second."""
        self._local_status = payload
        self._local_status_time = time.time()
        if not payload.get("found"):
            self._last_pushed_local = None
            return
        if not self._local_status_changed(payload):
            return

        refresh_payload = {
            "title": payload.get("title") or "Unknown",
            "artist": payload.get("artist") or "Unknown",
            "position": payload.get("position", 0.0),
            "duration": payload.get("duration", 0.0),
            "status": payload.get("status", "Paused"),
            "volume": self.last_known_normal_volume if self.last_known_normal_volume is not None else 100,
        }
        # Free from the local session; the UI shows them without any API call.
        for key in ("shuffle", "repeat", "can_seek", "can_next", "can_prev"):
            if payload.get(key) is not None:
                refresh_payload[key] = payload[key]
        await mqtt_client.publish("jarvis/sys/media_status", json.dumps(refresh_payload))
        await self._request_lyrics(mqtt_client, refresh_payload["title"], refresh_payload["artist"], refresh_payload["duration"])
        await self.track_queue(mqtt_client, payload)

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
            debug_text = (
                f"\n[DEBUG] Evaluating: '{actual_name}' by '{actual_artist}' (Pop: {popularity})\n"
                f"  ├─ Track Score:  {track_score:.2f}\n"
                f"  ├─ Artist Score: {artist_score:.2f}\n"
                f"  └─ Final Math:   (Base {base_match:.2f} * 0.85) + (Pop {pop_weight:.2f} * 0.15) = {composite:.0%}\n"
                f"--------------------------------------------------\n"
            )
            logging.debug(debug_text)
            
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
            text_sim = jellyfish.jaro_winkler_similarity(artist_name.lower(), item['name'].lower())
            phonetic_sim = jellyfish.jaro_winkler_similarity(jellyfish.metaphone(artist_name), jellyfish.metaphone(item['name']))
            if max(text_sim, phonetic_sim) < 0.75:
                logging.warning(f"Rejecting weak artist match: '{artist_name}' -> '{item['name']}' ({max(text_sim, phonetic_sim):.2f})")
                return False, f"Artist '{artist_name}' not found."

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
                # Fresh read: Spotify Connect's own volume state can lag behind
                # a PUT we just issued (e.g. the previous unduck).
                current_playback = self._get_current_playback(force_refresh=True)
                if not current_playback or not current_playback.get('is_playing'):
                    logging.info("[DUCK] Spotify is not currently playing. Skipping ducking.")
                    return False, "Not currently playing"

                # Trust our own recent baseline over a possibly-stale API read
                # -- but only for a few seconds, matching how long Spotify
                # Connect's read actually takes to catch up to a PUT we just
                # issued. The old window was 30s, long enough for a real
                # external change (e.g. the user adjusting volume in the
                # Spotify app itself) to also fall inside it and get silently
                # overridden by our stale cache.
                recently_self_initiated = (time.time() - self._last_duck_cycle_time) < 4.0
                if recently_self_initiated and self.last_known_normal_volume is not None:
                    self.pre_duck_volume = self.last_known_normal_volume
                else:
                    raw_vol = current_playback.get('device', {}).get('volume_percent')
                    current_vol = raw_vol if raw_vol is not None else 50

                    if current_vol > 50 or self.last_known_normal_volume is None:
                        self.last_known_normal_volume = current_vol
                        self.pre_duck_volume = current_vol
                    else:
                        self.pre_duck_volume = self.last_known_normal_volume

                self.ducked_device_id = device
                new_vol = max(0, int(self.pre_duck_volume * 0.8))

                logging.info(f"[DUCK] Ducking volume from {self.pre_duck_volume}% to {new_vol}% on device '{device}'")
                self.sp.volume(new_vol, device_id=device)
                self._last_duck_cycle_time = time.time()
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
            self._last_duck_cycle_time = time.time()

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

    def _local_first(self, kind: str, local_call) -> bool:
        """Runs a local-client command behind a per-`kind` circuit breaker (a
        client that refuses shuffle must not disable the play/next buttons).
        True means it was delivered (and, for shuffle/repeat/seek, confirmed
        by the session's own state); False means use the API."""
        now = time.time()
        if now < self._local_skip_until.get(kind, 0.0):
            return False
        try:
            delivered = bool(local_call())
        except Exception as e:
            logging.error(f"Local Spotify '{kind}' failed: {e}")
            delivered = False

        if delivered:
            self._local_failures[kind] = 0
            self.last_command_local = True
            return True
        self._local_failures[kind] = self._local_failures.get(kind, 0) + 1
        if self._local_failures[kind] >= self.LOCAL_FAILURE_LIMIT:
            self._local_failures[kind] = 0
            self._local_skip_until[kind] = now + self.LOCAL_SKIP_SECONDS
            logging.warning(f"Local Spotify '{kind}' failed {self.LOCAL_FAILURE_LIMIT}x in a row; using the API for {self.LOCAL_SKIP_SECONDS:.0f}s.")
        return False

    def _try_local_media_command(self, action: str) -> Optional[str]:
        """Presses the media button on the local Spotify client instead of
        calling the Web API. Returns the result message, or None when there
        is no local client to command (the caller then uses the API)."""
        local = self._lightweight_status_from_local_session()
        was_playing = local["is_playing"] if local else None
        if not self._local_first("button", lambda: send_spotify_command(action)):
            return None

        self._last_state_change_time = time.time()
        if action == "toggle":
            self._local_playing_state = None if was_playing is None else not was_playing
            if was_playing is None:
                return "Toggled playback."
            return "Music paused." if was_playing else "Resuming playback."
        self._local_playing_state = True
        return "Skipped to next track." if action == "next" else "Returned to previous track."

    # --- SHUFFLE / REPEAT / SEEK (local first, API on failure) ---
    def _set_shuffle(self, enabled: bool) -> Tuple[bool, str]:
        message = "Shuffle on." if enabled else "Shuffle off."
        if self._local_first("shuffle", lambda: set_spotify_shuffle(enabled)):
            return True, message
        device = self._get_any_device()
        if not device: return False, "Spotify is not active."
        self.sp.shuffle(enabled, device_id=device)
        return True, message

    def _set_repeat(self, mode: str) -> Tuple[bool, str]:
        if mode not in REPEAT_MODES:
            return False, f"Unknown repeat mode '{mode}'. Use off, track or context."
        message = f"Repeat set to {mode}."
        if self._local_first("repeat", lambda: set_spotify_repeat(mode)):
            return True, message
        device = self._get_any_device()
        if not device: return False, "Spotify is not active."
        self.sp.repeat(mode, device_id=device)
        return True, message

    def _seek(self, position_ms: int) -> Tuple[bool, str]:
        position_ms = max(0, int(position_ms))
        seconds = position_ms // 1000
        message = f"Moved to {seconds // 60}:{seconds % 60:02d}."
        if self._local_first("seek", lambda: seek_spotify(position_ms)):
            return True, message
        device = self._get_any_device()
        if not device: return False, "Spotify is not active."
        self.sp.seek_track(position_ms, device_id=device)
        return True, message

    # --- QUEUE ---
    def refresh_queue(self) -> bool:
        """The only place the queue costs an API request. False if blocked
        (rate limit / recent failure) or the request failed."""
        if time.time() < self._queue_refresh_blocked_until or not self._check_rate_limit():
            return False
        try:
            self.queue.load(self.sp.queue())
            return True
        except Exception as e:
            self._queue_refresh_blocked_until = time.time() + self.QUEUE_REFRESH_BACKOFF_SECONDS
            logging.error(f"Queue refresh failed: {e}")
            return False

    def queue_payload(self, limit: int = 10) -> Dict[str, Any]:
        """What jarvis/sys/spotify_queue carries."""
        return {"queue": self.queue.snapshot(limit), "remaining": self.queue.remaining}

    async def track_queue(self, mqtt_client, local_status: Dict[str, Any]) -> None:
        """Follows playback through the local queue from the free local media
        session; refetches (one API request) only when playback leaves the
        queue we expected, the queue is nearly used up, or shuffle changed
        (Spotify reorders everything). Does nothing until a queue was first
        requested -- no one watching means no API cost."""
        if not self.queue.loaded:
            return
        shuffle = local_status.get("shuffle")
        shuffle_changed = shuffle is not None and self._last_shuffle is not None and shuffle != self._last_shuffle
        if shuffle is not None:
            self._last_shuffle = shuffle

        outcome, _ = self.queue.observe(local_status.get("title"), int(round(local_status.get("duration", 0.0) * 1000)) or None)
        needs_refetch = shuffle_changed or outcome == DIVERGED or (outcome == ADVANCED and self.queue.remaining <= self.QUEUE_REFILL_AT)
        if needs_refetch:
            await asyncio.to_thread(self.refresh_queue)
        if needs_refetch or outcome == ADVANCED:
            await mqtt_client.publish("jarvis/sys/spotify_queue", json.dumps(self.queue_payload()), retain=True)

    # --- MAIN EXECUTION ENGINE ---
    def execute_command(self, action: str, volume: Optional[int] = None, track_name: Optional[str] = None, 
                        artist_name: Optional[str] = None, playlist_name: Optional[str] = None, 
                        search_query: Optional[str] = None, choice_index: Optional[int] = None,
                        enabled: Optional[bool] = None, mode: Optional[str] = None,
                        position_ms: Optional[int] = None) -> Tuple[bool, Any]:
        """Clean router that delegates actions to the appropriate class helper methods."""
        self.last_command_local = False
        try:
            if action in ["duck", "unduck"]:
                return self._handle_ducking(action)

            if action == "shuffle" and enabled is not None:
                return self._set_shuffle(enabled)
            if action == "repeat" and mode is not None:
                return self._set_repeat(mode)
            if action == "seek" and position_ms is not None:
                return self._seek(position_ms)

            if action in ["queue", "queue_refresh"]:
                if action == "queue_refresh" or not self.queue.loaded:
                    if not self.refresh_queue():
                        return False, "Could not fetch the queue right now."
                return True, f"{self.queue.remaining} tracks up next."

            if action in ["toggle", "next", "prev"]:
                local_message = self._try_local_media_command(action)
                if local_message is not None:
                    return True, local_message

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
                    playback = self._get_current_playback()
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
                # _get_any_device, not _ensure_active_device -- the latter
                # wakes/resumes playback when nothing's active, which made
                # dragging the volume slider start the music.
                device = self._get_any_device()
                if not device: return False, "Spotify is not active."
                clean_vol = max(0, min(100, volume))
                self.sp.volume(clean_vol, device_id=device)
                self.last_known_normal_volume = clean_vol
                self._last_duck_cycle_time = time.time()
                return True, f"Volume changed to {clean_vol}%."
            
            return False, f"Action '{action}' is not recognized."
                
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 403:
                if action == "play" and not any([track_name, artist_name, playlist_name, search_query, choice_index]):
                    # Verify actual state -- a bare 403 doesn't always mean "already playing".
                    try:
                        playback = self._get_current_playback(force_refresh=True)
                    except Exception:
                        playback = None
                    if playback and playback.get("is_playing"):
                        return False, "Music is already playing."
                    return False, "Nothing queued to resume. Try naming a song, artist, or playlist."
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
                await mqtt_client.subscribe("jarvis/sys/spotify_local_status")
                await mqtt_client.publish("jarvis/sys/module_ready", json.dumps({"module": "music"}))

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

                        if topic == "jarvis/sys/spotify_local_status":
                            await manager.handle_local_status_update(mqtt_client, payload)
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
                                    raw_volume = status_data.get("volume")
                                    refresh_payload = {
                                        "title": status_data.get("track", "Unknown"),
                                        "artist": status_data.get("artist", "Unknown"),
                                        "position": status_data.get("progress_ms", 0) / 1000.0,
                                        "duration": status_data.get("duration_ms", 0) / 1000.0,
                                        "status": "Playing" if status_data.get("is_playing") else "Paused",
                                        "volume": raw_volume if isinstance(raw_volume, int) else 100
                                    }
                                    _add_playback_modes(refresh_payload, status_data)
                                    await mqtt_client.publish("jarvis/sys/media_status", json.dumps(refresh_payload))
                                    await manager.maybe_publish_lyrics(mqtt_client, refresh_payload["title"], refresh_payload["artist"], refresh_payload["duration"])
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
                                choice_index=payload.get("choice_index"),
                                enabled=payload.get("enabled"),
                                mode=payload.get("mode"),
                                position_ms=payload.get("position_ms")
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

                                if action_cmd in ["queue", "queue_refresh"]:
                                    await mqtt_client.publish("jarvis/sys/spotify_queue", json.dumps(manager.queue_payload()), retain=True)
                                    continue

                                # 1. Duck and Unduck don't change track metadata. Skip refresh completely.
                                if action_cmd in ["duck", "unduck"]:
                                    continue
                                
                                # 2. Always refresh status on explicit playback commands to keep UI synced
                                # (Skipped after a local button press: clTerminal.py's media listeners already push it, no API needed.)
                                if action_cmd in ["play", "pause", "toggle", "next", "prev", "status"] and not manager.last_command_local:
                                    async def background_refresh():
                                        await asyncio.sleep(1.0)
                                        try:
                                            status_data = await asyncio.to_thread(manager.status, lightweight=True, force_refresh=True)
                                            if status_data and status_data.get("status") == "success":
                                                raw_volume = status_data.get("volume")
                                                refresh_payload = {
                                                    "title": status_data.get("track", "Unknown"),
                                                    "artist": status_data.get("artist", "Unknown"),
                                                    "position": status_data.get("progress_ms", 0) / 1000.0,
                                                    "duration": status_data.get("duration_ms", 0) / 1000.0,
                                                    "status": "Playing" if status_data.get("is_playing") else "Paused",
                                                    "volume": raw_volume if isinstance(raw_volume, int) else 100
                                                }
                                                _add_playback_modes(refresh_payload, status_data)
                                                async with aiomqtt.Client("localhost") as bg_client:
                                                    await bg_client.publish("jarvis/sys/media_status", json.dumps(refresh_payload))
                                                    await manager.maybe_publish_lyrics(bg_client, refresh_payload["title"], refresh_payload["artist"], refresh_payload["duration"])
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