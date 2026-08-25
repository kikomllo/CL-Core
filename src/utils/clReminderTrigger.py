import os
import sys
import json
import subprocess
import logging
import time

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "..", "data", "reminders")

def get_volume():
    try:
        out = subprocess.check_output(["amixer", "get", "Master"]).decode()
        if "[off]" in out:
            return 0
        import re
        match = re.search(r"\[(\d+)%\]", out)
        if match:
            return int(match.group(1))
    except Exception as e:
        logging.error(f"Failed to check volume: {e}")
        
    return 100

def boot_ecosystem_if_offline() -> bool:
    try:
        import socket
        lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            lock_socket.bind(("127.0.0.1", 64000))
            is_offline = True
            lock_socket.close()
        except socket.error:
            is_offline = False

        if is_offline:
            logging.info("Jarvis ecosystem is offline. Booting it up!")
            boot_path = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "boot.py"))
            # Launch in a new session so it outlives this script    
            if sys.platform == 'win32':
                subprocess.Popen([sys.executable, boot_path], cwd=os.path.dirname(boot_path), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                subprocess.Popen([sys.executable, boot_path], cwd=os.path.dirname(boot_path), start_new_session=True)
            
            import paho.mqtt.client as mqtt
            ecosystem_ready = False
            
            def on_msg(client, userdata, msg):
                nonlocal ecosystem_ready
                if msg.topic == "jarvis/sys/ecosystem_online":
                    ecosystem_ready = True
                    
            for _ in range(30):
                time.sleep(1)
                try:
                    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
                    c.on_message = on_msg
                    c.connect("localhost")
                    c.subscribe("jarvis/sys/ecosystem_online")
                    c.loop_start()
                    
                    # Wait up to 30 seconds for the ecosystem to announce itself
                    wait_time = 0
                    while not ecosystem_ready and wait_time < 30:
                        time.sleep(1)
                        wait_time += 1
                        
                    c.loop_stop()
                    c.disconnect()
                    break
                except Exception:
                    pass
                
            return True
    except Exception as e:
        logging.error(f"Failed to boot ecosystem: {e}")
    return False

def main():
    if len(sys.argv) < 2:
        logging.error("No reminder ID provided.")
        return
        
    reminder_id = sys.argv[1]
    meta_path = os.path.join(DATA_DIR, f"{reminder_id}.json")
    
    if not os.path.exists(meta_path):
        logging.error(f"Reminder {reminder_id} not found.")
        return
        
    with open(meta_path, 'r') as f:
        meta = json.load(f)
        
    text = meta.get("text", "You have a reminder!")
    audio_path = meta.get("audio_path", "")
    
    was_offline = boot_ecosystem_if_offline()
    
    vol = get_volume()
    logging.info(f"Triggering reminder {reminder_id}. System volume is {vol}%.")
    
    # 1. Unconditionally send desktop notification
    if sys.platform != 'win32':
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        if hasattr(os, "getuid"):
            env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
            env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        try:
            subprocess.run(["notify-send", "Jarvis Reminder", text, "-i", "dialog-information"], env=env)
        except Exception as e:
            logging.error(f"Failed to send notification: {e}")
    
    # 2. Play audio if volume is adequate
    if vol >= 5:
        if audio_path and os.path.exists(audio_path):
            logging.info("Sending TTS and Reminder Audio to centralized queue...")
            try:
                import paho.mqtt.client as mqtt
                import time
                tts_done_count = 0
                
                def on_message(client, userdata, msg):
                    nonlocal tts_done_count
                    if msg.topic == "jarvis/sys/tts_done":
                        tts_done_count += 1
                        
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
                c.on_message = on_message
                c.connect("localhost")
                c.subscribe("jarvis/sys/tts_done")
                c.loop_start()
                
                # Route the playback through the centralized TTS queue to serialize audio access
                c.publish("jarvis/sys/speak", json.dumps({"text": "Playing reminder.", "skip_ducking": True}))
                c.publish("jarvis/sys/play_audio", json.dumps({"path": audio_path}))
                
                start_time = time.time()
                while tts_done_count < 2 and time.time() - start_time < 20.0:
                    time.sleep(0.1)
                    
                c.loop_stop()
                c.disconnect()
            except Exception as e:
                logging.error(f"Failed to trigger reminder audio: {e}")
                
        else:
            logging.warning("No audio file found for this reminder. Falling back to live TTS...")
            try:
                import paho.mqtt.client as mqtt
                import time
                tts_finished = False
                
                def on_message(client, userdata, msg):
                    nonlocal tts_finished
                    if msg.topic == "jarvis/sys/tts_done":
                        tts_finished = True
                        
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
                c.on_message = on_message
                c.connect("localhost")
                c.subscribe("jarvis/sys/tts_done")
                c.loop_start()
                
                c.publish("jarvis/sys/speak", json.dumps({"text": f"Sir, reminder: {text}", "skip_ducking": True}))
                
                start_time = time.time()
                while not tts_finished and time.time() - start_time < 5.0:
                    time.sleep(0.1)
                    
                c.loop_stop()
                c.disconnect()
            except Exception as e:
                logging.error(f"Failed to play fallback TTS: {e}")
    
    try:
        if os.path.exists(meta_path):
            os.remove(meta_path)
            logging.info(f"Erased reminder metadata: {meta_path}")
            
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)
            logging.info(f"Erased reminder audio: {audio_path}")
    except Exception as e:
        logging.error(f"Failed to clean up reminder files: {e}")

    # 3. If ecosystem was booted solely for this reminder, shut down all processes cleanly
    if was_offline:
        logging.info("Ecosystem was launched for this reminder. Shutting down all processes...")
        time.sleep(1.0)
        try:
            import paho.mqtt.client as mqtt
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
            c.connect("localhost")
            msg = c.publish("jarvis/sys/manager", json.dumps({"action": "shutdown"}))
            msg.wait_for_publish()
            c.disconnect()
        except Exception as e:
            logging.error(f"Failed to publish ecosystem shutdown command: {e}")

if __name__ == "__main__":
    main()
