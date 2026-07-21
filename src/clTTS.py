import asyncio
import logging
import json
import os
import uuid
import random
import edge_tts
from pygame import mixer
import aiomqtt
from typing import Dict, Any
import sys

from utils.clConfigLoader import ConfigLoader

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="\r\033[K[%(asctime)s] [TTS] %(message)s", datefmt="%H:%M:%S")

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

    async def generate_and_play(self, client, text, voice="en-GB-RyanNeural", duck_audio=True, request_reply=False) -> None:
        temp_file = os.path.join(self.assets_dir, f"tts_{uuid.uuid4().hex}.mp3")
        
        communicate = edge_tts.Communicate(text, voice, rate="+27%", pitch="-5Hz")
        await communicate.save(temp_file)

        async with self.semaphore:
            try:
                if client and duck_audio:
                    await client.publish("pc/spotify/control", json.dumps({"action": "duck"}))
                    await asyncio.sleep(0.3)

                if os.path.exists(self.blip_path):
                    mixer.music.load(self.blip_path)
                    mixer.music.play()
                    while mixer.music.get_busy(): await asyncio.sleep(0.05)
                
                for _ in range(5):
                    try:
                        mixer.music.load(temp_file)
                        break
                    except Exception: await asyncio.sleep(0.2)
                
                mixer.music.play()
                while mixer.music.get_busy(): await asyncio.sleep(0.05)
                
            finally:
                mixer.music.unload()
                if os.path.exists(temp_file):
                    try: os.remove(temp_file)
                    except Exception: pass
                
                if client and duck_audio:
                    await client.publish("pc/spotify/control", json.dumps({"action": "unduck"}))
                
                if client:
                    await client.publish("jarvis/sys/tts_done", "1")

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
                
            await self.generate_and_play(client, phrase, duck_audio=True)

async def run_tts_service():
    manager = TTSManager()
    while True:
        try:
            async with aiomqtt.Client("localhost") as client:
                await client.subscribe("jarvis/sys/speak")
                await client.subscribe("jarvis/sys/tts_stop")
                await client.subscribe("jarvis/sys/tts_request") # NEW SUB
                
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
            logging.error(f"MQTT Connection Error: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logging.info("TTS service shutting down.")
            break
        except Exception as e:
            logging.error(f"Service Error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run_tts_service())