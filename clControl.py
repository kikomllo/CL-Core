# --- IMPORTS ---
import asyncio
import argparse
import sys
import os
from dotenv import load_dotenv, set_key

from tapo import ApiClient, requests

try:
    Color = getattr(requests, 'Color')
except Exception as e:
    print("ERROR: Could not find 'Color'")
    sys.exit(1)

# --- CREDENTIALS ---
load_dotenv()

EMAIL = os.getenv("TAPO_EMAIL")
PASSWORD = os.getenv("TAPO_PASSWORD")
BULB_IP = os.getenv("TAPO_IP")
BULB_MODEL = os.getenv("TAPO_MODEL")

if (not (EMAIL and PASSWORD)): 
    print("Error: Credentials not found.")
    sys.exit(1)

# --- CUSTOM COLORS ---
CUSTOM_COLORS = {
    # --- AESTHETIC COLLECTION ---
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
    
    # --- PRODUCTIVITY COLLECTION ---
    "productivity": {
        "CleanSky": (200, 5),
        "DesertSand": (40, 8),
        "FocusGold": (45, 10),
        "Moonlight": (220, 8),
        "ZenPeach": (25, 12)
    }
}

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

    print(f"--- Bulb Status ---")
    print(f"Power:\t\t{'ON' if on else 'OFF'}")
    print(f"Brightness:\t{brightness}%")
    if on == True:
        print(f"Hue:\t\t{hue}")
        print(f"Temperature:\t{color_temp}")
        print(f"Saturation:\t{saturation}")

# --- HELPER FUNC DEFAULT COLORS ---
def get_valid_colors():
    return [k for k in dir(Color) if not k.startswith("_") and k[0].isupper()]

# --- COLOR LIST ---
def get_list():
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

# --- DISCOVERY ---
async def discovery_mode(client):
    print("Searching for Tapo devices...")
    
    try:
        devices_iter = await client.discover_devices("255.255.255.255")
        
        devices = []
        for item in devices_iter:
            if hasattr(item, "get"):
                dev = item.get()
                if dev:
                    devices.append(dev)

            else: devices.append(item)

        if not devices:
            print("No devices found.")
            return
        
        print(f"Found {len(devices)} device(s):")

        print("{:<18} {:<18} {:<18}".format("#", 'MODEL', "IP"))
        for i, info in enumerate(devices):
            model = getattr(info, 'model', 'Unknown')
            ip = getattr(info, 'ip', 'Unknown')
            print("{:<18} {:<18} {:<18}".format(i, model, ip))

        choice = input("\nSelect a device number to use (or press Enter to cancel): ")
        if choice.isdigit() and int(choice) < len(devices):
            selected_ip = devices[int(choice)].ip
            selected_model = devices[int(choice)].model
            print(f"Selected: {selected_ip}")
            
            update_env = input("Would you like to save this IP to your .env file? (y/n): ")
            if update_env.lower() == 'y':
                set_key(os.path.join(os.path.dirname(__file__), ".env"), "TAPO_IP", selected_ip)
                set_key(os.path.join(os.path.dirname(__file__), ".env"), "TAPO_MODEL", selected_model)
                print("IP updated in .env!")
            
    except Exception as e:
        print(f"Discovery error: {e}")
    return None


# --- MAIN CONTROL ---
async def control_bulb(client, toggle=None, on=None, off=None, color=None, lum=None, temp=None):    
    try:
        get_device = getattr(client, (BULB_MODEL).lower())
        device = await get_device(BULB_IP)

        if (not device):
            print(f"Error connecting to bulb with ip: {BULB_IP}. Make sure the bulb is on and connect to your current Network.")
            return
        
        tasks = []
        
        if on:
            tasks.append(device.on())
        elif off:
            tasks.append(device.off())
        
        if (toggle):
            info = await device.get_device_info()
    
            if (info.device_on):  tasks.append(device.off())
            elif (not info.device_on): tasks.append(device.on())

        if lum:
            tasks.append(device.set_brightness(lum))
            print(f"Set brightness to {lum}%")

        if temp:
            tasks.append(device.set_color_temperature(int(2500 + temp*(6500-2500)/100))) #2500-6500 Kelvin to percent
            print(f"Set color temperature to {temp}%")

        if color:
            input_clean = color.lower()
            target_key = None
            group = None

            for group in CUSTOM_COLORS:
                for k in CUSTOM_COLORS[group].keys(): 
                    if k.lower() == input_clean:
                        target_key = k
                        h, s = CUSTOM_COLORS[group][target_key]
                        break
                if target_key: break
            
            #target_key = next((k for k in CUSTOM_COLORS.keys() if k.lower() == input_clean), None)
            if (target_key):
                tasks.append(device.set_hue_saturation(h, s))
                print(f"Set color to: {target_key}")
            else:
                target_key = next((k for k in dir(Color) if k.lower() == input_clean), None)
            
                if target_key:
                    target_obj = getattr(Color, target_key)
                    tasks.append(device.set_color(target_obj))
                    print(f"Set color to: {target_key}")
                else:
                    print(f"Error: '{color}' not recognized. Use --list to see all options.")

        if tasks:
            await asyncio.gather(*tasks)

    except Exception as e:
        print(f"Error connecting to bulb with ip: {BULB_IP}.\nMake sure the bulb is on, this is the correct ip (use --discovery) and it is connected to your current Network.\n\nError: {e}")

async def execute_from_module(args_list):
    if not (BULB_IP and BULB_MODEL):
        print("Module Error: Missing IP/MODEL in .env")
        return
        
    client = ApiClient(EMAIL, PASSWORD)
    
    is_on = "--on" in args_list
    is_off = "--off" in args_list
    
    await control_bulb(client, on=is_on, off=is_off)

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="CLI Control for Tapo Bulb")
    
    parser.add_argument("--color", "-c", type=str, help="Color name (e.g., RED, BLUE, GREEN)")
    parser.add_argument("-l", "--lum", "--Luminance", "-b", "--brightness", type=int, help="Luminance/Brightness level (1-100)")
    parser.add_argument("-t", "--temp", "--temperature", type=int, help="Color Temperature level (1-100)")
    parser.add_argument("--toggle", action="store_true", help="Turn Light On/Off")
    parser.add_argument("--on", action="store_true", help="Turn Light On")
    parser.add_argument("--off", action="store_true", help="Turn Light Off")
    parser.add_argument("--status", action="store_true", help="Check current Bulb state")
    parser.add_argument("--list", action="store_true", help="Show all available color namesl")
    parser.add_argument("-d", "--discovery", action="store_true", help="Show all available devices in network")
    
    args = parser.parse_args()

    if args.list: 
        get_list()
        return
    
    if not (BULB_IP and BULB_MODEL) and not args.discovery:
        print("No IP address or MODEL found in .env. Run 'clControl --discover' to find your bulb.")
        return
    
    client = ApiClient(EMAIL, PASSWORD)

    if args.discovery:
        asyncio.run(discovery_mode(client))
        return

    if len(sys.argv) == 1:
        asyncio.run(control_bulb(client, toggle=True))
        return
    
    if args.status: asyncio.run(get_status(client))
    else: asyncio.run(control_bulb(client, toggle=args.toggle, on=args.on, off=args.off, color=args.color, lum=args.lum, temp=args.temp))

# --- RUN MAIN ---
if __name__ == "__main__":
    main()