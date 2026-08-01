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
        
        # Load the dynamic responses once at boot
        responses_data = ConfigLoader().load_json("responses.json")
        self.tts_responses = responses_data.get("mqtt", {})
        
        core_data = ConfigLoader().load_json("core.json")
        self.silent_mode = core_data.get("settings", {}).get("silent_mode", False)
        
        mixer.init()
        logging.info("Audio mixer initialized.")
        self.clean_old_cache(max_files=30)

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

    def get_audio_rms(self, file_path):
        try:
            container = av.open(file_path)
            rms_list = []
            for frame in container.decode(audio=0):
                arr = frame.to_ndarray()
                if arr.size > 0:
                    rms_list.append(float(np.sqrt(np.mean(arr**2))))
                else:
                    rms_list.append(0.0)
            return rms_list
        except Exception as e:
            logging.error(f"Failed to extract RMS: {e}")
            return []

    async def generate_and_play(self, client, text, voice="en-GB-RyanNeural", request_reply=False, ignore_silent=False) -> None:
        if self.silent_mode and not ignore_silent and not ("alarm" in text.lower()):
            logging.info("[TTS] Silent mode active. Skipping TTS playback.")
            if request_reply and client:
                await client.publish("jarvis/sys/mic_control", json.dumps({"action": "request_reply"}))
            return
            
        file_hash = hashlib.md5(f"{text}_{voice}".encode()).hexdigest()
        temp_file = os.path.join(self.assets_dir, f"tts_{file_hash}.mp3")

        if not os.path.exists(temp_file):
            communicate = edge_tts.Communicate(text, voice, rate="+27%", pitch="-5Hz")
            await communicate.save(temp_file)
            self.clean_old_cache(max_files=30)

        async with self.semaphore:
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
                
                for _ in range(5):
                    try:
                        mixer.music.load(temp_file)
                        break
                    except Exception: await asyncio.sleep(0.2)
                
                rms_list = self.get_audio_rms(temp_file)
                frame_duration = 0.024
                
                # --- SILENCE TRUNCATION ALGORITHM ---
                scaled_rms = [min(100, int(r * 500)) for r in rms_list]
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
                    if request_reply:
                        await client.publish("jarvis/sys/mic_control", json.dumps({"action": "request_reply"}))

    async def play_audio_file(self, client, file_path: str) -> None:
        """Plays an existing audio file safely through the TTS queue to avoid ALSA locks."""
        if not os.path.exists(file_path):
            logging.error(f"[TTS] Audio file not found: {file_path}")
            return
            
        async with self.semaphore:
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
                frame_duration = 0.024
                
                scaled_rms = [min(100, int(r * 500)) for r in rms_list]
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


    async def handle_tts_request(self, client, payload: dict) -> None:
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
                
            request_reply = False
            if payload.get("append_followup"):
                phrase += " Anything else sir?"
                request_reply = True

            await self.generate_and_play(client, phrase, request_reply=request_reply)

async def run_tts_service():
    manager = TTSManager()
    attempt = 0
    while True:
        try:
            async with aiomqtt.Client("localhost") as client:
                attempt = 0
                await client.subscribe("jarvis/sys/speak")
                await client.subscribe("jarvis/sys/tts_stop")
                await client.subscribe("jarvis/sys/tts_request")
                await client.subscribe("jarvis/sys/silent_mode")
                await client.subscribe("jarvis/sys/play_audio")
                
                logging.info("TTS Microservice initialized. Listening on MQTT topics...")
                
                async for message in client.messages:
                    topic = message.topic.value
                    
                    if topic == "jarvis/sys/tts_stop":
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
                            await manager.generate_and_play(
                                client, 
                                payload.get("text", ""), 
                                request_reply=payload.get("request_reply", False),
                                ignore_silent=payload.get("ignore_silent", False) or payload.get("skip_ducking", False)
                            )
                        except json.JSONDecodeError:
                            logging.error("Received malformed TTS JSON.")
                            
                    elif topic == "jarvis/sys/tts_request":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            await manager.handle_tts_request(client, payload)
                        except json.JSONDecodeError:
                            logging.error("Malformed TTS request JSON.")
                            
                    elif topic == "jarvis/sys/play_audio":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            audio_path = payload.get("path")
                            if audio_path:
                                # Run in background so it doesn't block the MQTT loop, just like TTS
                                asyncio.create_task(manager.play_audio_file(client, audio_path))
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