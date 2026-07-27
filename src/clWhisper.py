import sys
import json
import base64
import numpy as np
import logging
import asyncio
import aiomqtt
from faster_whisper import WhisperModel

from utils.clConfigLoader import ConfigLoader

import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('WHISPER')

class InferenceEngine:
    def __init__(self):
        config = ConfigLoader().load_json("core.json").get("settings", {})
        self.language = config.get("language", "auto")
        size = config.get("stt_model", "base")
        hw = config.get("hardware", "cpu").lower()
        
        device = "cuda" if hw in ["gpu", "cuda"] else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        
        logging.info(f"Loading Whisper '{size}' into {device.upper()} memory...")
        self.model = WhisperModel(size, device=device, compute_type=compute)
        logging.info("Model loaded. Waiting for audio arrays over MQTT...")

    def transcribe(self, audio_array: np.ndarray) -> str:
        args = {
            "audio": audio_array,
            "beam_size": 2,
            "vad_filter": True,
            "condition_on_previous_text": False, # Hallucination prevention parameter
            "initial_prompt": "",
            "vad_parameters": dict(min_silence_duration_ms=500)
        }
        if self.language != "auto": 
            args["language"] = self.language
            
        segments, _ = self.model.transcribe(**args)
        text = "".join([s.text for s in segments]).strip()
        
        # --- WHISPER HALLUCINATION TRAP ---
        lower_text = text.lower()
        known_hallucinations = [
            "thank you for watching", 
            "like and subscribe", 
            "be careful with this video", 
            "see you in the next", 
            "take care", 
            "bye bye", 
            "amara.org", 
            "thanks for watching",
            "subscribe to the channel"
        ]
        
        if any(h in lower_text for h in known_hallucinations):
            logging.warning(f"Whisper Hallucination intercepted and purged: '{text}'")
            return ""
            
        return text

async def main():
    engine = InferenceEngine()
    
    attempt = 0
    while True:
        try:
            async with aiomqtt.Client("localhost") as client:
                attempt = 0
                await client.subscribe("jarvis/sys/audio_process")
                
                async for message in client.messages:
                    payload = json.loads(message.payload.decode('utf-8'))
                    
                    # Reconstruct the numpy array from the Base64 MQTT message
                    audio_bytes = base64.b64decode(payload["audio_b64"])
                    audio_array = np.frombuffer(audio_bytes, dtype=np.float32)
                    
                    # Run inference safely in the background
                    text = await asyncio.to_thread(engine.transcribe, audio_array)
                    
                    if text:
                        logging.info(f"Transcription: '{text}'")
                        await client.publish("jarvis/sensor/voice", text)
                    else:
                        await client.publish("jarvis/sys/audio_process", json.dumps({"state": "idle"}))
                        
        except Exception as e:
            delay = min(60, 2 ** attempt)
            logging.error(f"Inference Engine Error: {e}. Retrying in {delay}s...")
            await asyncio.sleep(delay)
            attempt += 1

if __name__ == "__main__":
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())