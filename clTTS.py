# --- IMPORTS ---
import asyncio
import argparse
import logging
import json
import os
import edge_tts
from pygame import mixer
import aiomqtt

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [TTS] %(message)s", datefmt="%H:%M:%S")

# --- CONFIGURATION ---
DEFAULT_VOICE = "en-GB-RyanNeural" 
DEFAULT_PITCH = "-5Hz"
DEFAULT_RATE = "+5%"
TEMP_FILE = "tts_output.mp3"

def init_audio():
    """Initializes the pygame mixer for MP3 playback."""
    mixer.init()
    logging.info("Audio mixer initialized.")

async def generate_and_play(text, voice=DEFAULT_VOICE, rate=DEFAULT_RATE, pitch=DEFAULT_PITCH):
    """Generates speech via Edge-TTS, saves to temp file, and plays it."""
    logging.info(f"Generating speech: '{text}' (Voice: {voice})")
    
    try:
        # 1. Generate the audio asynchronously
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicate.save(TEMP_FILE)
        
        # 2. Load and play the audio
        mixer.music.load(TEMP_FILE)
        mixer.music.play()
        
        # 3. Wait for playback to finish naturally without blocking asyncio
        while mixer.music.get_busy():
            await asyncio.sleep(0.1)
            
        # 4. Clean up the file so we don't clutter the disk
        mixer.music.unload()
        if os.path.exists(TEMP_FILE):
            os.remove(TEMP_FILE)
            
        logging.info("Playback complete.")
        
    except Exception as e:
        logging.error(f"Error during TTS generation/playback: {e}")

# --- MQTT MICROSERVICE LOOP ---
async def tts_service_listener():
    """Listens endlessly for text strings to speak."""
    logging.info("TTS Microservice initialized. Listening on MQTT topics...")
    try:
        async with aiomqtt.Client("localhost") as client:
            await client.subscribe("jarvis/sys/speak")
            
            async for message in client.messages:
                try:
                    payload = json.loads(message.payload.decode('utf-8'))
                    text_to_speak = payload.get("text")
                    
                    if text_to_speak:
                        asyncio.create_task(generate_and_play(text_to_speak))
                    else:
                        logging.warning("Received TTS payload, but 'text' field was missing.")
                        
                except json.JSONDecodeError:
                    raw_text = message.payload.decode('utf-8').strip()
                    if raw_text:
                        asyncio.create_task(generate_and_play(raw_text))
                    else:
                        logging.error("Received malformed or empty data.")
                        
    except aiomqtt.MqttError as e:
        logging.error(f"MQTT Connection Error: {e} (Is Mosquitto running?)")
    except asyncio.CancelledError:
        logging.info("TTS Service shutting down.")

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="Microservice Control for TTS Engine")
    
    parser.add_argument("-t", "--text", type=str, help="Text to speak immediately from the terminal")
    parser.add_argument("-v", "--voice", type=str, default=DEFAULT_VOICE, help="Override default voice for testing")
    
    args = parser.parse_args()

    init_audio()
    
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    # Branch 1: Manual Direct Command
    if args.text:
        logging.info("Executing manual terminal command directly...")
        asyncio.run(generate_and_play(args.text, voice=args.voice))
        
    # Branch 2: Boot into Microservice Mode
    else:
        try:
            asyncio.run(tts_service_listener())
        except KeyboardInterrupt:
            logging.info("Exiting TTS Service.")

if __name__ == "__main__":
    main()