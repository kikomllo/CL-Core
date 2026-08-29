import os
import sys
import subprocess
import platform

def main():
    print("==================================================")
    print("JARVIS ECOSYSTEM BOOTLOADER")
    print("==================================================")
    
    # OS specifics
    is_windows = platform.system() == "Windows"
    venv_dir = ".venv"
    
    python_exe = os.path.join(venv_dir, "Scripts", "python.exe") if is_windows else os.path.join(venv_dir, "bin", "python")
    pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe") if is_windows else os.path.join(venv_dir, "bin", "pip")
    
    # 1. Ensure Virtual Environment Exists
    if not os.path.exists(python_exe):
        print(f"[BOOT] Creating Python virtual environment in '{venv_dir}'...")
        try:
            subprocess.run([sys.executable, "-m", "venv", "--clear", venv_dir], check=True)
            print("[BOOT] Virtual environment created successfully.")
        except subprocess.CalledProcessError as e:
            print(f"[BOOT] FATAL: Failed to create virtual environment: {e}")
            sys.exit(1)
            
    # 2. Check for dependencies update
    req_file = "requirements.txt"
    timestamp_file = os.path.join(venv_dir, ".req_timestamp")
    
    should_install = True
    if os.path.exists(req_file) and os.path.exists(timestamp_file):
        req_mtime = os.path.getmtime(req_file)
        with open(timestamp_file, "r") as f:
            try:
                last_install = float(f.read().strip())
                if req_mtime <= last_install:
                    should_install = False
                    print("[BOOT] Dependencies are up to date.")
            except ValueError:
                pass
            
    if should_install:
        print("[BOOT] Checking/Installing dependencies from requirements.txt (This may take a moment)...")
        try:
            subprocess.run([pip_exe, "install", "-r", req_file], check=True)
            # Save timestamp
            with open(timestamp_file, "w") as f:
                f.write(str(os.path.getmtime(req_file)))
            print("[BOOT] Dependencies successfully updated.")
        except subprocess.CalledProcessError as e:
            print(f"[BOOT] ERROR: Failed to install dependencies: {e}")
            print("[BOOT] Will attempt to continue anyway...")
            
    # 3. Launch the supervisor
    print(f"[BOOT] Launching Ecosystem Supervisor ({'Windows' if is_windows else 'Linux'} Native)...")
    env = os.environ.copy()
    
    # 4. Single Instance Lock & Cleanup
    import socket
    import time
    
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    is_locked = False
    try:
        lock_socket.bind(("127.0.0.1", 64000))
    except socket.error:
        is_locked = True
        
    if is_locked:
        print("[BOOT] Existing Jarvis instance detected. Attempting graceful shutdown...")
        try:
            shutdown_script = "import paho.mqtt.publish as p; import json; p.single('jarvis/sys/manager', json.dumps({'action': 'shutdown'}), hostname='localhost')"
            subprocess.run([python_exe, "-c", shutdown_script], check=False)
            
            print("[BOOT] Waiting for existing instance to shutdown...")
            for _ in range(15):
                time.sleep(1)
                try:
                    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    lock_socket.bind(("127.0.0.1", 64000))
                    print("[BOOT] Old instance successfully shut down.")
                    is_locked = False
                    break
                except socket.error:
                    pass
        except Exception as e:
            print(f"[BOOT] Error during graceful shutdown attempt: {e}")

    # FORCE CLEANUP: Always kill any lingering processes to ensure a clean boot
    print("[BOOT] Sweeping system for any lingering ecosystem processes...")
    ecosystem_scripts = [
        "clJarvis.py", "clUI.py", "clKeybinds.py", "clUtilities.py", 
        "clUpdater.py", "clTrayIcon.py", "clWhisper.py", "clDaemon.py", 
        "clSpotify.py", "clTTS.py", "clControl.py", "clMic.py", "clTerminal.py"
    ]
    for script in ecosystem_scripts:
        if is_windows:
            subprocess.run(f'wmic process where "name=\'python.exe\' and commandline like \'%{script}%\'" call terminate', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(['pkill', '-f', f'python.*{script}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
    time.sleep(1)
            
    if is_locked:
        try:
            lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            lock_socket.bind(("127.0.0.1", 64000))
        except socket.error:
            print("[BOOT] FATAL: Port 64000 is still locked after forceful cleanup. Cannot start.")
            sys.exit(1)

    # Execute clJarvis replacing the current process (on Unix) or launching subprocess (on Windows)
    if is_windows:
        sys.exit(subprocess.run([python_exe, "clJarvis.py"], env=env).returncode)
    else:
        # Prevent the lock socket from closing during os.execve
        os.set_inheritable(lock_socket.fileno(), True)
        os.execve(python_exe, [python_exe, "clJarvis.py"], env)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
