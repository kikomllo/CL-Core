import subprocess
import sys
import time

def main():
    print("="*50)
    print("BOOTING JARVIS SMART HOME OS")
    print("="*50 + "\n")

    python_bin = sys.executable
    processes = []

    try:
        # 1: Boot clControl.py
        print(">>> Starting clControl.py (Light Actuator)...")
        p_control = subprocess.Popen([python_bin, "clControl.py"])
        processes.append(p_control)
        time.sleep(1)

        # 2: Boot clSpotify.py
        print(">>> Starting clSpotify.py (Music Actuator)...")
        p_spotify = subprocess.Popen([python_bin, "clSpotify.py"])
        processes.append(p_spotify)
        time.sleep(1)

        # 3: Boot clDaemon.py
        print(">>> Starting clDaemon.py (Central Brain)...")
        p_daemon = subprocess.Popen([python_bin, "clDaemon.py"]) 
        processes.append(p_daemon)
        time.sleep(1)

        # 4: Boot clVoice.py
        print(">>> Starting clVoice.py (Voice Sensor)...")
        p_voice = subprocess.Popen([python_bin, "clVoice.py"])
        processes.append(p_voice)
        
        # 5: Boot clTerminal.py
        print(">>> Starting clTerminal.py (Terminal Actuator)...")
        p_terminal = subprocess.Popen([python_bin, "clTerminal.py"])
        processes.append(p_terminal)

        print("\n" + "="*50)
        print("ALL SYSTEMS ONLINE. Press Ctrl+C to shutdown.")
        print("="*50 + "\n")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n" + "="*50)
        print("SHUTDOWN SEQUENCE INITIATED...")
        print("="*50)
        
        for p in processes:
            p.terminate()
            
        for p in processes:
            p.wait()
            
        print("All services stopped successfully. Goodbye!")

if __name__ == "__main__":
    main()