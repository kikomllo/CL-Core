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
from tapo import ApiClient
from pywizlight import wizlight, PilotBuilder, discovery as wiz_discovery
import aiomqtt

# NEW: Import your centralized env loader
from utils.clEnvLoader import EnvLoader

# --- LOGGING SETUP ---
import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('CONTROL')

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class LightManager:
    """Enterprise state controller for single-device execution, dual discovery, and self-healing recovery."""
    
    def __init__(self):
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        self.env = EnvLoader()

        self.color_matrix = self._load_color_matrix()
        
        import asyncio
        self.poll_trigger = asyncio.Event()
        
        self.devices_file = os.path.abspath(os.path.join(self.base_dir, "..", "config", "devices.json"))
        self.lights = self._load_devices()

        # Default fallback for CLI / single-bulb compatibility if no devices exist
        self.light_type: str = self.env.get("LIGHT_TYPE", "tapo").lower()
        self.bulb_ip: str = self.env.get("LIGHT_IP", "")
        self.bulb_mac: str = self.env.get("LIGHT_MAC", "")
        
        self.email: str = self.env.get("TAPO_EMAIL", "")
        self.password: str = self.env.get("TAPO_PASSWORD", "")
        
        if not (self.email and self.password):
            logging.warning("Tapo Credentials missing in .env file.")
            
        self.tapo_client: ApiClient = ApiClient(self.email, self.password)
        self.last_discovered_devices: List[Dict[str, str]] = []
        
    def _load_devices(self) -> Dict[str, Any]:
        if os.path.exists(self.devices_file):
            try:
                with open(self.devices_file, 'r', encoding='utf-8') as f:
                    return json.load(f).get("lights", {})
            except Exception as e:
                logging.error(f"Failed to load devices.json: {e}")
        return {}
        
    def _save_devices(self) -> None:
        try:
            with open(self.devices_file, 'w', encoding='utf-8') as f:
                json.dump({"lights": self.lights}, f, indent=2)
            self.poll_trigger.set()
        except Exception as e:
            logging.error(f"Failed to save devices.json: {e}")
        
    def _load_color_matrix(self) -> Dict[str, Any]:
        matrix_path = os.path.abspath(os.path.join(self.base_dir, "..", "config", "entities.json"))
        try:
            with open(matrix_path, 'r', encoding='utf-8') as f:
                return json.load(f).get("colors", {})
        except Exception as e:
            logging.error(f"Failed to load entities.json: {e}")
            return {}

    def update_env_credentials(self, ip: str, mac: str, light_type: str) -> None:
        self.env.update("LIGHT_IP", ip)
        self.env.update("LIGHT_MAC", mac)
        self.env.update("LIGHT_TYPE", light_type)
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
        port_sem = asyncio.Semaphore(50)
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
                           color: str = None, lum: int = None, temp: int = None, retry_attempt: bool = False, target_name: str = "all") -> None:
        
        targets = []
        if target_name == "all" or not target_name:
            if self.lights:
                for name, dev in self.lights.items():
                    targets.append((name, dev['ip'], dev['mac'], dev['type']))
            elif self.bulb_ip:
                targets.append(("default", self.bulb_ip, self.bulb_mac, self.light_type))
        else:
            # find matching light
            for name, dev in self.lights.items():
                if target_name.lower() in name.lower() or name.lower() in target_name.lower():
                    targets.append((name, dev['ip'], dev['mac'], dev['type']))
                    break
            
            if not targets and self.bulb_ip:
                 targets.append(("default", self.bulb_ip, self.bulb_mac, self.light_type))
                 
        if not targets:
            raise ValueError("No matching lights found to execute command.")

        async def execute_single(name, ip, mac, l_type):
            try:
                if l_type == "wiz":
                    await self._execute_wiz_target(ip, toggle, on, off, color, lum, temp)
                else:
                    await self._execute_tapo_target(ip, toggle, on, off, color, lum, temp)
            except Exception as e:
                logging.error(f"Hardware communication error for {name}: {e}")

        await asyncio.gather(*(execute_single(*t) for t in targets))
        
    async def _execute_wiz_target(self, ip, toggle, on, off, color, lum, temp):
        bulb = wizlight(ip)
        if toggle:
            await asyncio.wait_for(bulb.updateState(), timeout=3.0)
            if bulb.status: await bulb.turn_off()
            else: await bulb.turn_on(PilotBuilder(brightness=255))
            return
        elif off:
            await bulb.turn_off()
            return

        if on or lum is not None or temp is not None or color:
            brightness = lum if lum is not None else 255
            if color:
                c_data = self.color_matrix.get(color.lower().strip())
                if c_data:
                    if c_data["type"] == "rgb":
                        r, g, b = c_data["r"], c_data["g"], c_data["b"]
                        await bulb.turn_on(PilotBuilder(brightness=brightness, rgb=(r, g, b)))
                        return
                    elif c_data["type"] == "temp":
                        temp = c_data["val"]
            if temp is not None:
                kelvin = int(2700 + (temp * (6500 - 2700) / 100))
                await bulb.turn_on(PilotBuilder(brightness=brightness, colortemp=kelvin))
                return
            await bulb.turn_on(PilotBuilder(brightness=brightness))

    async def _execute_tapo_target(self, ip, toggle, on, off, color, lum, temp):
        model = os.getenv("TAPO_MODEL", "l530").lower()
        get_device = getattr(self.tapo_client, model, self.tapo_client.l530)
        device = await get_device(ip)
        
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
        if lum is not None: tasks.append(device.set_brightness(lum))
        if color:
            c_data = self.color_matrix.get(color.lower().strip())
            if c_data:
                if c_data["type"] == "rgb":
                    import colorsys
                    r, g, b = c_data["r"], c_data["g"], c_data["b"]
                    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
                    tapo_h = int(h * 360)
                    tapo_s = int(s * 100)
                    tasks.append(device.set_hue_saturation(tapo_h, tapo_s))
                elif c_data["type"] == "temp":
                    temp = c_data["val"] 
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
            msg = "No responsive smart light targets discovered on local network architectures."
            logging.warning(msg)
            if voice_mode:
                async with aiomqtt.Client("localhost") as mqtt_client:
                    await mqtt_client.publish("jarvis/feedback", json.dumps({
                        "device": "smart_lights", "status": "error", "message": msg
                    }))
            return

        for p_dev in parsed_devices:
            for saved_name, saved_dev in self.lights.items():
                if p_dev['mac'] == saved_dev['mac'] or p_dev['ip'] == saved_dev['ip']:
                    p_dev['saved_name'] = saved_name
                    break
        self.last_discovered_devices = parsed_devices

        if voice_mode:
            try:
                async with aiomqtt.Client("localhost") as mqtt_client:
                    await mqtt_client.publish("jarvis/feedback", json.dumps({
                        "status": "success",
                        "device": "smart_lights",
                        "action": "awaiting_selection",
                        "message": f"Network scan complete. I found {len(parsed_devices)} hardware targets. Please select a device index from your display.",
                        "devices": parsed_devices  # Clean structured JSON data data payload
                    }))
            except Exception as e:
                logging.error(f"Failed to deploy discovery feedback: {e}")
        else:
            # CLI Mode: Output a clean, beautiful terminal UI wrapper
            print("\n" + "=" * 60)
            print("{:^60}".format("DISCOVERED HARDWARE TARGETS"))
            print("=" * 60)
            table_str = "  {:<5} {:<8} {:<15} {:<15}\n".format("IDX", "TYPE", "MODEL", "IP")
            table_str += "  " + "-" * 54 + "\n"
            for i, dev in enumerate(parsed_devices):
                table_str += "  {:<5} {:<8} {:<15} {:<15}\n".format(f"[{i}]", dev['type'].upper(), dev['model'], dev['ip'])
            print(table_str)
            print("=" * 60)
            self._handle_cli_save(parsed_devices)

    def _handle_cli_save(self, devices: List[Dict[str, str]]) -> None:
        choice = input("\nSelect a device index to target (or press Enter to exit): ")
        if choice.isdigit() and int(choice) < len(devices):
            selected = devices[int(choice)]
            if input(f"Confirm save for {selected['ip']} to local workspace configurations? (y/n): ").lower() == 'y':
                name = f"LIGHT_{len(self.lights)+1}"
                self.lights[name] = {
                    "ip": selected["ip"],
                    "mac": selected["mac"],
                    "type": selected["type"].lower()
                }
                self._save_devices()
                logging.info(f"Target locked. Saved as {name} to devices.json.")
                
    def _load_word_to_number(self) -> Dict[str, str]:
        """Loads the spoken word-to-number mapping directly from core.json."""
        config_path = os.path.abspath(os.path.join(self.base_dir, "..", "config", "core.json"))
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f).get("word_to_number", {})
        except Exception as e:
            logging.error(f"Failed to load word_to_number from core.json: {e}")
            return {}

    def _handle_discovery_selection(self, raw_target: Any) -> Dict[str, Any]:
        if not hasattr(self, 'last_discovered_devices') or not self.last_discovered_devices:
            return {"status": "error", "action": "awaiting_selection", "message": "No devices in memory. Please run a scan first."}

        parsed_idx = None
        clean_str = str(raw_target).lower().strip()

        # 1. First Pass: Isolate exact numeric index from spoken words or digits
        if isinstance(raw_target, int):
            if 0 <= raw_target < len(self.last_discovered_devices):
                parsed_idx = raw_target
        else:
            word_to_num = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
            for word in clean_str.split():
                val = word_to_num.get(word, word)
                if val.isdigit():
                    num = int(val)
                    if 0 <= num < len(self.last_discovered_devices):
                        parsed_idx = num
                    break

        # 2. Index Routing
        if parsed_idx is not None:
            selected_device = self.last_discovered_devices[parsed_idx]
            
        # 3. Semantic Routing (Fallback)
        else:
            scored_matches = []
            for dev in self.last_discovered_devices:
                score = 0
                if dev.get('type', '').lower() in clean_str: score += 1
                if dev.get('model', '').lower() in clean_str: score += 1
                if dev.get('ip', '') in clean_str: score += 5
                
                # Check for IP tail (e.g., '88' for '192.168.1.88')
                ip_tail = dev.get('ip', '').split('.')[-1]
                if ip_tail in clean_str.split(): score += 5

                if score > 0:
                    scored_matches.append((score, dev))

            if scored_matches:
                scored_matches.sort(key=lambda x: x[0], reverse=True)
                top_score = scored_matches[0][0]
                top_devices = [dev for s, dev in scored_matches if s == top_score]
                
                if len(top_devices) == 1:
                    selected_device = top_devices[0]
                else:
                    return {
                        "status": "error",
                        "action": "awaiting_selection",
                        "message": f"I found multiple matching devices for '{raw_target}'. Please specify the exact IP address or device index."
                    }
            else:
                return {
                    "status": "error",
                    "message": f"Could not find a valid device matching '{raw_target}'. Number out of bounds or unrecognised name."
                }

        # 4. Success Execution - Request Naming
        name = selected_device.get('saved_name')
        if not name:
            name = f"LIGHT_{len(self.lights)+1}"
            self.lights[name] = {
                "ip": selected_device['ip'],
                "mac": selected_device['mac'],
                "type": selected_device['type'].lower()
            }
            self._save_devices()
        
        return {
            "status": "success",
            "action": "request_naming",
            "temp_name": name,
            "message": f"Selected {name.replace('_', ' ')}." if selected_device.get('saved_name') else f"Successfully connected to {selected_device['type']}."
        }

