# --- IMPORTS ---
import asyncio
import argparse
import platform
import socket
import os
import logging
import json
import sys
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv, set_key
from tapo import ApiClient, requests
import aiomqtt

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [CONTROL] %(message)s", datefmt="%H:%M:%S")

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    Color = getattr(requests, 'Color')
except Exception as e:
    logging.error("Could not find 'Color' module in Tapo library.")
    sys.exit(1)

# --- CONFIGURATION & CONSTANTS ---
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

COMMON_SUBNETS = ["192.168.0", "192.168.1", "192.168.2", "192.168.15", "192.168.68", "192.168.86", "10.0.0", "10.0.1"]

class TapoManager:
    """Enterprise state controller for Smart Device execution and discovery."""
    
    def __init__(self):
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        self.env_path: str = os.path.abspath(os.path.join(self.base_dir, "..", ".env"))
        load_dotenv(self.env_path)

        self.email: str = os.getenv("TAPO_EMAIL", "")
        self.password: str = os.getenv("TAPO_PASSWORD", "")
        self.bulb_ip: str = os.getenv("TAPO_IP", "")
        self.bulb_model: str = os.getenv("TAPO_MODEL", "")
        
        if not (self.email and self.password):
            logging.error("Credentials not found in .env file.")
            sys.exit(1)
            
        self.client: ApiClient = ApiClient(self.email, self.password)
        self.last_discovered_devices: List[Dict[str, str]] = []

    def update_env_credentials(self, ip: str, model: str) -> None:
        """Safely pushes discovered IP and Model targets to permanent disk storage."""
        set_key(self.env_path, "TAPO_IP", ip)
        set_key(self.env_path, "TAPO_MODEL", model)
        self.bulb_ip = ip
        self.bulb_model = model

    # --- DEVICE CONTROL ENGINE ---
    async def control_bulb(self, target_ip: str = None, target_model: str = None, 
                           toggle: bool = False, on: bool = False, off: bool = False, 
                           color: str = None, lum: int = None, temp: int = None) -> None:
        
        target_ip = target_ip or self.bulb_ip
        target_model = target_model or self.bulb_model

        if not target_ip or not target_model:
            raise ValueError("Target IP or Model is missing.")

        get_device = getattr(self.client, target_model.lower(), None)
        if not get_device:
            raise ValueError(f"Model '{target_model}' not supported by Tapo library.")
            
        device = await get_device(target_ip)
        target_power_state = None
        
        if toggle:
            info = await device.get_device_info()
            if info.device_on:
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
            if lum is not None:
                tasks.append(device.set_brightness(lum))
                logging.info(f"Setting brightness to {lum}%")

            if temp is not None:
                tasks.append(device.set_color_temperature(int(2500 + temp*(6500-2500)/100)))
                logging.info(f"Setting color temperature to {temp}%")

            if color:
                tasks.append(self._process_color_command(device, color))

            if tasks:
                await asyncio.gather(*tasks)

    async def _process_color_command(self, device: Any, color: str) -> None:
        input_clean = color.lower()
        target_key, h, s = None, None, None
        
        for group in CUSTOM_COLORS:
            for k, (hue, sat) in CUSTOM_COLORS[group].items():
                if k.lower() == input_clean:
                    target_key, h, s = k, hue, sat
                    break
            if target_key: break
        
        if target_key:
            logging.info(f"Setting custom color to: {target_key}")
            await device.set_hue_saturation(h, s)
            return
            
        target_key = next((k for k in dir(Color) if k.lower() == input_clean), None)
        if target_key:
            logging.info(f"Setting default color to: {target_key}")
            await device.set_color(getattr(Color, target_key))
            return
            
        logging.warning(f"Color '{color}' not recognized.")

    # --- NETWORK DISCOVERY ENGINE ---
    async def discovery_mode(self, voice_mode: bool = False, forced_subnet: Optional[str] = None) -> None:
        base_ip = forced_subnet if forced_subnet else self._get_subnet_base()
        
        logging.info(f"Initiating Primary Network Sweep on {base_ip}.X...")
        devices = await self._sweep_ips([f"{base_ip}.{i}" for i in range(1, 255)])
        
        if not devices:
            logging.warning(f"No Tapo devices found on {base_ip}.X. Initiating Global Sweep...")
            global_ips = [f"{sub}.{i}" for sub in COMMON_SUBNETS if sub != base_ip for i in range(1, 255)]
            devices = await self._sweep_ips(global_ips)

        if not devices:
            logging.warning("Global sweep complete. No devices found.")
            return

        self.last_discovered_devices = devices
        print(f"\nFound {len(devices)} device(s):")
        print("{:<5} {:<18} {:<18}".format("#", 'MODEL', "IP"))
        print("-" * 45)
        for i, dev in enumerate(devices):
            print("{:<5} {:<18} {:<18}".format(i, dev['model'], dev['ip']))

        if voice_mode:
            logging.info("Awaiting voice response to save device...")
            try:
                async with aiomqtt.Client("localhost") as mqtt_client:
                    await mqtt_client.publish("jarvis/sys/mic_open", "1")
            except Exception as e:
                logging.error(f"Error booting microphone remotely: {e}")
        else:
            self._handle_cli_save(devices)

    def _handle_cli_save(self, devices: List[Dict[str, str]]) -> None:
        choice = input("\nSelect a device number to use (or press Enter to cancel): ")
        if choice.isdigit() and int(choice) < len(devices):
            selected = devices[int(choice)]
            if input(f"Save {selected['ip']} ({selected['model']}) to .env? (y/n): ").lower() == 'y':
                self.update_env_credentials(selected["ip"], selected["model"])
                logging.info("Credentials written to .env and updated in RAM.")

    # --- NETWORK HELPERS ---
    def _get_subnet_base(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                parts = s.getsockname()[0].split('.')
                return f"{parts[0]}.{parts[1]}.{parts[2]}"
        except Exception:
            return "192.168.1"

    async def _sweep_ips(self, ips_to_check: List[str]) -> List[Dict[str, str]]:
        port_sem = asyncio.Semaphore(300)
        alive_ips = [ip for ip in await asyncio.gather(*(self._check_port(ip, port_sem) for ip in ips_to_check)) if ip]
        
        if not alive_ips: return []
        
        tcp_sem = asyncio.Semaphore(15)
        return [res for res in await asyncio.gather(*(self._probe_ip(ip, tcp_sem) for ip in alive_ips)) if res]

    async def _check_port(self, ip: str, semaphore: asyncio.Semaphore) -> Optional[str]:
        async with semaphore:
            for port in [443, 80]:
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=0.5)
                    writer.close()
                    await writer.wait_closed()
                    return ip
                except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                    continue
        return None

    async def _probe_ip(self, ip: str, semaphore: asyncio.Semaphore) -> Optional[Dict[str, str]]:
        async with semaphore:
            for model_guess in ["l530", "l510", "p100", "p110", "l900", "l920", "l930", "generic_device"]:
                if not hasattr(self.client, model_guess): continue
                try:
                    device = await getattr(self.client, model_guess)(ip)
                    info = await asyncio.wait_for(device.get_device_info(), timeout=3.0)
                    true_model = getattr(info, 'model', model_guess).upper()
                    return {"ip": ip, "model": true_model}
                except Exception:
                    continue
        return None

