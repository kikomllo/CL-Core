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
        
        mixer.init()
        logging.info("Audio mixer initialized.")

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

    async def generate_and_play(self, client, text, voice="en-GB-RyanNeural", duck_audio=True, request_reply=False) -> None:
        file_hash = hashlib.md5(f"{text}_{voice}".encode()).hexdigest()
        temp_file = os.path.join(self.assets_dir, f"tts_{file_hash}.mp3")

        if not os.path.exists(temp_file):
            communicate = edge_tts.Communicate(text, voice, rate="+27%", pitch="-5Hz")
            await communicate.save(temp_file)

        async with self.semaphore:
            try:
                if client and duck_audio:
                    await client.publish("pc/spotify/control", json.dumps({"action": "duck", "silent": True}))
                    await asyncio.sleep(0.3)

                if client:
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "active"}))

                if os.path.exists(self.blip_path):
                    mixer.music.load(self.blip_path)
                    mixer.music.play()
                    while mixer.music.get_busy(): await asyncio.sleep(0.05)
                
                for _ in range(5):
                    try:
                        mixer.music.load(temp_file)
                        break
                    except Exception: await asyncio.sleep(0.2)
                
                rms_list = self.get_audio_rms(temp_file)
                frame_duration = 0.024
                
                # --- SILENCE TRUNCATION ALGORITHM ---
                # Pre-calculate scaled RMS values
                scaled_rms = [min(100, int(r * 500)) for r in rms_list]
                # Scan backwards to find the last frame that actually contains sound
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
                    
                    # Kill the playback instantly when we hit the trailing silence
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

                # --- NEW: DO NOT UNDUCK IF THE MIC IS ABOUT TO OPEN ---
                if client and duck_audio and not request_reply:
                    await client.publish("pc/spotify/control", json.dumps({"action": "unduck", "silent": True}))
                
                if client:
                    await client.publish("jarvis/sys/tts_done", "1")
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "idle"}))
                    if request_reply:
                        await client.publish("jarvis/sys/mic_control", json.dumps({"action": "request_reply"}))

    async def handle_tts_request(self, client, payload: dict) -> None:
        """Formats dynamic text from templates and sends it to the voice generator."""
        target_topic = payload.get("target_topic", "")
        command = payload.get("command", {})
        action = command.get("action", "")
        
        response_key = action

        # Contextual mapping
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

            await self.generate_and_play(client, phrase, duck_audio=True, request_reply=request_reply)

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
                
                logging.info("TTS Microservice initialized. Listening on MQTT topics...")
                
                async for message in client.messages:
                    topic = message.topic.value
                    
                    if topic == "jarvis/sys/tts_stop":
                        mixer.music.stop()
                        continue
                    
                    if topic == "jarvis/sys/speak":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            await manager.generate_and_play(
                                client, 
                                payload.get("text", ""), 
                                duck_audio=not payload.get("skip_ducking", False),
                                request_reply=payload.get("request_reply", False)
                            )
                        except json.JSONDecodeError:
                            logging.error("Received malformed TTS JSON.")
                            
                    elif topic == "jarvis/sys/tts_request":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            await manager.handle_tts_request(client, payload)
                        except json.JSONDecodeError:
                            logging.error("Malformed TTS request JSON.")

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