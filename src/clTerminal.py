# --- IMPORTS ---
import os
import sys
import subprocess
import platform
import json
import logging
import asyncio
import argparse
import aiomqtt
import time
import re
import webbrowser
import urllib.parse
import shutil
from typing import Tuple, Optional, Dict, Any, List

# --- LOGGING SETUP ---
import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('TERMINAL')

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

CURRENT_OS = platform.system().lower()

# Browsers host DRM/streaming media sessions (Netflix etc.) under an opaque,
# Windows-generated AUMID with no registered link to any of their own
# processes -- GetApplicationUserModelId returns "no AUMID" for every
# firefox.exe process on a real machine even while its Netflix SMTC session
# is live, and SMTC's own API exposes no PID either. Name/AUMID matching is
# structurally impossible for these, so they're only matched via the
# leftover-pairing fallback in list_app_volumes().
WINDOWS_BROWSER_PROCESS_NAMES = {"firefox.exe", "chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "vivaldi.exe"}

class TerminalManager:
    """Encapsulates system state, OS execution routing, and memory handling."""
    
    def __init__(self):
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        self.shortcuts: Dict[str, Any] = {"apps": {}, "folders": {}, "system_keywords": {}}
        self.last_opened_dir: str = os.path.expanduser("~")
        self.terminal_is_open: bool = False
        self.pid_file: str = os.path.join(os.path.expanduser("~"), ".jarvis_nav_pid")
        self.media_trigger = asyncio.Event()
        
        self._load_shortcuts()

    def _load_shortcuts(self) -> None:
        """Loads system.json into isolated class memory."""
        shortcuts_path = os.path.abspath(os.path.join(self.base_dir, "..", "config", "system.json"))
        try:
            with open(shortcuts_path, 'r', encoding='utf-8') as f:
                self.shortcuts = json.load(f)
            logging.info(f"Loaded {len(self.shortcuts.get('apps', {}))} apps and {len(self.shortcuts.get('folders', {}))} folders.")
        except FileNotFoundError:
            logging.warning("system.json not found. Operating with empty dictionaries.")
        except json.JSONDecodeError as e:
            logging.critical(f"Syntax error in system.json: {e}")

    # --- PID LIFECYCLE MANAGEMENT ---
    def _clear_pids(self) -> bool:
        """Synchronously kills all tracked terminal PIDs and clears the file."""
        killed_any = False
        if self.terminal_is_open and os.path.exists(self.pid_file):
            try:
                with open(self.pid_file, 'r') as f:
                    pids = f.read().splitlines()
            except Exception as e:
                logging.error(f"Failed to read PID tracker file: {e}")
                return False
            
            for p in pids:
                if not p.strip(): continue
                try:
                    target_pid = int(p.strip())
                    if CURRENT_OS == "linux":
                        os.kill(target_pid, 9) 
                    elif CURRENT_OS == "windows":
                        subprocess.run(f"taskkill /F /PID {target_pid} /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    killed_any = True
                except Exception:
                    pass
            
            open(self.pid_file, 'w').close()
            time.sleep(0.5)
        
        self.terminal_is_open = False
        return killed_any

    def _save_pid(self, pid: int) -> None:
        """Appends a new Process ID to the tracker file."""
        with open(self.pid_file, 'a') as f:
            f.write(f"{pid}\n")
        self.terminal_is_open = True

    # --- EXECUTION ROUTERS ---
    def _handle_open(self, target: str) -> Tuple[bool, str]:
        target_clean = target.lower().strip()
        sys_kw = self.shortcuts.get("system_keywords", {})
        terminal_aliases = sys_kw.get("terminal_aliases", ["terminal", "console", "shell", "cmd"])
        go_back_keywords = sys_kw.get("go_back", ["back", "up", "..", "previous", "return"])

        # A. Open Base Terminal
        if target_clean in terminal_aliases:
            if CURRENT_OS == "linux":
                term_emu = sys_kw.get("default_terminal", "gnome-terminal")
                subprocess.Popen([term_emu, "--working-directory", self.last_opened_dir, "--", "bash", "-c", f"echo $$ >> {self.pid_file}; exec bash"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.terminal_is_open = True
            elif CURRENT_OS == "windows":
                proc = subprocess.Popen(["cmd", "/k", f"title JarvisNavigation && cd /d {self.last_opened_dir}"], creationflags=subprocess.CREATE_NEW_CONSOLE)
                self._save_pid(proc.pid)
            return True, "Launched Terminal."

        # B. Open Standard App
        if target_clean in self.shortcuts.get("apps", {}):
            app_cmd = self.shortcuts["apps"][target_clean].get(CURRENT_OS)
            if app_cmd:
                subprocess.Popen(app_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True, f"Launched application: {target_clean}"
            return False, f"No executable defined for app '{target_clean}' on {CURRENT_OS}."

        # C. Path Resolution
        resolved_path = None
        if any(target_clean.startswith(kw) for kw in go_back_keywords):
            if self.last_opened_dir and os.path.exists(self.last_opened_dir):
                resolved_path = os.path.abspath(os.path.join(self.last_opened_dir, os.pardir))
        elif target_clean in self.shortcuts.get("folders", {}):
            guess = os.path.expanduser(self.shortcuts["folders"][target_clean])
            if os.path.isdir(guess):
                resolved_path = guess
        else:
            home_dir = os.path.expanduser("~")
            current_base = self.last_opened_dir if self.last_opened_dir else home_dir
            voiced_path = target_clean.replace(" ", "/")
            
            for path_guess in [
                os.path.expanduser(target_clean),
                os.path.join(home_dir, target.title().replace(" ", "/")),
                os.path.join(home_dir, voiced_path),
                f"/{voiced_path}",
                os.path.join(current_base, voiced_path),
                os.path.join(current_base, target.title().replace(" ", "/"))
            ]:
                if os.path.isdir(path_guess):
                    resolved_path = path_guess
                    break

        if resolved_path:
            self.last_opened_dir = resolved_path
            self._clear_pids()
            
            if CURRENT_OS == "linux":
                term_emu = sys_kw.get("default_terminal", "gnome-terminal")
                spawn_cmd = f"echo $$ >> {self.pid_file}; ls; exec bash"
                subprocess.Popen([term_emu, "--title=JarvisNavWindow", "--working-directory", resolved_path, "--", "bash", "-c", spawn_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.terminal_is_open = True
            elif CURRENT_OS == "windows":
                proc = subprocess.Popen(["cmd", "/k", f"title JarvisNavigation && cd /d {resolved_path} && dir"], creationflags=subprocess.CREATE_NEW_CONSOLE)
                self._save_pid(proc.pid)
            return True, f"Spawned terminal at: {resolved_path}"

        return False, f"Target '{target}' not found."

    def _handle_close(self, target: str) -> Tuple[bool, str]:
        target_clean = target.lower().strip()
        terminal_aliases = self.shortcuts.get("system_keywords", {}).get("terminal_aliases", ["terminal", "console", "shell", "cmd"])
        
        # A. Close Child Terminals
        if target_clean in terminal_aliases:
            if self._clear_pids():
                return True, "Closed tracked terminal instances."

        # B. Close Standard App
        if target_clean in self.shortcuts.get("apps", {}):
            app_data = self.shortcuts["apps"][target_clean]
            kill_target = app_data.get(f"{CURRENT_OS}_kill")
            
            if not kill_target:
                launch_cmd = app_data.get(CURRENT_OS, "")
                if CURRENT_OS == "windows":
                    exe_matches = re.findall(r'[\w.-]+\.exe', launch_cmd, re.IGNORECASE)
                    kill_target = exe_matches[-1] if exe_matches else launch_cmd.split()[0]
                elif CURRENT_OS == "linux":
                    kill_target = launch_cmd.split()[0].split('/')[-1]

            if not kill_target:
                return False, f"Could not determine kill target for '{target_clean}'."

            if CURRENT_OS == "linux":
                subprocess.Popen(["pkill", "-f", kill_target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif CURRENT_OS == "windows":
                subprocess.run(f"taskkill /F /IM {kill_target} /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
            return True, f"Sent termination signal to process: {kill_target}"
            
        return False, f"Target '{target}' not found in apps."

    def _handle_power(self, action: str) -> Tuple[bool, str]:
        if action == "shutdown":
            subprocess.Popen(["shutdown", "now"] if CURRENT_OS == "linux" else ["shutdown", "/s", "/t", "0"])
            return True, "Initiating system shutdown."
        elif action == "restart":
            subprocess.Popen(["reboot"] if CURRENT_OS == "linux" else ["shutdown", "/r", "/t", "0"])
            return True, "Initiating system reboot."
        elif action == "eco_mode_on":
            if CURRENT_OS == "linux":
                # Pause media before turning off monitors
                if shutil.which("playerctl"):
                    subprocess.Popen(["playerctl", "pause"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # Turn off monitors
                if shutil.which("xset"):
                    subprocess.Popen(["xset", "dpms", "force", "off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True, "Eco mode activated. Monitors powered down."
            elif CURRENT_OS == "windows":
                try:
                    import ctypes
                    # VK_MEDIA_PLAY_PAUSE is a toggle -- best-effort, no dedicated pause key.
                    ctypes.windll.user32.keybd_event(0xB3, 0, 0x0001, 0)
                    ctypes.windll.user32.keybd_event(0xB3, 0, 0x0001 | 0x0002, 0)
                    HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF = 0xFFFF, 0x0112, 0xF170, 2
                    ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF)
                    return True, "Eco mode activated. Monitor powered down."
                except Exception as e:
                    return False, f"Failed to power down monitor: {e}"
            return False, "Eco mode not supported on this OS."
        elif action == "eco_mode_off":
            if CURRENT_OS == "linux" and shutil.which("xset"):
                subprocess.Popen(["xset", "dpms", "force", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True, "Monitors powered up."
            elif CURRENT_OS == "windows":
                try:
                    import ctypes
                    # A tiny mouse nudge wakes the display more reliably than SC_MONITORPOWER.
                    MOUSEEVENTF_MOVE = 0x0001
                    ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 0, 1, 0, 0)
                    ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 0, -1, 0, 0)
                    return True, "Monitor powered up."
                except Exception as e:
                    return False, f"Failed to power up monitor: {e}"
            return False, "Eco mode off not supported on this OS."
        return False, "Invalid power command."

    def _handle_media(self, action: str) -> Tuple[bool, str]:
        if CURRENT_OS == "windows":
            # VK_MEDIA_PLAY_PAUSE always toggles -- no separate play/pause on Windows.
            vk_map = {"media_play": 0xB3, "media_pause": 0xB3, "media_next": 0xB0, "media_prev": 0xB1}
            vk = vk_map.get(action)
            if vk is None:
                return False, "Invalid media action."
            try:
                import ctypes
                KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
                ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
                ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
                label = {0xB3: "play/pause", 0xB0: "next", 0xB1: "previous"}[vk]
                return True, f"Media {label} executed."
            except Exception as e:
                return False, f"Windows media key simulation failed: {e}"

        if CURRENT_OS != "linux":
            return False, "Media control is currently only supported on Linux and Windows."

        if not shutil.which("playerctl"):
            return False, "playerctl is not installed. Please run 'sudo apt install playerctl'."

        cmd_map = {
            "media_play": "play",
            "media_pause": "pause",
            "media_next": "next",
            "media_prev": "previous"
        }

        player_action = cmd_map.get(action)
        if player_action:
            subprocess.Popen(["playerctl", player_action], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, f"Media {player_action} executed."
        return False, "Invalid media action."

    def _handle_web(self, action: str, target: str) -> Tuple[bool, str]:
        """Handles web navigation and direct browser searches natively."""
        try:
            if action == "search":
                encoded_query = urllib.parse.quote_plus(target)
                target_url = f"https://www.google.com/search?q={encoded_query}"
                webbrowser.open(target_url, new=2)
                return True, f"Executed web search for: '{target}'"
                
            elif action == "open_site":
                clean_target = target.replace(" ", "")
                
                if not clean_target.startswith("http"):
                    clean_target = f"https://{clean_target}"
                    
                webbrowser.open(clean_target, new=2)
                return True, f"Opened URL: {clean_target}"
                
            return False, "Invalid web action specified."
        except Exception as e:
            return False, f"Browser execution failed: {str(e)}"

    def execute_command(self, action: str, target: Optional[str] = None, level: Optional[int] = None) -> Tuple[bool, str]:
        """Main routing switchboard for the actuator."""
        try:
            if action == "open" and target:
                return self._handle_open(target)
            elif action == "close" and target:
                return self._handle_close(target)
            elif action in ["search", "open_site"] and target:
                return self._handle_web(action, target)
            elif action in ["shutdown", "restart", "eco_mode_on", "eco_mode_off"]:
                return self._handle_power(action)
            elif action in ["media_play", "media_pause", "media_next", "media_prev"]:
                return self._handle_media(action)
            elif action == "volume" and level is not None:
                clean_level = max(0, min(100, level))
                if CURRENT_OS == "linux":
                    if shutil.which("wpctl"):
                        # PipeWire
                        subprocess.Popen(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{clean_level}%"])
                        return True, f"Volume set to {clean_level}% via PipeWire."
                    elif shutil.which("pactl"):
                        # PulseAudio
                        subprocess.Popen(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{clean_level}%"])
                        return True, f"Volume set to {clean_level}% via PulseAudio."
                    elif shutil.which("amixer"):
                        # ALSA Fallback
                        subprocess.Popen(["amixer", "sset", "Master", f"{clean_level}%"])
                        return True, f"Volume set to {clean_level}% via ALSA."
                    else:
                        return False, "No audio subsystem found (wpctl, pactl, or amixer)."
                elif CURRENT_OS == "windows":
                    try:
                        from pycaw.pycaw import AudioUtilities
                        endpoint_volume = AudioUtilities.GetSpeakers().EndpointVolume
                        endpoint_volume.SetMasterVolumeLevelScalar(clean_level / 100.0, None)
                        return True, f"Volume set to {clean_level}%."
                    except ImportError:
                        return False, "Windows volume control requires the 'pycaw' library (pip install pycaw comtypes)."
                    except Exception as e:
                        return False, f"Failed to set Windows volume: {e}"
                return False, f"Volume control not supported on {CURRENT_OS}."
            elif action == "set_app_volume" and target is not None and level is not None:
                clean_level = max(0, min(100, level))
                if CURRENT_OS == "windows":
                    return self._set_app_volume_windows(target, clean_level)
                elif CURRENT_OS == "linux":
                    return self._set_app_volume_linux(target, clean_level)
                return False, f"App volume control not supported on {CURRENT_OS}."
            elif action == "toggle_app_mute" and target is not None:
                if CURRENT_OS == "windows":
                    return self._toggle_app_mute_windows(target)
                elif CURRENT_OS == "linux":
                    return self._toggle_app_mute_linux(target)
                return False, f"App volume control not supported on {CURRENT_OS}."
            elif action == "toggle_app_playback" and target is not None:
                # target is a media_player id (SMTC AUMID / MPRIS player name)
                # from list_app_volumes, not the audio-session id used above --
                # the two OS APIs address the same app differently.
                if CURRENT_OS == "windows":
                    return self._toggle_app_playback_windows(target)
                elif CURRENT_OS == "linux":
                    return self._toggle_app_playback_linux(target)
                return False, f"App playback control not supported on {CURRENT_OS}."
            return False, f"Action '{action}' is not recognized."
        except Exception as e:
            return False, f"OS Execution Error: {str(e)}"

    # --- PER-APP VOLUME MIXER ---
    def list_app_volumes(self) -> List[Dict[str, Any]]:
        """Enumerates per-application audio sessions with their current
        volume and mute state -- pycaw on Windows, pactl (PulseAudio/
        pipewire-pulse) on Linux. Grouped by app name (Windows) since one
        app can own several sessions (e.g. browser tabs); Linux's
        sink-inputs are already one-per-stream.

        Each app is then matched (by name, best-effort) against the OS's
        media transport sessions (SMTC/MPRIS) -- an audio session and a
        media session are two separate OS concepts. Only apps with a match
        are returned at all (this is a "what can I control" list, not a
        general volume mixer) -- an app with volume but no matching media
        session (e.g. Discord voice chat) has nothing to show here."""
        if CURRENT_OS == "windows":
            apps = self._list_app_volumes_windows()
            media_sessions = self._list_windows_media_sessions()
        elif CURRENT_OS == "linux":
            apps = self._list_app_volumes_linux()
            media_sessions = self._list_linux_media_sessions()
        else:
            return []

        matched = []
        for app in apps:
            media = None
            # Windows: ask the OS directly for the AUMID of the process(es)
            # actually named like this app, and match exactly, before
            # falling back to guessing from names. Not every app registers
            # a human-readable AUMID -- Firefox's own SMTC session reports
            # an opaque, Windows-generated id (e.g. "308046B0AF4A39CB")
            # that shares no substring with "firefox" at all, so name-based
            # matching alone can never find it. A multi-process app can
            # also have the AUMID registered on a completely different
            # process than the one pycaw happened to enumerate as owning
            # the audio session, hence trying app["pid"] first (cheap) and
            # then every same-named process system-wide (thorough).
            if CURRENT_OS == "windows":
                if app.get("pid"):
                    aumid = self._get_process_aumid(app["pid"])
                    if aumid:
                        media = media_sessions.get(aumid)
                if media is None:
                    media = self._find_media_session_by_process_name(app["id"], media_sessions)
            if media is None:
                # Matches on the display name, not "id" -- id is the process
                # name on Windows (fine to match on) but a numeric
                # sink-input id on Linux, which carries no app-name
                # information at all.
                media = self._find_matching_media_session(app["name"], media_sessions)
            if media:
                app["now_playing"] = media.get("title") or media.get("artist") or ""
                app["is_playing"] = media.get("status") == "Playing"
                app["media_player"] = media.get("player")
                matched.append(app)

        if CURRENT_OS == "windows":
            # Browsers can't be matched by AUMID at all (see
            # WINDOWS_BROWSER_PROCESS_NAMES) -- pair any browser still
            # without a match to whatever media session nothing else
            # claimed, but only when it's unambiguous which browser owns it.
            claimed = {a["media_player"] for a in matched}
            leftover = [v for k, v in media_sessions.items() if k not in claimed]
            unmatched_browsers = [a for a in apps if a not in matched and a["id"].lower() in WINDOWS_BROWSER_PROCESS_NAMES]
            if len(unmatched_browsers) == 1 and leftover:
                # A browser with several tabs can leave several sessions
                # behind (e.g. Netflix paused in one tab, YouTube playing in
                # another) -- the one actually playing should win over a
                # merely-paused leftover, not make the whole match bail out.
                playing = [m for m in leftover if m.get("status") == "Playing"]
                candidates = playing if playing else leftover
                if len(candidates) == 1:
                    app, media = unmatched_browsers[0], candidates[0]
                    app["now_playing"] = media.get("title") or media.get("artist") or ""
                    app["is_playing"] = media.get("status") == "Playing"
                    app["media_player"] = media.get("player")
                    matched.append(app)
        return matched

    def _get_process_aumid(self, pid: int) -> Optional[str]:
        """The AUMID Windows itself associates with a specific process (via
        GetApplicationUserModelId) -- exact, not guessed from the process
        name. Returns None if the process has no registered AUMID at all,
        or on any lookup failure (both are normal/expected for plenty of
        processes, not errors). Despite being documented under shell32.h,
        this is actually exported by kernel32.dll, not shell32.dll."""
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        try:
            kernel32 = ctypes.windll.kernel32
            h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h_process:
                logging.debug(f"AUMID lookup: OpenProcess failed for pid {pid} (error {ctypes.get_last_error()})")
                return None
            try:
                length = wintypes.UINT(0)
                kernel32.GetApplicationUserModelId(h_process, ctypes.byref(length), None)
                if length.value == 0:
                    return None
                buf = ctypes.create_unicode_buffer(length.value)
                result = kernel32.GetApplicationUserModelId(h_process, ctypes.byref(length), buf)
                if result != 0:
                    return None
                return buf.value
            finally:
                kernel32.CloseHandle(h_process)
        except Exception as e:
            logging.debug(f"AUMID lookup failed for pid {pid}: {e}")
            return None

    def _find_media_session_by_process_name(self, process_name: str, media_sessions: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Tries every currently-running process sharing this exact
        executable name (not just the one pycaw's audio-session
        enumeration happened to expose) for a matching AUMID. A
        multi-process app (e.g. Firefox, which can have a dozen+ processes
        all named 'firefox.exe') doesn't necessarily register its AUMID on
        the specific process that's actually rendering audio."""
        try:
            import psutil
        except ImportError:
            return None
        try:
            candidates = [p.pid for p in psutil.process_iter(["name"]) if p.info.get("name") == process_name]
        except Exception as e:
            logging.debug(f"Failed to enumerate processes named '{process_name}': {e}")
            return None
        for pid in candidates:
            aumid = self._get_process_aumid(pid)
            if aumid and aumid in media_sessions:
                return media_sessions[aumid]
        return None

    @staticmethod
    def _normalize_app_key(name: str) -> str:
        """Loosely matches an audio-session app name against a media-session
        id -- the two OS APIs name the same app differently (pycaw's
        'Spotify.exe' vs. SMTC's AUMID; pactl's 'Spotify' vs. MPRIS's
        'spotify.instance1234'), so this strips the parts that vary."""
        name = name.lower()
        name = re.sub(r"\.exe$", "", name)
        name = re.sub(r"\.instance\d+$", "", name)
        return name

    def _find_matching_media_session(self, app_name: str, media_sessions: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        key = self._normalize_app_key(app_name)
        if not key:
            return None
        for session_key, state in media_sessions.items():
            session_key_norm = self._normalize_app_key(session_key)
            if not session_key_norm:
                continue
            if key in session_key_norm or session_key_norm in key:
                return state
        return None

    def _list_windows_media_sessions(self) -> Dict[str, Dict[str, Any]]:
        """All current SMTC sessions, keyed by app user model ID -- not
        filtered to Spotify like _find_windows_spotify_session, since every
        app's media session is a candidate match here."""
        try:
            from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SMTC
        except ImportError:
            return {}

        async def _enumerate():
            manager = await SMTC.request_async()
            found = {}
            for session in manager.get_sessions():
                aumid = session.source_app_user_model_id or ""
                if not aumid:
                    continue
                try:
                    # GlobalSystemMediaTransportControlsSessionPlaybackStatus:
                    # 0=Closed, 1=Opened, 2=Changing, 3=Stopped, 4=Playing,
                    # 5=Paused. Only Closed means "no real session" -- a strict
                    # Playing/Paused-only filter (fine for the single "current"
                    # session read elsewhere in this file) dropped browser-hosted
                    # DRM/streaming sessions here that report one of the other
                    # states (e.g. Netflix in Firefox), even though the OS's own
                    # media keys could still control them.
                    playback_info = session.get_playback_info()
                    status_code = int(playback_info.playback_status)
                    if status_code == 0:
                        continue
                    status = "Playing" if status_code == 4 else "Paused"
                    info = await session.try_get_media_properties_async()
                except Exception as e:
                    # One session in a bad/transient state (e.g. mid-transition,
                    # metadata not ready yet) must not abort the whole
                    # enumeration and silently drop every other app's match.
                    logging.warning(f"Skipping SMTC session '{aumid}': {e}")
                    continue
                found[aumid] = {"title": info.title or "", "artist": info.artist or "", "status": status, "player": aumid}
            return found

        try:
            return asyncio.run(_enumerate())
        except Exception as e:
            logging.error(f"Failed to enumerate SMTC sessions: {e}")
            return {}

    def _toggle_app_playback_windows(self, target: str) -> Tuple[bool, str]:
        try:
            from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SMTC
        except ImportError:
            return False, "Windows media control requires the 'winsdk' library (pip install winsdk)."

        async def _toggle():
            manager = await SMTC.request_async()
            for session in manager.get_sessions():
                aumid = session.source_app_user_model_id or ""
                if aumid.lower() == target.lower():
                    await session.try_toggle_play_pause_async()
                    return True
            return False

        try:
            found = asyncio.run(_toggle())
        except Exception as e:
            return False, f"Failed to toggle playback: {e}"
        if not found:
            return False, f"No media session found for '{target}'."
        return True, f"Toggled playback for {target}."

    def _list_linux_media_sessions(self) -> Dict[str, Dict[str, Any]]:
        """All current MPRIS players (playerctl -l), keyed by player name --
        not filtered to Spotify like _find_linux_spotify_player, since
        every app's media session is a candidate match here. Sync (plain
        subprocess.run), matching this file's other Linux audio helpers --
        the async playerctl helpers exist for the polling loops instead."""
        if not shutil.which("playerctl"):
            return {}
        try:
            names_output = subprocess.run(["playerctl", "-l"], capture_output=True, text=True, timeout=3).stdout
        except Exception as e:
            logging.error(f"Failed to list playerctl players: {e}")
            return {}

        found = {}
        for name in names_output.splitlines():
            name = name.strip()
            if not name:
                continue
            try:
                status = subprocess.run(["playerctl", "--player", name, "status"], capture_output=True, text=True, timeout=3).stdout.strip()
                if status not in ("Playing", "Paused"):
                    continue
                title = subprocess.run(["playerctl", "--player", name, "metadata", "title"], capture_output=True, text=True, timeout=3).stdout.strip()
                artist = subprocess.run(["playerctl", "--player", name, "metadata", "artist"], capture_output=True, text=True, timeout=3).stdout.strip()
            except Exception:
                continue
            found[name] = {"title": title, "artist": artist, "status": status, "player": name}
        return found

    def _toggle_app_playback_linux(self, target: str) -> Tuple[bool, str]:
        if not shutil.which("playerctl"):
            return False, "playerctl not found."
        subprocess.Popen(["playerctl", "--player", target, "play-pause"])
        return True, f"Toggled playback for {target}."

    def _list_app_volumes_windows(self) -> List[Dict[str, Any]]:
        try:
            from pycaw.pycaw import AudioUtilities
        except ImportError:
            return []
        apps: Dict[str, Dict[str, Any]] = {}
        for session in AudioUtilities.GetAllSessions():
            process = session.Process
            volume = session.SimpleAudioVolume
            if process is None or volume is None:
                continue  # system sounds session, not a real app
            name = process.name()
            if name not in apps:
                # id keeps the real process name (needed to re-match the
                # session later); name is display-only. pid is a fast-path
                # AUMID guess in list_app_volumes()'s media-session match --
                # not every app's own name has anything to do with its AUMID,
                # and for multi-process apps (e.g. Firefox) this specific pid
                # (whichever session pycaw enumerated first) isn't
                # necessarily the one Windows registered the AUMID on either,
                # hence the system-wide-by-name fallback alongside it.
                apps[name] = {
                    "id": name,
                    "name": re.sub(r"\.exe$", "", name, flags=re.IGNORECASE),
                    "volume": round(volume.GetMasterVolume() * 100),
                    "muted": bool(volume.GetMute()),
                    "pid": process.pid,
                }
        return list(apps.values())

    def _windows_sessions_for(self, name: str) -> List[Any]:
        from pycaw.pycaw import AudioUtilities
        return [s for s in AudioUtilities.GetAllSessions() if s.Process and s.Process.name() == name and s.SimpleAudioVolume]

    def _set_app_volume_windows(self, target: str, clean_level: int) -> Tuple[bool, str]:
        try:
            sessions = self._windows_sessions_for(target)
            if not sessions:
                return False, f"App '{target}' not found."
            for s in sessions:
                s.SimpleAudioVolume.SetMasterVolume(clean_level / 100.0, None)
            return True, f"{target} volume set to {clean_level}%."
        except ImportError:
            return False, "Windows app volume control requires the 'pycaw' library (pip install pycaw comtypes)."
        except Exception as e:
            return False, f"Failed to set app volume: {e}"

    def _toggle_app_mute_windows(self, target: str) -> Tuple[bool, str]:
        try:
            sessions = self._windows_sessions_for(target)
            if not sessions:
                return False, f"App '{target}' not found."
            new_mute = not bool(sessions[0].SimpleAudioVolume.GetMute())
            for s in sessions:
                s.SimpleAudioVolume.SetMute(new_mute, None)
            return True, f"{target} muted." if new_mute else f"{target} unmuted."
        except ImportError:
            return False, "Windows app volume control requires the 'pycaw' library (pip install pycaw comtypes)."
        except Exception as e:
            return False, f"Failed to toggle app mute: {e}"

    def _list_app_volumes_linux(self) -> List[Dict[str, Any]]:
        if not shutil.which("pactl"):
            return []
        try:
            output = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=3).stdout
        except Exception as e:
            logging.error(f"Failed to list pactl sink-inputs: {e}")
            return []
        return self._parse_pactl_sink_inputs(output)

    @staticmethod
    def _parse_pactl_sink_inputs(output: str) -> List[Dict[str, Any]]:
        apps: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("Sink Input #"):
                if current and current.get("name"):
                    apps.append(current)
                current = {"id": line.split("#", 1)[1], "name": None, "volume": 0, "muted": False}
            elif current is None:
                continue
            elif line.startswith("Mute:"):
                current["muted"] = "yes" in line.lower()
            elif line.startswith("Volume:"):
                match = re.search(r"(\d+)%", line)
                if match:
                    current["volume"] = int(match.group(1))
            elif line.startswith("application.name ="):
                current["name"] = line.split("=", 1)[1].strip().strip('"')
        if current and current.get("name"):
            apps.append(current)
        return apps

    def _set_app_volume_linux(self, target: str, clean_level: int) -> Tuple[bool, str]:
        if not shutil.which("pactl"):
            return False, "pactl not found."
        subprocess.Popen(["pactl", "set-sink-input-volume", target, f"{clean_level}%"])
        return True, f"App volume set to {clean_level}%."

    def _toggle_app_mute_linux(self, target: str) -> Tuple[bool, str]:
        if not shutil.which("pactl"):
            return False, "pactl not found."
        current = next((a for a in self._list_app_volumes_linux() if a["id"] == target), None)
        if current is None:
            return False, f"Sink input '{target}' not found."
        new_mute = not current["muted"]
        subprocess.Popen(["pactl", "set-sink-input-mute", target, "1" if new_mute else "0"])
        return True, "Muted." if new_mute else "Unmuted."

async def _get_linux_media_state(player: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Reads now-playing state from playerctl (MPRIS). player=None targets
    whatever playerctl considers current; pass a specific player name (see
    _find_linux_spotify_player) to target Spotify specifically regardless of
    which player the OS considers current."""
    player_args = ["--player", player] if player else []

    async def _run(*args) -> str:
        proc = await asyncio.create_subprocess_exec("playerctl", *player_args, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()

    title = await _run("metadata", "title")
    artist = await _run("metadata", "artist")
    status = await _run("status")

    if status not in ("Playing", "Paused"):
        return None

    try:
        position = float(await _run("position"))
    except ValueError:
        position = 0.0

    # mpris:length is in microseconds
    try:
        duration = float(await _run("metadata", "mpris:length")) / 1_000_000.0
    except ValueError:
        duration = 0.0

    return {"title": title, "artist": artist, "status": status, "position": position, "duration": duration}


async def _find_linux_spotify_player() -> Optional[str]:
    """The official Spotify Linux client registers as an MPRIS player named
    'spotify' (or 'spotify.instanceNNN' for multiple instances) -- distinct
    from whatever playerctl considers the OS's "current" player."""
    proc = await asyncio.create_subprocess_exec("playerctl", "-l", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    for name in stdout.decode().splitlines():
        name = name.strip()
        if name.lower().startswith("spotify"):
            return name
    return None


async def _get_linux_spotify_state() -> Optional[Dict[str, Any]]:
    player = await _find_linux_spotify_player()
    if player is None:
        return None
    return await _get_linux_media_state(player)


async def _extract_smtc_state(session) -> Optional[Dict[str, Any]]:
    playback_info = session.get_playback_info()
    # GlobalSystemMediaTransportControlsSessionPlaybackStatus: 4=Playing, 5=Paused
    status = {4: "Playing", 5: "Paused"}.get(int(playback_info.playback_status))
    if status is None:
        return None

    info = await session.try_get_media_properties_async()
    timeline = session.get_timeline_properties()
    return {
        "title": info.title or "",
        "artist": info.artist or "",
        "status": status,
        "position": timeline.position.total_seconds(),
        "duration": timeline.end_time.total_seconds(),
    }


async def _get_windows_media_state() -> Optional[Dict[str, Any]]:
    """Reads now-playing state from SMTC -- the Windows equivalent of playerctl."""
    from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SMTC

    session_manager = await SMTC.request_async()
    session = session_manager.get_current_session()
    if session is None:
        return None
    return await _extract_smtc_state(session)


async def _find_windows_spotify_session():
    """SMTC's 'current' session is whichever app the OS considers current --
    this instead scans every registered session for Spotify's own (matched
    by its app user model ID), regardless of which one that is."""
    from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SMTC

    session_manager = await SMTC.request_async()
    for session in session_manager.get_sessions():
        aumid = session.source_app_user_model_id or ""
        if "spotify" in aumid.lower():
            return session
    return None


async def _get_windows_spotify_state() -> Optional[Dict[str, Any]]:
    session = await _find_windows_spotify_session()
    if session is None:
        return None
    return await _extract_smtc_state(session)


async def poll_media_status(manager: TerminalManager) -> None:
    """Polls the OS's own "current" media session (playerctl on Linux, SMTC
    on Windows) -- whichever app the OS considers current, not necessarily
    Spotify. Published separately from jarvis/sys/media_status (which is
    Spotify-only, populated by clSpotify.py's own status calls) so the
    dashboard's Spotify panel never ends up showing a browser tab's title."""
    if CURRENT_OS == "linux":
        if not shutil.which("playerctl"):
            return
        get_media_state = _get_linux_media_state
    elif CURRENT_OS == "windows":
        try:
            import winsdk.windows.media.control  # noqa: F401 -- import check only
        except ImportError:
            logging.warning("winsdk is not installed; Windows media status polling disabled. Run: pip install winsdk")
            return
        get_media_state = _get_windows_media_state
    else:
        return

    last_state = {}
    while True:
        try:
            async with aiomqtt.Client("localhost") as mqtt_client:
                while True:
                    try:
                        current_state = await get_media_state()

                        if current_state:
                            state_changed = False
                            if not last_state:
                                state_changed = True
                            elif current_state["title"] != last_state.get("title") or \
                                 current_state["artist"] != last_state.get("artist") or \
                                 current_state["status"] != last_state.get("status"):
                                state_changed = True

                            if state_changed:
                                await mqtt_client.publish("jarvis/sys/os_media_status", json.dumps(current_state))
                                last_state = current_state

                        try:
                            await asyncio.wait_for(manager.media_trigger.wait(), timeout=10.0)
                            manager.media_trigger.clear()
                        except asyncio.TimeoutError:
                            pass
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logging.error(f"Media polling error: {e}")
                        await asyncio.sleep(2)
        except aiomqtt.MqttError:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break


async def poll_spotify_local_status(manager: TerminalManager) -> None:
    """Polls specifically for a local Spotify client's own media session
    (Spotify-filtered SMTC on Windows, an MPRIS player named 'spotify' on
    Linux) and publishes it to jarvis/sys/spotify_local_status -- lets
    clSpotify.py's status() skip a real Spotify Web API call for its most
    frequent (UI-polling) case. Unlike poll_media_status, this always
    publishes every tick (not only on change) with a 'found' flag, so a
    subscriber can tell "no local Spotify session" from "haven't heard from
    this yet" by freshness alone rather than guessing from silence. Shares
    manager.media_trigger with poll_media_status -- both loops waking on
    the same media-changing-action signal is intentional, and Event.set()
    wakes every waiter, not just one."""
    if CURRENT_OS == "linux":
        if not shutil.which("playerctl"):
            return
        get_spotify_state = _get_linux_spotify_state
    elif CURRENT_OS == "windows":
        try:
            import winsdk.windows.media.control  # noqa: F401 -- import check only
        except ImportError:
            return
        get_spotify_state = _get_windows_spotify_state
    else:
        return

    while True:
        try:
            async with aiomqtt.Client("localhost") as mqtt_client:
                while True:
                    try:
                        state = await get_spotify_state()
                        payload = dict(state, found=True) if state else {"found": False}
                        await mqtt_client.publish("jarvis/sys/spotify_local_status", json.dumps(payload))

                        try:
                            await asyncio.wait_for(manager.media_trigger.wait(), timeout=10.0)
                            manager.media_trigger.clear()
                        except asyncio.TimeoutError:
                            pass
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logging.error(f"Spotify local status polling error: {e}")
                        await asyncio.sleep(2)
        except aiomqtt.MqttError:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break

# --- MQTT SERVICE LISTENER ---
async def mqtt_service_listener(manager: TerminalManager) -> None:
    logging.info(f"Terminal Service initialized for {CURRENT_OS.upper()}. Listening on 'pc/system/control'...")
    while True:
        try:
            async with aiomqtt.Client("localhost") as mqtt_client:
                await mqtt_client.subscribe("pc/system/control")
                await mqtt_client.publish("jarvis/sys/module_ready", json.dumps({"module": "terminal"}))
                async for message in mqtt_client.messages:
                    try:
                        payload = json.loads(message.payload.decode('utf-8'))
                        logging.info(f"Command Received: {payload}")
                        
                        action = payload.get("action")

                        if action == "media_refresh":
                            manager.media_trigger.set()
                            continue

                        if action == "list_app_volumes":
                            apps = await asyncio.to_thread(manager.list_app_volumes)
                            await mqtt_client.publish("jarvis/sys/app_volumes", json.dumps({"apps": apps}))
                            continue

                        success, msg = await asyncio.to_thread(
                            manager.execute_command,
                            action,
                            payload.get("target"),
                            payload.get("level")
                        )

                        action = payload.get("action")
                        if action in ["media_play", "media_pause", "media_next", "media_prev"]:
                            manager.media_trigger.set()
                            # Short delay to allow playerctl to apply the change before polling
                            await asyncio.sleep(0.5)
                            manager.media_trigger.set()

                        if action in ["set_app_volume", "toggle_app_mute", "toggle_app_playback"] and success:
                            # The caller doesn't know the resulting state (especially
                            # for a mute toggle) without a fresh read -- push one so
                            # the UI re-syncs instead of guessing.
                            apps = await asyncio.to_thread(manager.list_app_volumes)
                            await mqtt_client.publish("jarvis/sys/app_volumes", json.dumps({"apps": apps}))

                        # clDaemon.py's jarvis/feedback handler defaults 'silent' to
                        # False and speaks the message + reopens the mic for a
                        # follow-up otherwise -- fine for a voice-triggered command,
                        # but a UI-driven action (e.g. dragging an app-volume slider)
                        # needs to opt out, same as clSpotify.py's actions already do.
                        await mqtt_client.publish("jarvis/feedback", json.dumps({
                            "device": "terminal",
                            "status": "success" if success else "error",
                            "message": msg,
                            "silent": payload.get("silent", False)
                        }))
                    except json.JSONDecodeError:
                        logging.error("Received malformed JSON data.")
        except aiomqtt.MqttError as e:
            logging.error(f"MQTT Connection Error: {e}")
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logging.info("Terminal service shutting down.")
            break

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="Microservice Control for OS Terminal")
    parser.add_argument("--open", type=str, help="Open an app or path by dictionary nickname")
    parser.add_argument("--search", type=str, help="Search Google for a query")
    parser.add_argument("--site", type=str, help="Open a direct URL")
    parser.add_argument("--shutdown", action="store_true", help="Shutdown the computer")
    args = parser.parse_args()

    manager = TerminalManager()

    if args.open:
        success, msg = manager.execute_command("open", target=args.open)
        logging.info(msg)
    elif args.search:
        success, msg = manager.execute_command("search", target=args.search)
        logging.info(msg)
    elif args.site:
        success, msg = manager.execute_command("open_site", target=args.site)
        logging.info(msg)
    elif args.shutdown:
        success, msg = manager.execute_command("shutdown")
        logging.info(msg)
    else:
        async def main_loop():
            task1 = asyncio.create_task(mqtt_service_listener(manager))
            task2 = asyncio.create_task(poll_media_status(manager))
            task3 = asyncio.create_task(poll_spotify_local_status(manager))
            await asyncio.gather(task1, task2, task3)

        try:
            asyncio.run(main_loop())
        except KeyboardInterrupt:
            logging.info("Exiting Service Mode.")

if __name__ == "__main__":
    main()