import json
import logging
import asyncio
import os
import shutil
import time
import subprocess
from datetime import datetime, timedelta
import aiomqtt
import dateparser.search
import re

import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('CLUTILITIES')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALARMS_DIR = os.path.join(BASE_DIR, "..", "data", "alarms")
REMINDERS_DIR = os.path.join(BASE_DIR, "..", "data", "reminders")
TODOS_DIR = os.path.join(BASE_DIR, "..", "data", "todos")
EVENTS_DIR = os.path.join(BASE_DIR, "..", "data", "events")

os.makedirs(ALARMS_DIR, exist_ok=True)
os.makedirs(REMINDERS_DIR, exist_ok=True)
os.makedirs(TODOS_DIR, exist_ok=True)
os.makedirs(EVENTS_DIR, exist_ok=True)

class JarvisUtilities:
    def __init__(self):
        self.mqtt_client = None

    def schedule_systemd_timer(self, unit_prefix: str, script_name: str, item_id: str, scheduled_time: datetime):
        time_str = scheduled_time.strftime('%Y-%m-%d %H:%M:%S')
        trigger_script = os.path.abspath(os.path.join(BASE_DIR, "utils", script_name))
        
        if sys.platform == 'win32':
            sleep_sec = max(0.0, (scheduled_time - datetime.now()).total_seconds())
            cmd = [
                sys.executable, "-c",
                f"import time, subprocess, sys; time.sleep({sleep_sec}); subprocess.run([sys.executable, {repr(trigger_script)}, {repr(item_id)}])"
            ]
            try:
                subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
                logging.info(f"Windows background timer scheduled for {time_str} (ID: {item_id})")
                return True
            except Exception as e:
                logging.error(f"Failed to schedule Windows timer: {e}")
                return False

        cmd = [
            "systemd-run", 
            "--user", 
            f"--on-calendar={time_str}", 
            "--unit", f"{unit_prefix}-{item_id}",
            "python3", trigger_script, item_id
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logging.info(f"Systemd timer scheduled for {time_str} (ID: {item_id})")
            return True
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to schedule systemd timer: {e.stderr}")
            return False

    async def run(self):
        logging.info("Utilities Module Online. Listening for alarms, reminders, and todos...")
        while True:
            try:
                async with aiomqtt.Client("localhost") as client:
                    self.mqtt_client = client
                    await client.subscribe("jarvis/sys/alarm/create")
                    await client.subscribe("jarvis/sys/alarm/control")
                    await client.subscribe("jarvis/sys/reminder/create")
                    await client.subscribe("jarvis/sys/reminder/control")
                    await client.subscribe("jarvis/sys/todo/create")
                    await client.subscribe("jarvis/sys/todo/control")
                    await client.subscribe("jarvis/sys/todo/request")
                    await client.subscribe("jarvis/sys/calendar/create")
                    await client.subscribe("jarvis/sys/calendar/request")
                    await client.subscribe("jarvis/sys/calendar/control")
                    await client.publish("jarvis/sys/module_ready", json.dumps({"module": "utilities"}))
                    
                    async for message in client.messages:
                        topic = message.topic.value
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                        except json.JSONDecodeError:
                            continue
                            
                        # ALARMS
                        if topic == "jarvis/sys/alarm/create":
                            await self.handle_alarm_create(payload)
                        elif topic == "jarvis/sys/alarm/control":
                            action = payload.get("action")
                            if action in ["list", "intent_delete_alarm"]:
                                await self.handle_alarm_list(is_delete_mode=(action == "intent_delete_alarm"))
                            elif action in ["delete", "cancel"]:
                                await self.handle_alarm_delete(payload.get("id"))
                            elif action == "delete_all":
                                await self.handle_alarm_delete("all")
                                
                        # REMINDERS
                        elif topic == "jarvis/sys/reminder/create":
                            await self.handle_reminder_create(payload)
                        elif topic == "jarvis/sys/reminder/control":
                            action = payload.get("action")
                            if action in ["list", "intent_delete_reminder"]:
                                await self.handle_reminder_list(is_delete_mode=(action == "intent_delete_reminder"))
                            elif action == "delete":
                                await self.handle_reminder_delete(payload.get("id"))
                                
                        # TODOS
                        elif topic == "jarvis/sys/todo/create":
                            await self.handle_todo_create(payload)
                        elif topic == "jarvis/sys/todo/control":
                            action = payload.get("action")
                            if action == "delete":
                                await self.handle_todo_delete(payload.get("id"))
                            elif action == "complete":
                                await self.handle_todo_complete(payload.get("id"))
                            elif action == "list":
                                await self.handle_todo_list()
                        elif topic == "jarvis/sys/todo/request":
                            await self.handle_todo_list()
                            
                        # CALENDAR
                        elif topic == "jarvis/sys/calendar/create":
                            await self.handle_calendar_create(payload)
                        elif topic == "jarvis/sys/calendar/request":
                            action = payload.get("action")
                            if action in ["read", "list"]:
                                await self.handle_calendar_list()
                            elif action == "daily_briefing":
                                await self.handle_calendar_briefing()
                        elif topic == "jarvis/sys/calendar/control":
                            action = payload.get("action")
                            if action == "delete":
                                await self.handle_calendar_delete(payload.get("id"))
                            
            except Exception as e:
                logging.error(f"MQTT Error in Utilities: {e}")
                await asyncio.sleep(5)

    # -------------------------------------------------------------------------
    # ALARM LOGIC
    # -------------------------------------------------------------------------
    async def handle_alarm_list(self, is_delete_mode=False):
        alarms = []
        for file in os.listdir(ALARMS_DIR):
            if file.endswith('.json'):
                try:
                    with open(os.path.join(ALARMS_DIR, file), 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        dt = datetime.fromisoformat(data['time'])
                        if dt > datetime.now():
                            alarms.append(data)
                except Exception: pass
        alarms.sort(key=lambda x: datetime.fromisoformat(x['time']))
        
        action_name = "request_delete_selection" if is_delete_mode else "list"
        await self.mqtt_client.publish("jarvis/feedback", json.dumps({
            "device": "alarms",
            "status": "success",
            "action": action_name,
            "is_delete_mode": is_delete_mode,
            "alarms": alarms
        }))

        options = []
        if is_delete_mode and alarms: options.append("[ALL] Delete All Alarms")
        for i, a in enumerate(alarms):
            dt = datetime.fromisoformat(a['time'])
            options.append(f"[{i}] {dt.strftime('%m-%d %H:%M')}")
            
        title = "Delete Alarm" if is_delete_mode else "Active Alarms"
        await self.mqtt_client.publish("jarvis/sys/ui_options", json.dumps({
            "title": title,
            "options": options if options else ["No alarms scheduled"]
        }))

    async def handle_alarm_delete(self, alarm_id):
        if not alarm_id: return
        if str(alarm_id).lower().strip() == "all":
            for file in os.listdir(ALARMS_DIR):
                if file.endswith('.json'):
                    aid = file.replace('.json', '')
                    try:
                        subprocess.run(["systemctl", "--user", "stop", f"jarvis-alarm-{aid}.timer"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        subprocess.run(["systemctl", "--user", "stop", f"jarvis-alarm-{aid}.service"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        subprocess.run(["systemctl", "--user", "reset-failed", f"jarvis-alarm-{aid}.*"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception: pass
                    os.remove(os.path.join(ALARMS_DIR, file))
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "All alarms cancelled.", "skip_ducking": True}))
            return

        try:
            subprocess.run(["systemctl", "--user", "stop", f"jarvis-alarm-{alarm_id}.timer"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["systemctl", "--user", "stop", f"jarvis-alarm-{alarm_id}.service"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["systemctl", "--user", "reset-failed", f"jarvis-alarm-{alarm_id}.*"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception: pass
            
        file_path = os.path.join(ALARMS_DIR, f"{alarm_id}.json")
        if os.path.exists(file_path): os.remove(file_path)
        await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "Alarm cancelled.", "skip_ducking": True}))

    async def handle_alarm_create(self, payload):
        raw_input = payload.get("time_str") or payload.get("time") or payload.get("raw_text") or ""
        time_input = self.normalize_time_string(raw_input)
        
        await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "Setting alarm...", "skip_ducking": True}))
        
        def parse_date():
            now = datetime.now()
            dt = dateparser.parse(time_input, languages=['en'], settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now})
            if not dt:
                dates = dateparser.search.search_dates(time_input, languages=['en'], settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now})
                if dates: dt = dates[0][1]
            return dt

        scheduled_time = await asyncio.to_thread(parse_date)
        if not scheduled_time:
            logging.warning(f"[ALARM] Could not parse alarm time from '{time_input}'.")
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "I couldn't figure out the time for that alarm, sir.", "skip_ducking": True}))
            return
            
        if scheduled_time <= datetime.now(): scheduled_time += timedelta(days=1)
            
        alarm_id = str(int(time.time()))
        alarm_data = {
            "id": alarm_id,
            "time": scheduled_time.isoformat(),
            "challenge_type": "phrase",
            "expected_answer": "turn off alarm",
            "tts_prompt": "Wake up, sir! Alarm activated. Please speak the deactivation code to dismiss."
        }
        
        with open(os.path.join(ALARMS_DIR, f"{alarm_id}.json"), "w", encoding="utf-8") as f:
            json.dump(alarm_data, f, indent=2)
            
        success = self.schedule_systemd_timer("jarvis-alarm", "clAlarmTrigger.py", alarm_id, scheduled_time)
        if success:
            display_time = scheduled_time.strftime("%I:%M %p")
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": f"Alarm set for {display_time}.", "skip_ducking": True}))
        else:
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "Failed to set alarm timer.", "skip_ducking": True}))

    # -------------------------------------------------------------------------
    # REMINDER LOGIC
    # -------------------------------------------------------------------------
    async def handle_reminder_list(self, is_delete_mode=False):
        reminders = []
        for file in os.listdir(REMINDERS_DIR):
            if file.endswith('.json'):
                try:
                    with open(os.path.join(REMINDERS_DIR, file), 'r') as f:
                        data = json.load(f)
                        dt = datetime.fromisoformat(data['time'])
                        if dt > datetime.now():
                            reminders.append(data)
                except Exception: pass
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

    async def handle_reminder_delete(self, reminder_id):
        if not reminder_id: return
        try:
            subprocess.run(["systemctl", "--user", "stop", f"jarvis-reminder-{reminder_id}.timer"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["systemctl", "--user", "stop", f"jarvis-reminder-{reminder_id}.service"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["systemctl", "--user", "reset-failed", f"jarvis-reminder-{reminder_id}.*"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception: pass
            
        file_path = os.path.join(REMINDERS_DIR, f"{reminder_id}.json")
        audio_path = os.path.join(REMINDERS_DIR, f"{reminder_id}.mp3")
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(audio_path): os.remove(audio_path)
            
        await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "Reminder cancelled.", "skip_ducking": True}))
        
    async def handle_reminder_create(self, payload):
        raw_time = payload.get("time") or payload.get("raw_text") or payload.get("task") or ""
        time_input = self.normalize_time_string(raw_time)
        reminder_text = payload.get("task") or payload.get("raw_text") or payload.get("time") or "Reminder"
        tmp_audio_path = payload.get("audio_path", "")
        
        await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "Scheduling...", "skip_ducking": True}))
        
        def parse_date():
            now = datetime.now()
            dt = dateparser.parse(time_input, languages=['en'], settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now})
            if not dt:
                dates = dateparser.search.search_dates(time_input, languages=['en'], settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now})
                if dates: dt = dates[0][1]
            return dt

        dt = await asyncio.to_thread(parse_date)
        if not dt:
            logging.warning("Could not parse time from reminder text.")
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "I couldn't figure out the time for that reminder, sir.", "skip_ducking": True}))
            return
            
        reminder_id = payload.get("reminder_id", str(int(time.time())))
        
        audio_dest = os.path.join(REMINDERS_DIR, f"{reminder_id}.mp3")
        copied_user_audio = False
        fallback_reason = "No audio_path provided in payload"
        
        if tmp_audio_path:
            if os.path.exists(tmp_audio_path):
                ext = os.path.splitext(tmp_audio_path)[1]
                if ext:
                    audio_dest = os.path.join(REMINDERS_DIR, f"{reminder_id}{ext}")
                try:
                    import shutil
                    shutil.copy2(tmp_audio_path, audio_dest)
                    logging.info(f"Copied user audio from {tmp_audio_path} to {audio_dest}")
                    copied_user_audio = True
                except Exception as e:
                    fallback_reason = f"Failed to copy user audio: {e}"
                    logging.error(fallback_reason)
                    audio_dest = os.path.join(REMINDERS_DIR, f"{reminder_id}.mp3")
            else:
                fallback_reason = f"Provided audio_path does not exist: {tmp_audio_path}"
        
        if not copied_user_audio:
            logging.info(f"[TTS FALLBACK] Generating TTS for reminder. Reason: {fallback_reason}")
            try:
                import edge_tts
                tts_text = f"Sir, reminder: {reminder_text}"
                communicate = edge_tts.Communicate(tts_text, "en-GB-RyanNeural", rate="+15%", pitch="-5Hz")
                await communicate.save(audio_dest)
            except Exception as e:
                logging.error(f"Failed to generate TTS audio for reminder: {e}")
                audio_dest = ""
            
        meta = {
            "id": reminder_id,
            "text": reminder_text,
            "time": dt.isoformat(),
            "time_created": datetime.now().isoformat(),
            "audio_path": audio_dest
        }
        with open(os.path.join(REMINDERS_DIR, f"{reminder_id}.json"), 'w') as f:
            json.dump(meta, f, indent=2)
            
        success = self.schedule_systemd_timer("jarvis-reminder", "clReminderTrigger.py", reminder_id, dt)
        if success:
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": f"Reminder scheduled for {dt.strftime('%I:%M %p')}.", "skip_ducking": True}))
        else:
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "Failed to schedule the reminder timer.", "skip_ducking": True}))

    # -------------------------------------------------------------------------
    # TODO LOGIC
    # -------------------------------------------------------------------------
    async def handle_todo_create(self, payload):
        task = payload.get("task")
        if not task:
            task = payload.get("raw_text")
        if not task:
            await self.mqtt_client.publish("jarvis/feedback", json.dumps({
                "device": "utilities",
                "status": "success",
                "action": "request_todo_add",
                "message": "What do you want me to add to the to-do list?"
            }))
            return
            
        todo_id = str(int(time.time()))
        todo_data = {
            "id": todo_id,
            "task": task,
            "list_name": payload.get("list_name", "My To-Do List"),
            "time_created": datetime.now().isoformat(),
            "completed": False
        }
        
        with open(os.path.join(TODOS_DIR, f"{todo_id}.json"), "w", encoding="utf-8") as f:
            json.dump(todo_data, f, indent=2)
            
        await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({
            "text": f"Added to your to-do list.",
            "skip_ducking": True
        }))
        await self.handle_todo_list()

    async def handle_todo_delete(self, todo_id):
        if not todo_id: return
        file_path = os.path.join(TODOS_DIR, f"{todo_id}.json")
        if os.path.exists(file_path):
            os.remove(file_path)
        await self.handle_todo_list()

    async def handle_todo_complete(self, todo_id):
        if not todo_id: return
        file_path = os.path.join(TODOS_DIR, f"{todo_id}.json")
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                data["completed"] = True
                with open(file_path, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logging.error(f"Failed to complete todo {todo_id}: {e}")
        await self.handle_todo_list()

    async def handle_todo_list(self):
        todos = []
        for file in os.listdir(TODOS_DIR):
            if file.endswith('.json'):
                try:
                    with open(os.path.join(TODOS_DIR, file), 'r', encoding='utf-8') as f:
                        todos.append(json.load(f))
                except Exception: pass
                
        # Sort by uncompleted first, then by time created
        todos.sort(key=lambda x: (x.get('completed', False), x.get('time_created', '')))
        
        await self.mqtt_client.publish("jarvis/sys/todo/status", json.dumps({
            "status": "success",
            "todos": todos
        }))

    # -------------------------------------------------------------------------
    # CALENDAR LOGIC
    # -------------------------------------------------------------------------
    async def handle_calendar_create(self, payload):
        event_title = payload.get("event") or payload.get("raw_text")
        time_str = payload.get("time_str")
        
        if not event_title:
            await self.mqtt_client.publish("jarvis/feedback", json.dumps({
                "device": "utilities",
                "status": "success",
                "action": "request_calendar_add",
                "message": "What event would you like to schedule?"
            }))
            return
            
        if not time_str:
            await self.mqtt_client.publish("jarvis/feedback", json.dumps({
                "device": "utilities",
                "status": "success",
                "action": "request_calendar_time",
                "event": event_title,
                "message": "When is this event?"
            }))
            return

        time_str = self.normalize_time_string(time_str)
        
        dt_res = dateparser.search.search_dates(time_str, settings={'PREFER_DATES_FROM': 'future'})
        if not dt_res:
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": "I couldn't figure out the time for that event, sir.", "skip_ducking": True}))
            return

        dt = dt_res[0][1]
        event_id = str(payload.get("id")) if payload.get("id") else str(int(time.time()))
        
        # GCal Compatible Schema Prep
        event_data = {
            "id": event_id,
            "summary": event_title,
            "start": {
                "dateTime": dt.isoformat(),
                "timeZone": "Local"
            },
            "end": {
                "dateTime": (dt + timedelta(hours=1)).isoformat(), # Default 1 hr duration
                "timeZone": "Local"
            },
            "description": "",
            "colorId": "1"
        }

        with open(os.path.join(EVENTS_DIR, f"{event_id}.json"), "w", encoding="utf-8") as f:
            json.dump(event_data, f, indent=2)

        # Broadcast UI refresh with updated events array
        await self.handle_calendar_list()

        display_time = dt.strftime('%B %-d at %I:%M %p') if dt.date() != datetime.now().date() else dt.strftime('%I:%M %p')
        await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": f"Event '{event_title}' scheduled for {display_time}.", "skip_ducking": True}))

    async def handle_calendar_delete(self, event_id):
        if not event_id: return
        file_path = os.path.join(EVENTS_DIR, f"{event_id}.json")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logging.error(f"Failed to delete event {event_id}: {e}")
        await self.handle_calendar_list()

    async def handle_calendar_list(self):
        events = []
        for file in os.listdir(EVENTS_DIR):
            if file.endswith('.json'):
                try:
                    with open(os.path.join(EVENTS_DIR, file), 'r', encoding='utf-8') as f:
                        events.append(json.load(f))
                except Exception as e:
                    logging.error(f"Failed to read event {file}: {e}")
                    
        events.sort(key=lambda x: x.get('start', {}).get('dateTime', ''))
        await self.mqtt_client.publish("jarvis/sys/calendar/status", json.dumps({
            "status": "success",
            "events": events
        }))

    async def handle_calendar_briefing(self):
        today_events = []
        now = datetime.now()
        
        for file in os.listdir(EVENTS_DIR):
            if file.endswith('.json'):
                try:
                    with open(os.path.join(EVENTS_DIR, file), 'r', encoding='utf-8') as f:
                        ev = json.load(f)
                        dt = datetime.fromisoformat(ev['start']['dateTime'])
                        if dt.date() == now.date():
                            today_events.append(ev)
                except Exception as e:
                    logging.error(f"Failed to read event {file}: {e}")
                    
        today_events.sort(key=lambda x: datetime.fromisoformat(x['start']['dateTime']))
        
        if not today_events:
            msg = "Good morning sir. You have no events on your calendar for today."
        else:
            msg = f"Good morning sir. You have {len(today_events)} events today. "
            if len(today_events) == 1:
                ev = today_events[0]
                dt = datetime.fromisoformat(ev['start']['dateTime'])
                msg += f"Your only event is '{ev['summary']}' at {dt.strftime('%I:%M %p')}."
            else:
                first_ev = today_events[0]
                dt = datetime.fromisoformat(first_ev['start']['dateTime'])
                msg += f"Your first event is '{first_ev['summary']}' at {dt.strftime('%I:%M %p')}."

        await self.mqtt_client.publish("jarvis/sys/speak", json.dumps({"text": msg, "skip_ducking": True}))

    def normalize_time_string(self, time_input):
        time_input = re.sub(r'\ba\.m\.?', 'am', time_input, flags=re.IGNORECASE)
        time_input = re.sub(r'\bp\.m\.?', 'pm', time_input, flags=re.IGNORECASE)
        time_input = re.sub(r'\b(\d{1,2})\.(\d{2})\s*(am|pm)\b', r'\1:\2 \3', time_input, flags=re.IGNORECASE)
        time_input = re.sub(r'\b([1-9])([0-5]\d)\s*(am|pm)\b', r'\1:\2 \3', time_input, flags=re.IGNORECASE)
        time_input = re.sub(r'\b(1[0-2]|0[1-9])([0-5]\d)\s*(am|pm)\b', r'\1:\2 \3', time_input, flags=re.IGNORECASE)
        time_input = re.sub(r'\b(\d{1,2})\s*(am|pm)\b', r'\1:00 \2', time_input, flags=re.IGNORECASE)
        time_input = re.sub(r'\b(\d{1,2})\s*am\s*(\d{2})\b', r'\1:\2 am', time_input, flags=re.IGNORECASE)
        time_input = re.sub(r'\b(\d{1,2})\s*pm\s*(\d{2})\b', r'\1:\2 pm', time_input, flags=re.IGNORECASE)
        time_input = re.sub(r'\b(\d{1,2})\s+(\d{2})\s*(am|pm)\b', r'\1:\2 \3', time_input, flags=re.IGNORECASE)
        return time_input

if __name__ == "__main__":
    daemon = JarvisUtilities()
    asyncio.run(daemon.run())
