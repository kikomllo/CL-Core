import subprocess
import sys
import time
import json
import os
import signal
import platform
import paho.mqtt.client as mqtt_client

# --- OS SETTINGS & DEBUG FLAG ---
def load_settings():
    config_path = os.path.join("config", "core.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            mode = cfg.get("ecosystem", {}).get("mode") or cfg.get("settings", {}).get("ecosystem_state", "STANDARD")
            return cfg.get("settings", {}), mode.upper()
    except Exception:
        return {}, "STANDARD"

SETTINGS, ECOSYSTEM_MODE = load_settings()
DEBUG_MQTT = (ECOSYSTEM_MODE == "DEBUG")
SYSTEM_HAS_ANNOUNCED = False
client = None

import re
import threading
import datetime

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

class TeeLogger:
    def __init__(self, filename):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        self.terminal = sys.__stdout__
        self.log = open(filename, "w", encoding="utf-8")
        self.lock = threading.Lock()
        self.buffers = {}
        
    def write(self, message):
        if not message:
            return

        tid = threading.current_thread().ident
        
        with self.lock:
            if tid not in self.buffers:
                self.buffers[tid] = ""
                
            self.buffers[tid] += message
            
            if '\n' in self.buffers[tid]:
                lines = self.buffers[tid].split('\n')
                self.buffers[tid] = lines[-1]
                complete_lines = lines[:-1]
                
                has_ts_pattern = re.compile(r'^(\x1b\[[0-9;]*m)*\[\d{2}:\d{2}:\d{2}\]')
                timestamp = datetime.datetime.now().strftime("[%H:%M:%S] ")
                
                out_lines = []
                for line in complete_lines:
                    if line.strip() != '' and not has_ts_pattern.match(line):
                        out_lines.append(timestamp + line)
                    else:
                        out_lines.append(line)
                        
                formatted_message = '\n'.join(out_lines) + '\n'

                if ECOSYSTEM_MODE != "BACKGROUND":
                    self.terminal.write(formatted_message)
                
                clean_msg = ANSI_ESCAPE.sub('', formatted_message).replace('\r', '')
                if clean_msg:
                    self.log.write(clean_msg)
                    self.log.flush()
            
    def flush(self):
        with self.lock:
            if ECOSYSTEM_MODE != "BACKGROUND":
                self.terminal.flush()
            self.log.flush()

# Redirect all standard output to the logger
sys.stdout = TeeLogger("logs/latest_run.log")
sys.stderr = sys.stdout

def stream_reader(container_name, pipe):
    """Continuously reads from a subprocess pipe in the background."""
    for line in iter(pipe.readline, b''):
        sys.stdout.write(line.decode('utf-8', errors='replace'))
        sys.stdout.flush()

# --- NATIVE HOST SERVICES (not in Docker) ---
NATIVE_SERVICES = [
    ("UI",        "src/clUI.py"),
    ("Keybinds",  "src/clKeybinds.py"),
    ("Utilities", "src/clUtilities.py"),
    ("Updater",   "src/clUpdater.py"),
    ("Tray Icon", "src/clTrayIcon.py"),
]

def load_modules_config():
    global NATIVE_SERVICES
    config_path = os.path.join("config", "modules.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {"native": {"UI": True, "Keybinds": True, "Utilities": True}, "docker": {}}
        
    # Force Docker services to run natively (Docker scrapped)
    docker_to_native = {
        "jarvis-whisper": ("Whisper", "src/clWhisper.py"),
        "jarvis-brain": ("Brain", "src/clDaemon.py"),
        "jarvis-music": ("Music", "src/clSpotify.py"),
        "jarvis-tts": ("TTS", "src/clTTS.py"),
        "jarvis-light": ("Light", "src/clControl.py"),
        "jarvis-mic": ("Mic", "src/clMic.py"),
        "jarvis-terminal": ("Terminal", "src/clTerminal.py"),
    }
    for d_key, (n_desc, n_script) in docker_to_native.items():
        if cfg.get("docker", {}).get(d_key, False):
            cfg["native"][n_desc] = True
            if (n_desc, n_script) not in NATIVE_SERVICES:
                NATIVE_SERVICES.append((n_desc, n_script))
            cfg["docker"][d_key] = False
                
    return cfg

MODULES_CONFIG = load_modules_config()

PROCESSES = {}
READY_MODULES = set()
EXPECTED_MODULES = set()

def update_expected_modules():
    global EXPECTED_MODULES, READY_MODULES
    READY_MODULES.clear()
    EXPECTED_MODULES.clear()
    native_cfg = MODULES_CONFIG.get("native", {})
    for desc, filename in NATIVE_SERVICES:
        if native_cfg.get(desc, True):
            EXPECTED_MODULES.add(desc.lower())

def _wait_for_module_ready(target_mod: str, timeout_s: float = 30.0) -> None:
    """Polls READY_MODULES on its own thread. Must never be called from
    on_message itself -- that callback runs on the single MQTT network
    thread (via loop_start()), the same thread that delivers the
    module_ready messages this is waiting for, so blocking there would
    deadlock: the wait could never observe the message that satisfies it."""
    print(f"[SUPERVISOR] Waiting for {target_mod} to initialize before continuing...")
    deadline = time.time() + timeout_s
    while target_mod not in READY_MODULES and time.time() < deadline:
        time.sleep(0.1)
    if target_mod in READY_MODULES:
        print(f"[SUPERVISOR] {target_mod} is ready.")
    else:
        print(f"[SUPERVISOR] Timed out waiting for {target_mod} to initialize.")

def _perform_full_reboot() -> None:
    """The actual restart_all_modules sequence, run on its own thread (never
    from on_message directly -- see _wait_for_module_ready)."""
    global MODULES_CONFIG, SYSTEM_HAS_ANNOUNCED

    print("\n" + "="*60)
    print("[SUPERVISOR] INITIATING FULL ECOSYSTEM REBOOT")
    print("="*60)
    for desc, filename in NATIVE_SERVICES:
        stop_native(filename)
    time.sleep(1.0)

    MODULES_CONFIG = load_modules_config()
    SYSTEM_HAS_ANNOUNCED = False

    print("[SUPERVISOR] Starting native host services...")
    update_expected_modules()
    native_cfg = MODULES_CONFIG.get("native", {})
    for desc, filename in NATIVE_SERVICES:
        if native_cfg.get(desc, True):
            start_native(desc, filename)

            target_mod = None
            if "clWhisper" in filename:
                target_mod = "whisper"
            elif "clTTS" in filename:
                target_mod = "tts"

            if target_mod:
                _wait_for_module_ready(target_mod)

    print("[SUPERVISOR] ECOSYSTEM REBOOT COMPLETE\n" + "="*60)

def start_native(desc, filename):
    print(f"[SUPERVISOR] Launching {filename}...")
    env = os.environ.copy()
    env["JARVIS_ECOSYSTEM"] = "1"
    
    proc = subprocess.Popen(
        [sys.executable, filename],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env
    )
    PROCESSES[filename] = proc
    t = threading.Thread(target=stream_reader, args=(filename, proc.stdout), daemon=True)
    t.start()
    return proc

def stop_native(filename):
    global client
    if filename in PROCESSES:
        proc = PROCESSES.pop(filename)
        if proc.poll() is None:
            if "clUI.py" in filename:
                try:
                    if client is not None:
                        client.publish("jarvis/sys/ui_control", json.dumps({"action": "save_state"}))
                    time.sleep(1.0)
                except Exception as e:
                    print(f"[SUPERVISOR] Failed to send save_state to UI: {e}")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def on_message(client, userdata, msg):
    global SYSTEM_HAS_ANNOUNCED, ECOSYSTEM_MODE, DEBUG_MQTT
    topic = msg.topic
    try:
        payload_str = msg.payload.decode('utf-8')
    except UnicodeDecodeError:
        payload_str = "<binary_payload>"

    if topic == "jarvis/sys/volume":
        try:
            vol = json.loads(payload_str)
            rms = vol.get("rms", 0)
            bar = vol.get("bar", "-" * 40)
            status = vol.get("status", "STANDARD")
            b_noise = vol.get("b_noise")
            if b_noise is not None:
                a_thresh = vol.get("a_thresh", 0)
                s_thresh = vol.get("s_thresh", 0)
                if ECOSYSTEM_MODE != "BACKGROUND":
                    sys.__stdout__.write(f"\r\033[K[{status}] Vol: {rms:5d} ||{bar}|| ACT: {a_thresh} SIL: {s_thresh} AVG: {b_noise}")
                    sys.__stdout__.flush()
            else:
                if ECOSYSTEM_MODE != "BACKGROUND":
                    sys.__stdout__.write(f"\r\033[K[{status}] Vol: {rms:5d} ||{bar}||")
                    sys.__stdout__.flush()
        except Exception:
            pass
        return

    if topic == "jarvis/sys/state_change":
        try:
            payload = json.loads(payload_str) if payload_str else {}
            new_mode = str(payload.get("action", "")).upper()
            if new_mode in ["DEBUG", "NORMAL", "STANDARD", "BACKGROUND"]:
                ECOSYSTEM_MODE = new_mode
                DEBUG_MQTT = (ECOSYSTEM_MODE == "DEBUG")
                if DEBUG_MQTT:
                    client.subscribe("#")
                    print("\n\033[36m[SUPERVISOR] Ecosystem shifted to DEBUG mode. Global MQTT Packet Sniffer ACTIVE.\033[0m")
                else:
                    client.unsubscribe("#")
                    client.subscribe("jarvis/sys/manager")
                    client.subscribe("jarvis/sys/ui_control")
                    client.subscribe("jarvis/sys/volume")
                    client.subscribe("jarvis/sys/whisper_state")
                    client.subscribe("jarvis/sys/state_change")
                    print(f"\n\033[32m[SUPERVISOR] Ecosystem shifted to {ECOSYSTEM_MODE} mode.\033[0m")
        except Exception:
            pass

    if DEBUG_MQTT and topic not in ["jarvis/sys/volume", "jarvis/sys/audio_process", "jarvis/sensor/mic_vol"]:
        print(f"\r\033[K\033[36m[DEBUG-MQTT] [CH: {topic}] {payload_str}\033[0m")

    if topic == "jarvis/sys/manager":
        try:
            payload = json.loads(payload_str)
            action = payload.get("action")

            if action == "restart_module":
                raw_target = str(payload.get("target", "")).lower().strip()
                clean_target = raw_target.replace(" module", "").replace(" service", "").replace(" container", "").strip()
                if clean_target in ["system", "ecosystem", "all"]:
                    action = "restart_all_modules"

            if action in ["shutdown", "shutdown_ecosystem", "stop_all_modules"]:
                print("\n" + "="*60)
                print("[SUPERVISOR] INITIATING CLEAN ECOSYSTEM SHUTDOWN")
                print("="*60)
                for desc, filename in NATIVE_SERVICES:
                    stop_native(filename)
                print("[SUPERVISOR] Shutdown complete. Goodbye.")
                os._exit(0)

            elif action == "restart_all_modules":
                # Runs on its own thread -- this whole branch executes inside
                # on_message, on the single MQTT network thread (loop_start()),
                # which is also the thread that delivers the module_ready
                # messages _perform_full_reboot waits on. Running it inline
                # here deadlocked: the wait could never see the very message
                # that would satisfy it, since this callback was busy blocking
                # on it.
                threading.Thread(target=_perform_full_reboot, daemon=True).start()

            elif action == "restart_module":
                raw_target = str(payload.get("target", "")).lower().strip()
                print(f"[SUPERVISOR] Requested restart for module target: '{raw_target}'")
                
                clean_target = raw_target.replace(" module", "").replace(" service", "").replace(" container", "").strip()
                restarted = False
                # Check native host services first
                for desc, filename in NATIVE_SERVICES:
                    desc_lower = desc.lower()
                    file_lower = filename.lower()
                    if (clean_target in desc_lower or desc_lower in clean_target or 
                        clean_target in file_lower or 
                        ("ui" in clean_target and "ui" in file_lower) or 
                        ("update" in clean_target and desc_lower == "updater")):
                        print(f"[SUPERVISOR] Restarting native host service: {desc} ({filename})...")
                        stop_native(filename)
                        time.sleep(0.5)
                        start_native(desc, filename)
                        restarted = True
                        break
                
                if not restarted:
                    print(f"[SUPERVISOR] Unable to match target '{raw_target}' to any active service.")

        except json.JSONDecodeError:
            print("[SUPERVISOR] Received malformed JSON command.")
        except Exception as e:
            print(f"[SUPERVISOR] Execution Error: {e}")

    elif topic == "jarvis/sys/ui_control":
        try:
            payload = json.loads(payload_str)
            action = payload.get("action")
            if action in ("set_fullscreen", "set_overlay"):
                # The UI process handles this internally via MQTT; nothing to do here
                pass
        except Exception:
            pass
    
    elif topic == "jarvis/sys/module_ready":
        if SYSTEM_HAS_ANNOUNCED:
            return
            
        try:
            payload = json.loads(payload_str)
            mod_name = payload.get("module", "").lower()
            if mod_name:
                READY_MODULES.add(mod_name)
                print(f"[SUPERVISOR] Module '{mod_name}' is ready. ({len(READY_MODULES)}/{len(EXPECTED_MODULES)})")
                
                if len(READY_MODULES) >= len(EXPECTED_MODULES):
                    SYSTEM_HAS_ANNOUNCED = True
                    print("\n" + "="*50)
                    print("[SUPERVISOR] ALL EXPECTED MODULES ONLINE.")
                    print("[SUPERVISOR] ALL SYSTEMS GO!")
                    print("="*50 + "\n")
                    
                    try:
                        client.publish("jarvis/sys/ecosystem_online", "1")
                        client.publish("jarvis/sys/speak", json.dumps({
                            "text": "System online!", 
                            "skip_ducking": False, 
                            "request_reply": False
                        }))
                    except Exception: pass
        except Exception:
            pass

def main():
    global client
    print("="*50)
    print(f"BOOTING JARVIS HOST SUPERVISOR")
    print(f"Ecosystem State: {ECOSYSTEM_MODE}")
    print("="*50 + "\n")

    # 1. Start Docker ecosystem (This boots the Mosquitto Broker)
    # (Removed - Mosquitto is now run natively)
    import subprocess
    try:
        subprocess.run(["mosquitto", "-d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[SUPERVISOR] Checked Mosquitto Broker status.")
    except Exception:
        pass

    # 2. Connect global MQTT supervisor
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    try:
        client.connect("localhost", 1883, 60)
        client.subscribe("jarvis/sys/manager")
        client.subscribe("jarvis/sys/ui_control")
        client.subscribe("jarvis/sys/volume")
        client.subscribe("jarvis/sys/module_ready")
        client.subscribe("jarvis/sys/state_change")
        if DEBUG_MQTT:
            client.subscribe("#")
            print("\033[36m[SUPERVISOR] Global MQTT Packet Sniffer is ONLINE.\033[0m")
        client.loop_start()
    except Exception as e:
        print(f"[SUPERVISOR] FATAL: Could not connect to MQTT Broker. Is Mosquitto running? {e}")
        sys.exit(1)

    print("[SUPERVISOR] Starting native host services...")
    update_expected_modules()
    native_cfg = MODULES_CONFIG.get("native", {})
    for desc, filename in NATIVE_SERVICES:
        if native_cfg.get(desc, True):
            start_native(desc, filename)
            
            target_mod = None
            if "clWhisper" in filename:
                target_mod = "whisper"
            elif "clTTS" in filename:
                target_mod = "tts"
                
            if target_mod:
                print(f"[SUPERVISOR] Waiting for {target_mod} to initialize before continuing...")
                timeout = 300
                while target_mod not in READY_MODULES and timeout > 0:
                    time.sleep(0.1)
                    timeout -= 1



    # 3. Global hotkeys are now managed via GNOME Settings (Wayland Compatibility)

    print("\n" + "="*50)
    print("HOST SUPERVISOR ONLINE. Press Ctrl+C to shutdown.")
    print("="*50 + "\n")

    try:
        while True:
            time.sleep(3)
            # Resurrect dead native services
            native_cfg = MODULES_CONFIG.get("native", {})
            for desc, filename in NATIVE_SERVICES:
                if not native_cfg.get(desc, True):
                    continue
                proc = PROCESSES.get(filename)
                if proc and proc.poll() is not None:
                    print(f"[SUPERVISOR] FATAL: {filename} died unexpectedly! Resurrecting...")
                    start_native(desc, filename)
    except KeyboardInterrupt:
        print("\n[SUPERVISOR] Manual shutdown triggered (Ctrl+C).")

    # --- SHUTDOWN ---
    for desc, filename in NATIVE_SERVICES:
        desc_label = filename.split('/')[-1].replace('.py', '')
        print(f"[SUPERVISOR] Terminating {desc_label} process...")
        stop_native(filename)

    client.loop_stop()
    print("[SUPERVISOR] Shutdown complete. Goodbye.")
    
if __name__ == "__main__":
    main()
