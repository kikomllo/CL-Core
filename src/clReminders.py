import json
import logging
import asyncio
import os
import shutil
import time
import subprocess
from datetime import datetime
import aiomqtt
import dateparser.search

# Logging setup
import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('CLREMINDERS')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data", "reminders")
os.makedirs(DATA_DIR, exist_ok=True)

class JarvisReminders:
    def __init__(self):
        self.mqtt_client = None

    def schedule_systemd_timer(self, reminder_id: str, scheduled_time: datetime):
        # Format for systemd-run: YYYY-MM-DD HH:MM:SS
        time_str = scheduled_time.strftime('%Y-%m-%d %H:%M:%S')
        trigger_script = os.path.abspath(os.path.join(BASE_DIR, "utils", "clReminderTrigger.py"))
        
        cmd = [
            "systemd-run", 
            "--user", 
            f"--on-calendar={time_str}", 
            "--unit", f"jarvis-reminder-{reminder_id}",
            "python3", trigger_script, reminder_id
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logging.info(f"Systemd timer scheduled for {time_str}")
            return True
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to schedule systemd timer: {e.stderr}")
            return False

    async def run(self):
        logging.info("Reminders Module Online. Listening for new reminders...")
        while True:
            try:
                async with aiomqtt.Client("localhost") as client:
                    self.mqtt_client = client
                    await client.subscribe("jarvis/sys/reminder/create")
                    await client.subscribe("jarvis/sys/reminder/control")
                    
                    async for message in client.messages:
                        topic = message.topic.value
                        payload = json.loads(message.payload.decode('utf-8'))
                        
                        if topic == "jarvis/sys/reminder/create":
                            await self.handle_create(payload)
                        elif topic == "jarvis/sys/reminder/control":
                            action = payload.get("action")
                            if action == "list" or action == "intent_delete_reminder":
                                await self.handle_list(is_delete_mode=(action == "intent_delete_reminder"))
                            elif action == "delete":
                                await self.handle_delete(payload.get("id"))
            except Exception as e:
                logging.error(f"MQTT Error in Reminders: {e}")
                await asyncio.sleep(5)

    async def handle_list(self, is_delete_mode=False):
        reminders = []
        for file in os.listdir(DATA_DIR):
            if file.endswith('.json'):
                try:
                    with open(os.path.join(DATA_DIR, file), 'r') as f:
                        data = json.load(f)
                        dt = datetime.fromisoformat(data['time'])
                        if dt > datetime.now():
                            reminders.append(data)
                except Exception:
                    pass
                    
        reminders.sort(key=lambda x: datetime.fromisoformat(x['time']))
        
        await self.mqtt_client.publish("jarvis/feedback", json.dumps({
            "device": "reminders",
            "status": "success",
            "action": "list",
            "is_delete_mode": is_delete_mode,
            "reminders": reminders
        }))

        options = []
        for i, r in enumerate(reminders):
            dt = datetime.fromisoformat(r['time'])
            options.append(f"[{i}] {dt.strftime('%m-%d %H:%M')} - {r['text']}")
            
        title = "Delete Reminder" if is_delete_mode else "Active Reminders"
        await self.mqtt_client.publish("jarvis/sys/ui_options", json.dumps({
            "title": title,
            "options": options if options else ["No reminders scheduled"]
        }))

    async def handle_delete(self, reminder_id):
        if not reminder_id: return
        
        # Stop systemd timer
        try:
            subprocess.run(["systemd-run", "--user", "--quiet", "--unit", f"jarvis-reminder-{reminder_id}", "/bin/true"], check=False)
            subprocess.run(["systemctl", "--user", "stop", f"jarvis-reminder-{reminder_id}.timer"], check=False)
            subprocess.run(["systemctl", "--user", "stop", f"jarvis-reminder-{reminder_id}.service"], check=False)
        except Exception as e:
            logging.error(f"Failed to stop timer for {reminder_id}: {e}")
            
        # Delete file
        file_path = os.path.join(DATA_DIR, f"{reminder_id}.json")
        if os.path.exists(file_path):
            os.remove(file_path)
            
        await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({
            "text": "Reminder cancelled.",
            "skip_ducking": True
        }))
        
    async def handle_create(self, payload):
        raw_text = payload.get("raw_text", "")
        tmp_audio_path = payload.get("audio_path", "")
        
        # 0. Instant feedback
        await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({
            "text": "Scheduling...",
            "skip_ducking": True
        }))
        
        # 1. Parse date from text (run in thread to prevent blocking event loop)
        def parse_date():
            return dateparser.search.search_dates(raw_text, languages=['en'])
        dates_found = await asyncio.to_thread(parse_date)
        
        if not dates_found:
            logging.warning("Could not parse time from reminder text.")
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "I couldn't figure out the time for that reminder, sir.", "skip_ducking": True}))
            return
            
        # Get the first found date
        date_str, dt = dates_found[0]
        reminder_id = payload.get("reminder_id", str(int(time.time())))
        
        # 2. Check if audio exists
        await asyncio.sleep(0.5)
        audio_dest = os.path.abspath(tmp_audio_path) if tmp_audio_path else ""
        if audio_dest and os.path.exists(audio_dest):
            logging.info(f"Verified reminder audio at {audio_dest}")
        else:
            logging.warning(f"Audio path {audio_dest} not found! The reminder will not have audio.")
            audio_dest = ""
            
        # Save metadata
        meta_path = os.path.join(DATA_DIR, f"{reminder_id}.json")
        meta = {
            "id": reminder_id,
            "text": raw_text,
            "time": dt.isoformat(),
            "audio_path": audio_dest
        }
        with open(meta_path, 'w') as f:
            json.dump(meta, f, indent=2)
            
        # 4. Schedule systemd timer
        success = self.schedule_systemd_timer(reminder_id, dt)
        
        if success:
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({
                "text": f"Reminder scheduled for {date_str}.",
                "skip_ducking": True
            }))
        else:
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({
                "text": "Failed to schedule the reminder timer.",
                "skip_ducking": True
            }))

if __name__ == "__main__":
    daemon = JarvisReminders()
    asyncio.run(daemon.run())
