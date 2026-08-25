import os
import sys
import json
import subprocess
import logging
import time
import pygame

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "..", "data", "alarms")
ASSETS_DIR = os.path.join(BASE_DIR, "..", "..", "assets")
ALARM_SOUND_PATH = os.path.join(ASSETS_DIR, "alarm.wav")

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
            logging.info("[ALARM TRIGGER] Jarvis ecosystem is offline. Booting up for alarm!")
            boot_path = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "boot.py"))
            if sys.platform == 'win32':
                subprocess.Popen([sys.executable, boot_path], cwd=os.path.dirname(boot_path), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                subprocess.Popen([sys.executable, boot_path], cwd=os.path.dirname(boot_path), start_new_session=True)
            time.sleep(3.5)
            return True
    except Exception as e:
        logging.error(f"[ALARM TRIGGER] Failed to boot ecosystem: {e}")
    return False

def main():
    if len(sys.argv) < 2:
        logging.error("[ALARM TRIGGER] No alarm ID provided.")
        return
        
    alarm_id = sys.argv[1]
    meta_path = os.path.join(DATA_DIR, f"{alarm_id}.json")
    
    if not os.path.exists(meta_path):
        logging.error(f"[ALARM TRIGGER] Alarm metadata {alarm_id} not found.")
        return
        
    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)
        
    tts_prompt = meta.get("tts_prompt", "Wake up, sir! Alarm activated. Please speak the deactivation code to dismiss.")
    challenge_type = meta.get("challenge_type", "phrase")
    expected_answer = meta.get("expected_answer", "turn off alarm")
    
    was_offline = boot_ecosystem_if_offline()
    
    try:
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        
        is_deactivated = False
        mic_is_active = False
        
        def on_message(client, userdata, msg):
            nonlocal is_deactivated, mic_is_active
            if msg.topic == "jarvis/sys/alarm/deactivate":
                try:
                    payload = json.loads(msg.payload.decode('utf-8'))
                    if payload.get("id") == alarm_id or payload.get("id") is None:
                        logging.info(f"[ALARM TRIGGER] Deactivation signal received for alarm {alarm_id}!")
                        is_deactivated = True
                except Exception as e:
                    logging.error(f"[ALARM TRIGGER] Error parsing deactivation message: {e}")
            elif msg.topic == "jarvis/sys/mic_state":
                try:
                    payload = json.loads(msg.payload.decode('utf-8'))
                    state = payload.get("state", "idle")
                    if state in ["listening", "recording", "processing"]:
                        mic_is_active = True
                    else:
                        mic_is_active = False
                except Exception:
                    pass

        c.on_message = on_message
        c.connect("localhost")
        c.subscribe("jarvis/sys/alarm/deactivate")
        c.subscribe("jarvis/sys/mic_state")
        c.loop_start()
        
        # Publish ring signal to daemon to lock interface
        c.publish("jarvis/sys/alarm/ring", json.dumps({
            "id": alarm_id,
            "challenge_type": challenge_type,
            "expected_answer": expected_answer,
            "tts_prompt": tts_prompt
        }))
        
        # Initialize pygame mixer for alarm sound playback
        pygame.mixer.init()
        sound_loaded = False
        if os.path.exists(ALARM_SOUND_PATH):
            pygame.mixer.music.load(ALARM_SOUND_PATH)
            sound_loaded = True
            
        start_time = time.time()
        max_duration = 300  # 5 minutes max ringing
        
        logging.info("[ALARM TRIGGER] Alarm sound loop started.")
        
        while not is_deactivated and (time.time() - start_time < max_duration):
            if mic_is_active:
                if pygame.mixer.music.get_busy():
                    pygame.mixer.music.pause()
                time.sleep(0.1)
                continue
                
            if sound_loaded:
                if not pygame.mixer.music.get_busy():
                    pygame.mixer.music.unpause()
                    pygame.mixer.music.play()
            else:
                c.publish("jarvis/sys/speak", json.dumps({
                    "text": "Alarm ringing, sir!",
                    "skip_ducking": True
                }))
                
            time.sleep(0.2)
            
        if sound_loaded:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
            
        c.loop_stop()
        c.disconnect()
        
        # Cleanup metadata file
        if os.path.exists(meta_path):
            os.remove(meta_path)
            
        # Cleanup systemd unit
        try:
            subprocess.run(["systemctl", "--user", "stop", f"jarvis-alarm-{alarm_id}.timer"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["systemctl", "--user", "stop", f"jarvis-alarm-{alarm_id}.service"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["systemctl", "--user", "reset-failed", f"jarvis-alarm-{alarm_id}.*"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
            
        # Auto-shutdown if launched offline
        if was_offline:
            logging.info("[ALARM TRIGGER] Jarvis was offline before alarm. Triggering ecosystem shutdown.")
            try:
                c2 = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
                c2.connect("localhost")
                c2.publish("jarvis/sys/speak", json.dumps({"text": "Alarm dismissed. Shutting down.", "skip_ducking": True}))
                time.sleep(2.0)
                c2.publish("jarvis/sys/manager", json.dumps({"action": "shutdown"}))
                c2.disconnect()
            except Exception as e:
                logging.error(f"[ALARM TRIGGER] Failed to send shutdown command: {e}")
                
    except Exception as e:
        logging.error(f"[ALARM TRIGGER] Error in trigger process: {e}")

if __name__ == "__main__":
    main()
