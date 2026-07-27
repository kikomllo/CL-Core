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
            return cfg.get("settings", {}), cfg.get("ecosystem", {}).get("mode", "STANDARD")
    except Exception:
        return {}, "STANDARD"

SETTINGS, ECOSYSTEM_MODE = load_settings()
DEBUG_MQTT = SETTINGS.get("debug_mqtt", False)

# --- NATIVE HOST SERVICES (not in Docker) ---
NATIVE_SERVICES = [
    ("UI",        "src/clUI.py"),
    ("Reminders", "src/clReminders.py"),
]

PROCESSES = {}

def start_native(desc, filename):
    print(f"[SUPERVISOR] Launching {filename}...")
    proc = subprocess.Popen([sys.executable, filename])
    PROCESSES[filename] = proc
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
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        capture_output=False,
        text=True
    )
    if result.returncode != 0:
        print("[SUPERVISOR] FATAL: Docker Ecosystem failed to start!")
        sys.exit(1)
    print("[SUPERVISOR] Docker containers are running.")
    # Give the MQTT broker a moment to be ready
    time.sleep(1.5)

def tear_down_docker():
    print("[SUPERVISOR] Bringing down Docker containers...")
    subprocess.run(["docker", "compose", "down", "-t", "1"], capture_output=False, text=True)

def on_message(client, userdata, msg):
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
                print(f"\r\033[K[{status}] Vol: {rms:5d} ||{bar}|| ACT: {a_thresh} SIL: {s_thresh} AVG: {b_noise}", end='', flush=True)
            else:
                print(f"\r\033[K[{status}] Vol: {rms:5d} ||{bar}||", end='', flush=True)
        except Exception:
            pass
        return

    if DEBUG_MQTT and topic != "jarvis/sys/volume":
        print(f"\r\033[K\033[36m[DEBUG-MQTT] [CH: {topic}] {payload_str}\033[0m")

    if topic == "jarvis/sys/manager":
        try:
            payload = json.loads(payload_str)
            action = payload.get("action")

            if action == "restart_all_modules":
                print("\n" + "="*60)
                print("[SUPERVISOR] INITIATING FULL ECOSYSTEM REBOOT")
                print("="*60)
                tear_down_docker()
                time.sleep(1.0)
                boot_docker()
                print("[SUPERVISOR] ECOSYSTEM REBOOT COMPLETE\n" + "="*60)

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

def main():
    print("="*50)
    print(f"BOOTING JARVIS HOST SUPERVISOR")
    print(f"Ecosystem State: {ECOSYSTEM_MODE}")
    print("="*50 + "\n")

    # 1. Start Docker ecosystem first
    boot_docker()

    # Attach mic log for boot messages only (Booting/READY) — not the volume meter
    # Volume meter is now streamed via MQTT directly to avoid Docker log buffering
    print("[SUPERVISOR] Attaching to jarvis-mic...")
    time.sleep(2)
    mic_log = subprocess.Popen(
        ["docker", "logs", "-f", "--tail", "0", "jarvis-mic"],
        stdout=sys.stdout, stderr=sys.stdout
    )

    # 2. Connect global MQTT supervisor
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1)
    client.on_message = on_message
    try:
        client.connect("localhost", 1883, 60)
        client.subscribe("jarvis/sys/manager")
        client.subscribe("jarvis/sys/ui_control")
        if DEBUG_MQTT:
            client.subscribe("#")
            print("\033[36m[SUPERVISOR] Global MQTT Packet Sniffer is ONLINE.\033[0m")
        client.loop_start()
    except Exception as e:
        print(f"[SUPERVISOR] FATAL: Could not connect to MQTT Broker. Is Mosquitto running? {e}")
        tear_down_docker()
        sys.exit(1)

    # 3. Register global hotkeys
    try:
        from pynput import keyboard

        def on_fullscreen_toggle():
            print("\n[SUPERVISOR] Global hotkey detected! Activating UI Fullscreen Mode...")
            try:
                client.publish("jarvis/sys/ui_control", json.dumps({"action": "set_fullscreen"}))
            except Exception as e:
                print(f"[SUPERVISOR] Hotkey publish error: {e}")

        def on_overlay_toggle():
            print("\n[SUPERVISOR] Global hotkey detected! Activating UI Overlay Mode...")
            try:
                client.publish("jarvis/sys/ui_control", json.dumps({"action": "set_overlay"}))
            except Exception as e:
                print(f"[SUPERVISOR] Hotkey publish error: {e}")

        def on_activate_abort():
            print("\n[SUPERVISOR] Global ABORT hotkey triggered!")
            try:
                client.publish("jarvis/sys/abort", json.dumps({"action": "abort"}))
            except Exception as e:
                print(f"[SUPERVISOR] Abort publish error: {e}")

        hotkey = keyboard.GlobalHotKeys({
            '<ctrl>+<alt>+<shift>+j': on_activate_abort,
            '<ctrl>+<alt>+<shift>+f': on_fullscreen_toggle,
            '<ctrl>+<alt>+<shift>+o': on_overlay_toggle,
        })
        hotkey.start()
        print("[SUPERVISOR] Global hotkeys are registered.")
    except Exception as e:
        print(f"[SUPERVISOR] Warning: Could not register hotkeys: {e}")

    # 4. Start native host services
    print("[SUPERVISOR] Starting native host services...")
    for desc, filename in NATIVE_SERVICES:
        start_native(desc, filename)

    print("\n" + "="*50)
    print("HOST SUPERVISOR ONLINE. Press Ctrl+C to shutdown.")
    print("="*50 + "\n")

    try:
        while True:
            time.sleep(3)
            # Resurrect dead native services
            for desc, filename in NATIVE_SERVICES:
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
        mic_log.terminate()
    except Exception:
        pass

    client.loop_stop()
    tear_down_docker()
    print("[SUPERVISOR] Shutdown complete. Goodbye.")

if __name__ == "__main__":
    main()