async def poll_light_status(manager: LightManager) -> None:
    """Background task to poll lights and publish status."""
    failure_tracker = {}
    while True:
        try:
            async with aiomqtt.Client("localhost") as mqtt_client:
                while True:
                    async def poll_single(name, info_dict):
                        ip = info_dict.get("ip")
                        l_type = info_dict.get("type", "tapo").lower()
                        if not ip: return None
                        try:
                            if l_type == "wiz":
                                bulb = wizlight(ip)
                                await asyncio.wait_for(bulb.updateState(), timeout=3.0)
                                is_on = bulb.status
                            else:
                                model = manager.env.get("TAPO_MODEL", "l530").lower()
                                get_device = getattr(manager.tapo_client, model, manager.tapo_client.l530)
                                device = await get_device(ip)
                                info = await asyncio.wait_for(device.get_device_info(), timeout=3.0)
                                is_on = info.device_on
                            failure_tracker[name] = 0
                            return {"name": name.replace("_", " ").title(), "is_on": is_on, "offline": False}
                        except Exception as e:
                            fails = failure_tracker.get(name, 0) + 1
                            failure_tracker[name] = fails
                            logging.debug(f"Light polling error for {name} (fail {fails}): {repr(e)}")
                            is_offline = fails >= 3
                            return {"name": name.replace("_", " ").title(), "is_on": False, "offline": is_offline}

                    # Do an initial poll
                    tasks = [poll_single(n, i) for n, i in manager.lights.items()]
                    results = await asyncio.gather(*tasks)
                    statuses = [r for r in results if r]
                            
                    if statuses:
                        await mqtt_client.publish("jarvis/sys/light_status", json.dumps({"lights": statuses}))

                    while True:
                        await manager.poll_trigger.wait()
                        manager.poll_trigger.clear()
                        
                        tasks = [poll_single(n, i) for n, i in manager.lights.items()]
                        results = await asyncio.gather(*tasks)
                        statuses = [r for r in results if r]
                                
                        if statuses:
                            await mqtt_client.publish("jarvis/sys/light_status", json.dumps({"lights": statuses}))
        except aiomqtt.MqttError:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break

