import asyncio
import logging
import json
import os
import uuid
import edge_tts
from pygame import mixer
import aiomqtt
from typing import Dict, Any
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [TTS] %(message)s", datefmt="%H:%M:%S")

class TTSManager:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.assets_dir = os.path.abspath(os.path.join(self.base_dir, "..", "assets"))
        self.blip_path = os.path.join(self.assets_dir, "blip.mp3")
        self.semaphore = asyncio.Semaphore(1) 
        
        mixer.init()
        logging.info("Audio mixer initialized.")

    async def generate_and_play(self, client, text, voice="en-GB-RyanNeural", duck_audio=True, request_reply=False) -> None:
        temp_file = os.path.join(self.assets_dir, f"tts_{uuid.uuid4().hex}.mp3")
        
        # 1. Generate in the background (Non-blocking)
        communicate = edge_tts.Communicate(text, voice, rate="+27%", pitch="-5Hz")
        await communicate.save(temp_file)

        # 2. Acquire Semaphore for Playback
        async with self.semaphore:
            try:
                if client and duck_audio:
                    await client.publish("pc/spotify/control", json.dumps({"action": "duck"}))
                    await asyncio.sleep(0.3)

                if os.path.exists(self.blip_path):
                    mixer.music.load(self.blip_path)
                    mixer.music.play()
                    while mixer.music.get_busy(): await asyncio.sleep(0.05)
                
                # Robust Load with Retry
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
                if client and request_reply:
                    await client.publish("jarvis/sys/tts_done", "1")

async def run_tts_service():
    manager = TTSManager()
    try:
        async with aiomqtt.Client("localhost") as client:
            await client.subscribe("jarvis/sys/speak")
            await client.subscribe("jarvis/sys/tts_stop")
            
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
    except Exception as e:
        logging.error(f"Service Error: {e}")
if __name__ == "__main__":
    asyncio.run(run_tts_service())