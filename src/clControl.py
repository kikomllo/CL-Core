# --- IMPORTS ---
import asyncio
import argparse
import socket
import os
import logging
import json
import sys
import colorsys
from typing import Optional, Dict, Any, List, Tuple
from dotenv import load_dotenv, set_key
from tapo import ApiClient
from pywizlight import wizlight, PilotBuilder, discovery as wiz_discovery
import aiomqtt

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [CONTROL] %(message)s", datefmt="%H:%M:%S")

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class LightManager:
    """Enterprise state controller for single-device execution, dual discovery, and self-healing recovery."""
    
    def __init__(self):
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        self.env_path: str = os.path.abspath(os.path.join(self.base_dir, "..", ".env"))
        load_dotenv(self.env_path)

        self.color_matrix = self._load_color_matrix()

        self.light_type: str = os.getenv("LIGHT_TYPE", "tapo").lower()
        self.bulb_ip: str = os.getenv("LIGHT_IP", "")
        self.bulb_mac: str = os.getenv("LIGHT_MAC", "")
        
        self.email: str = os.getenv("TAPO_EMAIL", "")
        self.password: str = os.getenv("TAPO_PASSWORD", "")
        
        if self.light_type == "tapo" and not (self.email and self.password):
            logging.error("Tapo Credentials missing in .env file.")
            sys.exit(1)
            
        self.tapo_client: ApiClient = ApiClient(self.email, self.password)
        self.last_discovered_devices: List[Dict[str, str]] = []
        
    def _load_color_matrix(self) -> Dict[str, Any]:
        """Loads the absolute truth JSON mapping for colors and temperatures."""
        matrix_path = os.path.abspath(os.path.join(self.base_dir, "..", "config", "colors.json"))
        try:
            with open(matrix_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load colors.json: {e}")
            return {}

    def update_env_credentials(self, ip: str, mac: str, light_type: str) -> None:
        set_key(self.env_path, "LIGHT_IP", ip)
        set_key(self.env_path, "LIGHT_MAC", mac)
        set_key(self.env_path, "LIGHT_TYPE", light_type)
        self.bulb_ip = ip
        self.bulb_mac = mac
        self.light_type = light_type

    # --- TAPO MANUAL NETWORK SWEEPERS ---
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
                if not hasattr(self.tapo_client, model_guess): continue
                try:
                    device = await getattr(self.tapo_client, model_guess)(ip)
                    info = await asyncio.wait_for(device.get_device_info(), timeout=3.0)
                    true_model = getattr(info, 'model', model_guess).upper()
                    mac = getattr(info, 'mac', 'UNKNOWN').replace('-', ':').upper()
                    return {"ip": ip, "model": true_model, "mac": mac}
                except Exception:
                    continue
        return None

    # --- SELF-HEALING ARCHITECTURE ---
    async def attempt_network_repair(self) -> bool:
        if not self.bulb_mac:
            logging.warning("Auto-recovery bypassed: LIGHT_MAC is not assigned in your .env configuration.")
            return False
            
        logging.warning(f"[SELF-HEALING] Lost connection to {self.light_type.upper()}. Scanning local network for MAC: {self.bulb_mac}...")
        
        try:
            if self.light_type == "wiz":
                results = await wiz_discovery.discover_lights(broadcast_space="255.255.255.255")
                for dev in results:
                    if dev.mac.lower() == self.bulb_mac.lower():
                        logging.info(f"[SELF-HEALING] WiZ Bulb recovered at new IP: {dev.ip}")
                        self.update_env_credentials(dev.ip, self.bulb_mac, "wiz")
                        return True
                        
            elif self.light_type == "tapo":
                base_ip = self._get_subnet_base()
                results = await self._sweep_ips([f"{base_ip}.{i}" for i in range(1, 255)])
                for dev in results:
                    if dev['mac'].lower() == self.bulb_mac.lower():
                        logging.info(f"[SELF-HEALING] Tapo Bulb recovered at new IP: {dev['ip']}")
                        self.update_env_credentials(dev['ip'], self.bulb_mac, "tapo")
                        return True
        except Exception as e:
            logging.error(f"[SELF-HEALING] Auto-recovery sequence encountered an execution error: {e}")
            
        return False

    # --- CONCURRENT LIGHT EXECUTION ENGINE ---
    async def control_bulb(self, toggle: bool = False, on: bool = False, off: bool = False, 
                           color: str = None, lum: int = None, temp: int = None, retry_attempt: bool = False) -> None:
        
        if not self.bulb_ip:
            raise ValueError("Target execution IP is completely missing from configurations.")

        try:
            if self.light_type == "wiz":
                await self._execute_wiz(toggle, on, off, color, lum, temp)
            else:
                await self._execute_tapo(toggle, on, off, color, lum, temp)
        except Exception as e:
            logging.error(f"Hardware communication interface error: {e}")
            if not retry_attempt and await self.attempt_network_repair():
                await self.control_bulb(toggle, on, off, color, lum, temp, retry_attempt=True)
            else:
                raise RuntimeError("Device connection could not be established or repaired.")

    async def _execute_wiz(self, toggle: bool, on: bool, off: bool, color: str, lum: int, temp: int) -> None:
        bulb = wizlight(self.bulb_ip)
        
        if toggle:
            await bulb.toggle()
            return
        elif off:
            await bulb.turn_off()
            return

        if on or lum is not None or temp is not None or color:
            brightness = lum if lum is not None else 255
            
            # Use RGB values directly from JSON
            if color:
                c_data = self.color_matrix.get(color.lower().strip())
                if c_data:
                    if c_data["type"] == "rgb":
                        r, g, b = c_data["r"], c_data["g"], c_data["b"]
                        await bulb.turn_on(PilotBuilder(brightness=brightness, rgb=(r, g, b)))
                        logging.info(f"Setting WiZ Color to RGB: {(r, g, b)}")
                        return
                    elif c_data["type"] == "temp":
                        temp = c_data["val"]

            if temp is not None:
                kelvin = int(2700 + (temp * (6500 - 2700) / 100))
                await bulb.turn_on(PilotBuilder(brightness=brightness, colortemp=kelvin))
                logging.info(f"Setting WiZ Temperature to {kelvin}K")
                return

            await bulb.turn_on(PilotBuilder(brightness=brightness))

    async def _execute_tapo(self, toggle: bool, on: bool, off: bool, color: str, lum: int, temp: int) -> None:
        model = os.getenv("TAPO_MODEL", "l530").lower()
        get_device = getattr(self.tapo_client, model, self.tapo_client.l530)
        device = await get_device(self.bulb_ip)
        
        if toggle:
            info = await device.get_device_info()
            if info.device_on: await device.off()
            else: await device.on()
            return
        elif on: await device.on()
        elif off:
            await device.off()
            return

        tasks = []
        if lum is not None:
            tasks.append(device.set_brightness(lum))
            
        # Convert JSON RGB to Tapo HSV mathematically
        if color:
            c_data = self.color_matrix.get(color.lower().strip())
            if c_data:
                if c_data["type"] == "rgb":
                    r, g, b = c_data["r"], c_data["g"], c_data["b"]
                    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
                    tapo_h = int(h * 360)
                    tapo_s = int(s * 100)
                    tasks.append(device.set_hue_saturation(tapo_h, tapo_s))
                elif c_data["type"] == "temp":
                    temp = c_data["val"] 
            else:
                logging.warning(f"Color '{color}' not found in colors.json truth matrix.")

        if temp is not None:
            tasks.append(device.set_color_temperature(int(2500 + temp * (6500 - 2500) / 100)))

        if tasks:
            await asyncio.gather(*tasks)

    # --- DUAL ECOSYSTEM NETWORK DISCOVERY ENGINE ---
    async def discovery_mode(self, voice_mode: bool = False) -> None:
        logging.info("Broadcasting dual network discovery queries for Tapo and WiZ protocols...")
        
        async def scan_wiz():
            try: return await wiz_discovery.discover_lights(broadcast_space="255.255.255.255")
            except Exception: return []

        async def scan_tapo():
            base_ip = self._get_subnet_base()
            return await self._sweep_ips([f"{base_ip}.{i}" for i in range(1, 255)])

        wiz_res, tapo_res = await asyncio.gather(scan_wiz(), scan_tapo())
        
        parsed_devices = []
        for dev in wiz_res:
            parsed_devices.append({"type": "wiz", "model": "WIZ_BULB", "ip": dev.ip, "mac": dev.mac})
        for dev in tapo_res:
            parsed_devices.append({"type": "tapo", "model": dev['model'], "ip": dev['ip'], "mac": dev['mac']})

        if not parsed_devices:
            logging.warning("No responsive devices discovered on local network architectures.")
            return

        self.last_discovered_devices = parsed_devices
        print(f"\nDiscovered {len(parsed_devices)} smart light network targets:")
        print("{:<5} {:<8} {:<18} {:<18}".format("#", "TYPE", "MODEL", "IP"))
        print("-" * 55)
        for i, dev in enumerate(parsed_devices):
            print("{:<5} {:<8} {:<18} {:<18}".format(i, dev['type'].upper(), dev['model'], dev['ip']))

        if voice_mode:
            try:
                async with aiomqtt.Client("localhost") as mqtt_client:
                    await mqtt_client.publish("jarvis/sys/mic_open", "1")
            except Exception as e:
                logging.error(f"Failed to deploy remote mic trigger via system socket: {e}")
        else:
            self._handle_cli_save(parsed_devices)

    def _handle_cli_save(self, devices: List[Dict[str, str]]) -> None:
        choice = input("\nSelect a device index to target (or press Enter to exit): ")
        if choice.isdigit() and int(choice) < len(devices):
            selected = devices[int(choice)]
            if input(f"Confirm save for {selected['ip']} to local workspace configurations? (y/n): ").lower() == 'y':
                self.update_env_credentials(selected["ip"], selected["mac"], selected["type"])
                logging.info(f"Target locked. Operating system variables updated to track local {selected['type'].upper()} interface.")

# --- MQTT SERVICE LISTENER ---
async def mqtt_service_listener(manager: LightManager) -> None:
    logging.info("Service Mode initialized. Listening on MQTT topics...")
    try:
        async with aiomqtt.Client("localhost") as mqtt_client:
            await mqtt_client.subscribe("home/room/+/set") 
            await mqtt_client.subscribe("system/discovery")
            
            async for message in mqtt_client.messages:
                try:
                    payload = json.loads(message.payload.decode('utf-8'))
                    logging.info(f"Incoming Request Loop Event: {payload}")
                    action = payload.get("action")
                    
                    if action == "discover":
                        asyncio.create_task(manager.discovery_mode(voice_mode=True))
                        continue
                        
                    if action == "save_discovery":
                        idx = payload.get("index")
                        if idx is not None and 0 <= idx < len(manager.last_discovered_devices):
                            sel = manager.last_discovered_devices[idx]
                            manager.update_env_credentials(sel["ip"], sel["mac"], sel["type"])
                            logging.info(f"SUCCESS: System tracking modified to drive {sel['type'].upper()} profile.")
                        continue

                    try:
                        await manager.control_bulb(
                            on=(action == "on"), off=(action == "off"), toggle=(action == "toggle"),
                            color=payload.get("color"), lum=payload.get("lum"), temp=payload.get("temp")
                        )
                        await mqtt_client.publish("jarvis/feedback", json.dumps({
                            "device": "smart_lights", "status": "success", "message": f"Successfully shifted hardware targets to '{action}' state."
                        }))
                    except Exception as e:
                        await mqtt_client.publish("jarvis/feedback", json.dumps({
                            "device": "smart_lights", "status": "error", "message": str(e)
                        }))
                except json.JSONDecodeError:
                    logging.error("JSON formatting syntax mismatch caught on parsing sequence.")
    except aiomqtt.MqttError as e:
        logging.error(f"MQTT service backbone tracking failed connection check: {e}")

# --- UTILITIES ---
def print_color_list(color_matrix: Dict[str, Any]) -> None:
    if not color_matrix:
        print("\nError: No colors found in colors.json matrix.\n")
        return

    print("\n{:^68}".format("--- AVAILABLE COLOR PRESETS ---"))
    print("-" * 68)
    
    temps = sorted(set([k.title() for k, v in color_matrix.items() if v.get("type") == "temp"]))
    colors = sorted(set([k.title() for k, v in color_matrix.items() if v.get("type") == "rgb"]))
    
    print("\nWhites & Temperatures:\n")
    for i in range(0, len(temps), 4):
        row = temps[i:i+4]
        print("{:<17} {:<17} {:<17} {:<17}".format(*(row + [""] * (4 - len(row)))))
        
    print("\nColors (RGB Matrix):\n")
    for i in range(0, len(colors), 4):
        row = colors[i:i+4]
        print("{:<17} {:<17} {:<17} {:<17}".format(*(row + [""] * (4 - len(row)))))
        
    print("\n" + "-" * 68 + "\n")

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="Microservice Control for Unified Smart Lights")
    parser.add_argument("--color", "-c", type=str)
    parser.add_argument("-l", "--lum", type=int)
    parser.add_argument("-t", "--temp", type=int)
    parser.add_argument("--toggle", action="store_true")
    parser.add_argument("--on", action="store_true")
    parser.add_argument("--off", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("-d", "--discovery", action="store_true")
    
    args = parser.parse_args()

    manager = LightManager()

    if args.list:
        print_color_list(manager.color_matrix)
        return

    if args.discovery:
        asyncio.run(manager.discovery_mode())
        return

    if any([args.on, args.off, args.toggle, args.color, args.lum, args.temp]):
        asyncio.run(manager.control_bulb(
            toggle=args.toggle, on=args.on, off=args.off, 
            color=args.color, lum=args.lum, temp=args.temp
        ))
    else:
        try:
            asyncio.run(mqtt_service_listener(manager))
        except KeyboardInterrupt:
            logging.info("Exiting Light Actuator runtime environment.")

if __name__ == "__main__":
    main()