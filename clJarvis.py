import subprocess
import sys
import time
import json
import os
import signal
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

import re
import threading

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

class TeeLogger:
    def __init__(self, filename):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        self.terminal = sys.__stdout__
        self.log = open(filename, "w", encoding="utf-8")
        
    def write(self, message):
        if ECOSYSTEM_MODE != "BACKGROUND":
            self.terminal.write(message)
        
        clean_msg = ANSI_ESCAPE.sub('', message).replace('\r', '')
        if clean_msg:
            self.log.write(clean_msg)
            self.log.flush()
            
    def flush(self):
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
]
DOCKER_LOG_PROCS = []

def load_modules_config():
    config_path = os.path.join("config", "modules.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"native": {"UI": True, "Keybinds": True, "Utilities": True}, "docker": {}}

MODULES_CONFIG = load_modules_config()

PROCESSES = {}

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
    if filename in PROCESSES:
        proc = PROCESSES.pop(filename)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

def boot_docker():
    print("[SUPERVISOR] Booting Docker Ecosystem...")
    services = ["mqtt-broker"]
    docker_cfg = MODULES_CONFIG.get("docker", {})
    for container_name, enabled in docker_cfg.items():
        if enabled:
            services.append(container_name)
            
    result = subprocess.run(
        ["docker", "compose", "up", "-d"] + services,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print("[SUPERVISOR] FATAL: Docker Ecosystem failed to start!")
        if result.stderr:
            print(f"[SUPERVISOR] Docker Error Output:\n{result.stderr.strip()}")
        sys.exit(1)
    elif result.stdout and DEBUG_MQTT:
        print(f"[SUPERVISOR] Docker Output:\n{result.stdout.strip()}")
        
    print("[SUPERVISOR] Docker containers are running.")
    # Give the MQTT broker a moment to be ready
    time.sleep(1.5)

def tear_down_docker():
    print("[SUPERVISOR] Bringing down Docker containers...")
    result = subprocess.run(["docker", "compose", "down", "-t", "1"], capture_output=True, text=True)
    if result.returncode != 0 and result.stderr:
        print(f"[SUPERVISOR] Docker Teardown Warning:\n{result.stderr.strip()}")

def attach_docker_logs():
    global DOCKER_LOG_PROCS
    print("[SUPERVISOR] Attaching to core Docker logs (Mic, Daemon, Whisper)...")
    for proc in DOCKER_LOG_PROCS:
        try:
            proc.terminate()
        except:
            pass
    DOCKER_LOG_PROCS.clear()
    
    for container in ["jarvis-mic", "jarvis-brain", "jarvis-whisper"]:
        proc = subprocess.Popen(
            ["docker", "logs", "-f", "--tail", "0", container],
            stdout=subprocess.PIPE,  
            stderr=subprocess.STDOUT 
        )
        t = threading.Thread(target=stream_reader, args=(container, proc.stdout), daemon=True)
        t.start()
        DOCKER_LOG_PROCS.append(proc)

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

            if action in ["shutdown", "shutdown_ecosystem", "stop_all_modules"]:
                print("\n" + "="*60)
                print("[SUPERVISOR] INITIATING CLEAN ECOSYSTEM SHUTDOWN")
                print("="*60)
                for desc, filename in NATIVE_SERVICES:
                    stop_native(filename)
                tear_down_docker()
                print("[SUPERVISOR] Shutdown complete. Goodbye.")
                os._exit(0)

            elif action == "restart_all_modules":
                print("\n" + "="*60)
                print("[SUPERVISOR] INITIATING FULL ECOSYSTEM REBOOT")
                print("="*60)
                for desc, filename in NATIVE_SERVICES:
                    stop_native(filename)
                tear_down_docker()
                time.sleep(1.0)
                
                global MODULES_CONFIG
                MODULES_CONFIG = load_modules_config()
                
                boot_docker()
                attach_docker_logs()
                
                SYSTEM_HAS_ANNOUNCED = False
                
                print("[SUPERVISOR] Starting native host services...")
                native_cfg = MODULES_CONFIG.get("native", {})
                for desc, filename in NATIVE_SERVICES:
                    if native_cfg.get(desc, True):
                        start_native(desc, filename)
                        
                print("[SUPERVISOR] ECOSYSTEM REBOOT COMPLETE\n" + "="*60)

            elif action == "restart_module":
                raw_target = str(payload.get("target", "")).lower().strip()
                print(f"[SUPERVISOR] Requested restart for module target: '{raw_target}'")
                
                restarted = False
                # Check native host services first
                for desc, filename in NATIVE_SERVICES:
                    desc_lower = desc.lower()
                    file_lower = filename.lower()
                    if raw_target in desc_lower or desc_lower in raw_target or raw_target in file_lower or ("ui" in raw_target and "ui" in file_lower):
                        print(f"[SUPERVISOR] Restarting native host service: {desc} ({filename})...")
                        stop_native(filename)
                        time.sleep(0.5)
                        start_native(desc, filename)
                        restarted = True
                        break
                
                if not restarted:
                    # Check docker containers
                    container_map = {
                        "whisper": "jarvis-whisper",
                        "brain": "jarvis-brain",
                        "daemon": "jarvis-brain",
                        "music": "jarvis-music",
                        "spotify": "jarvis-music",
                        "tts": "jarvis-tts",
                        "mic": "jarvis-mic",
                        "voice": "jarvis-mic",
                        "sensor": "jarvis-mic",
                        "light": "jarvis-lights",
                    }
                    matched_container = None
                    for key, cname in container_map.items():
                        if key in raw_target:
                            matched_container = cname
                            break
                    if not matched_container and raw_target.startswith("jarvis-"):
                        matched_container = raw_target

                    if matched_container:
                        print(f"[SUPERVISOR] Restarting docker container: {matched_container}...")
                        subprocess.run(["docker", "restart", matched_container])
                    else:
                        print(f"[SUPERVISOR] Unable to match target '{raw_target}' to any active service or container.")

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
    
    elif topic == "jarvis/sys/whisper_state":
        if SYSTEM_HAS_ANNOUNCED:
            return
            
        try:
            payload = json.loads(payload_str)
            if payload.get("state") == "ready":
                SYSTEM_HAS_ANNOUNCED = True
                print("\n" + "="*50)
                print("[SUPERVISOR] WHISPER INFERENCE ENGINE ONLINE.")
                print("[SUPERVISOR] ALL SYSTEMS GO!")
                print("="*50 + "\n")
                
                client.publish("jarvis/sys/speak", json.dumps({
                    "text": "System online!", 
                    "skip_ducking": False, 
                    "request_reply": False
                }))
        except Exception:
            pass

def main():
    print("="*50)
    print(f"BOOTING JARVIS HOST SUPERVISOR")
    print(f"Ecosystem State: {ECOSYSTEM_MODE}")
    print("="*50 + "\n")

    # 1. Start Docker ecosystem (This boots the Mosquitto Broker)
    boot_docker()

    print("[SUPERVISOR] Starting native host services...")
    native_cfg = MODULES_CONFIG.get("native", {})
    for desc, filename in NATIVE_SERVICES:
        if native_cfg.get(desc, True):
            start_native(desc, filename)

    print("[SUPERVISOR] Giving UI and services 3 seconds to mount and subscribe...")
    time.sleep(3)

    attach_docker_logs()

    # 2. Connect global MQTT supervisor (This will now trigger the TTS *after* UI is ready)
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1)
    client.on_message = on_message
    try:
        client.connect("localhost", 1883, 60)
        client.subscribe("jarvis/sys/manager")
        client.subscribe("jarvis/sys/ui_control")
        client.subscribe("jarvis/sys/volume")
        client.subscribe("jarvis/sys/whisper_state")
        client.subscribe("jarvis/sys/state_change")
        if DEBUG_MQTT:
            client.subscribe("#")
            print("\033[36m[SUPERVISOR] Global MQTT Packet Sniffer is ONLINE.\033[0m")
        client.loop_start()
    except Exception as e:
        print(f"[SUPERVISOR] FATAL: Could not connect to MQTT Broker. Is Mosquitto running? {e}")
        tear_down_docker()
        sys.exit(1)

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

    try:
        for proc in DOCKER_LOG_PROCS:
            proc.terminate()
    except Exception:
        pass

    client.loop_stop()
    tear_down_docker()
    print("[SUPERVISOR] Shutdown complete. Goodbye.")
    
if __name__ == "__main__":
    main()