# --- MQTT SERVICE LISTENER ---
async def mqtt_service_listener(manager: LightManager) -> None:
    logging.info("Service Mode initialized. Listening on MQTT topics...")
    while True:
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
                            await manager.discovery_mode(voice_mode=True)
                            continue
                            
                        if action == "refresh_lights":
                            manager.poll_trigger.set()
                            continue
                            
                        if action == "save_discovery":
                            result = manager._handle_discovery_selection(payload.get("index"))
                            feedback_payload = {"device": "smart_lights", **result}
                            await mqtt_client.publish("jarvis/feedback", json.dumps(feedback_payload))
                            continue
                            
                        if action == "list_saved":
                            if not manager.lights:
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "message": "You have no saved lights."}))
                                continue
                            devices = [f"[{i}] {k.replace('_', ' ').title()}" for i, k in enumerate(manager.lights.keys())]
                            await mqtt_client.publish("jarvis/sys/ui_options", json.dumps({"options": devices, "title": "Saved Lights"}))
                            msg = "They are displayed. What would you like to do? The options are remove, set as default, or rename."
                            await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "message": msg, "action": "request_light_action"}))
                            continue

                        def _match_light(target, lights_dict):
                            keys = list(lights_dict.keys())
                            # Try stripping common conversational prefixes for indices
                            clean_target = target.replace("NUMBER_", "").replace("INDEX_", "").replace("OPTION_", "")
                            if clean_target.isdigit():
                                target = clean_target # For fallthrough
                                idx = int(clean_target)
                                if 0 <= idx < len(keys):
                                    return keys[idx]
                            elif target.isdigit():
                                idx = int(target)
                                if 0 <= idx < len(keys):
                                    return keys[idx]
                            for k in keys:
                                if target in k.upper() or k.upper() in target:
                                    return k
                            return None

                        if action == "intent_rename_light":
                            target_str = payload.get("target_str", "").strip()
                            if not target_str:
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "action": "request_light_rename", "message": "Which light would you like to rename, and to what?"}))
                                continue
                            words = target_str.split(" to ")
                            if len(words) >= 2:
                                old_name = words[0].strip().upper().replace(" ", "_")
                                new_name = words[-1].strip().upper().replace(" ", "_")
                                match = _match_light(old_name, manager.lights)
                                if match:
                                    manager.lights[new_name] = manager.lights.pop(match)
                                    manager._save_devices()
                                    msg = f"Renamed {match.replace('_', ' ')} to {new_name.replace('_', ' ')}."
                                else:
                                    msg = f"Could not find a light matching {old_name}."
                            else:
                                msg = "Please specify the current name and the new name, like 'rename desk light to ceiling light'."
                            await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "message": msg}))
                            continue

                        if action == "intent_remove_light":
                            target_str = payload.get("target_str", "").strip().upper().replace(" ", "_")
                            if not target_str:
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "action": "request_light_remove", "message": "Which light would you like to remove?"}))
                                continue
                            if target_str == "all":
                                manager.lights.clear()
                                manager._save_devices()
                                msg = "Removed all saved lights."
                            else:
                                match = _match_light(target_str, manager.lights)
                                if match:
                                    del manager.lights[match]
                                    manager._save_devices()
                                    msg = f"Removed {match.replace('_', ' ')}."
                                else:
                                    msg = f"Could not find a light matching {target_str}."
                            await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "message": msg}))
                            continue

                        if action == "intent_set_default_light":
                            target_str = payload.get("target_str", "").strip().upper().replace(" ", "_")
                            if not target_str:
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "action": "request_light_default", "message": "Which light would you like to set as default?"}))
                                continue
                            match = _match_light(target_str, manager.lights)
                            if match:
                                dev = manager.lights[match]
                                manager.update_env_credentials(dev['ip'], dev['mac'], dev['type'])
                                msg = f"Set {match.replace('_', ' ')} as the default legacy light."
                            else:
                                msg = f"Could not find a light matching {target_str}."
                            await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "message": msg}))
                            continue
                        # --- ISOLATED HARDWARE TASK ---
                        async def execute_hardware(payload_data, action_cmd):
                            is_silent = payload_data.get("silent", False)
                            light_target = payload_data.get("light_target", "all")
                            try:
                                await manager.control_bulb(
                                    on=(action_cmd == "on"), off=(action_cmd == "off"), toggle=(action_cmd == "toggle"),
                                    color=payload_data.get("color"), lum=payload_data.get("lum"), temp=payload_data.get("temp"),
                                    target_name=light_target
                                )
                                await mqtt_client.publish("jarvis/feedback", json.dumps({
                                    "device": "smart_lights", "status": "success",
                                    "message": f"Successfully shifted hardware targets to '{action_cmd}' state.",
                                    "action_cmd": action_cmd,
                                    "target": light_target,
                                    "silent": is_silent
                                }))
                                manager.poll_trigger.set()
                            except Exception as e:
                                await mqtt_client.publish("jarvis/feedback", json.dumps({
                                    "device": "smart_lights", "status": "error", "message": str(e), "silent": is_silent
                                }))

                        asyncio.create_task(execute_hardware(payload, action))
                        
                    except json.JSONDecodeError:
                        logging.error("JSON formatting syntax mismatch caught on parsing sequence.")
        except aiomqtt.MqttError as e:
            logging.error(f"MQTT Connection Error: {e} (Is Mosquitto running?)")
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logging.info("Light Control service shutting down.")
            break

