import os
import re
import sys
import json
import numpy as np
import pyaudio
import logging
import collections
import time
import site
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

# --- LINUX C-LEVEL ALSA & JACK SILENCER ---
def py_error_handler(filename, line, function, err, fmt):
    pass

try:
    ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
    c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
    asound = cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except Exception:
    pass

# --- LOGGING SETUP ---
import warnings
warnings.filterwarnings("ignore")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("faster_whisper").setLevel(logging.WARNING)

logging.basicConfig(level=logging.INFO, format="\r\033[K[%(asctime)s] [VOICE] %(message)s", datefmt="%H:%M:%S")

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

# --- WINDOWS CUDA PATH INJECTION ---
if sys.platform == 'win32':
    cuda_path = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if os.path.exists(cuda_path):
        for version in os.listdir(cuda_path):
            if version.startswith("v12"):
                bin_folder = os.path.join(cuda_path, version, "bin")
                os.environ["PATH"] = bin_folder + os.pathsep + os.environ["PATH"]
                try: os.add_dll_directory(bin_folder)
                except OSError: pass
                
    for site_pkg in site.getsitepackages() + [site.getusersitepackages()]:
        for lib in ["cublas", "cudnn"]:
            lib_bin = os.path.join(site_pkg, "nvidia", lib, "bin")
            if os.path.exists(lib_bin):
                os.environ["PATH"] = lib_bin + os.pathsep + os.environ["PATH"]
                try: os.add_dll_directory(lib_bin)
                except OSError: pass

from faster_whisper import WhisperModel

