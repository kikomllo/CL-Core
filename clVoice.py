import os
import re
import sys
import pyaudio
import numpy as np
import logging
import json
import collections
import time
import paho.mqtt.publish as publish
from faster_whisper import WhisperModel
import paho.mqtt.client as mqtt_client

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["TFLITE_NUM_THREADS"] = "1"

# --- SILENCE ALSA WARNINGS ---
import warnings
from contextlib import contextmanager

warnings.filterwarnings("ignore")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("faster_whisper").setLevel(logging.WARNING)

@contextmanager
def silence_alsa():
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(2)
    sys.stderr.flush()
    os.dup2(devnull, 2)
    os.close(devnull)
    try:
        yield
    finally:
        os.dup2(old_stderr, 2)
        os.close(old_stderr)

if sys.platform == 'win32':
    cuda_path = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if os.path.exists(cuda_path):
        for version in os.listdir(cuda_path):
            if version.startswith("v12"):
                bin_folder = os.path.join(cuda_path, version, "bin")
                os.environ["PATH"] = bin_folder + os.pathsep + os.environ["PATH"]
                try: os.add_dll_directory(bin_folder)
                except OSError: pass
                
    import site
    for site_pkg in site.getsitepackages() + [site.getusersitepackages()]:
        for lib in ["cublas", "cudnn"]:
            lib_bin = os.path.join(site_pkg, "nvidia", lib, "bin")
            if os.path.exists(lib_bin):
                os.environ["PATH"] = lib_bin + os.pathsep + os.environ["PATH"]
                try: os.add_dll_directory(lib_bin)
                except OSError: pass

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [VOICE] %(message)s", datefmt="%H:%M:%S")

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1280

MAX_RECORD_SECONDS = 10
INITIAL_SILENCE_SECONDS = 1.5
SILENCE_LIMIT_SECONDS = 1.0

MIN_BASELINE = 2000              
VOICE_ACTIVATION_BUFFER = 1.40  
SILENCE_CUTOFF_BUFFER = 1.15    
MAX_CEILING_BUFFER = 3.00       

FORCE_MIC = False
TTS_BUSY = False

def load_settings():
    config_path = os.path.join(os.path.dirname(__file__), "settings.json")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"language": "en", "hardware": "cpu", "stt_model": "small"}

def init_wakeword(target_word):
    import openwakeword
    from openwakeword.model import Model
    available_models = openwakeword.get_pretrained_model_paths()
    jarvis_path = next((path for path in available_models if target_word in path), None)
    if not jarvis_path:
        logging.critical(f"Erro: Pre trained '{target_word}' Model not found!")
        sys.exit(1)
    return Model(wakeword_models=[jarvis_path])

def init_whisper(hardware_choice, size_model):
    if hardware_choice in ["gpu", "cuda"]:
        stt_device = "cuda"
        stt_compute = "float16"
        logging.info(f"Booting Whisper STT ({size_model}) -> GRAPHICS CARD (CUDA)")
    else:
        stt_device = "cpu"
        stt_compute = "int8"
        logging.info(f"Booting Whisper STT ({size_model}) -> PROCESSOR (CPU)")
    try:
        return WhisperModel(size_model, device=stt_device, compute_type=stt_compute)
    except Exception as e:
        logging.critical(f"Error loading voice module: {e}")
        if hardware_choice in ["gpu", "cuda"]:
            logging.critical("Retrying with CPU...")
            try: return WhisperModel(size_model, device="cpu", compute_type="int8")
            except Exception: sys.exit(1)
        else:
            sys.exit(1)

def calculate_thresholds(ambient_noise_buffer):
    if len(ambient_noise_buffer) > 25:
        true_background_history = list(ambient_noise_buffer)[:-25]
        baseline_noise = np.mean(true_background_history)
    else:
        baseline_noise = np.mean(ambient_noise_buffer) if ambient_noise_buffer else MIN_BASELINE
    baseline_noise = max(baseline_noise, MIN_BASELINE)
    activation = min(baseline_noise * VOICE_ACTIVATION_BUFFER, baseline_noise * MAX_CEILING_BUFFER)
    silence = min(baseline_noise * SILENCE_CUTOFF_BUFFER, baseline_noise * MAX_CEILING_BUFFER)
    return baseline_noise, activation, silence

