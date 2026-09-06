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
            return False, f"Action '{action}' is not recognized."
        except Exception as e:
            return False, f"OS Execution Error: {str(e)}"

async def _get_linux_media_state() -> Optional[Dict[str, Any]]:
    """Reads now-playing state from playerctl (MPRIS)."""
    # Get Title
    title_proc = await asyncio.create_subprocess_exec("playerctl", "metadata", "title", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    title_stdout, _ = await title_proc.communicate()
    title = title_stdout.decode().strip()

    # Get Artist
    artist_proc = await asyncio.create_subprocess_exec("playerctl", "metadata", "artist", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    artist_stdout, _ = await artist_proc.communicate()
    artist = artist_stdout.decode().strip()

    # Get Status
    status_proc = await asyncio.create_subprocess_exec("playerctl", "status", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    status_stdout, _ = await status_proc.communicate()
    status = status_stdout.decode().strip()

    if status not in ("Playing", "Paused"):
        return None

    # Get Position
    pos_proc = await asyncio.create_subprocess_exec("playerctl", "position", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    pos_stdout, _ = await pos_proc.communicate()
    try:
        position = float(pos_stdout.decode().strip())
    except ValueError:
        position = 0.0

    # Get Duration (mpris:length is in microseconds)
    len_proc = await asyncio.create_subprocess_exec("playerctl", "metadata", "mpris:length", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    len_stdout, _ = await len_proc.communicate()
    try:
        duration = float(len_stdout.decode().strip()) / 1_000_000.0
    except ValueError:
        duration = 0.0

    return {"title": title, "artist": artist, "status": status, "position": position, "duration": duration}


async def _get_windows_media_state() -> Optional[Dict[str, Any]]:
    """Reads now-playing state from SMTC -- the Windows equivalent of playerctl."""
    from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SMTC

    session_manager = await SMTC.request_async()
    session = session_manager.get_current_session()
    if session is None:
        return None

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


async def poll_media_status(manager: TerminalManager) -> None:
    """Polls local OS media state and publishes via MQTT -- playerctl on Linux, SMTC on Windows."""
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
                                await mqtt_client.publish("jarvis/sys/media_status", json.dumps(current_state))
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
                        
                        await mqtt_client.publish("jarvis/feedback", json.dumps({
                            "device": "terminal",
                            "status": "success" if success else "error",
                            "message": msg
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
            await asyncio.gather(task1, task2)

        try:
            asyncio.run(main_loop())
        except KeyboardInterrupt:
            logging.info("Exiting Service Mode.")

if __name__ == "__main__":
    main()