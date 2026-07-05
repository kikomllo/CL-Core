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

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [TERMINAL] %(message)s", datefmt="%H:%M:%S")

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

CURRENT_OS = platform.system().lower()

# --- MEMORY & STATE ---
SHORTCUTS = {"apps": {}, "folders": {}}
LAST_OPENED_DIR = os.path.expanduser("~")
TERMINAL_IS_OPEN = False

def load_shortcuts():
    """Reads the shortcuts.json file and caches it in memory."""
    global SHORTCUTS
    base_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(base_dir, "shortcuts.json")
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            SHORTCUTS = json.load(f)
        logging.info(f"Loaded {len(SHORTCUTS.get('apps', {}))} apps and {len(SHORTCUTS.get('folders', {}))} folders from shortcuts.json.")
    except FileNotFoundError:
        logging.warning("shortcuts.json not found. Using empty dictionaries.")
    except json.JSONDecodeError as e:
        logging.critical(f"Syntax error in shortcuts.json: {e}")

# --- CORE EXECUTION ENGINE ---
def execute_command(action, target=None, level=None):
    """Routes the command to the correct native OS API."""
    global TERMINAL_IS_OPEN, LAST_OPENED_DIR
    
    try:
        # 1. APPLICATION & FOLDER LAUNCHER
        if action == "open" and target:
            target_clean = target.lower().strip()
            
            sys_kw = SHORTCUTS.get("system_keywords", {})
            terminal_aliases = sys_kw.get("terminal_aliases", ["terminal", "console", "shell", "cmd"])
            go_back_keywords = sys_kw.get("go_back", ["back", "up", "..", "previous", "return"])

            # --- Check A: Is the user just asking to open a Terminal? ---
            if target_clean in terminal_aliases:
                pid_file = os.path.join(os.path.expanduser("~"), ".jarvis_nav_pid")
                if CURRENT_OS == "linux":
                    subprocess.Popen(["gnome-terminal", "--working-directory", LAST_OPENED_DIR, "--", "bash", "-c", f"echo $$ >> {pid_file}; exec bash"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif CURRENT_OS == "windows":
                    proc = subprocess.Popen(["cmd", "/k", f"title JarvisNavigation && cd /d {LAST_OPENED_DIR}"], creationflags=subprocess.CREATE_NEW_CONSOLE)
                    with open(pid_file, 'a') as f: 
                        f.write(f"{proc.pid}\n")
                TERMINAL_IS_OPEN = True
                return True, "Launched Terminal."

            # --- Check B: Is it an App in shortcuts.json? ---
            if target_clean in SHORTCUTS.get("apps", {}):
                os_commands = SHORTCUTS["apps"][target_clean]
                app_cmd = os_commands.get(CURRENT_OS)
                
                if app_cmd:
                    subprocess.Popen(app_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True, f"Launched application: {target_clean}"
                return False, f"No '{CURRENT_OS}' executable defined for app '{target_clean}'."
            
            # --- Check C: Path Resolution (Shortcuts, Back, or Smart Voice path) ---
            resolved_path = None
            
            if any(target_clean.startswith(kw) for kw in go_back_keywords):
                if LAST_OPENED_DIR and os.path.exists(LAST_OPENED_DIR):
                    resolved_path = os.path.abspath(os.path.join(LAST_OPENED_DIR, os.pardir))
            
            elif target_clean in SHORTCUTS.get("folders", {}):
                raw_path = SHORTCUTS["folders"][target_clean]
                guess = os.path.expanduser(raw_path)
                if os.path.isdir(guess):
                    resolved_path = guess
            
            else:
                home_dir = os.path.expanduser("~")
                current_base = LAST_OPENED_DIR if LAST_OPENED_DIR else home_dir
                
                voiced_path = target_clean.replace(" ", "/")
                voiced_path_title = target.title().replace(" ", "/")
                
                possible_paths = [
                    os.path.expanduser(target_clean),
                    os.path.join(home_dir, voiced_path_title),
                    os.path.join(home_dir, voiced_path),
                    f"/{voiced_path}",
                    os.path.join(current_base, voiced_path),
                    os.path.join(current_base, voiced_path_title)
                ]
                
                for path_guess in possible_paths:
                    if os.path.isdir(path_guess):
                        resolved_path = path_guess
                        break

            # --- THE NAVIGATION ENGINE (SURGICAL KILL & RESPAWN) ---
            if resolved_path:
                LAST_OPENED_DIR = resolved_path
                pid_file = os.path.join(os.path.expanduser("~"), ".jarvis_nav_pid")
                
                # 1. Kill ALL tracked windows synchronously before spawning the new one
                if TERMINAL_IS_OPEN and os.path.exists(pid_file):
                    with open(pid_file, 'r') as f:
                        pids = f.read().splitlines()
                    
                    for p in pids:
                        if not p.strip(): continue
                        try:
                            target_pid = int(p.strip())
                            if CURRENT_OS == "linux":
                                os.kill(target_pid, 9) 
                            elif CURRENT_OS == "windows":
                                # FIX: Synchronous shell execution prevents OS race conditions
                                subprocess.run(f"taskkill /F /PID {target_pid} /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception: pass
                    
                    open(pid_file, 'w').close()
                    time.sleep(0.5) 

                # 2. Spawn the new terminal and record its PID
                if CURRENT_OS == "linux":
                    spawn_cmd = f"echo $$ >> {pid_file}; ls; exec bash"
                    subprocess.Popen(
                        ["gnome-terminal", "--title=JarvisNavWindow", "--working-directory", resolved_path, 
                         "--", "bash", "-c", spawn_cmd], 
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                elif CURRENT_OS == "windows":
                    proc = subprocess.Popen(
                        ["cmd", "/k", f"title JarvisNavigation && cd /d {resolved_path} && dir"], 
                        creationflags=subprocess.CREATE_NEW_CONSOLE
                    )
                    with open(pid_file, 'a') as f:
                        f.write(f"{proc.pid}\n")
                
                TERMINAL_IS_OPEN = True
                return True, f"Spawned terminal at: {resolved_path}"

            return False, f"Target '{target}' not found in shortcuts or system directories."

        # 2. APPLICATION CLOSER
        elif action == "close" and target:
            target_clean = target.lower().strip()
            
            sys_kw = SHORTCUTS.get("system_keywords", {})
            terminal_aliases = sys_kw.get("terminal_aliases", ["terminal", "console", "shell", "cmd"])
            
            # --- Check A: Close ALL tracked child terminals via synchronous PID strike ---
            if target_clean in terminal_aliases:
                killed_any = False
                pid_file = os.path.join(os.path.expanduser("~"), ".jarvis_nav_pid")
                if os.path.exists(pid_file):
                    with open(pid_file, 'r') as f:
                        pids = f.read().splitlines()
                        
                    for p in pids:
                        if not p.strip(): continue
                        try:
                            target_pid = int(p.strip())
                            if CURRENT_OS == "linux":
                                os.kill(target_pid, 15)
                                killed_any = True
                            elif CURRENT_OS == "windows":
                                # FIX: Synchronous shell execution prevents OS race conditions
                                subprocess.run(f"taskkill /F /PID {target_pid} /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                killed_any = True
                        except Exception: pass
                    
                    open(pid_file, 'w').close()
                
                TERMINAL_IS_OPEN = False
                
                # If we successfully killed child terminals, stop here. 
                # If the file was empty, fall through to Check B to try killing the main terminal app.
                if killed_any:
                    return True, "Closed tracked terminal instances."
            
            # --- Check B: Is it a standard App in shortcuts.json? ---
            if target_clean in SHORTCUTS.get("apps", {}):
                app_data = SHORTCUTS["apps"][target_clean]
                
                kill_target = app_data.get(f"{CURRENT_OS}_kill")
                
                if not kill_target:
                    launch_cmd = app_data.get(CURRENT_OS, "")
                    if CURRENT_OS == "windows":
                        exe_matches = re.findall(r'[\w.-]+\.exe', launch_cmd, re.IGNORECASE)
                        kill_target = exe_matches[-1] if exe_matches else launch_cmd.split()[0]
                    elif CURRENT_OS == "linux":
                        kill_target = launch_cmd.split()[0].split('/')[-1]

                if not kill_target:
                    return False, f"Could not determine a kill target for '{target_clean}'."

                if CURRENT_OS == "linux":
                    subprocess.Popen(["pkill", "-f", kill_target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif CURRENT_OS == "windows":
                    # Warning: If kill_target is "WindowsTerminal.exe" and Jarvis is running inside it, Jarvis will be killed!
                    subprocess.run(f"taskkill /F /IM {kill_target} /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    
                return True, f"Sent termination signal to process: {kill_target}"
                
            return False, f"Target '{target}' not found in shortcuts.json apps."

        # 3. POWER MANAGEMENT
        elif action == "shutdown":
            if CURRENT_OS == "linux":
                subprocess.Popen(["shutdown", "now"])
            elif CURRENT_OS == "windows":
                subprocess.Popen(["shutdown", "/s", "/t", "0"])
            return True, "Initiating system shutdown."
            
        elif action == "restart":
            if CURRENT_OS == "linux":
                subprocess.Popen(["reboot"])
            elif CURRENT_OS == "windows":
                subprocess.Popen(["shutdown", "/r", "/t", "0"])
            return True, "Initiating system reboot."

        # 4. VOLUME CONTROL
        elif action == "volume" and level is not None:
            clean_level = max(0, min(100, level))
            if CURRENT_OS == "linux":
                subprocess.Popen(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{clean_level}%"])
                return True, f"Linux master volume set to {clean_level}%."
            elif CURRENT_OS == "windows":
                return False, "Windows volume control requires the 'pycaw' library."

        return False, f"Action '{action}' is not recognized by the Terminal module."

    except Exception as e:
        return False, f"OS Execution Error: {str(e)}"
    
# --- MQTT SERVICE LISTENER ---
async def mqtt_service_listener():
    logging.info(f"Terminal Service initialized for {CURRENT_OS.upper()}. Listening on topic 'pc/system/control'...")
    try:
        async with aiomqtt.Client("localhost") as mqtt_client:
            await mqtt_client.subscribe("pc/system/control")
            
            async for message in mqtt_client.messages:
                try:
                    payload = json.loads(message.payload.decode('utf-8'))
                    logging.info(f"Command Received: {payload}")
                    
                    action = payload.get("action")
                    target = payload.get("target")
                    level = payload.get("level")
                    
                    success, msg = await asyncio.to_thread(execute_command, action, target, level)
                    
                    feedback = {
                        "device": "terminal",
                        "status": "success" if success else "error",
                        "message": msg
                    }
                    await mqtt_client.publish("jarvis/feedback", json.dumps(feedback))
                    
                except json.JSONDecodeError:
                    logging.error("Received malformed JSON data.")
    except aiomqtt.MqttError as e:
        logging.error(f"MQTT Connection Error: {e}")
    except asyncio.CancelledError:
        logging.info("Terminal service shutting down.")

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="Microservice Control for OS Terminal")
    parser.add_argument("--open", type=str, help="Open an app or path by dictionary nickname")
    parser.add_argument("--shutdown", action="store_true", help="Shutdown the computer")
    args = parser.parse_args()

    load_shortcuts()

    if args.open:
        success, msg = execute_command("open", target=args.open)
        logging.info(msg)
    elif args.shutdown:
        success, msg = execute_command("shutdown")
        logging.info(msg)
    else:
        try:
            asyncio.run(mqtt_service_listener())
        except KeyboardInterrupt:
            logging.info("Exiting Service Mode.")

if __name__ == "__main__":
    main()