def send_spotify_action(action):
    try:
        publish.single("pc/spotify/control", json.dumps({"action": action}), hostname="localhost")
        msg = "Audio Ducking initiated." if action == "duck" else "Audio Unducked."
        logging.info(msg)
    except Exception as e:
        logging.warning(f"Failed to send {action} command: {e}")

def transcribe_audio(stt_model, command_audio, lang_chosen):
    transcribe_args = {
        "audio": command_audio,
        "beam_size": 2,
        "vad_filter": True,
        "initial_prompt": "Hey Jarvis, turn on the lights. Play Spotify. Play some music. Play the song. Set the color to blue. Status report.",
        "vad_parameters": dict(min_silence_duration_ms=500)
    }
    if lang_chosen != "auto":
        transcribe_args["language"] = lang_chosen
    segments, info = stt_model.transcribe(**transcribe_args)
    if lang_chosen == "auto":
        logging.info(f"Automatic language detected: {info.language} (Certainty: {info.language_probability:.2f})")
    return "".join([segment.text for segment in segments]).strip()

def record_command(mic_stream, activation_threshold, silence_threshold, is_remote_trigger=False):
    current_wait_limit = 5.0 if is_remote_trigger else INITIAL_SILENCE_SECONDS
    logging.info(f"LISTENING... (Trigger: >{activation_threshold:.0f} | Cutoff: <{silence_threshold:.0f} | Timeout: {current_wait_limit}s)")
    frames = []
    
    max_chunks = int(RATE / CHUNK * MAX_RECORD_SECONDS)
    silence_limit_chunks = int(RATE / CHUNK * SILENCE_LIMIT_SECONDS)
    wait_limit_chunks = int(RATE / CHUNK * current_wait_limit)
    
    silence_counter, wait_counter = 0, 0
    started_speaking = False
    
    try:
        available_frames = mic_stream.get_read_available()
        if available_frames > 0:
            mic_stream.read(available_frames, exception_on_overflow=False)
    except Exception:
        pass

    for _ in range(max_chunks):
        data = mic_stream.read(CHUNK, exception_on_overflow=False)
        audio_chunk = np.frombuffer(data, dtype=np.int16)
        frames.append(audio_chunk)
        
        rms = np.sqrt(np.mean(np.square(audio_chunk.astype(np.float32))))
        
        if not started_speaking:
            if rms > activation_threshold:
                started_speaking = True
                silence_counter = 0
            else:
                wait_counter += 1
        else:
            if rms < silence_threshold:
                silence_counter += 1
            else:
                silence_counter = 0
                
        if started_speaking and silence_counter >= silence_limit_chunks:
            logging.info("Silence Detected! Command captured with success.")
            break
            
        if not started_speaking and wait_counter >= wait_limit_chunks:
            logging.warning("No voice detected. Closing microphone!")
            break
            
    audio_int16 = np.concatenate(frames)
    return audio_int16.astype(np.float32) / 32768.0

def on_mic_trigger(client, userdata, msg):
    global FORCE_MIC
    FORCE_MIC = True
    logging.info("Remote trigger captured! Ignoring Wake Word and opening mic...")

# --- MQTT LISTENER ---
def on_mqtt_message(client, userdata, msg):
    global FORCE_MIC, TTS_BUSY
    topic = msg.topic
    
    if topic == "jarvis/sys/mic_open":
        FORCE_MIC = True
        logging.info("Remote trigger captured! Ignoring Wake Word and opening mic...")
        
    elif topic == "jarvis/sys/tts_done":
        TTS_BUSY = False

def start_mqtt_listener():
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1)
    client.on_message = on_mqtt_message
    client.connect("localhost", 1883, 60)
    client.subscribe("jarvis/sys/mic_open")
    client.subscribe("jarvis/sys/tts_done") # Subscribe to the new handshake topic
    client.loop_start()

