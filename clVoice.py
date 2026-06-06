import os
import re
import sys
import pyaudio
import numpy as np
import logging
import json
import collections
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
    """Redireciona os erros C (ALSA/JACK) para o vazio temporariamente."""
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

# --- FIX GRAPHICS CARD NVIDIA (WINDOWS) ---
if sys.platform == 'win32':
    cuda_path = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if os.path.exists(cuda_path):
        for version in os.listdir(cuda_path):
            if version.startswith("v12"):
                bin_folder = os.path.join(cuda_path, version, "bin")
                os.environ["PATH"] = bin_folder + os.pathsep + os.environ["PATH"]
                try: 
                    os.add_dll_directory(bin_folder)
                except OSError: 
                    pass
                
    import site
    for site_pkg in site.getsitepackages() + [site.getusersitepackages()]:
        for lib in ["cublas", "cudnn"]:
            lib_bin = os.path.join(site_pkg, "nvidia", lib, "bin")
            if os.path.exists(lib_bin):
                os.environ["PATH"] = lib_bin + os.pathsep + os.environ["PATH"]
                try: 
                    os.add_dll_directory(lib_bin)
                except OSError: 
                    pass
# ------------------------------------------------------

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [VOICE] %(message)s", datefmt="%H:%M:%S")

# --- AUDIO CONFIGURATION ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1280

# --- SMART LISTENING RULES ---
MAX_RECORD_SECONDS = 10
INITIAL_SILENCE_SECONDS = 1.5
SILENCE_LIMIT_SECONDS = 1.0

# --- DYNAMIC SENSITIVITY PERCENTAGE BUFFERS ---
MIN_BASELINE = 2000             
VOICE_ACTIVATION_BUFFER = 1.40  
SILENCE_CUTOFF_BUFFER = 1.15    
MAX_CEILING_BUFFER = 3.00       

# --- GLOBALS ---
FORCE_MIC = False

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def load_settings():
    """Lê as preferências gerais do utilizador."""
    config_path = os.path.join(os.path.dirname(__file__), "settings.json")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"language": "auto", "hardware": "cpu", "stt_model": "base"}

def init_wakeword(target_word):
    """Initializes the OpenWakeWord model."""
    import openwakeword
    from openwakeword.model import Model
    
    modelos_disponiveis = openwakeword.get_pretrained_model_paths()
    caminho_jarvis = next((caminho for caminho in modelos_disponiveis if target_word in caminho), None)
    
    if not caminho_jarvis:
        logging.critical(f"Erro: Pre trained '{target_word}' Model not found!")
        sys.exit(1)
        
    return Model(wakeword_model_paths=[caminho_jarvis])

def init_whisper(hardware_choice, size_model):
    """Initializes the Faster-Whisper STT model with fallback logic."""
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
            try: 
                return WhisperModel(size_model, device="cpu", compute_type="int8")
            except Exception: 
                sys.exit(1)
        else:
            sys.exit(1)

def calculate_thresholds(ambient_noise_buffer):
    """Calculates activation and silence thresholds based on rolling audio memory."""
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
    """Sends duck/unduck commands to the Spotify actuator."""
    try:
        publish.single("pc/spotify/control", json.dumps({"action": action}), hostname="localhost")
        msg = "Audio Ducking initiated." if action == "duck" else "Audio Unducked."
        logging.info(msg)
    except Exception as e:
        logging.warning(f"Failed to send {action} command: {e}")

def transcribe_audio(stt_model, command_audio, lang_chosen):
    """Passes the raw audio through Whisper and returns text."""
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

def record_command(mic_stream, activation_threshold, silence_threshold):
    """Records the user's command dynamically based on noise thresholds."""
    logging.info(f"LISTENING... (Trigger: >{activation_threshold:.0f} | Cutoff: <{silence_threshold:.0f})")
    frames = []
    
    max_chunks = int(RATE / CHUNK * MAX_RECORD_SECONDS)
    silence_limit_chunks = int(RATE / CHUNK * SILENCE_LIMIT_SECONDS)
    wait_limit_chunks = int(RATE / CHUNK * INITIAL_SILENCE_SECONDS)
    
    silence_counter, wait_counter = 0, 0
    started_speaking = False
    
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
            logging.info("No voice detected. Closing microphone!")
            break
            
    audio_int16 = np.concatenate(frames)
    return audio_int16.astype(np.float32) / 32768.0

# --- MQTT LISTENER ---
def on_mic_trigger(client, userdata, msg):
    global FORCE_MIC
    FORCE_MIC = True
    logging.info("Remote trigger captured! Ignoring Wake Word and opening mic...")

def start_mqtt_listener():
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1)
    client.on_message = on_mic_trigger
    client.connect("localhost", 1883, 60)
    client.subscribe("jarvis/sys/mic_open")
    client.loop_start()

# ==========================================
# MAIN ORCHESTRATOR
# ==========================================

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
            # 1: Read Audio & Calculate Volume
            audio_data = np.frombuffer(mic_stream.read(CHUNK, exception_on_overflow=False), dtype=np.int16)
            current_rms = np.sqrt(np.mean(np.square(audio_data.astype(np.float32))))
            ambient_noise_buffer.append(current_rms)
            
            # --- DEBUG: LIVE VOLUME METER ---
            bar_length = int(max(0, min(current_rms / 100, 40))) 
            meter = "█" * bar_length + "-" * (40 - bar_length)
            print(f"[LIVE] Vol: {current_rms:5.0f} |{meter}|".ljust(80), end='\r')
            
            # 2: Wake Word Prediction
            prediction = oww_model.predict(audio_data)
            
            for mdl in oww_model.prediction_buffer.keys():
                scores = list(oww_model.prediction_buffer[mdl])
                
                if scores[-1] > 0.5 or FORCE_MIC:
                    print("\n") 
                    if FORCE_MIC:
                        FORCE_MIC = False
                    else:
                        print("="*50)
                        logging.info("WAKE WORD DETECTED!")
                    
                    # 3: Ducking & Math
                    send_spotify_action("duck")
                    
                    base_noise, act_thresh, sil_thresh = calculate_thresholds(ambient_noise_buffer)
                    logging.info(f"Room Baseline: {base_noise:.0f} | Activate: {act_thresh:.0f} | Silence: {sil_thresh:.0f}")
                        
                    # 4: Record & Unduck
                    command_audio = record_command(mic_stream, act_thresh, sil_thresh)
                    send_spotify_action("unduck")
                    
                    # 5. Transcribe
                    settings = load_settings()
                    text = transcribe_audio(stt_model, command_audio, settings.get("language", "auto"))
                    logging.info(f"Heard: \"{text}\"")
                    
                    # --- ABORT/CANCEL LOGIC ---
                    clean_text = re.sub(r'[.,!?]', '', text.lower()).strip()
                    
                    cancel_keywords = ["cancel", "abort", "nevermind", "never mind", "cancelar", "esquece"]
                    
                    if clean_text in cancel_keywords:
                        logging.warning("User aborted the command. Dropping transcript and returning to standby.")
                    
                    # 6. Publish Intent (Only if not cancelled)
                    elif text:
                        logging.info("Forwarding transcript to Central Daemon...")
                        try:
                            publish.single("jarvis/sensor/voice", text, hostname="localhost")
                        except Exception as e:
                            logging.error(f"Failed to reach MQTT Broker: {e}")
                        
                    print("="*50 + "\n")
                    
                    # 7: Reset State
                    oww_model.reset()
                    ambient_noise_buffer.clear()
                    
                    # Silent Flush Buffer
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