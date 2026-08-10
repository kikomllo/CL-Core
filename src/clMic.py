import os
import sys
import json
import base64
import numpy as np
import pyaudio
import logging
import collections
import time
import threading
import wave
import paho.mqtt.publish as publish
import paho.mqtt.client as mqtt_client
from typing import Dict, Any, Tuple, Optional, Deque
from contextlib import contextmanager
from ctypes import CFUNCTYPE, c_char_p, c_int, cdll

# --- OPTIMIZATION FLAGS ---
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["TFLITE_NUM_THREADS"] = "1"

def py_error_handler(filename, line, function, err, fmt): pass
try:
    ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
    c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
    asound = cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except Exception: pass

import warnings
warnings.filterwarnings("ignore")
logging.getLogger("httpx").setLevel(logging.WARNING)

import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('MIC')

@contextmanager
def silence_c_errors():
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(2)
    sys.stderr.flush()
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(old_stderr, 2)
        os.close(old_stderr)
        os.close(devnull)

class VoiceSensor:
    """Lightweight front-end for VAD and Audio Capture."""
    FORMAT: int = pyaudio.paInt16
    CHANNELS: int = 1
    RATE: int = 16000
    CHUNK: int = 1280
    MAX_RECORD_SECONDS: int = 10
    INITIAL_SILENCE_SECONDS: float = 3.0
    SILENCE_LIMIT_SECONDS: float = 0.8

    MIN_BASELINE: int = 850              
    VOICE_ACT_BUFFER: float = 1.40  
    SILENCE_CUT_BUFFER: float = 1.25    
    MAX_CEILING_BUFFER: float = 3.00        

    def __init__(self, target_word: str = "hey_jarvis"):
        self.target_word: str = target_word
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        
        self.tts_busy: bool = False
        self.tts_queue_count: int = 0  
        self.already_spoke: bool = False
        self.ambient_noise_buffer: Deque[float] = collections.deque(maxlen=100)
        self.fast_ema: Optional[float] = None
        self.slow_ema: Optional[float] = None
        self.tts_lock_time: float = 0.0
        
        self.active_window_end: float = 0.0
        self.pending_active_window: bool = False
        self.awaiting_reply: bool = False
        self.attention_mode: bool = False
        
        self.is_processing: bool = False
        self.processing_timer: float = 0.0
        
        self.system_ready: bool = False
        self.ptt_active: bool = False
        
        self.vad_hangtime: int = 0
        self.pre_speech_buffer: Deque[np.ndarray] = collections.deque(maxlen=15)
        self.ring_buffer: Deque[np.ndarray] = collections.deque(maxlen=30) # <--- ADD THIS HERE
        self.last_vol_publish: float = 0.0
        
        self.attention_multiplier = self._load_attention_multiplier()
        
        with silence_c_errors():
            self.audio: pyaudio.PyAudio = pyaudio.PyAudio()
            
        self.mic_stream: Optional[pyaudio.Stream] = None
        self.oww_model = self._init_wakeword()
        self.mqtt: mqtt_client.Client = self._init_mqtt()

    def _load_attention_multiplier(self) -> float:
        config_path = os.path.abspath(os.path.join(self.base_dir, "..", "config", "core.json"))
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return float(json.load(f).get("vad_settings", {}).get("attention_mode_multiplier", 1.50))
        except Exception:
            return 1.50

    def _init_wakeword(self) -> Any:
        logging.info("Booting Wake Word Engine...")
        import openwakeword
        from openwakeword.model import Model
        
        custom_model_dir = os.path.join(self.base_dir, "..", "config", "models")
        os.makedirs(custom_model_dir, exist_ok=True)
        custom_path = os.path.join(custom_model_dir, f"{self.target_word}.onnx")
        
        if os.path.exists(custom_path):
            jarvis_path = custom_path
            logging.info(f"Loaded custom wake word model from: {custom_path}")
        else:
            available_models = openwakeword.get_pretrained_model_paths()
            jarvis_path = next((path for path in available_models if self.target_word in path), None)
            
        if not jarvis_path:
            logging.critical(f"Error: '{self.target_word}.onnx' not found in {custom_model_dir} or pre-trained models!")
            sys.exit(1)
            
        try:
            return Model(wakeword_models=[jarvis_path])
        except TypeError:
            return Model(wakeword_model_paths=[jarvis_path])

    def _init_mqtt(self) -> mqtt_client.Client:
        client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1)
        client.on_message = self._on_mqtt_message
        
        try:
            client.connect("localhost", 1883, 60)
            client.subscribe("jarvis/sys/speak")
            client.subscribe("jarvis/sys/mic_open")
            client.subscribe("jarvis/sys/mic_control")
            client.subscribe("jarvis/sys/tts_state") 
            client.subscribe("jarvis/sys/whisper_state")
            client.subscribe("jarvis/sys/audio_process")
            client.loop_start()
            return client
        except Exception as e:
            logging.critical(f"Failed to connect to MQTT broker: {e}")
            sys.exit(1)
            
    def _on_mqtt_message(self, client: mqtt_client.Client, userdata: Any, msg: mqtt_client.MQTTMessage) -> None:
        payload_str = msg.payload.decode('utf-8')
        payload = {}
        if payload_str.strip().startswith("{"):
            try: payload = json.loads(payload_str)
            except json.JSONDecodeError: pass
        else:
            payload = {"action": payload_str}

        action = payload.get("action")

        if msg.topic == "jarvis/sys/audio_process":
            if payload.get("state") == "idle":
                self.is_processing = False

        elif msg.topic == "jarvis/sys/tts_state":
            state = payload.get("state")
            self.is_processing = False
            if state == "active":
                self.tts_busy = True
                self.tts_lock_time = time.time()
            elif state == "idle":
                self.tts_busy = False
                self.oww_model.reset()
                self.pre_speech_buffer.clear()
                self.vad_hangtime = 0

                try:
                    available = self.mic_stream.get_read_available()
                    if available > 0: self.mic_stream.read(available, exception_on_overflow=False)
                except Exception: pass

                logging.info("TTS pipeline clear. Releasing microphone lock.")
        
        elif msg.topic == "jarvis/sys/whisper_state":
            if payload.get("state") == "ready":
                self.system_ready = True
                logging.info("Whisper Engine handshake received. Unlocking microphone.")
                
        elif msg.topic == "jarvis/sys/mic_open" or action == "open_window":
            self.pending_active_window = True
            
        elif action == "ptt_start":
            self.ptt_active = True
            self.pending_active_window = True
            logging.info("PTT Started - Forcing active listening.")
            
        elif action == "ptt_stop":
            self.ptt_active = False
            logging.info("PTT Stopped.")
            
        elif action == "ptt_toggle":
            self.ptt_active = not self.ptt_active
            if self.ptt_active:
                self.pending_active_window = True
                logging.info("PTT Toggled ON - Forcing active listening.")
            else:
                logging.info("PTT Toggled OFF.")
            
        elif action == "request_reply":
            self.tts_busy = False
            self.awaiting_reply = True
            self.ambient_noise_buffer.clear()
            self.oww_model.reset()
            self.pre_speech_buffer.clear()
            self.vad_hangtime = 0
            
        elif action == "attention_on":
            self.attention_mode = True
            self.ambient_noise_buffer.clear()
            self.oww_model.reset()
            self.pre_speech_buffer.clear()
            self.vad_hangtime = 0
            
        elif action == "attention_off":
            self.attention_mode = False
            logging.info("Attention Mode DEACTIVATED.")

    def _publish(self, topic: str, payload: Any) -> None:
        try:
            data = json.dumps(payload) if isinstance(payload, dict) else payload
            self.mqtt.publish(topic, data)
        except Exception as e:
            logging.warning(f"MQTT Publish Error on '{topic}': {e}")

    def _calculate_thresholds(self) -> Tuple[float, float, float]:
        if len(self.ambient_noise_buffer) > 10:
            true_background = list(self.ambient_noise_buffer)[:-5]
            baseline = float(np.percentile(true_background, 75))
        else:
            baseline = float(np.percentile(self.ambient_noise_buffer, 75)) if self.ambient_noise_buffer else float(self.MIN_BASELINE)
            
        baseline = max(baseline, self.MIN_BASELINE)
        
        act_mod = self.VOICE_ACT_BUFFER
        sil_mod = self.SILENCE_CUT_BUFFER
        ceil_mod = self.MAX_CEILING_BUFFER
        
        if self.attention_mode:
            act_mod *= self.attention_multiplier
            sil_mod *= self.attention_multiplier
            ceil_mod *= self.attention_multiplier
            
        activation = min(baseline * act_mod, baseline * ceil_mod)
        silence = min(baseline * sil_mod, baseline * ceil_mod)
        
        return baseline, activation, silence

    def _record_command(self, act_thresh: float, sil_thresh: float, bypass_wakeword: bool, pass_pre_frames: list = None, is_already_speaking: bool = False, wakeword_triggered: bool = False) -> str:
        if self.tts_busy: 
            if not self.attention_mode: self._publish("jarvis/sys/mic_state", {"state": "idle"})
            return ""

        # --- Dynamic wait limit for standalone wake words ---
        if wakeword_triggered and not is_already_speaking:
            wait_limit = 0.8
        elif bypass_wakeword:
            wait_limit = 5.0
        else:
            wait_limit = self.INITIAL_SILENCE_SECONDS
        
        if not self.attention_mode:
            logging.info(f"LISTENING... (Trigger: >{act_thresh:.0f} | Cutoff: <{sil_thresh:.0f} | Timeout: {wait_limit}s)")
            self._publish("jarvis/sys/mic_state", {"state": "listening"})
        
        frames = pass_pre_frames if pass_pre_frames else []
        max_chunks = int(self.RATE / self.CHUNK * self.MAX_RECORD_SECONDS)
        silence_limit_chunks = int(self.RATE / self.CHUNK * self.SILENCE_LIMIT_SECONDS)
        wait_limit_chunks = int(self.RATE / self.CHUNK * wait_limit)
        
        silence_counter, wait_counter = 0, 0
        started_speaking = is_already_speaking
        
        last_rms = 0
        flatline_counter = 0
        FLATLINE_LIMIT_CHUNKS = int(self.RATE / self.CHUNK * 1.5)
        was_ptt = getattr(self, "ptt_active", False)
        
        try:
            available = self.mic_stream.get_read_available()
            if available > 0: self.mic_stream.read(available, exception_on_overflow=False)
        except Exception: pass

        for _ in range(max_chunks):
            # --- FIX: Ensure we reset mic_state to idle if interrupted by TTS ---
            if self.tts_busy: 
                if not self.attention_mode: self._publish("jarvis/sys/mic_state", {"state": "idle"})
                return ""

            data = self.mic_stream.read(self.CHUNK, exception_on_overflow=False)
            audio_chunk = np.frombuffer(data, dtype=np.int16)
            frames.append(audio_chunk) 
            
            audio_float = audio_chunk.astype(np.float32)
            vad_audio = np.append(audio_float[0], audio_float[1:] - 0.95 * audio_float[:-1])
            raw_rms = np.sqrt(np.mean(np.square(vad_audio)))
            rms = ((raw_rms / 500.0) ** 1.5) * 500.0 if raw_rms > 0 else 0
            
            bar_length = int(max(0, min(rms / 100, 40))) 
            meter = "█" * bar_length + "-" * (40 - bar_length)
            self._publish("jarvis/sys/volume", {
                "rms": int(rms),
                "bar": meter,
                "status": "RECORDING"
            })
            
            if not started_speaking:
                if rms > act_thresh:
                    started_speaking, silence_counter = True, 0
                    if not self.attention_mode:
                        self._publish("jarvis/sys/mic_state", {"state": "recording"})
                else:
                    wait_counter += 1
            else:
                rms_delta = abs(rms - last_rms)
                
                if rms_delta < (act_thresh * 0.15): 
                    flatline_counter += 1
                else:
                    flatline_counter = 0
                
                if rms < sil_thresh: 
                    silence_counter += 1
                else: 
                    silence_counter = 0
                    
            if getattr(self, "ptt_active", False):
                started_speaking, silence_counter, flatline_counter = True, 0, 0
                was_ptt = True
            elif was_ptt:
                logging.info("PTT Released -> Breaking recording loop.")
                break
                    
            last_rms = rms

            if started_speaking and (silence_counter >= silence_limit_chunks or flatline_counter >= FLATLINE_LIMIT_CHUNKS):
                if not self.attention_mode:
                    reason = "Silence" if silence_counter >= silence_limit_chunks else "Volume Flatline"
                    logging.info(f"{reason} Detected! Audio captured.")
                break
                
            if not started_speaking and wait_counter >= wait_limit_chunks:
                if frames and wakeword_triggered:
                    if not self.attention_mode: 
                        logging.info("Standalone wake word timeout. BYPASSING WHISPER.")
                    self._publish("jarvis/sys/mic_state", {"state": "idle"})
                    return "hey jarvis"
                else:
                    if not self.attention_mode: 
                        logging.warning("No voice detected. Closing mic.")
                    self._publish("jarvis/sys/mic_state", {"state": "idle"})
                    return ""
                
        final_audio = np.concatenate(frames).astype(np.float32) / 32768.0
        audio_b64 = base64.b64encode(final_audio.tobytes()).decode('utf-8')
        
        self._publish("jarvis/sys/audio_process", {
            "audio_b64": audio_b64
        })
        logging.info("Audio buffer dispatched to Inference Engine.")
        
        self.is_processing = True
        self.processing_timer = time.time()
        self._publish("jarvis/sys/mic_state", {"state": "processing"}) 
        
        return ""

    def _flush_buffer(self):
        try:
            available = self.mic_stream.get_read_available()
            if available > 0:
                self.mic_stream.read(available, exception_on_overflow=False)
        except Exception:
            pass

    def listen(self) -> None:
        with silence_c_errors():
            self.mic_stream = self.audio.open(
                format=self.FORMAT, channels=self.CHANNELS, rate=self.RATE, 
                input=True, frames_per_buffer=self.CHUNK
            )
            
        import time
        logging.info("Waiting for Ecosystem (Whisper) to come online...")
        
        while not self.system_ready:
            self._flush_buffer() # Keep clearing the mic so we don't build up a massive delay
            time.sleep(0.2)
        
        logging.info(f"--- SYSTEM READY: Listening for '{self.target_word}' ---")

        while True:
            if self.tts_busy:
                if hasattr(self, 'tts_lock_time') and (time.time() - self.tts_lock_time) > 15.0:
                    self.tts_busy = False
                    self.tts_queue_count = 0
                    continue

                try:
                    available = self.mic_stream.get_read_available()
                    if available > 0: self.mic_stream.read(available, exception_on_overflow=False)
                except Exception: pass
                time.sleep(0.1)
                continue

            if self.pending_active_window:
                self.active_window_end = time.time() + 7.0
                self.pending_active_window = False 

            audio_data = np.frombuffer(self.mic_stream.read(self.CHUNK, exception_on_overflow=False), dtype=np.int16)
            self.ring_buffer.append(audio_data)
            
            audio_float = audio_data.astype(np.float32)
            
            # 1. High-Pass Filter (Removes bass/thumps below ~300Hz)
            hp_audio = np.append(audio_float[0], audio_float[1:] - 0.95 * audio_float[:-1])

            # 2. Low-Pass Filter (Removes cymbals/high-freq noise above ~3000Hz)
            vad_audio = np.append(hp_audio[0], 0.6 * hp_audio[1:] + 0.4 * hp_audio[:-1])

            raw_rms = np.sqrt(np.mean(np.square(vad_audio)))
            # Exponential expansion (noise gate)
            current_rms = ((raw_rms / 500.0) ** 1.5) * 500.0 if raw_rms > 0 else 0
            self.ambient_noise_buffer.append(current_rms)
            
            if self.fast_ema is None:
                self.fast_ema = current_rms
                self.slow_ema = current_rms
            else:
                self.fast_ema = 0.2 * current_rms + 0.8 * self.fast_ema
                self.slow_ema = 0.01 * current_rms + 0.99 * self.slow_ema
                
            bar_length = int(max(0, min(current_rms / 100, 40))) 
            meter = "█" * bar_length + "-" * (40 - bar_length)
            
            status_tag = "STANDARD"
            if self.attention_mode: status_tag = "ATTENTION"
            elif self.awaiting_reply: status_tag = "REPLY"
            elif time.time() < self.active_window_end: status_tag = "ACTIVE_WIN"
            
            b_noise, a_thresh, s_thresh = self._calculate_thresholds()

            # Fallback: Unlock if Whisper/Daemon crashes and never responds for 15 seconds
            if self.is_processing and (time.time() - self.processing_timer > 15.0):
                self.is_processing = False
                self._publish("jarvis/sys/mic_state", {"state": "idle"})

            if not self.is_processing:
                now = time.time()
                is_speaking = current_rms > a_thresh
                publish_interval = 0.08 if is_speaking else 0.33
                
                if (now - self.last_vol_publish > publish_interval):
                    self._publish("jarvis/sys/volume", {
                        "rms": int(current_rms),
                        "bar": meter,
                        "status": status_tag,
                        "b_noise": int(b_noise),
                        "a_thresh": int(a_thresh),
                        "s_thresh": int(s_thresh)
                    })
                    self.last_vol_publish = now
            
            is_active_window = time.time() < self.active_window_end
            bypass_wakeword = self.attention_mode or self.awaiting_reply or is_active_window
            
            voice_triggered = (self.attention_mode and (current_rms > a_thresh)) or self.awaiting_reply or is_active_window
            wakeword_triggered = False

            if not bypass_wakeword:
                model_ran = False
                if current_rms > a_thresh:
                    self.vad_hangtime = 15
                    while self.pre_speech_buffer:
                        self.oww_model.predict(self.pre_speech_buffer.popleft())
                    self.oww_model.predict(audio_data)
                    model_ran = True
                            
                elif self.vad_hangtime > 0:
                    self.vad_hangtime -= 1
                    self.oww_model.predict(audio_data)
                    model_ran = True
                    
                else:
                    self.pre_speech_buffer.append(audio_data)
                    self.oww_model.reset()
                    
                if model_ran:
                    for mdl in self.oww_model.prediction_buffer.keys():
                        if list(self.oww_model.prediction_buffer[mdl])[-1] > 0.5:
                            wakeword_triggered = True
                            break
            
            if wakeword_triggered or voice_triggered:
                if not self.attention_mode: logging.info("\n")
                
                if wakeword_triggered:
                    logging.info("="*50)
                    logging.info("WAKE WORD DETECTED!")
                    
                if self.awaiting_reply: self.awaiting_reply = False
                if is_active_window: self.active_window_end = 0.0
                
                if len(self.ambient_noise_buffer) < 5:
                    try:
                        available = self.mic_stream.get_read_available()
                        if available > 0: self.mic_stream.read(available, exception_on_overflow=False)
                        for _ in range(5):
                            data = self.mic_stream.read(self.CHUNK, exception_on_overflow=False)
                            audio_float = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                            vad_audio = np.append(audio_float[0], audio_float[1:] - 0.95 * audio_float[:-1])
                            raw_rms = np.sqrt(np.mean(np.square(vad_audio)))
                            chunk_rms = ((raw_rms / 500.0) ** 1.5) * 500.0 if raw_rms > 0 else 0
                            self.ambient_noise_buffer.append(chunk_rms)
                    except Exception: pass

                b_noise, a_thresh, s_thresh = self._calculate_thresholds()
                
                if not self.attention_mode:
                    logging.info(f"Room Baseline: {b_noise:.0f} | Activate: {a_thresh:.0f} | Silence: {s_thresh:.0f}")
                    
                pass_pre_frames = []
                is_already_speaking = False
                
                if wakeword_triggered or current_rms > a_thresh:
                    pass_pre_frames = list(self.ring_buffer)
                    
                    # If they are actively talking right now, tell the recorder not to wait for new audio
                    if current_rms > a_thresh:
                        is_already_speaking = True
                    
                command_text = self._record_command(a_thresh, s_thresh, bypass_wakeword, pass_pre_frames, is_already_speaking, wakeword_triggered)
                
                if command_text:
                    self._publish("jarvis/sensor/voice", command_text)
                    
                if not self.attention_mode: logging.info("="*50 + "\n")
                
                self.oww_model.reset()
                self.ambient_noise_buffer.clear()
                self.pre_speech_buffer.clear()
                self.ring_buffer.clear()
                self.vad_hangtime = 0
                
                # --- Explicit buffer flush with a tiny delay to ignore physical volume bumps ---
                time.sleep(0.15)
                self._flush_buffer()

    def shutdown(self) -> None:
        if self.mic_stream is not None:
            self.mic_stream.stop_stream()
            self.mic_stream.close()
        self.audio.terminate()
        self.mqtt.loop_stop()
        self.mqtt.disconnect()

def main():
    try:
        sensor = VoiceSensor(target_word="hey_jarvis")
        sensor.listen()
    except KeyboardInterrupt:
        logging.info("MANUAL SHUTDOWN TRIGGERED...")
    finally:
        if 'sensor' in locals(): sensor.shutdown()

if __name__ == "__main__":
    main()