import os
import sys
import pyaudio
import numpy as np
import logging
import json
import paho.mqtt.publish as publish
from faster_whisper import WhisperModel
import paho.mqtt.client as mqtt_client

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
SILENCE_THRESHOLD = 1700

def load_settings():
    """Lê as preferências gerais do utilizador."""
    config_path = os.path.join(os.path.dirname(__file__), "settings.json")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {
            "language": "auto", 
            "hardware": "cpu", 
            "stt_model": "base"
        }

# --- AUDIO CAPTURE HELPER ---
def record_command(mic_stream):
    logging.info(f"LISTENING... (Maximum {MAX_RECORD_SECONDS}s, Auto-stop 1s silence)")
    frames = []
    
    # --- CONVERSION: SECONDS TO AUDIO BLOCKS ---
    max_chunks = int(RATE / CHUNK * MAX_RECORD_SECONDS)
    silence_limit_chunks = int(RATE / CHUNK * SILENCE_LIMIT_SECONDS)
    wait_limit_chunks = int(RATE / CHUNK * INITIAL_SILENCE_SECONDS)
    
    silence_counter = 0
    wait_counter = 0
    started_speaking = False
    
    for _ in range(max_chunks):
        data = mic_stream.read(CHUNK, exception_on_overflow=False)
        audio_chunk = np.frombuffer(data, dtype=np.int16)
        frames.append(audio_chunk)
        
        # --- CONVERTED TO FLOAT32 TO PREVENT MATH FAILS WITH INT16 ---
        rms = np.sqrt(np.mean(np.square(audio_chunk.astype(np.float32))))
        
        #print(f"DEBUG - Current Volume: {rms:.2f}")
        
        if rms > SILENCE_THRESHOLD:
            started_speaking = True
            silence_counter = 0
        else:
            if started_speaking:
                silence_counter += 1
            else:
                wait_counter += 1
                
        if started_speaking and silence_counter >= silence_limit_chunks:
            logging.info("Silence Detected! Command captured with success.")
            break
            
        if not started_speaking and wait_counter >= wait_limit_chunks:
            logging.info("No voice detected. Closing microphone!")
            break
            
    audio_int16 = np.concatenate(frames)
    return audio_int16.astype(np.float32) / 32768.0

# --- REMOTE MIC ---
FORCE_MIC = False

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

# --- MAIN ---
def main():
    logging.info("Initiating safety checks and loading configs...")
    
    target_word = "hey_jarvis"
    
    settings = load_settings()
    hardware_choice = settings.get("hardware", "cpu").lower()
    size_model = settings.get("stt_model", "base")

    logging.info("Booting Wake Word Engine...")
    
    import openwakeword
    from openwakeword.model import Model
    
    modelos_disponiveis = openwakeword.get_pretrained_model_paths()
    caminho_jarvis = next((caminho for caminho in modelos_disponiveis if 'hey_jarvis' in caminho), None)
    
    if not caminho_jarvis:
        logging.critical("Erro: Pre trained Jarvis Model not found!")
        sys.exit(1)
        
    oww_model = Model(wakeword_model_paths=[caminho_jarvis])

    if hardware_choice in ["gpu", "cuda"]:
        stt_device = "cuda"
        stt_compute = "float16"
        logging.info(f"Booting Whisper STT Engine ({size_model}) -> Optimized for GRAPHICS CARD (CUDA)")
    else:
        stt_device = "cpu"
        stt_compute = "int8"
        logging.info(f"Booting Whisper STT Engine ({size_model}) -> Optimized for PROCESSOR (CPU)")

    try:
        stt_model = WhisperModel(size_model, device=stt_device, compute_type=stt_compute)
    except Exception as e:
        logging.critical(f"Error loading voice module: {e}")
        if (hardware_choice in ["gpu", "cuda"]):
            logging.critical("Tip: if chosen device = 'gpu', verify if CUDA drivers are installed. Retrying with CPU.")
            stt_device = "cpu"
            stt_compute = "int8"
            
            try: stt_model = WhisperModel(size_model, device=stt_device, compute_type=stt_compute)
            except Exception as e: sys.exit(1)
        else:
            sys.exit(1)

    with silence_alsa():
        audio = pyaudio.PyAudio()
        
    mic_stream = None
    
    try:
        mic_stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
        logging.info(f"--- SYSTEM READY: Listening for '{target_word}' ---")

        start_mqtt_listener()
        
        global FORCE_MIC

        while True:
            audio_data = np.frombuffer(mic_stream.read(CHUNK, exception_on_overflow=False), dtype=np.int16)
            prediction = oww_model.predict(audio_data)
            
            for mdl in oww_model.prediction_buffer.keys():
                scores = list(oww_model.prediction_buffer[mdl])
                
                if scores[-1] > 0.5 or FORCE_MIC:
                    
                    if FORCE_MIC:
                        FORCE_MIC = False
                    else:
                        print("\n" + "="*50)
                        logging.info("WAKE WORD DETECTED!")
                        
                    command_audio = record_command(mic_stream)
                    
                    settings = load_settings()
                    lang_chosen = settings.get("language", "auto")
                    
                    transcribe_args = {
                        "audio": command_audio,
                        "beam_size": 2,
                        "vad_filter": True,
                        "vad_parameters": dict(min_silence_duration_ms=500)
                    }
                    
                    if lang_chosen != "auto":
                        transcribe_args["language"] = lang_chosen

                    segments, info = stt_model.transcribe(**transcribe_args)
                    
                    if lang_chosen == "auto":
                        logging.info(f"Automatic language detected: {info.language} (Certainty: {info.language_probability:.2f})")
                    
                    text = "".join([segment.text for segment in segments]).strip()
                    logging.info(f"Heard: \"{text}\"")
                    
                    if text:
                        logging.info("Forwarding transcript to Central Daemon...")
                        try:
                            publish.single("jarvis/sensor/voice", text, hostname="localhost")
                        except Exception as e:
                            logging.error(f"Failed to reach MQTT Broker: {e}")
                        
                    print("="*50 + "\n")
                    oww_model.reset()
                    
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