import asyncio
import logging
import json
import os
import uuid
import hashlib
import random
import edge_tts
import av
import numpy as np
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
from pygame import mixer
import aiomqtt
from typing import Dict, Any
import sys

from utils.clConfigLoader import ConfigLoader

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('TTS')

class TTSManager:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.assets_dir = os.path.abspath(os.path.join(self.base_dir, "..", "assets"))
        self.blip_path = os.path.join(self.assets_dir, "blip.mp3")
        self.semaphore = asyncio.Semaphore(1) 
        self.abort_counter = 0
        
        # Load the dynamic responses once at boot
        responses_data = ConfigLoader().load_json("responses.json")
        self.tts_responses = responses_data.get("mqtt", {})
        
        core_data = ConfigLoader().load_json("core.json")
        self.silent_mode = core_data.get("settings", {}).get("silent_mode", False)
        
        mixer.init()
        logging.info("Audio mixer initialized.")
        self.clean_old_cache(max_files=200)

    def clean_old_cache(self, max_files: int = 30) -> None:
        """Removes oldest cached tts_*.mp3 files to prevent assets folder bloat."""
        try:
            tts_files = []
            for f in os.listdir(self.assets_dir):
                if f.startswith("tts_") and f.endswith(".mp3"):
                    full_path = os.path.join(self.assets_dir, f)
                    tts_files.append((full_path, os.path.getmtime(full_path)))
            
            if len(tts_files) > max_files:
                tts_files.sort(key=lambda x: x[1])
                files_to_remove = tts_files[:-max_files]
                for path, _ in files_to_remove:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                logging.info(f"Cleaned up {len(files_to_remove)} old cached TTS files.")
        except Exception as e:
            logging.error(f"Failed to clean TTS cache: {e}")

    def get_audio_rms(self, file_path, suppress_log=False):
        try:
            container = av.open(file_path)
            rms_list = []
            for frame in container.decode(audio=0):
                arr = frame.to_ndarray()
                if arr.size > 0:
                    arr_f = arr.astype(np.float32)
                    if arr.dtype == np.int16:
                        arr_f /= 32768.0
                    elif arr.dtype == np.int32:
                        arr_f /= 2147483648.0
                    rms_list.append(float(np.sqrt(np.mean(arr_f**2))))
                else:
                    rms_list.append(0.0)
            return rms_list
        except Exception as e:
            if not suppress_log:
                logging.error(f"Failed to extract RMS: {e}")
            return []

    async def generate_and_play(self, client, text, voice="en-GB-RyanNeural", ignore_silent=False, abort_count=0) -> None:
        if self.silent_mode and not ignore_silent and not ("alarm" in text.lower()):
            logging.info("[TTS] Silent mode active. Skipping TTS playback.")
            return
            
        file_hash = hashlib.md5(f"{text}_{voice}".encode()).hexdigest()
        temp_file = os.path.join(self.assets_dir, f"tts_{file_hash}.mp3")

        if not os.path.exists(temp_file):
            # edge-tts needs live internet access for every phrase -- retry once
            # on a transient network/DNS blip before giving up. On total failure,
            # still emit the tts_state:idle the daemon waits on to reopen the mic
            # for a pending follow-up, so a dropped connection can't strand it
            # waiting for a completion signal that would otherwise never arrive.
            for attempt in range(2):
                try:
                    communicate = edge_tts.Communicate(text, voice, rate="+27%", pitch="-5Hz")
                    await communicate.save(temp_file)
                    break
                except Exception as e:
                    logging.error(f"[TTS] edge-tts request failed (attempt {attempt + 1}/2): {e}")
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                    else:
                        if client:
                            await client.publish("jarvis/sys/tts_state", json.dumps({"state": "idle"}))
                            await client.publish("jarvis/sys/tts_done", "1")
                        return
        else:
            try:
                os.utime(temp_file, None)
            except Exception:
                pass
        self.clean_old_cache(max_files=200)

        async with self.semaphore:
            if self.abort_counter != abort_count:
                logging.info("[TTS] Task aborted before playing.")
                return
                
            try:
                if client:
                    # Broadcast state to the Daemon so it can handle the ducking
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "active"}))
                    # Give the Daemon 200ms to duck Spotify before we start talking
                    await asyncio.sleep(0.2)

                if os.path.exists(self.blip_path):
                    mixer.music.load(self.blip_path)
                    mixer.music.play()
                    while mixer.music.get_busy(): await asyncio.sleep(0.02)
                
                loaded = False
                for _ in range(5):
                    try:
                        mixer.music.load(temp_file)
                        loaded = True
                        break
                    except Exception: await asyncio.sleep(0.2)
                
                if not loaded:
                    logging.warning(f"Mixer failed to load TTS audio. Deleting corrupted cache: {temp_file}")
                    try: os.remove(temp_file)
                    except Exception: pass
                    return
                
                rms_list = []
                for attempt in range(5):
                    rms_list = self.get_audio_rms(temp_file, suppress_log=(attempt < 4))
                    if rms_list:
                        break
                    await asyncio.sleep(0.2)
                    
                if not rms_list:
                    logging.warning(f"Failed to extract RMS. Deleting corrupted cache: {temp_file}")
                    try: os.remove(temp_file)
                    except Exception: pass
                    return
                frame_duration = 0.024
                
                # --- SILENCE TRUNCATION ALGORITHM ---
                scaled_rms = [0 if np.isnan(r) else min(100, int(r * 500)) for r in rms_list]
                last_valid_idx = 0
                for i in range(len(scaled_rms) - 1, -1, -1):
                    if scaled_rms[i] > 0:
                        last_valid_idx = i
                        break
                
                cutoff_time = (last_valid_idx + 4) * frame_duration
                # ------------------------------------

                mixer.music.play()
                start_time = asyncio.get_event_loop().time()
                
                while mixer.music.get_busy():
                    elapsed = asyncio.get_event_loop().time() - start_time
                    
                    if elapsed >= cutoff_time:
                        mixer.music.stop()
                        break
                        
                    frame_idx = int(elapsed / frame_duration)
                    if frame_idx < len(scaled_rms):
                        val = scaled_rms[frame_idx]
                        if client:
                            await client.publish("jarvis/sys/audio_vol", json.dumps({"rms": val}))
                            
                    await asyncio.sleep(0.04)
                
            finally:
                mixer.music.unload()
                
                if client:
                    await client.publish("jarvis/sys/tts_done", "1")
                    # Broadcast idle so the Daemon knows it is safe to unduck (if mic isn't opening)
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "idle"}))
                    await asyncio.sleep(0.05)

    async def play_audio_file(self, client, file_path: str, abort_count=0) -> None:
        """Plays an existing audio file safely through the TTS queue to avoid ALSA locks."""
        if not os.path.exists(file_path):
            logging.error(f"[TTS] Audio file not found: {file_path}")
            return
            
        async with self.semaphore:
            if self.abort_counter != abort_count:
                logging.info("[TTS] Audio file playback aborted.")
                return
                
            try:
                if client:
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "active"}))
                    await asyncio.sleep(0.2)
                    
                for _ in range(5):
                    try:
                        mixer.music.load(file_path)
                        break
                    except Exception: await asyncio.sleep(0.2)
                    
                rms_list = self.get_audio_rms(file_path)
                
                try:
                    import av
                    with av.open(file_path) as container:
                        true_duration = float(container.duration) / av.time_base
                except Exception:
                    true_duration = len(rms_list) * 0.024
                    
                frame_duration = true_duration / max(1, len(rms_list))
                
                scaled_rms = [0 if np.isnan(r) else min(100, int(r * 500)) for r in rms_list]
                
                if file_path.endswith('.wav'):
                    cutoff_time = true_duration + 1.0 # Never truncate user WAV recordings
                else:
                    last_valid_idx = 0
                    for i in range(len(scaled_rms) - 1, -1, -1):
                        if scaled_rms[i] > 0:
                            last_valid_idx = i
                            break
                    cutoff_time = (last_valid_idx + 4) * frame_duration
                    if cutoff_time < 0.5: cutoff_time = 10.0 # fallback
                
                mixer.music.play()
                start_time = asyncio.get_event_loop().time()
                
                while mixer.music.get_busy():
                    await asyncio.sleep(0.01)
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= cutoff_time:
                        mixer.music.stop()
                        break
                        
                    frame_idx = int(elapsed / frame_duration)
                    if frame_idx < len(scaled_rms):
                        val = scaled_rms[frame_idx]
                        if client:
                            await client.publish("jarvis/sys/audio_vol", json.dumps({"rms": val}))
                            
                    await asyncio.sleep(0.04)
            finally:
                mixer.music.unload()
                if client:
                    await client.publish("jarvis/sys/tts_done", "1")
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "idle"}))


    async def handle_tts_request(self, client, payload: dict, abort_count=0) -> None:
        """Formats dynamic text from templates and sends it to the voice generator."""
        target_topic = payload.get("target_topic", "")
        command = payload.get("command", {})
        action = command.get("action", "")
        
        response_key = action

        if target_topic == "home/room/desk_light/set":
            if "color" in command: response_key = "color"
            elif "lum" in command: response_key = "lum"
            elif "temp" in command: response_key = "temp"
            
        elif target_topic == "pc/spotify/control" and action == "play":
            if "track_name" in command and "artist_name" in command: response_key = "play_track_artist"
            elif "track_name" in command: response_key = "play_track"
            elif "artist_name" in command: response_key = "play_artist"
            elif "playlist_name" in command: response_key = "play_playlist"

        phrases = self.tts_responses.get(target_topic, {}).get(response_key, [])

        if phrases:
            raw_phrase = random.choice(phrases)
            try:
                phrase = raw_phrase.format(**command)
            except KeyError:
                phrase = raw_phrase 
                
            followup_text = payload.get("followup_text")
            if followup_text:
                phrase += f" {followup_text}"

            await self.generate_and_play(client, phrase, abort_count=abort_count)

