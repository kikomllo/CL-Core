# --- IMPORTS ---
import asyncio
import argparse
import logging
import json
import os
import edge_tts
from pygame import mixer
import aiomqtt
import uuid

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [TTS] %(message)s", datefmt="%H:%M:%S")

# --- CONFIGURATION ---
DEFAULT_VOICE = "en-GB-RyanNeural" 
DEFAULT_PITCH = "-5Hz"
DEFAULT_RATE = "+27%"

# --- STATE MANAGEMENT ---
TTS_LOCK = asyncio.Lock()  
ACTIVE_TTS_TASKS = 0

def init_audio():
    """Initializes the pygame mixer for MP3 playback."""
    mixer.init()
    logging.info("Audio mixer initialized.")

async def generate_and_play(client, text, voice=DEFAULT_VOICE, rate=DEFAULT_RATE, pitch=DEFAULT_PITCH, duck_audio=True, request_reply=False):
    """Generates speech via Edge-TTS, saves to unique temp file, and plays it safely."""
    
    async with TTS_LOCK:
        logging.info(f"Generating speech: '{text}' (Voice: {voice})")
        
        # 1. Dynamically locate the assets folder
        base_dir = os.path.dirname(os.path.abspath(__file__))
        assets_dir = os.path.abspath(os.path.join(base_dir, "..", "assets"))
        
        # 2. Assign the temporary file and blip sound
        temp_file = os.path.join(assets_dir, f"tts_output_{uuid.uuid4().hex}.mp3")
        blip_path = os.path.join(assets_dir, "blip.mp3")
        
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            await communicate.save(temp_file)
            
            if client and duck_audio:
                await client.publish("pc/spotify/control", json.dumps({"action": "duck"}))
                await asyncio.sleep(0.2) 
            
            # 3. Play the absolute path blip
            if os.path.exists(blip_path):
                mixer.music.load(blip_path)
                mixer.music.play()
                while mixer.music.get_busy():
                    await asyncio.sleep(0.05)
                mixer.music.set_volume(2)
                    
            
            mixer.music.load(temp_file)
            mixer.music.play()
            
            while mixer.music.get_busy():
                await asyncio.sleep(0.05)
                
            mixer.music.unload()
            logging.info("Playback complete.")
            
        except Exception as e:
            logging.error(f"Error during TTS generation/playback: {e}")
            
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception as e:
                    logging.warning(f"Could not delete temp file {temp_file}: {e}")
            
            if client and duck_audio:
                await client.publish("pc/spotify/control", json.dumps({"action": "unduck"}))
                
            # --- FIX: Only trigger the mic handshake if the brain explicitly requested a reply ---
            if client and request_reply:
                await client.publish("jarvis/sys/tts_done", "1")

# --- MQTT MICROSERVICE LOOP ---
async def tts_service_listener():
    """Listens endlessly for text strings to speak."""
    logging.info("TTS Microservice initialized. Listening on MQTT topics...")
    try:
        async with aiomqtt.Client("localhost") as client:
            await client.subscribe("jarvis/sys/speak")
            await client.subscribe("jarvis/sys/tts_stop") # <-- NEW: Subscribe to the kill switch
            
            async for message in client.messages:
                topic = message.topic.value
                
                # --- FIX: Native Pygame Audio Interrupt ---
                if topic == "jarvis/sys/tts_stop":
                    logging.info("TTS Kill switch received! Halting audio output.")
                    mixer.music.stop() # This instantly breaks the 'get_busy()' loop in the playback function!
                    continue
                
                # Normal speech handling...
                if topic == "jarvis/sys/speak":
                    try:
                        payload = json.loads(message.payload.decode('utf-8'))
                        text_to_speak = payload.get("text")
                        skip_duck = payload.get("skip_ducking", False)
                        request_reply = payload.get("request_reply", False)  
                        
                        if text_to_speak:
                            asyncio.create_task(generate_and_play(
                                client, 
                                text_to_speak, 
                                duck_audio=not skip_duck, 
                                request_reply=request_reply
                            ))
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