# --- MQTT SERVICE LISTENER ---
async def mqtt_service_listener(manager: TapoManager) -> None:
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
                        asyncio.create_task(manager.discovery_mode(voice_mode=True))
                        continue
                        
                    if action == "save_discovery":
                        idx = payload.get("index")
                        if idx is not None and 0 <= idx < len(manager.last_discovered_devices):
                            sel = manager.last_discovered_devices[idx]
                            manager.update_env_credentials(sel["ip"], sel["model"])
                            logging.info(f"SUCCESS: Device {sel['model']} ({sel['ip']}) saved!")
                        else:
                            logging.error("The spoken number was not found in the device list.")
                        continue

                    # Execute normal lighting actions
                    try:
                        await manager.control_bulb(
                            target_ip=payload.get("ip"), target_model=payload.get("model"),
                            on=(action == "on"), off=(action == "off"), toggle=(action == "toggle"),
                            color=payload.get("color"), lum=payload.get("lum"), temp=payload.get("temp")
                        )
                        await mqtt_client.publish("jarvis/feedback", json.dumps({
                            "device": "tapo_lights", "status": "success", 
                            "message": f"Executed '{action}' on {manager.bulb_model}."
                        }))
                    except Exception as e:
                        await mqtt_client.publish("jarvis/feedback", json.dumps({
                            "device": "tapo_lights", "status": "error", "message": str(e)
                        }))
                except json.JSONDecodeError:
                    logging.error("Received malformed JSON data.")
    except aiomqtt.MqttError as e:
        logging.error(f"MQTT Connection Error: {e}")
    except asyncio.CancelledError:
        logging.info("Service shutting down.")

# --- UTILITIES ---
def print_color_list() -> None:
    colors = [k for k in dir(Color) if not k.startswith("_") and k[0].isupper()]
    print("\n{:^68}".format("--- AVAILABLE COLOR PRESETS ---"))
    print("-" * 68)
    
    print("\nDefault Color Presets:\n")
    for i in range(0, len(colors), 4):
        print("{:<17} {:<17} {:<17} {:<17}".format(*colors[i:i+4] + [""] * (4-len(colors[i:i+4]))))
    
    for group, items in CUSTOM_COLORS.items():
        keys = list(items.keys())
        print(f"\n{group.capitalize()} Collection:\n")
        for i in range(0, len(keys), 4):
            print("{:<17} {:<17} {:<17} {:<17}".format(*keys[i:i+4] + [""] * (4-len(keys[i:i+4]))))
    print("\n" + "-" * 68 + "\n")

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="Microservice Control for Tapo Bulb")
    parser.add_argument("--color", "-c", type=str)
    parser.add_argument("-l", "--lum", type=int)
    parser.add_argument("-t", "--temp", type=int)
    parser.add_argument("--toggle", action="store_true")
    parser.add_argument("--on", action="store_true")
    parser.add_argument("--off", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("-d", "--discovery", action="store_true")
    
    args = parser.parse_args()

    if args.list:
        print_color_list()
        return

    manager = TapoManager()

    if args.discovery:
        asyncio.run(manager.discovery_mode())
        return

    if args.status:
        logging.info("Status check via CLI is transitioning to the manager scope...")
        return

    if any([args.on, args.off, args.toggle, args.color, args.lum, args.temp]):
        logging.info("Executing manual override command directly...")
        asyncio.run(manager.control_bulb(
            toggle=args.toggle, on=args.on, off=args.off, 
            color=args.color, lum=args.lum, temp=args.temp
        ))
    else:
        try:
            asyncio.run(mqtt_service_listener(manager))
        except KeyboardInterrupt:
            logging.info("Exiting Service Mode.")

if __name__ == "__main__":
    main()