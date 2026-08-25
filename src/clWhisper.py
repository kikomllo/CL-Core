import sys
import json
import base64
import numpy as np
import logging
import asyncio
import aiomqtt

import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))

if sys.platform == "win32":
    try:
        # CTranslate2 needs CUDA DLLs in the PATH on Windows.
        # Adding them via os.add_dll_directory fixes the "Library cublas64_12.dll is not found" error.
        for path in sys.path:
            cublas_bin = os.path.join(path, "nvidia", "cublas", "bin")
            cudnn_bin = os.path.join(path, "nvidia", "cudnn", "bin")
            if os.path.exists(cublas_bin):
                os.add_dll_directory(cublas_bin)
            if os.path.exists(cudnn_bin):
                os.add_dll_directory(cudnn_bin)
    except Exception as e:
        pass

from faster_whisper import WhisperModel
from utils.clConfigLoader import ConfigLoader
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
        self.model_size = size
        hw = config.get("hardware", "cpu").lower()
        
        device = "cuda" if hw in ["gpu", "cuda"] else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        
        self.model_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
        os.makedirs(self.model_dir, exist_ok=True)
        
        num_workers = len(self.languages) if self.processing_mode == "parallel" else 1
        logging.info(f"Loading Whisper '{size}' into {device.upper()} memory (Workers: {num_workers}, Languages: {self.languages})...")
        
        try:
            self.model = WhisperModel(size, device=device, compute_type=compute, download_root=self.model_dir, num_workers=num_workers)
        except Exception as e:
            if "incomplete" in str(e).lower() or "corrupt" in str(e).lower():
                logging.warning(f"Detected corrupted model cache for size '{size}'. Wiping cache and retrying download...")
                import shutil
                
                # Faster-whisper uses huggingface hub cache format by default: "models--Systran--faster-whisper-{size}"
                target_cache_dir = os.path.join(self.model_dir, f"models--Systran--faster-whisper-{size}")
                target_lock_dir = os.path.join(self.model_dir, ".locks", f"models--Systran--faster-whisper-{size}")
                
                for d in [target_cache_dir, target_lock_dir]:
                    if os.path.exists(d):
                        try:
                            shutil.rmtree(d)
                        except Exception as rm_e:
                            logging.error(f"Failed to remove corrupted directory {d}: {rm_e}")
                
                logging.info(f"Retrying WhisperModel download for '{size}'...")
                self.model = WhisperModel(size, device=device, compute_type=compute, download_root=self.model_dir, num_workers=num_workers)
            else:
                if device == "cuda":
                    logging.error(f"GPU initialization failed ({e}). Falling back to CPU...")
                    device = "cpu"
                    compute = "int8"
                    self.model = WhisperModel(size, device=device, compute_type=compute, download_root=self.model_dir, num_workers=num_workers)
                else:
                    raise e
                    
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
                
            try:
                segments, info = self.model.transcribe(**args)
                segments_list = list(segments)
            except Exception as e:
                if "cublas" in str(e).lower() or "cudnn" in str(e).lower() or "cuda" in str(e).lower():
                    logging.error(f"GPU inference failed ({e}). Forcing CPU fallback...")
                    self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8", download_root=self.model_dir, num_workers=1)
                    segments, info = self.model.transcribe(**args)
                    segments_list = list(segments)
                else:
                    raise e
                    
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
                    await client.publish("jarvis/sys/module_ready", json.dumps({"module": "whisper"}), retain=False)
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
            
            # Save the latest audio buffer for commands that need it (like reminders)
            import wave
            import uuid
            scratch_dir = os.path.abspath(os.path.join(engine.model_dir, "..", "data", "scratch"))
            os.makedirs(scratch_dir, exist_ok=True)
            unique_id = str(uuid.uuid4())[:8]
            wav_path = os.path.join(scratch_dir, f"voice_command_{unique_id}.wav")
            try:
                # Normalize volume to match TTS loudness (Target RMS ~ 0.15)
                current_rms = np.sqrt(np.mean(audio_array**2))
                if current_rms > 0.001:
                    gain = 0.15 / current_rms
                    # Limit gain to prevent blowing up background noise, and prevent extreme ducking
                    gain = max(0.5, min(gain, 5.0))
                    norm_audio = np.clip(audio_array * gain, -1.0, 1.0)
                else:
                    norm_audio = audio_array
                    
                audio_int16 = (norm_audio * 32767).astype(np.int16)
                with wave.open(wav_path, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(audio_int16.tobytes())
            except Exception as e:
                logging.error(f"Failed to save {wav_path}: {e}")
                wav_path = ""
            
            # Run inference safely in the background
            text = await asyncio.to_thread(engine.transcribe, audio_array)
            
            if engine.abort_flag:
                logging.info("Inference aborted mid-way. Discarding result.")
                continue
                
            try:
                async with aiomqtt.Client("localhost") as publish_client:
                    if text:
                        logging.info(f"Transcription: '{text}'")
                        payload = json.dumps({"text": text, "audio_path": wav_path})
                        await publish_client.publish("jarvis/sensor/voice", payload)
                    else:
                        logging.info(f"Transcription: ''")
                        await publish_client.publish("jarvis/sys/audio_process", json.dumps({"state": "idle"}))
            except Exception as e:
                logging.error(f"Failed to publish transcription: {e}")

    await asyncio.gather(mqtt_listener(), inference_worker())

if __name__ == "__main__":
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.exceptions.CancelledError):
        pass