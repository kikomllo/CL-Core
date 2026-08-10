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
        core_data = ConfigLoader().load_json("core.json")
        config = core_data.get("settings", {})
        self.abort_keywords = core_data.get("nlp_rules", {}).get("abort_keywords", ["abort", "cancel", "nevermind"])
        self.abort_flag = False
        
        # Backwards compatibility and new settings
        langs = config.get("languages", config.get("language", "en"))
        if isinstance(langs, str):
            langs = [langs]
        # Limit to 2 languages as per user request
        self.languages = langs[:2]
        self.processing_mode = config.get("stt_processing_mode", "parallel")
        
        size = config.get("stt_model", "base")
        hw = config.get("hardware", "cpu").lower()
        
        device = "cuda" if hw in ["gpu", "cuda"] else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        
        self.model_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
        os.makedirs(self.model_dir, exist_ok=True)
        
        num_workers = len(self.languages) if self.processing_mode == "parallel" else 1
        logging.info(f"Loading Whisper '{size}' into {device.upper()} memory (Workers: {num_workers}, Languages: {self.languages})...")
        self.model = WhisperModel(size, device=device, compute_type=compute, download_root=self.model_dir, num_workers=num_workers)
        logging.info("Model loaded. Waiting for audio arrays over MQTT...")

    def transcribe(self, audio_array: np.ndarray) -> str:
        import concurrent.futures
        
        def run_inference(lang):
            args = {
                "audio": audio_array,
                "beam_size": 2,
                "vad_filter": True,
                "condition_on_previous_text": False, # Hallucination prevention parameter
                "initial_prompt": "",
                "vad_parameters": dict(min_silence_duration_ms=500),
                "language": lang
            }
            if lang != "en":
                args["task"] = "translate"
                
            segments, info = self.model.transcribe(**args)
            segments_list = list(segments)
            text_parts = [s.text for s in segments_list]
            text = "".join(text_parts).strip()
            
            # Calculate average logprob to measure confidence
            if not segments_list:
                avg_logprob = -999.0
            else:
                avg_logprob = sum(s.avg_logprob for s in segments_list) / len(segments_list)
                
            return {"lang": lang, "text": text, "segments": segments_list, "confidence": avg_logprob}
            
        results = []
        if self.processing_mode == "parallel" and len(self.languages) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.languages)) as executor:
                futures = [executor.submit(run_inference, lang) for lang in self.languages]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        results.append(future.result())
                    except Exception as e:
                        logging.error(f"Inference error on thread: {e}")
        else:
            for lang in self.languages:
                try:
                    results.append(run_inference(lang))
                except Exception as e:
                    logging.error(f"Inference error: {e}")
                    
        if not results:
            return ""
            
        # Pick the result with the highest confidence
        best_result = max(results, key=lambda x: x["confidence"])
        
        # Run abort check on the best segments
        for s in best_result["segments"]:
            segment_lower = s.text.lower()
            if any(kw in segment_lower for kw in self.abort_keywords):
                logging.warning(f"Instant Abort triggered on segment: '{s.text}'. Halting inference!")
                return "abort"
                
        text = best_result["text"]
        
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
    audio_queue = asyncio.Queue()
    
    async def mqtt_listener():
        attempt = 0
        while True:
            try:
                async with aiomqtt.Client("localhost") as client:
                    attempt = 0
                    await client.publish("jarvis/sys/whisper_state", json.dumps({"state": "ready"}), retain=True)
                    await client.subscribe("jarvis/sys/audio_process")
                    await client.subscribe("jarvis/sys/abort")
                    
                    async for message in client.messages:
                        topic = message.topic.value
                        payload = json.loads(message.payload.decode('utf-8'))
                        
                        if topic == "jarvis/sys/abort":
                            engine.abort_flag = True
                            while not audio_queue.empty():
                                audio_queue.get_nowait()
                            continue
                            
                        if "audio_b64" not in payload:
                            continue
                        
                        audio_bytes = base64.b64decode(payload["audio_b64"])
                        audio_array = np.frombuffer(audio_bytes, dtype=np.float32)
                        await audio_queue.put(audio_array)
            except Exception as e:
                delay = min(60, 2 ** attempt)
                logging.error(f"MQTT Listener Error: {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                attempt += 1

    async def inference_worker():
        while True:
            audio_array = await audio_queue.get()
            engine.abort_flag = False
            
            # Run inference safely in the background
            text = await asyncio.to_thread(engine.transcribe, audio_array)
            
            if engine.abort_flag:
                logging.info("Inference aborted mid-way. Discarding result.")
                continue
                
            try:
                async with aiomqtt.Client("localhost") as publish_client:
                    if text:
                        logging.info(f"Transcription: '{text}'")
                        await publish_client.publish("jarvis/sensor/voice", text)
                    else:
                        logging.info(f"Transcription: ''")
                        await publish_client.publish("jarvis/sys/audio_process", json.dumps({"state": "idle"}))
            except Exception as e:
                logging.error(f"Failed to publish transcription: {e}")

    await asyncio.gather(mqtt_listener(), inference_worker())

if __name__ == "__main__":
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())