def print_color_list(color_matrix: Dict[str, Any]) -> None:
    if not color_matrix:
        print("\nError: No colors found in entities.json matrix.\n")
        return

    temps_grouped = {}
    colors_grouped = {}

    for name, data in color_matrix.items():
        c_type = data.get("type")
        if c_type == "temp":
            key = data.get("val")
            temps_grouped.setdefault(key, []).append(name.title())
        elif c_type == "rgb":
            key = (data.get("r"), data.get("g"), data.get("b"))
            colors_grouped.setdefault(key, []).append(name.title())

    print("\n" + "=" * 70)
    print("{:^70}".format("AVAILABLE COLOR PRESETS & ALIASES"))
    print("=" * 70)

    print("\n[ WHITES & TEMPERATURES ]\n")
    # Sort by temperature value to group logically
    for val, names in sorted(temps_grouped.items(), key=lambda x: x[0]):
        aliases = ", ".join(sorted(names))
        print(f"  • {aliases}")

    print("\n[ COLORS (RGB) ]\n")
    # Sort alphabetically by the primary alias
    for rgb, names in sorted(colors_grouped.items(), key=lambda x: sorted(x[1])[0]):
        aliases = ", ".join(sorted(names))
        print(f"  • {aliases}")
        
    print("\n" + "=" * 70 + "\n")

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
        async def run_services():
            await asyncio.gather(
                mqtt_service_listener(manager),
                poll_light_status(manager)
            )
            
        try:
            asyncio.run(run_services())
        except KeyboardInterrupt:
            logging.info("Exiting Light Actuator runtime environment.")

if __name__ == "__main__":
    main()