class VoiceSensor:
    """Enterprise class for managing audio capture, wake word detection, and STT pipelines."""
    
    FORMAT: int = pyaudio.paInt16
    CHANNELS: int = 1
    RATE: int = 16000
    CHUNK: int = 1280

    MAX_RECORD_SECONDS: int = 10
    INITIAL_SILENCE_SECONDS: float = 1.5
    BACKGROUND_GRACE_SECONDS: float = 2.5
    SILENCE_LIMIT_SECONDS: float = 1.0

    MIN_BASELINE: int = 2000              
    VOICE_ACT_BUFFER: float = 1.40  
    SILENCE_CUT_BUFFER: float = 1.15    
    MAX_CEILING_BUFFER: float = 3.00       

    def __init__(self, target_word: str = "hey_jarvis"):
        self.target_word: str = target_word
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        
        # Parallel Conversational State Tracking Memory
        self.tts_busy: bool = False
        self.already_spoke: bool = False
        self.ambient_noise_buffer: Deque[float] = collections.deque(maxlen=100)
        
        # Microservice Control States
        self.active_window_end: float = 0.0
        self.pending_active_window: bool = False
        self.window_grace_end: float = 0.0
        self.awaiting_reply: bool = False
        self.attention_mode: bool = False
        
        self.settings: Dict[str, Any] = self._load_settings()
        
        with silence_c_errors():
            self.audio: pyaudio.PyAudio = pyaudio.PyAudio()
            
        self.mic_stream: Optional[pyaudio.Stream] = None
        
        self.oww_model = self._init_wakeword()
        self.stt_model = self._init_whisper()
        self.mqtt: mqtt_client.Client = self._init_mqtt()

    def _load_settings(self) -> Dict[str, Any]:
        config_path = os.path.abspath(os.path.join(self.base_dir, "..", "config", "settings.json"))
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            logging.warning("Failed to load settings.json. Using fallback defaults.")
            return {"language": "en", "hardware": "cpu", "stt_model": "small"}

    def _init_wakeword(self) -> Any:
        logging.info("Booting Wake Word Engine...")
        import openwakeword
        from openwakeword.model import Model
        
        available_models = openwakeword.get_pretrained_model_paths()
        jarvis_path = next((path for path in available_models if self.target_word in path), None)
        if not jarvis_path:
            logging.critical(f"Error: Pre-trained '{self.target_word}' model not found!")
            sys.exit(1)
            
        try:
            return Model(wakeword_models=[jarvis_path])
        except TypeError:
            return Model(wakeword_model_paths=[jarvis_path])

    def _init_whisper(self) -> WhisperModel:
        hw_choice = self.settings.get("hardware", "cpu").lower()
        size = self.settings.get("stt_model", "base")
        
        if hw_choice in ["gpu", "cuda"]:
            stt_device, stt_compute = "cuda", "float16"
            logging.info(f"Booting Whisper STT ({size}) -> GRAPHICS CARD (CUDA)")
        else:
            stt_device, stt_compute = "cpu", "int8"
            logging.info(f"Booting Whisper STT ({size}) -> PROCESSOR (CPU)")
            
        try:
            return WhisperModel(size, device=stt_device, compute_type=stt_compute)
        except Exception as e:
            logging.critical(f"Error loading voice module: {e}")
            if hw_choice in ["gpu", "cuda"]:
                logging.critical("Retrying with CPU fallback...")
                try: 
                    return WhisperModel(size, device="cpu", compute_type="int8")
                except Exception: 
                    sys.exit(1)
            sys.exit(1)

    # --- MQTT CONTEXT BUS ENGINE ---
    def _init_mqtt(self) -> mqtt_client.Client:
        client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1)
        client.on_message = self._on_mqtt_message
        
        try:
            client.connect("localhost", 1883, 60)
            
            client.subscribe("jarvis/sys/speak")
            client.subscribe("jarvis/sys/mic_open")
            client.subscribe("jarvis/sys/mic_control")
            client.subscribe("jarvis/sys/tts_done") 
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

        # 1. TTS Tracking
        if msg.topic == "jarvis/sys/speak":
            self.tts_busy = True
            
        elif msg.topic == "jarvis/sys/tts_done":
            self.tts_busy = False
            self.ambient_noise_buffer.clear()
            self.oww_model.reset()
            
            self.window_grace_end = time.time() + self.BACKGROUND_GRACE_SECONDS
            
            try:
                available = self.mic_stream.get_read_available()
                if available > 0:
                    self.mic_stream.read(available, exception_on_overflow=False)
            except Exception: pass
            
            logging.info("TTS completion signal received. Releasing microphone lock.")

        # 2. Conversational States
        elif msg.topic == "jarvis/sys/mic_open" or action == "open_window":
            self.pending_active_window = True
            
        elif action == "request_reply":
            self.awaiting_reply = True
            
        elif action == "attention_on":
            self.attention_mode = True
            
            self.window_grace_end = time.time() + self.BACKGROUND_GRACE_SECONDS
            self.ambient_noise_buffer.clear()
            self.oww_model.reset()
            
            logging.info(f"Continuous Attention Mode ACTIVATED. Adapting room acoustics for {self.BACKGROUND_GRACE_SECONDS}s...")
            
        elif action == "attention_off":
            self.attention_mode = False
            logging.info("Attention Mode DEACTIVATED. Resetting to standard wake word anchors.")

    def _publish(self, topic: str, payload: Any) -> None:
        try:
            data = json.dumps(payload) if isinstance(payload, dict) else payload
            self.mqtt.publish(topic, data)
        except Exception as e:
            logging.warning(f"MQTT Publish Error on '{topic}': {e}")

    def _calculate_thresholds(self) -> Tuple[float, float, float]:
        if len(self.ambient_noise_buffer) > 10:
            true_background = list(self.ambient_noise_buffer)[:-5]
            baseline = np.mean(true_background)
        else:
            baseline = np.mean(self.ambient_noise_buffer) if self.ambient_noise_buffer else self.MIN_BASELINE
            
        baseline = max(baseline, self.MIN_BASELINE)
        activation = min(baseline * self.VOICE_ACT_BUFFER, baseline * self.MAX_CEILING_BUFFER)
        silence = min(baseline * self.SILENCE_CUT_BUFFER, baseline * self.MAX_CEILING_BUFFER)
        
        return baseline, activation, silence

    def _transcribe(self, command_audio: np.ndarray) -> str:
        lang = self.settings.get("language", "auto")
        args = {
            "audio": command_audio,
            "beam_size": 2,
            "vad_filter": True,
            "initial_prompt": "Hey Jarvis, turn on the lights. Play Spotify. Search online. Open site. youtube.com google.com",
            "vad_parameters": dict(min_silence_duration_ms=500)
        }
        if lang != "auto":
            args["language"] = lang
            
        segments, info = self.stt_model.transcribe(**args)
        return "".join([s.text for s in segments]).strip()

    def _record_command(self, act_thresh: float, sil_thresh: float, bypass_wakeword: bool) -> np.ndarray:
        wait_limit = 5.0 if bypass_wakeword else self.INITIAL_SILENCE_SECONDS
        logging.info(f"LISTENING... (Trigger: >{act_thresh:.0f} | Cutoff: <{sil_thresh:.0f} | Timeout: {wait_limit}s)")
        
        frames = []
        max_chunks = int(self.RATE / self.CHUNK * self.MAX_RECORD_SECONDS)
        silence_limit_chunks = int(self.RATE / self.CHUNK * self.SILENCE_LIMIT_SECONDS)
        wait_limit_chunks = int(self.RATE / self.CHUNK * wait_limit)
        
        silence_counter, wait_counter = 0, 0
        started_speaking = False
        
        try:
            available = self.mic_stream.get_read_available()
            if available > 0: self.mic_stream.read(available, exception_on_overflow=False)
        except Exception: pass

        for _ in range(max_chunks):
            if self.tts_busy:
                logging.warning("TTS interrupt detected! Aborting capture to prevent self-transcription.")
                return np.array([], dtype=np.float32)

            data = self.mic_stream.read(self.CHUNK, exception_on_overflow=False)
            audio_chunk = np.frombuffer(data, dtype=np.int16)
            frames.append(audio_chunk)
            
            rms = np.sqrt(np.mean(np.square(audio_chunk.astype(np.float32))))
            
            if not started_speaking:
                if rms > act_thresh:
                    started_speaking, silence_counter = True, 0
                else:
                    wait_counter += 1
            else:
                if rms < sil_thresh: silence_counter += 1
                else: silence_counter = 0
                    
            if started_speaking and silence_counter >= silence_limit_chunks:
                logging.info("Silence Detected! Command captured with success.")
                break
                
            if not started_speaking and wait_counter >= wait_limit_chunks:
                logging.warning("No voice detected. Closing microphone!")
                break
                
        audio_int16 = np.concatenate(frames)
        return audio_int16.astype(np.float32) / 32768.0

    # --- CORE RUNTIME SWITCHBOARD ---
    def listen(self) -> None:
        with silence_c_errors():
            self.mic_stream = self.audio.open(
                format=self.FORMAT, channels=self.CHANNELS, rate=self.RATE, 
                input=True, frames_per_buffer=self.CHUNK
            )
            
        self._publish("jarvis/sys/speak", {"text": "System online!", "skip_ducking": False, "request_reply": False})
        logging.info(f"--- SYSTEM READY: Listening for '{self.target_word}' ---")

        while True:
            if self.tts_busy:
                try:
                    available = self.mic_stream.get_read_available()
                    if available > 0: 
                        self.mic_stream.read(available, exception_on_overflow=False)
                except Exception: pass
                time.sleep(0.1)
                continue

            # --- THE ACTIVE WINDOW TIMING ENGINE ---
            if self.pending_active_window:
                self.active_window_end = time.time() + 7.0
                self.window_grace_end = time.time() + self.BACKGROUND_GRACE_SECONDS
                self.pending_active_window = False
                
                self.ambient_noise_buffer.clear() 
                
                logging.info(f"Continuous Window opened. Adapting room acoustics for {self.BACKGROUND_GRACE_SECONDS}s...")

            audio_data = np.frombuffer(self.mic_stream.read(self.CHUNK, exception_on_overflow=False), dtype=np.int16)
            current_rms = np.sqrt(np.mean(np.square(audio_data.astype(np.float32))))
            self.ambient_noise_buffer.append(current_rms)
            
            # Live Contextual Status Monitor
            bar_length = int(max(0, min(current_rms / 100, 40))) 
            meter = "█" * bar_length + "-" * (40 - bar_length)
            
            status_tag = "STANDARD"
            if self.attention_mode: status_tag = "ATTENTION"
            elif self.awaiting_reply: status_tag = "REPLY"
            elif time.time() < self.active_window_end: status_tag = "ACTIVE_WIN"
            
            print(f"\r\033[K[{status_tag}] Vol: {current_rms:5.0f} ||{meter}||", end='', flush=True)
            
            self.oww_model.predict(audio_data)
            
            for mdl in self.oww_model.prediction_buffer.keys():
                scores = list(self.oww_model.prediction_buffer[mdl])
                
                # Evaluate global bypass flags against time barriers
                is_active_window = time.time() < self.active_window_end
                bypass_wakeword = self.attention_mode or self.awaiting_reply or is_active_window
                
                b_noise, a_thresh, s_thresh = self._calculate_thresholds()
                
                wakeword_triggered = scores[-1] > 0.5
                
                in_grace_period = time.time() < self.window_grace_end
                voice_triggered = bypass_wakeword and (current_rms > a_thresh) and not in_grace_period
                
                if wakeword_triggered or voice_triggered:
                    print("\n")
                    
                    if wakeword_triggered:
                        print("="*50)
                        logging.info("WAKE WORD DETECTED!")
                        
                    self._publish("pc/spotify/control", {"action": "duck"})
                    
                    if not bypass_wakeword:
                        try:
                            self.tts_busy = True 
                            greeting = "Hello Sir, what can I do?" if not self.already_spoke else "Yes sir?"
                            self.already_spoke = True
                            
                            self._publish("jarvis/sys/speak", {
                                "text": greeting, "skip_ducking": True, "request_reply": True
                            })
                        except Exception as e:
                            logging.warning(f"Could not vocalize wake greeting: {e}")
                            self.tts_busy = False
                            
                    wait_start = time.time()
                    while self.tts_busy and (time.time() - wait_start) < 30.0:
                        time.sleep(0.1)
                        
                    if self.awaiting_reply:
                        self.awaiting_reply = False
                    if is_active_window:
                        self.active_window_end = 0.0
                    
                    b_noise, a_thresh, s_thresh = self._calculate_thresholds()
                    logging.info(f"Room Baseline: {b_noise:.0f} | Activate: {a_thresh:.0f} | Silence: {s_thresh:.0f}")
                        
                    command_audio = self._record_command(a_thresh, s_thresh, bypass_wakeword)
                    if command_audio.size > 0:
                        self._publish("pc/spotify/control", {"action": "unduck"})
                        text = self._transcribe(command_audio)
                        logging.info(f"Heard: \"{text}\"")
                        
                        if text:
                            logging.info("Forwarding transcript to Central Daemon...")
                            self._publish("jarvis/sensor/voice", text)
                    else:
                        self._publish("pc/spotify/control", {"action": "unduck"})
                        
                    print("="*50 + "\n")
                    
                    self.oww_model.reset()
                    self.ambient_noise_buffer.clear()
                    
                    if self.attention_mode or (time.time() < self.active_window_end):
                        self.window_grace_end = time.time() + self.BACKGROUND_GRACE_SECONDS
                    
                    try:
                        available = self.mic_stream.get_read_available()
                        if available > 0: self.mic_stream.read(available, exception_on_overflow=False)
                    except Exception: pass
                    
                    break

    def shutdown(self) -> None:
        if self.mic_stream is not None:
            self.mic_stream.stop_stream()
            self.mic_stream.close()
        self.audio.terminate()
        self.mqtt.loop_stop()
        self.mqtt.disconnect()

def main():
    logging.info("Initiating safety checks and loading configs...")
    try:
        sensor = VoiceSensor(target_word="hey_jarvis")
        sensor.listen()
    except KeyboardInterrupt:
        logging.info("MANUAL SHUTDOWN TRIGGERED...")
    except Exception as e:
        logging.critical(f"FATAL ERROR AT RUNTIME: {e}")
    finally:
        if 'sensor' in locals():
            sensor.shutdown()

if __name__ == "__main__":
    main()