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
DEFAULT_RATE = "+20%"
TEMP_FILE = "tts_output.mp3"

def init_audio():
    """Initializes the pygame mixer for MP3 playback."""
    mixer.init()
    logging.info("Audio mixer initialized.")

async def generate_and_play(client, text, voice=DEFAULT_VOICE, rate=DEFAULT_RATE, pitch=DEFAULT_PITCH, duck_audio=True):
    """Generates speech via Edge-TTS, saves to temp file, ducks audio conditionally, and plays it."""
    logging.info(f"Generating speech: '{text}' (Voice: {voice})")
    
    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicate.save(TEMP_FILE)
        
        # Conditionally duck audio
        if client and duck_audio:
            await client.publish("pc/spotify/control", json.dumps({"action": "duck"}))
            await asyncio.sleep(0.2) 
        
        """ if os.path.exists("blip.mp3"):
            mixer.music.load("blip.mp3")
            mixer.music.set_volume(0.3)
            mixer.music.play()
            while mixer.music.get_busy():
                await asyncio.sleep(0.05)
            mixer.music.set_volume(1) """
        
        mixer.music.load(TEMP_FILE)
        mixer.music.play()
        
        while mixer.music.get_busy():
            await asyncio.sleep(0.1)
            
        mixer.music.unload()
        if os.path.exists(TEMP_FILE):
            os.remove(TEMP_FILE)
            
        logging.info("Playback complete.")
        
    except Exception as e:
        logging.error(f"Error during TTS generation/playback: {e}")
        
    finally:
        # Conditionally unduck audio
        if client and duck_audio:
            await client.publish("pc/spotify/control", json.dumps({"action": "unduck"}))
            
        # Always send the handshake completion signal
        if client:
            await client.publish("jarvis/sys/tts_done", "1")

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
                    skip_duck = payload.get("skip_ducking", False)
                    
                    if text_to_speak:
                        asyncio.create_task(generate_and_play(client, text_to_speak, duck_audio=not skip_duck))
                    else:
                        logging.warning("Received TTS payload, but 'text' field was missing.")
                        
                except json.JSONDecodeError:
                    raw_text = message.payload.decode('utf-8').strip()
                    if raw_text:
                        asyncio.create_task(generate_and_play(client, raw_text))
                    else:
                        logging.error("Received malformed or empty data.")
                        
    except aiomqtt.MqttError as e:
        logging.error(f"MQTT Connection Error: {e}")
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
        
        asyncio.run(generate_and_play(None, args.text, voice=args.voice))
        
    # Branch 2: Boot into Microservice Mode
    else:
        try:
            asyncio.run(tts_service_listener())
        except KeyboardInterrupt:
            logging.info("Exiting TTS Service.")

if __name__ == "__main__":
    main()