async def run_tts_service():
    manager = TTSManager()
    attempt = 0
    while True:
        try:
            async with aiomqtt.Client("localhost") as client:
                attempt = 0
                await client.subscribe("jarvis/sys/speak")
                await client.subscribe("jarvis/sys/tts_stop")
                await client.subscribe("jarvis/sys/abort")
                await client.subscribe("jarvis/sys/tts_request")
                await client.subscribe("jarvis/sys/silent_mode")
                await client.subscribe("jarvis/sys/play_audio")
                logging.info("TTS Microservice initialized. Listening on MQTT topics...")
                await client.publish("jarvis/sys/module_ready", json.dumps({"module": "tts"}))
                
                async for message in client.messages:
                    topic = message.topic.value
                    
                    if topic in ["jarvis/sys/tts_stop", "jarvis/sys/abort"]:
                        manager.abort_counter += 1
                        mixer.music.stop()
                        continue

                    if topic == "jarvis/sys/silent_mode":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            manager.silent_mode = payload.get("silent_mode", False)
                            logging.info(f"[TTS] Silent mode updated: {manager.silent_mode}")
                        except json.JSONDecodeError:
                            pass
                        continue
                    
                    if topic == "jarvis/sys/speak":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            asyncio.create_task(manager.generate_and_play(
                                client, 
                                payload.get("text", ""), 
                                ignore_silent=payload.get("ignore_silent", False),
                                abort_count=manager.abort_counter
                            ))
                        except json.JSONDecodeError:
                            logging.error("Received malformed TTS JSON.")
                            
                    elif topic == "jarvis/sys/tts_request":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            asyncio.create_task(manager.handle_tts_request(
                                client, 
                                payload,
                                abort_count=manager.abort_counter
                            ))
                        except json.JSONDecodeError:
                            logging.error("Malformed TTS request JSON.")
                            
                    elif topic == "jarvis/sys/play_audio":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            audio_path = payload.get("path")
                            if audio_path:
                                asyncio.create_task(manager.play_audio_file(
                                    client, 
                                    audio_path,
                                    abort_count=manager.abort_counter
                                ))
                        except json.JSONDecodeError:
                            logging.error("Malformed play_audio JSON.")

        except aiomqtt.MqttError as e:
            delay = min(60, 2 ** attempt)
            logging.error(f"MQTT Connection Error: {e}. Retrying in {delay} seconds...")
            await asyncio.sleep(delay)
            attempt += 1
        except asyncio.CancelledError:
            logging.info("TTS service shutting down.")
            break
        except Exception as e:
            logging.error(f"Service Error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run_tts_service())