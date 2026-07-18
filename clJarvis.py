import subprocess
import sys
import time
import json
import os
import logging
import paho.mqtt.client as mqtt_client

# --- OS SETTINGS & DEBUG FLAG ---
def load_settings():
    config_path = os.path.join("config", "settings.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

SETTINGS = load_settings()
DEBUG_MQTT = SETTINGS.get("debug_mqtt", False)

# --- SUPERVISOR CONFIGURATION ---
MODULES = [
    ("Text-To-Speech", "src/clTTS.py"),
    ("Light Actuator", "src/clControl.py"),
    ("Music Actuator", "src/clSpotify.py"),
    ("Central Brain", "src/clDaemon.py"),
    ("Voice Sensor", "src/clVoice.py"),
    ("Terminal Actuator", "src/clTerminal.py")
]

PROCESSES = {}

def start_module(desc, filename):
    """Spawns a microservice and registers its process handle."""
    print(f">>> Starting {filename} ({desc})...")
    proc = subprocess.Popen([sys.executable, filename])
    PROCESSES[filename] = proc

def stop_module(filename):
    """Gracefully terminates a microservice using its registered handle."""
    if filename in PROCESSES:
        proc = PROCESSES[filename]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        del PROCESSES[filename]

# --- MQTT LISTENER & SNOOPER ---
def on_message(client, userdata, msg):
    """Listens for commands and optionally sniffs all OS traffic."""
    topic = msg.topic
    try:
        payload_str = msg.payload.decode('utf-8')
    except UnicodeDecodeError:
        payload_str = "<binary_payload>"

    # --- GLOBAL MQTT DEBUG SNOOPER ---
    if DEBUG_MQTT:
        # Prints in Cyan (\033[36m) to easily distinguish from standard service logs
        print(f"\r\033[K\033[36m[DEBUG-MQTT] [CH: {topic}] {payload_str}\033[0m")

    # --- SUPERVISOR COMMANDS ---
    if topic == "jarvis/sys/manager":
        try:
            payload = json.loads(payload_str)
            action = payload.get("action")
            target = payload.get("target")

            # --- GLOBAL ECOSYSTEM RESTART SEQUENCE ---
            if action == "restart_all_modules":
                print("\n" + "="*60)
                print("[SUPERVISOR] INITIATING FULL ECOSYSTEM REBOOT DIRECTIVE")
                print("="*60)
                
                for filename in list(PROCESSES.keys()):
                    print(f"[SUPERVISOR] Terminating: {filename}")
                    stop_module(filename)
                    
                time.sleep(1.5)
                
                print("\n[SUPERVISOR] Resurrecting infrastructure stack...")
                for desc, filename in MODULES:
                    start_module(desc, filename)
                    time.sleep(0.4)
                    
                print("\n" + "="*60)
                print("[SUPERVISOR] GLOBAL ECOSYSTEM REBOOT COMPLETE")
                print("="*60 + "\n")

            # --- INDIVIDUAL SURGICAL RESTART ---
            elif action == "restart_module" and target:
                print(f"\n[SUPERVISOR] Executing surgical restart for: {target}")
                stop_module(target)
                time.sleep(1.0) 
                
                desc = "Restarted Microservice"
                for d, f in MODULES:
                    if f == target:
                        desc = d
                        break
                        
                start_module(desc, target)
                
        except json.JSONDecodeError:
            print("[SUPERVISOR] Received malformed JSON command.")
        except Exception as e:
            print(f"[SUPERVISOR] Execution Error: {e}")

# --- MASTER BOOT SEQUENCE ---
def main():
    print("="*50)
    print("BOOTING JARVIS SMART HOME OS")
    print("="*50 + "\n")

    # 1. Boot the Supervisor Listener
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1)
    client.on_message = on_message
    
    try:
        client.connect("localhost", 1883, 60)
        client.subscribe("jarvis/sys/manager")
        
        if DEBUG_MQTT:
            client.subscribe("#")  # Subscribe to literally everything
            print("\033[36m[SUPERVISOR] Global MQTT Packet Sniffer is ONLINE.\033[0m")
            
        client.loop_start()
    except Exception as e:
        print(f"[SUPERVISOR] FATAL: Could not connect to MQTT Broker. Is Mosquitto running? {e}")
        sys.exit(1)

    # 2. Spawn the Microservices
    for desc, filename in MODULES:
        start_module(desc, filename)
        time.sleep(0.5)

    print("\n" + "="*50)
    print("ALL SYSTEMS ONLINE. Press Ctrl+C to shutdown.")
    print("="*50 + "\n")

    try:
        while True:
            time.sleep(3)
            for filename, proc in list(PROCESSES.items()):
                if proc.poll() is not None:  # A return code means the process died
                    print(f"\n[SUPERVISOR] FATAL: {filename} died unexpectedly! Resurrecting...")
                    
                    desc = next((d for d, f in MODULES if f == filename), "Unknown Module")
                    start_module(desc, filename)
    except KeyboardInterrupt:
        print("\n[SUPERVISOR] Manual shutdown triggered. Terminating all microservices...")
        for desc, filename in MODULES:
            stop_module(filename)
        print("[SUPERVISOR] System powered down. Goodbye.")

if __name__ == "__main__":
    main()