def main():
    logging.info("Initiating safety checks and loading configs...")
    target_word = "hey_jarvis"
    settings = load_settings()
    
    logging.info("Booting Wake Word Engine...")
    oww_model = init_wakeword(target_word)
    stt_model = init_whisper(settings.get("hardware", "cpu").lower(), settings.get("stt_model", "base"))

    with silence_alsa():
        audio = pyaudio.PyAudio()
        
    mic_stream = None
    
    try:
        mic_stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
        logging.info(f"--- SYSTEM READY: Listening for '{target_word}' ---")

        start_mqtt_listener()
        
        global FORCE_MIC
        ambient_noise_buffer = collections.deque(maxlen=50)

        while True:
            audio_data = np.frombuffer(mic_stream.read(CHUNK, exception_on_overflow=False), dtype=np.int16)
            current_rms = np.sqrt(np.mean(np.square(audio_data.astype(np.float32))))
            ambient_noise_buffer.append(current_rms)
            
            bar_length = int(max(0, min(current_rms / 100, 40))) 
            meter = "█" * bar_length + "-" * (40 - bar_length)
            print(f"[LIVE] Vol: {current_rms:5.0f} |{meter}|".ljust(80), end='\r')
            
            prediction = oww_model.predict(audio_data)
            
            for mdl in oww_model.prediction_buffer.keys():
                scores = list(oww_model.prediction_buffer[mdl])
                
                if scores[-1] > 0.5 or FORCE_MIC:
                    print("\n") 
                    is_remote = FORCE_MIC 
                    
                    if FORCE_MIC:
                        FORCE_MIC = False
                    else:
                        print("="*50)
                        logging.info("WAKE WORD DETECTED!")
                        
                        # 1. Duck the audio immediately
                        send_spotify_action("duck")
                        
                        try:
                            global TTS_BUSY
                            TTS_BUSY = True 
                            
                            # 2. Tell TTS to speak, but explicitly tell it NOT to touch the volume
                            greeting_payload = {
                                "text": "Hello Sir, what can I do for you?",
                                "skip_ducking": True
                            }
                            publish.single("jarvis/sys/speak", json.dumps(greeting_payload), hostname="localhost")
                            
                            wait_start = time.time()
                            while TTS_BUSY and (time.time() - wait_start) < 8.0:
                                time.sleep(0.1)
                                
                        except Exception as e:
                            logging.warning(f"Could not vocalize wake greeting: {e}")
                            TTS_BUSY = False
                    
                    base_noise, act_thresh, sil_thresh = calculate_thresholds(ambient_noise_buffer)
                    logging.info(f"Room Baseline: {base_noise:.0f} | Activate: {act_thresh:.0f} | Silence: {sil_thresh:.0f}")
                        
                    # 3. Record the command while audio is still ducked
                    command_audio = record_command(mic_stream, act_thresh, sil_thresh, is_remote_trigger=is_remote)
                    
                    # 4. Unduck the audio once listening is totally finished
                    send_spotify_action("unduck")
                    
                    settings = load_settings()
                    text = transcribe_audio(stt_model, command_audio, settings.get("language", "auto"))
                    logging.info(f"Heard: \"{text}\"")
                    
                    clean_text = re.sub(r'[.,!?]', '', text.lower()).strip()
                    cancel_keywords = ["cancel", "abort", "nevermind", "never mind", "cancelar", "esquece"]
                    
                    if clean_text in cancel_keywords:
                        logging.warning("User aborted the command. Dropping transcript and returning to standby.")
                    elif text:
                        logging.info("Forwarding transcript to Central Daemon...")
                        try:
                            publish.single("jarvis/sensor/voice", text, hostname="localhost")
                        except Exception as e:
                            logging.error(f"Failed to reach MQTT Broker: {e}")
                        
                    print("="*50 + "\n")
                    
                    oww_model.reset()
                    ambient_noise_buffer.clear()
                    
                    try:
                        available_frames = mic_stream.get_read_available()
                        if available_frames > 0:
                            mic_stream.read(available_frames, exception_on_overflow=False)
                    except Exception:
                        pass
                    
                    break
                    
    except KeyboardInterrupt:
        logging.info("MANUAL SHUTDOWN TRIGGERED...")
    except Exception as e:
        logging.critical(f"FATAL ERROR AT RUNTIME: {e}")
    finally:
        if mic_stream is not None:
            mic_stream.stop_stream()
            mic_stream.close()
        audio.terminate()

if __name__ == "__main__":
    main()