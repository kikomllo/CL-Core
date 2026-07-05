import subprocess
import sys
import time
import json
import logging
import paho.mqtt.client as mqtt_client

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

# --- MQTT LISTENER ---
def on_message(client, userdata, msg):
    """Listens for commands from the Central Brain."""
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
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
        client.loop_start()
    except Exception as e:
        print(f"[SUPERVISOR] FATAL: Could not connect to MQTT Broker. Is Mosquitto running? {e}")
        sys.exit(1)

    # 2. Spawn the Microservices
    for desc, filename in MODULES:
        start_module(desc, filename)
        time.sleep(0.5)  # Stagger boot to prevent CPU spikes

    print("\n" + "="*50)
    print("ALL SYSTEMS ONLINE. Press Ctrl+C to shutdown.")
    print("="*50 + "\n")

    # 3. Hold the main thread open permanently
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[SUPERVISOR] Manual shutdown triggered. Terminating all microservices...")
        for desc, filename in MODULES:
            stop_module(filename)
        print("[SUPERVISOR] System powered down. Goodbye.")

if __name__ == "__main__":
    main()