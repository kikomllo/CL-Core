# --- IMPORTS ---
import asyncio
import argparse
import platform
import socket
import os
import logging
from dotenv import load_dotenv, set_key
from tapo import ApiClient, requests
import aiomqtt
import json
import sys

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [CONTROL] %(message)s", datefmt="%H:%M:%S")

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    Color = getattr(requests, 'Color')
except Exception as e:
    logging.error("Could not find 'Color' module in Tapo library.")
    sys.exit(1)

# --- CREDENTIALS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_PATH)

EMAIL = os.getenv("TAPO_EMAIL")
PASSWORD = os.getenv("TAPO_PASSWORD")
BULB_IP = os.getenv("TAPO_IP")
BULB_MODEL = os.getenv("TAPO_MODEL")

if (not (EMAIL and PASSWORD)): 
    logging.error("Credentials not found in .env file.")
    sys.exit(1)

# --- CUSTOM COLORS ---
CUSTOM_COLORS = {
    "aesthetic": {
        "AmberGlow": (35, 40),
        "CyberpunkPink": (320, 35),
        "DeepSea": (215, 30),
        "ForestMist": (145, 20),
        "MidnightRose": (340, 20),
        "SoftMauve": (280, 15),
        "SoftRed": (0, 20),
        "VaporwaveBlue": (190, 30),
        "ShadyPurple": (186, 100),
    },
    "productivity": {
        "CleanSky": (200, 5),
        "DesertSand": (40, 8),
        "FocusGold": (45, 10),
        "Moonlight": (220, 8),
        "ZenPeach": (25, 12)
    }
}

LAST_DISCOVERED_DEVICES = []

# --- STATUS ---
async def get_status(client):
    get_device = getattr(client, (BULB_MODEL).lower())
    device = await get_device(BULB_IP)
    
    info = await device.get_device_info()
    
    on = True if (info.device_on) else False
    brightness = info.brightness
    hue = getattr(info, 'hue', 'N/A')
    color_temp = getattr(info, 'color_temp', 'N/A')
    saturation = getattr(info, 'saturation', 'N/A')

    # Kept as print() for clean CLI table formatting
    print(f"\n--- Bulb Status ---")
    print(f"Power:\t\t{'ON' if on else 'OFF'}")
    print(f"Brightness:\t{brightness}%")
    if on == True:
        print(f"Hue:\t\t{hue}")
        print(f"Temperature:\t{color_temp}")
        print(f"Saturation:\t{saturation}")
    print("")

# --- HELPER FUNC DEFAULT COLORS ---
def get_valid_colors():
    return [k for k in dir(Color) if not k.startswith("_") and k[0].isupper()]

# --- COLOR LIST ---
def get_list():
    # Kept as print() for clean CLI table formatting
    print("\n {:<19} {:<38} {}".format("---", "AVAILABLE COLOR PRESETS", "---"))
    colors = get_valid_colors()
    print("{}".format("-"*68))    
    print("{:<22} {:<30} {}\n".format("", "Default Color Presets", ""))
    for i in range(0, len(colors), 4):
        print("{:<18} {:<18} {:<18} {:<18}".format(*colors[i:i+4] + [""] * (4-len(colors[i:i+4]))))
    print("{}".format("-"*68))
    
    for group in CUSTOM_COLORS.keys():
        group_keys = list(CUSTOM_COLORS[group].keys())
        print("{:<22} {:<30} {}\n".format("", group.capitalize() + " Collection", ""))
        for i in range(0, len(group_keys), 4):
            print("{:<18} {:<18} {:<18} {:<18}".format(*group_keys[i:i+4] + [""] * (4-len(group_keys[i:i+4]))))
        print("{}".format("-"*68))
    print("\n")

# --- HELPER: GET SUBNET BASE ---
def get_subnet_base():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        parts = local_ip.split('.')
        return f"{parts[0]}.{parts[1]}.{parts[2]}"
    except Exception:
        return "192.168.1"

