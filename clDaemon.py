import sys
import asyncio
import os
import json
import aiomqtt
from dotenv import load_dotenv
from tapo import ApiClient, requests

# --- WINDOWS ASYNCIO FIX ---
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    Color = getattr(requests, 'Color')
except Exception:
    print("ERROR: Could not find 'Color' in tapo library.")

# --- CREDENTIALS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)

EMAIL = os.getenv("TAPO_EMAIL")
PASSWORD = os.getenv("TAPO_PASSWORD")
BULB_IP = os.getenv("TAPO_IP")
BULB_MODEL = os.getenv("TAPO_MODEL")

# --- CUSTOM COLORS ---
CUSTOM_COLORS = {
    "aesthetic": {
        "AmberGlow": (35, 40), "CyberpunkPink": (320, 35), "DeepSea": (215, 30),
        "ForestMist": (145, 20), "MidnightRose": (340, 20), "SoftMauve": (280, 15),
        "SoftRed": (0, 20), "VaporwaveBlue": (190, 30), "ShadyPurple": (186, 100),
    },
    "productivity": {
        "CleanSky": (200, 5), "DesertSand": (40, 8), "FocusGold": (45, 10),
        "Moonlight": (220, 8), "ZenPeach": (25, 12)
    }
}

# --- THE SUBSCRIBER ENGINE ---
async def process_payload(device, data):
    """Safely executes complex commands without race conditions."""
    target_power_state = None
    
    # 1. RESOLVE POWER STATE FIRST
    if "action" in data:
        action = data["action"]
        if action == "toggle":
            info = await device.get_device_info()
            if info.device_on:
                await device.off()
                target_power_state = False
            else:
                await device.on()
                target_power_state = True
        elif action == "on":
            await device.on()
            target_power_state = True
        elif action == "off":
            await device.off()
            target_power_state = False

    # 2. APPLY PROPERTIES IF ON
    if target_power_state is not False:
        tasks = []
        if "lum" in data:
            tasks.append(device.set_brightness(data["lum"]))
        
        if "temp" in data:
            temp = data["temp"]
            tasks.append(device.set_color_temperature(int(2500 + temp*(6500-2500)/100)))

        if "color" in data:
            input_clean = data["color"].lower()
            target_key = None
            
            for group in CUSTOM_COLORS:
                for k in CUSTOM_COLORS[group].keys(): 
                    if k.lower() == input_clean:
                        target_key = k
                        h, s = CUSTOM_COLORS[group][target_key]
                        tasks.append(device.set_hue_saturation(h, s))
                        break
                if target_key: break
            
            if not target_key:
                target_key = next((k for k in dir(Color) if k.lower() == input_clean), None)
                if target_key:
                    tasks.append(device.set_color(getattr(Color, target_key)))

        if tasks:
            await asyncio.gather(*tasks)

async def listen_to_broker(tapo_client, initial_device):
    device = initial_device
    try:
        async with aiomqtt.Client("localhost") as mqtt_client:
            await mqtt_client.subscribe("home/room/desk_light/set")
            print("[DAEMON] Listening to MQTT topic: home/room/desk_light/set")
            
            async for message in mqtt_client.messages:
                payload_str = message.payload.decode()
                print(f"\n[DAEMON] MQTT Received: {payload_str}")
                
                try:
                    data = json.loads(payload_str)
                    
                    # Attempt execution
                    await process_payload(device, data)
                    print("[DAEMON] Execution Successful.")
                    
                except json.JSONDecodeError:
                    print("[DAEMON] Error: Invalid JSON payload.")
                    
                except Exception as e:
                    # SELF-HEALING HOOK: If the bulb dropped the socket, catch it and rebuild!
                    print(f"[DAEMON] Tapo execution failed (Session likely dropped): {type(e).__name__} - {e}")
                    print("[DAEMON] Attempting to rebuild secure Tapo session...")
                    try:
                        get_device = getattr(tapo_client, (BULB_MODEL).lower())
                        device = await get_device(BULB_IP)
                        await process_payload(device, data)
                        print("[DAEMON] Recovery Successful! Command executed.")
                    except Exception as recovery_error:
                        print(f"[DAEMON] Critical Recovery Failure: {recovery_error}")
                        print("Ensure the bulb is powered on at the wall.")
                        
    except Exception as e:
        print(f"[DAEMON] Critical MQTT Broker Error: {e}")

async def main():
    print("[DAEMON] Booting up and warming Tapo connection...")
    try:
        tapo_client = ApiClient(EMAIL, PASSWORD)
        get_device = getattr(tapo_client, (BULB_MODEL).lower())
        initial_device = await get_device(BULB_IP)
        print("[DAEMON] Tapo connection established.")
        
        await listen_to_broker(tapo_client, initial_device)
        
    except Exception as e:
        print(f"[DAEMON] Fatal Error on Boot: {e}")

if __name__ == "__main__":
    asyncio.run(main())