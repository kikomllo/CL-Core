import subprocess
import time
import sys

def supervise_ui():
    print("[SUPERVISOR] Starting native UI...")
    while True:
        print("[SUPERVISOR] Launching src/clUI.py...")
        process = subprocess.Popen([sys.executable, "src/clUI.py"])
        process.wait()
        print(f"[SUPERVISOR] clUI.py crashed or exited with code {process.returncode}.")
        print("[SUPERVISOR] Restarting UI in 2 seconds...")
        time.sleep(2)

def supervise_docker():
    print("[SUPERVISOR] Booting Docker Ecosystem...")
    subprocess.run(["docker", "compose", "up", "-d"], check=True)
    print("[SUPERVISOR] Docker containers are running.")
    time.sleep(2) # Give MQTT broker a moment

if __name__ == "__main__":
    print("="*50)
    print(" JARVIS HOST SUPERVISOR ")
    print("="*50)
    
    supervise_docker()
    
    try:
        supervise_ui()
    except KeyboardInterrupt:
        print("\n[SUPERVISOR] Received shutdown signal (Ctrl+C).")
        print("[SUPERVISOR] Bringing down Docker containers...")
        subprocess.run(["docker", "compose", "down"])
        print("[SUPERVISOR] Shutdown complete.")