# --- HELPER: ASYNC PING SWEEP ---
async def async_ping(ip, semaphore):
    param_count = '-n' if platform.system().lower() == 'windows' else '-c'
    param_timeout = '-w' if platform.system().lower() == 'windows' else '-W'
    timeout_val = '500' if platform.system().lower() == 'windows' else '1' 
    async with semaphore:
        try:
            process = await asyncio.create_subprocess_exec(
                'ping', param_count, '1', param_timeout, timeout_val, ip,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await process.wait()
            if process.returncode == 0:
                return ip
        except Exception:
            pass
    return None

# --- HELPER: ASYNC TCP PROBE (BATCHED) ---
async def check_ip(client, ip, semaphore):
    async with semaphore:
        try:
            get_device = getattr(client, (BULB_MODEL).lower())
            device = await get_device(ip)
            info = await asyncio.wait_for(device.get_device_info(), timeout=0.8)
            model = getattr(info, 'model', 'Unknown')
            return {"ip": ip, "model": model.upper()}
        except Exception:
            return None

# --- DISCOVERY: ASYNC SUBNET SWEEP ---
async def discovery_mode(client, voice_mode=False):
    global LAST_DISCOVERED_DEVICES
    
    base_ip = get_subnet_base()
    logging.info(f"Initiating Two-Stage Network Sweep on {base_ip}.X...")
    
    ips_to_check = [f"{base_ip}.{i}" for i in range(1, 255)]
    
    logging.info("Stage 1: Pinging 254 IPs to find active hosts...")
    ping_sem = asyncio.Semaphore(150)
    ping_tasks = [async_ping(ip, ping_sem) for ip in ips_to_check]
    alive_ips_results = await asyncio.gather(*ping_tasks)
    
    alive_ips = [ip for ip in alive_ips_results if ip is not None]
    if not alive_ips:
        logging.warning("Sweep complete. No active devices found on the network.")
        return
        
    logging.info(f"Found {len(alive_ips)} active devices on the network.")

    logging.info("Stage 2: Probing active devices for Tapo bulbs...")
    tcp_sem = asyncio.Semaphore(50)
    tcp_tasks = [check_ip(client, ip, tcp_sem) for ip in alive_ips]
    results = await asyncio.gather(*tcp_tasks)
    
    devices = [res for res in results if res is not None]
    if not devices:
        logging.warning("Sweep complete. None of the active hosts were Tapo devices.")
        return

    LAST_DISCOVERED_DEVICES = devices

    # Tabular data remains as print()
    print(f"\nFound {len(devices)} device(s):")
    print("{:<5} {:<18} {:<18}".format("#", 'MODEL', "IP"))
    print("-" * 45)
    for i, dev in enumerate(devices):
        print("{:<5} {:<18} {:<18}".format(i, dev['model'], dev['ip']))
    print("")

    if voice_mode:
        logging.info("Awaiting user's voice response to save device...")
        try:
            async with aiomqtt.Client("localhost") as mqtt_client:
                await mqtt_client.publish("jarvis/sys/mic_open", "1")
        except Exception as e:
            logging.error(f"Error booting microphone remotely: {e}")
        return 

    else:
        choice = input("\nSelect a device number to use (or press Enter to cancel): ")
        if choice.isdigit() and int(choice) < len(devices):
            selected = devices[int(choice)]
            update_env = input(f"Save {selected['ip']} ({selected['model']}) to .env? (y/n): ")
            if update_env.lower() == 'y':
                env_path = os.path.join(os.path.dirname(__file__), ".env")
                set_key(env_path, "TAPO_IP", selected["ip"])
                set_key(env_path, "TAPO_MODEL", selected["model"])
                
                global BULB_IP, BULB_MODEL
                BULB_IP = selected["ip"]
                BULB_MODEL = selected["model"]
                logging.info("Variables successfully written to .env and updated in RAM.")

# --- MAIN CONTROL (DYNAMIC TARGETING) ---
async def control_bulb(client, target_ip=BULB_IP, target_model=BULB_MODEL, toggle=None, on=None, off=None, color=None, lum=None, temp=None):    
    if not target_ip or not target_model:
        logging.error("Target IP or Model is missing. Cannot execute command.")
        return

    try:
        get_device = getattr(client, target_model.lower())
        device = await get_device(target_ip)

        if (not device):
            logging.error(f"Error connecting to bulb with IP: {target_ip}.")
            return
        
        target_power_state = None
        
        if (toggle):
            info = await device.get_device_info()
            if (info.device_on): 
                await device.off()
                target_power_state = False
            else: 
                await device.on()
                target_power_state = True
        elif on:
            await device.on()
            target_power_state = True
        elif off:
            await device.off()
            target_power_state = False

        if target_power_state is not False:
            tasks = []
            if lum:
                tasks.append(device.set_brightness(lum))
                logging.info(f"Setting brightness to {lum}%")

            if temp:
                tasks.append(device.set_color_temperature(int(2500 + temp*(6500-2500)/100)))
                logging.info(f"Setting color temperature to {temp}%")

            if color:
                input_clean = color.lower()
                target_key = None
                
                for group in CUSTOM_COLORS:
                    for k in CUSTOM_COLORS[group].keys(): 
                        if k.lower() == input_clean:
                            target_key = k
                            h, s = CUSTOM_COLORS[group][target_key]
                            break
                    if target_key: break
                
                if (target_key):
                    tasks.append(device.set_hue_saturation(h, s))
                    logging.info(f"Setting color to: {target_key}")
                else:
                    target_key = next((k for k in dir(Color) if k.lower() == input_clean), None)
                    if target_key:
                        target_obj = getattr(Color, target_key)
                        tasks.append(device.set_color(target_obj))
                        logging.info(f"Setting color to: {target_key}")
                    else:
                        logging.warning(f"Color '{color}' not recognized.")

            if tasks:
                await asyncio.gather(*tasks)

    except Exception as e:
        logging.error(f"Failed to connect or send command to bulb {target_ip}: {e}")

# --- MQTT SERVICE LISTENER (MICROSERVICE MODE) ---
async def mqtt_service_listener(client_api):
    """Listens endlessly for commands from the Central Daemon."""
    logging.info("Service Mode initialized. Listening on MQTT topics...")
    try:
        async with aiomqtt.Client("localhost") as mqtt_client:
            await mqtt_client.subscribe("home/room/desk_light/set")
            await mqtt_client.subscribe("system/discovery")
            
            async for message in mqtt_client.messages:
                try:
                    payload = json.loads(message.payload.decode('utf-8'))
                    logging.info(f"Command Received: {payload}")
                    
                    action = payload.get("action")
                    
                    if action == "discover":
                        logging.info("Initiating Network Discovery via Voice Command.")
                        asyncio.create_task(discovery_mode(client_api, voice_mode=True))
                        continue
                        
                    if action == "save_discovery":
                        idx = payload.get("index")
                        if idx is not None and 0 <= idx < len(LAST_DISCOVERED_DEVICES):
                            selected = LAST_DISCOVERED_DEVICES[idx]
                            env_path = os.path.join(os.path.dirname(__file__), ".env")
                            set_key(env_path, "TAPO_IP", selected["ip"])
                            set_key(env_path, "TAPO_MODEL", selected["model"])
                            
                            global BULB_IP, BULB_MODEL
                            BULB_IP = selected["ip"]
                            BULB_MODEL = selected["model"]
                            
                            logging.info(f"SUCCESS: Device {selected['model']} ({selected['ip']}) was saved in .env and live memory!")
                        else:
                            logging.error("The spoken number was not found in the device list.")
                        continue
                        
                    # --- NORMAL CONTROL ---
                    ip_target = payload.get("ip", BULB_IP)
                    model_target = payload.get("model", BULB_MODEL)
                    
                    try:
                        await control_bulb(
                            client=client_api,
                            target_ip=ip_target,
                            target_model=model_target,
                            on=(action == "on"),
                            off=(action == "off"),
                            toggle=(action == "toggle"),
                            color=payload.get("color"),
                            lum=payload.get("lum"),
                            temp=payload.get("temp")
                        )
                        # --- SEND FEEDBACK TO BRAIN ---
                        feedback = {
                            "device": "tapo_lights",
                            "status": "success",
                            "message": f"Executed action '{action}' on {model_target}."
                        }
                        await mqtt_client.publish("jarvis/feedback", json.dumps(feedback))
                        
                    except Exception as e:
                        # --- NEW: SEND ERROR TO BRAIN ---
                        feedback = {
                            "device": "tapo_lights",
                            "status": "error",
                            "message": str(e)
                        }
                        await mqtt_client.publish("jarvis/feedback", json.dumps(feedback))
                        
                except json.JSONDecodeError:
                    logging.error("Received malformed JSON data.")
    except aiomqtt.MqttError as e:
        logging.error(f"MQTT Connection Error: {e} (Is Mosquitto running?)")
    except asyncio.CancelledError:
        logging.info("Service shutting down.")

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="Microservice Control for Tapo Bulb")
    
    parser.add_argument("--color", "-c", type=str, help="Color name (e.g., RED, BLUE, CyberpunkPink)")
    parser.add_argument("-l", "--lum", "--Luminance", "-b", "--brightness", type=int, help="Luminance level (1-100)")
    parser.add_argument("-t", "--temp", "--temperature", type=int, help="Color Temp level (1-100)")
    parser.add_argument("--toggle", action="store_true", help="Turn Light On/Off")
    parser.add_argument("--on", action="store_true", help="Turn Light On")
    parser.add_argument("--off", action="store_true", help="Turn Light Off")
    
    parser.add_argument("--status", action="store_true", help="Check current Bulb state")
    parser.add_argument("--list", action="store_true", help="Show all available color names")
    parser.add_argument("-d", "--discovery", action="store_true", help="Show all available devices in network")
    
    args = parser.parse_args()

    # 1. Handle Utility Commands
    if args.list: 
        get_list()
        return
        
    client = ApiClient(EMAIL, PASSWORD) if (EMAIL and PASSWORD) else None

    if args.discovery:
        if client: asyncio.run(discovery_mode(client))
        return
        
    if args.status:
        if client and BULB_IP and BULB_MODEL:
            asyncio.run(get_status(client))
        else:
            logging.error("Missing IP or MODEL in .env for status check.")
        return

    # 2. Check for Manual Direct Commands
    has_direct_command = any([args.on, args.off, args.toggle, args.color, args.lum, args.temp])

    if has_direct_command:
        logging.info("Executing manual override command directly...")
        asyncio.run(control_bulb(
            client=client,
            target_ip=BULB_IP,
            target_model=BULB_MODEL,
            toggle=args.toggle,
            on=args.on,
            off=args.off,
            color=args.color,
            lum=args.lum,
            temp=args.temp
        ))
    else:
        # 3. Boot into Microservice Mode if no arguments are provided
        if client:
            try:
                asyncio.run(mqtt_service_listener(client))
            except KeyboardInterrupt:
                logging.info("Exiting Service Mode.")

# --- RUN MAIN ---
if __name__ == "__main__":
    main()