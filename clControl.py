# --- IMPORTS ---
import asyncio
import argparse
import platform
import socket
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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_PATH)

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

# --- HELPER: GET SUBNET BASE ---
def get_subnet_base():
    """Finds the local network base (e.g., 192.168.1)."""
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
    """Sends a single ICMP ping asynchronously to check if a host is up."""
    
    param_count = '-n' if platform.system().lower() == 'windows' else '-c'
    param_timeout = '-w' if platform.system().lower() == 'windows' else '-W'
    timeout_val = '500' if platform.system().lower() == 'windows' else '1' 

    async with semaphore:
        try:
            # Spawn a silent OS-level ping subprocess
            process = await asyncio.create_subprocess_exec(
                'ping', param_count, '1', param_timeout, timeout_val, ip,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await process.wait()
            
            # Return code 0 means the ping was successful
            if process.returncode == 0:
                return ip
        except Exception:
            pass
    return None

# --- HELPER: ASYNC TCP PROBE (BATCHED) ---
async def check_ip(client, ip, semaphore):
    """Attempts a direct authenticated TCP handshake using a Semaphore to prevent socket exhaustion."""
    async with semaphore:
        try:
            get_device = getattr(client, (BULB_MODEL).lower())
            device = await get_device(ip)

            # Fast timeout since host is alive
            info = await asyncio.wait_for(device.get_device_info(), timeout=0.8)
            model = getattr(info, 'model', 'Unknown')
            return {"ip": ip, "model": model.upper()}
        except Exception as e:
            if ip == "192.168.1.90":
                print(f"[DEBUG] .90 Failed! Error: {type(e).__name__} - {e}")
            return None

# --- DISCOVERY: ASYNC SUBNET SWEEP ---
async def discovery_mode(client):
    base_ip = get_subnet_base()
    print(f"Initiating Two-Stage Network Sweep on {base_ip}.X...")
    
    ips_to_check = [f"{base_ip}.{i}" for i in range(1, 255)]
    
    # --- STAGE 1: ICMP PING SWEEP ---
    print(f"Stage 1: Pinging 254 IPs to find active hosts...")
    ping_sem = asyncio.Semaphore(150) # High concurrency is safe for ICMP
    ping_tasks = [async_ping(ip, ping_sem) for ip in ips_to_check]
    alive_ips_results = await asyncio.gather(*ping_tasks)
    
    alive_ips = [ip for ip in alive_ips_results if ip is not None]
    
    if not alive_ips:
        print("\nSweep complete. No active devices found on the network.")
        return
        
    print(f"-> Found {len(alive_ips)} active devices on the network.")

    # --- STAGE 2: TCP KLAP HANDSHAKE ---
    print("Stage 2: Probing active devices for Tapo bulbs...")
    tcp_sem = asyncio.Semaphore(50)
    tcp_tasks = [check_ip(client, ip, tcp_sem) for ip in alive_ips]
    results = await asyncio.gather(*tcp_tasks)
    
    devices = [res for res in results if res is not None]
    
    if not devices:
        print("\nSweep complete. None of the active hosts were Tapo devices.")
        return

    print(f"\nFound {len(devices)} device(s):")
    print("{:<5} {:<18} {:<18}".format("#", 'MODEL', "IP"))
    print("-" * 45)
    
    for i, dev in enumerate(devices):
        print("{:<5} {:<18} {:<18}".format(i, dev['model'], dev['ip']))

    choice = input("\nSelect a device number to use (or press Enter to cancel): ")
    if choice.isdigit() and int(choice) < len(devices):
        selected = devices[int(choice)]
        
        update_env = input(f"Save {selected['ip']} ({selected['model']}) to .env? (y/n): ")
        if update_env.lower() == 'y':
            env_path = os.path.join(os.path.dirname(__file__), ".env")
            set_key(env_path, "TAPO_IP", selected["ip"])
            set_key(env_path, "TAPO_MODEL", selected["model"])
            print("Variables successfully written to .env!")
            
# --- MAIN CONTROL ---
async def control_bulb(client, toggle=None, on=None, off=None, color=None, lum=None, temp=None):    
    try:
        get_device = getattr(client, (BULB_MODEL).lower())
        device = await get_device(BULB_IP)

        if (not device):
            print(f"Error connecting to bulb with ip: {BULB_IP}. Make sure the bulb is on and connect to your current Network.")
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
                print(f"Set brightness to {lum}%")

            if temp:
                tasks.append(device.set_color_temperature(int(2500 + temp*(6500-2500)/100)))
                print(f"Set color temperature to {temp}%")

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
        print(f"Error connecting to bulb with ip: {BULB_IP}.\nError: {e}")
        
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