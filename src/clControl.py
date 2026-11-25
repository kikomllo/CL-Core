# --- IMPORTS ---
import asyncio
import argparse
import socket
import os
import logging
import json
import sys
import colorsys
import time
from typing import Optional, Dict, Any, List, Tuple
from tapo import ApiClient
from pywizlight import wizlight, PilotBuilder, discovery as wiz_discovery
from pywizlight.utils import hex_to_percent, percent_to_hex
import aiomqtt

# Import centralized env loader
from utils.clEnvLoader import EnvLoader
from utils.clConfigLoader import ConfigLoader
from utils.clNetworkUtils import get_current_wifi_ssid
from utils import clTuya

# --- LOGGING SETUP ---
import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('CONTROL')

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _normalize_mac(mac: str) -> str:
    if not mac:
        return ""
    return mac.replace(":", "").replace("-", "").strip().lower()


class LightCommandError(Exception):
    """One or more lights couldn't be reached or refused a command; the message is spoken/shown as is."""


# Tapo bulbs without colour (the model is one global TAPO_MODEL, so a mixed set needs the per-light "colour" override).
TAPO_WHITE_ONLY_MODELS = {"l510", "l520", "l610"}


def _parse_rgb(rgb) -> Optional[Tuple[int, int, int]]:
    """An explicit colour from the dashboard picker: [r, g, b], each 0-255. None passes through; anything else is refused
    before any light is touched."""
    if rgb is None:
        return None
    try:
        r, g, b = rgb
    except (TypeError, ValueError):
        raise ValueError("rgb must be three numbers: [r, g, b].")
    channels = []
    for v in (r, g, b):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 255:
            raise ValueError("rgb values must be between 0 and 255.")
        channels.append(int(round(v)))
    return tuple(channels)


def _wiz_status_fields(bulb) -> Dict[str, Any]:
    """Brightness (%), the colour (rgb) or the white temperature (kelvin) the bulb reports, so the dashboard button
    shows what the light really is, also after a voice command."""
    out: Dict[str, Any] = {"brightness": None, "rgb": None, "kelvin": None}
    state = bulb.state[0] if bulb.state else None
    if state is None:
        return out
    level = state.get_brightness()
    if level is not None:
        out["brightness"] = hex_to_percent(level)
    r, g, b = state.get_rgb()
    if r is not None:
        out["rgb"] = [r, g, b]
    else:
        out["kelvin"] = state.get_colortemp()
    return out


def _tapo_status_fields(info) -> Dict[str, Any]:
    """Same as _wiz_status_fields for a Tapo bulb: it reports a colour temperature of 0 while in colour mode."""
    out: Dict[str, Any] = {"brightness": getattr(info, "brightness", None), "rgb": None, "kelvin": None}
    kelvin = getattr(info, "color_temp", 0) or 0
    if kelvin:
        out["kelvin"] = int(kelvin)
        return out
    hue, sat = getattr(info, "hue", None), getattr(info, "saturation", None)
    if hue is not None and sat is not None:
        out["rgb"] = [int(round(c * 255)) for c in colorsys.hsv_to_rgb(hue / 360.0, sat / 100.0, 1.0)]
    return out


def _colour_capability(entry: Dict[str, Any], detected: Optional[bool]) -> bool:
    """Whether the dashboard shows a colour button: the light's own "colour": true/false in devices.json wins,
    then what the bulb says it can do (WiZ) or its model (Tapo), and a light we know nothing about gets the button."""
    override = entry.get("colour")
    if isinstance(override, bool):
        return override
    return True if detected is None else detected


def _kind(entry: Dict[str, Any]) -> str:
    """What a device is for: "light" (the default, so old entries are unchanged) or "plug" (a heater: on/off only, never part of "all lights")."""
    return str(entry.get("kind", "light")).lower()


def _describe_failures(failures) -> str:
    names = " and ".join(name.replace("_", " ") for name, _ in failures)
    if all(isinstance(e, clTuya.TuyaError) for _, e in failures):
        return f"I couldn't reach the {names}. {failures[0][1]}"
    reasons = " ".join(str(e) for _, e in failures).lower()
    if "third-party compatibility" in reasons or "forbidden" in reasons or "unauthorized" in reasons:
        return f"The {names} light refused the login. Check Third-Party Compatibility and the account in the Tapo app."
    return f"I couldn't reach the {names} light."


# A Tapo call that has not answered by then is given up on (the library itself waits ~20 s for a TCP connect).
TAPO_TIMEOUT = 6.0
# How often the lights are polled with nobody asking, so a bulb switched elsewhere or a startup blip corrects itself.
POLL_INTERVAL = 25.0
# A Tuya plug takes one local session at a time and the phone app and the other machine want it too: read it every other cycle.
PLUG_POLL_INTERVAL = 45.0


async def _bounded(coro, timeout: float):
    """Await `coro` for at most `timeout` seconds and really return then. asyncio.wait_for waits for the cancelled call to
    finish, and the Tapo library (Rust) ignores cancellation until its own connect timeout, so a dead bulb held every poll
    and command for ~20 s; here the call is abandoned in the background instead."""
    task = asyncio.ensure_future(coro)
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        return task.result()
    task.cancel()
    task.add_done_callback(lambda t: t.cancelled() or t.exception())      # nothing awaits it: swallow its late error
    raise asyncio.TimeoutError(f"no answer within {timeout:g}s")


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
        self._tapo_locks: Dict[str, asyncio.Lock] = {}       # a bulb takes one session at a time
        self._tapo_devices: Dict[str, Any] = {}              # logged-in handle per IP, dropped on any error
        self.last_discovered_devices: List[Dict[str, str]] = []
        self.last_target: str = self._load_last_target()

    async def _tapo_call(self, ip: str, op, timeout: float = TAPO_TIMEOUT):
        """Runs `op(device)` against a Tapo bulb. Every call to one bulb goes through one lock, because a second handshake
        while another is in flight invalidates it (SESSION_TIMEOUT / "Handshake2 failed" when a toggle and a poll overlapped);
        the logged-in handle is reused, and a stale session is retried once with a fresh login. Never waits past `timeout`."""
        lock = self._tapo_locks.setdefault(ip, asyncio.Lock())
        async with lock:
            for attempt in (1, 2):
                try:
                    device = self._tapo_devices.get(ip)
                    if device is None:
                        model = os.getenv("TAPO_MODEL", self.env.get("TAPO_MODEL", "l530")).lower()
                        get_device = getattr(self.tapo_client, model, self.tapo_client.l530)
                        device = await _bounded(get_device(ip), timeout)
                        self._tapo_devices[ip] = device
                    return await _bounded(op(device), timeout)
                except Exception as e:
                    self._tapo_devices.pop(ip, None)
                    stale = any(w in str(e).lower() for w in ("session", "handshake", "expired"))
                    if attempt == 2 or not stale:       # only a dead session is worth a second try: a silent bulb will not answer, and a repeated toggle must not run twice
                        raise

    def _load_last_target(self) -> str:
        core_file = os.path.abspath(os.path.join(self.base_dir, "..", "config", "core.json"))
        if os.path.exists(core_file):
            try:
                with open(core_file, 'r', encoding='utf-8') as f:
                    return json.load(f).get("settings", {}).get("last_light_target", "all")
            except Exception as e:
                logging.error(f"Failed to load last_light_target from core.json: {e}")
        return "all"

    def _save_last_target(self, target: str) -> None:
        try:
            def update_cb(core):
                if "settings" not in core: core["settings"] = {}
                core["settings"]["last_light_target"] = target
            ConfigLoader().update_json_atomic("core.json", update_cb)
        except Exception as e:
            logging.error(f"Failed to save last_light_target to core.json: {e}")
        
    def _load_devices(self) -> Dict[str, Any]:
        self.current_ssid = get_current_wifi_ssid()
        if os.path.exists(self.devices_file):
            try:
                with open(self.devices_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if "networks" in data:
                        networks = data.get("networks", {})
                        # Exact network match
                        if self.current_ssid in networks:
                            logging.info(f"[CONTROL] Loaded {len(networks[self.current_ssid])} lights for current network '{self.current_ssid}'.")
                            return networks[self.current_ssid]
                        # Substring match
                        for net_name, lights in networks.items():
                            if net_name.lower() in self.current_ssid.lower() or self.current_ssid.lower() in net_name.lower():
                                logging.info(f"[CONTROL] Matched network '{net_name}' for SSID '{self.current_ssid}'.")
                                return lights
                        # No match found -> Return empty dict (no lights for this network)
                        logging.info(f"[CONTROL] No lights configured in devices.json for active SSID '{self.current_ssid}'.")
                        return {}
                    elif "lights" in data:
                        return data.get("lights", {})
            except Exception as e:
                logging.error(f"Failed to load devices.json: {e}")
        return {}

    def _save_devices(self) -> None:
        try:
            current_ssid = getattr(self, 'current_ssid', get_current_wifi_ssid())
            data = {"networks": {}}
            if os.path.exists(self.devices_file):
                try:
                    with open(self.devices_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    pass
            if "networks" not in data:
                data = {"networks": {}}

            data["networks"][current_ssid] = self.lights
            with open(self.devices_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.poll_trigger.set()
        except Exception as e:
            logging.error(f"Failed to save devices.json: {e}")
        
    def refresh_lights(self) -> None:
        """Refreshing the lights widget should also pick up a network
        change (different Wi-Fi -> different light profile), not just
        re-poll the on/off state of whatever was loaded at startup."""
        self.lights = self._load_devices()
        self.poll_trigger.set()

    def _load_color_matrix(self) -> Dict[str, Any]:
        matrix_path = os.path.abspath(os.path.join(self.base_dir, "..", "config", "entities.json"))
        try:
            with open(matrix_path, 'r', encoding='utf-8') as f:
                return json.load(f).get("colors", {})
        except Exception as e:
            logging.error(f"Failed to load entities.json: {e}")
            return {}

    def update_env_credentials(self, ip: str, mac: str, light_type: str, light_target: str = "main") -> None:
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
                    if _normalize_mac(dev.mac) == _normalize_mac(self.bulb_mac):
                        logging.info(f"[SELF-HEALING] WiZ Bulb recovered at new IP: {dev.ip}")
                        self.update_env_credentials(dev.ip, self.bulb_mac, "wiz")
                        return True
                        
            elif self.light_type == "tapo":
                base_ip = self._get_subnet_base()
                results = await self._sweep_ips([f"{base_ip}.{i}" for i in range(1, 255)])
                for dev in results:
                    if _normalize_mac(dev['mac']) == _normalize_mac(self.bulb_mac):
                        logging.info(f"[SELF-HEALING] Tapo Bulb recovered at new IP: {dev['ip']}")
                        self.update_env_credentials(dev['ip'], self.bulb_mac, "tapo")
                        return True
        except Exception as e:
            logging.error(f"[SELF-HEALING] Auto-recovery sequence encountered an execution error: {e}")
            
        return False

    # --- CONCURRENT LIGHT EXECUTION ENGINE ---
    def _is_plug(self, name: str) -> bool:
        return _kind(self.lights.get(name, {})) == "plug"

    def tuya_key(self, name: str) -> str:
        """The local key of a Tuya device: TUYA_KEY_<NAME> in .env (never in devices.json, which is tracked)."""
        return os.getenv("TUYA_KEY_" + name.upper().replace(" ", "_"), self.env.get("TUYA_KEY_" + name.upper().replace(" ", "_"), ""))

    def _resolve_targets(self, target_name: str = "all") -> List[Tuple[str, str, str, str]]:
        """Resolves a spoken target name to concrete (name, ip, mac, type) light
        entries, handling 'all'/generic-light synonyms and the sticky last-target
        memory used when a target isn't named at all."""
        all_synonyms = {"all", "all lights", "all the lights", "everything", "every light"}
        generic_targets = {"light", "the light", "lights", "the lights", ""}

        target_name_clean = str(target_name).lower().strip() if target_name else ""

        if target_name_clean in generic_targets:
            effective_target = getattr(self, 'last_target', 'all')
            if target_name_clean and self._is_plug(effective_target):      # "turn on the light" after "the heater" must not switch the heater
                effective_target = "all"
        elif target_name_clean in all_synonyms:
            self.last_target = "all"
            self._save_last_target("all")
            effective_target = "all"
        else:
            effective_target = target_name_clean

        targets = []
        if effective_target == "all" or not effective_target:
            if self.lights:
                for name, dev in self.lights.items():
                    if _kind(dev) != "plug":                  # a plug is only hit when it is named: "all lights" can never switch the heater
                        targets.append((name, dev['ip'], dev.get('mac', ''), dev['type']))
            elif self.bulb_ip:
                targets.append(("default", self.bulb_ip, self.bulb_mac, self.light_type))
        else:
            norm_target = effective_target.replace('_', ' ').strip()
            for name, dev in self.lights.items():
                norm_name = name.lower().replace('_', ' ').strip()
                if norm_target in norm_name or norm_name in norm_target:
                    targets.append((name, dev['ip'], dev.get('mac', ''), dev['type']))
                    self.last_target = name
                    self._save_last_target(name)
                    break

            if not targets and self.bulb_ip:
                 targets.append(("default", self.bulb_ip, self.bulb_mac, self.light_type))

        if not targets:
            raise ValueError("No matching lights found to execute command.")
        return targets

    async def _run_on_targets(self, targets, worker) -> None:
        """Runs `worker` on every target, letting one failure not stop the others, then raises
        LightCommandError naming the lights that failed so the caller doesn't report success."""
        failures = []

        async def guarded(name, ip, mac, l_type):
            try:
                await worker(name, ip, mac, l_type)
            except Exception as e:
                logging.error(f"Hardware communication error for {name}: {e}")
                failures.append((name, e))

        await asyncio.gather(*(guarded(*t) for t in targets))
        if failures:
            raise LightCommandError(_describe_failures(failures))

    async def control_bulb(self, toggle: bool = False, on: bool = False, off: bool = False,
                           color: str = None, lum: int = None, temp: int = None, retry_attempt: bool = False, target_name: str = "all",
                           rgb=None) -> None:
        rgb = _parse_rgb(rgb)
        targets = self._resolve_targets(target_name)
        extra = {"rgb": rgb} if rgb else {}          # only passed when given: the other call shapes stay as they were
        if color or lum is not None or temp is not None or rgb:
            for name, _, _, _ in targets:                # refused before any light is touched, not silently ignored
                if self._is_plug(name):
                    raise LightCommandError(f"The {name.replace('_', ' ')} is a plug: it only switches on and off.")

        async def execute_single(name, ip, mac, l_type):
            if l_type == "tuya":
                await self._execute_tuya_target(name, toggle, on, off)
            elif l_type == "wiz":
                await self._execute_wiz_target(ip, toggle, on, off, color, lum, temp, **extra)
            else:
                await self._execute_tapo_target(ip, toggle, on, off, color, lum, temp, **extra)

        await self._run_on_targets(targets, execute_single)

    # Default step (percentage points) for a relative "lower/raise the brightness"
    # request that doesn't name a specific percentage.
    BRIGHTNESS_STEP = 20

    async def adjust_brightness(self, direction: str, target_name: str = "all") -> None:
        """Reads each target light's current brightness and nudges it up or
        down by BRIGHTNESS_STEP, clamped to 1-100."""
        targets = self._resolve_targets(target_name)
        for name, _, _, _ in targets:
            if self._is_plug(name):
                raise LightCommandError(f"The {name.replace('_', ' ')} is a plug: it has no brightness.")
        delta = self.BRIGHTNESS_STEP if direction == "up" else -self.BRIGHTNESS_STEP

        async def adjust_single(name, ip, mac, l_type):
            if l_type == "wiz":
                bulb = wizlight(ip)
                await asyncio.wait_for(bulb.updateState(), timeout=3.0)
                current_hex = bulb.state[0].get_brightness() if bulb.state and bulb.state[0] else None
                current_percent = hex_to_percent(current_hex) if current_hex is not None else 50
                new_percent = max(1, min(100, current_percent + delta))
                await self._execute_wiz_target(ip, False, True, False, None, new_percent, None)
            else:
                async def step(device):                  # read and write inside one locked session
                    info = await device.get_device_info()
                    current_percent = getattr(info, 'brightness', 50) or 50
                    new_percent = max(1, min(100, current_percent + delta))
                    await self._apply_tapo(device, False, True, False, None, new_percent, None)
                await self._tapo_call(ip, step)

        await self._run_on_targets(targets, adjust_single)
        
    async def _execute_tuya_target(self, name, toggle, on, off):
        entry, key = self.lights[name], self.tuya_key(name)
        if toggle:
            await clTuya.toggle_power(entry, key)
        elif on or off:
            await clTuya.set_power(entry, key, bool(on))

    async def _execute_wiz_target(self, ip, toggle, on, off, color, lum, temp, rgb=None):
        bulb = wizlight(ip)
        if toggle:
            await asyncio.wait_for(bulb.updateState(), timeout=3.0)
            if bulb.status: await bulb.turn_off()
            else: await bulb.turn_on(PilotBuilder(brightness=255))
            return
        elif off:
            await bulb.turn_off()
            return

        if on or lum is not None or temp is not None or color or rgb:
            # PilotBuilder.brightness expects a 0-255 value, not the 0-100 percent lum already is.
            brightness = percent_to_hex(lum) if lum is not None else 255
            if rgb:                                   # an explicit colour beats a colour name
                await bulb.turn_on(PilotBuilder(brightness=brightness, rgb=rgb))
                return
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

    async def _execute_tapo_target(self, ip, toggle, on, off, color, lum, temp, rgb=None):
        await self._tapo_call(ip, lambda device: self._apply_tapo(device, toggle, on, off, color, lum, temp, rgb))

    async def _apply_tapo(self, device, toggle, on, off, color, lum, temp, rgb=None):
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
        if rgb:                                       # an explicit colour beats a colour name
            h, s, v = colorsys.rgb_to_hsv(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
            tasks.append(device.set_hue_saturation(int(h * 360), int(s * 100)))
        elif color:
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
            p_mac = _normalize_mac(p_dev.get('mac', ''))
            for saved_name, saved_dev in self.lights.items():
                s_mac = _normalize_mac(saved_dev.get('mac', ''))
                if (p_mac and p_mac == s_mac) or (p_dev.get('ip') and p_dev['ip'] == saved_dev.get('ip')):
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
            header = "\n" + "=" * 60 + "\n" + "{:^60}".format("DISCOVERED HARDWARE TARGETS") + "\n" + "=" * 60 + "\n"
            table_str = "  {:<5} {:<8} {:<15} {:<15}\n".format("IDX", "TYPE", "MODEL", "IP")
            table_str += "  " + "-" * 54 + "\n"
            for i, dev in enumerate(parsed_devices):
                table_str += "  {:<5} {:<8} {:<15} {:<15}\n".format(f"[{i}]", dev['type'].upper(), dev['model'], dev['ip'])
            footer = "=" * 60 + "\n"
            logging.info(header + table_str + footer)
            self._handle_cli_save(parsed_devices)

    def _handle_cli_save(self, devices: List[Dict[str, str]]) -> None:
        choice = input("\nSelect a device index to target (or press Enter to exit): ")
        if choice.isdigit() and int(choice) < len(devices):
            selected = devices[int(choice)]
            if input(f"Confirm save for {selected['ip']} to local workspace configurations? (y/n): ").lower() == 'y':
                sel_mac = _normalize_mac(selected.get('mac', ''))
                existing_name = None
                for k, dev_info in self.lights.items():
                    if (sel_mac and sel_mac == _normalize_mac(dev_info.get('mac', ''))) or (selected.get('ip') == dev_info.get('ip')):
                        existing_name = k
                        break
                
                name = existing_name or f"LIGHT_{len(self.lights)+1}"
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
            sel_mac = _normalize_mac(selected_device.get('mac', ''))
            for k, dev_info in self.lights.items():
                if (sel_mac and sel_mac == _normalize_mac(dev_info.get('mac', ''))) or (selected_device.get('ip') == dev_info.get('ip')):
                    name = k
                    selected_device['saved_name'] = k
                    break

        if not name:
            name = f"LIGHT_{len(self.lights)+1}"
            self.lights[name] = {
                "ip": selected_device['ip'],
                "mac": selected_device['mac'],
                "type": selected_device['type'].lower()
            }
            self._save_devices()
        
        self.update_env_credentials(selected_device['ip'], selected_device['mac'], selected_device['type'].lower(), "main")

        return {
            "status": "success",
            "action": "request_naming",
            "temp_name": name,
            "message": f"Selected {name.replace('_', ' ')}." if selected_device.get('saved_name') else f"Successfully connected to {selected_device['type']}."
        }

async def poll_light_status(manager: LightManager) -> None:
    """Background task: polls the lights and publishes jarvis/sys/light_status when asked (poll_trigger) and every POLL_INTERVAL."""
    failure_tracker = {}
    plug_cache = {}                       # name -> (time, status): a plug is read less often than a bulb, to keep its sessions rare
    forced = True

    async def poll_single(name, info_dict):
        ip = info_dict.get("ip")
        l_type = info_dict.get("type", "tapo").lower()
        kind = _kind(info_dict)
        if not ip: return None
        if kind == "plug" and not forced:
            cached = plug_cache.get(name)
            if cached and time.monotonic() - cached[0] < PLUG_POLL_INTERVAL:
                return cached[1]
        try:
            if l_type == "tuya":
                is_on = await clTuya.get_power(info_dict, manager.tuya_key(name))
                detected = False
                fields = {"brightness": None, "rgb": None, "kelvin": None}
            elif l_type == "wiz":
                bulb = wizlight(ip)
                await asyncio.wait_for(bulb.updateState(), timeout=3.0)
                is_on = bulb.status
                bulb_type = getattr(bulb, "bulbtype", None)
                detected = bool(bulb_type.features.color) if bulb_type else None
                fields = _wiz_status_fields(bulb)
            else:
                model = manager.env.get("TAPO_MODEL", "l530").lower()
                info = await manager._tapo_call(ip, lambda device: device.get_device_info())
                is_on = info.device_on
                detected = model not in TAPO_WHITE_ONLY_MODELS
                fields = _tapo_status_fields(info)
            failure_tracker[name] = 0
            status = {"name": name.replace("_", " ").title(), "is_on": is_on, "offline": False, "kind": kind,
                      "brand": l_type, "colour": _colour_capability(info_dict, detected), **fields}
            if kind == "plug": plug_cache[name] = (time.monotonic(), status)
            return status
        except Exception as e:
            fails = failure_tracker.get(name, 0) + 1
            failure_tracker[name] = fails
            logging.debug(f"Light polling error for {name} (fail {fails}): {repr(e)}")
            is_offline = fails >= 3
            # is_on is unknown (None, not "off") until the light has failed enough polls to be called offline.
            status = {"name": name.replace("_", " ").title(), "is_on": False if is_offline else None, "offline": is_offline, "kind": kind,
                      "brand": l_type, "colour": _colour_capability(info_dict, False if kind == "plug" else None)}
            if kind == "plug": plug_cache[name] = (time.monotonic(), status)
            return status

    while True:
        try:
            async with aiomqtt.Client("localhost") as mqtt_client:
                while True:
                    forced = forced or manager.poll_trigger.is_set()       # asked for (a command, a refresh): read the plugs too
                    manager.poll_trigger.clear()
                    results = await asyncio.gather(*(poll_single(n, i) for n, i in list(manager.lights.items())))
                    statuses = [r for r in results if r]
                    await mqtt_client.publish("jarvis/sys/light_status", json.dumps({"network": manager.current_ssid, "lights": statuses}))
                    forced = False
                    try:
                        await asyncio.wait_for(manager.poll_trigger.wait(), timeout=POLL_INTERVAL)
                    except asyncio.TimeoutError:
                        pass
        except aiomqtt.MqttError:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break

# --- MQTT SERVICE LISTENER ---
async def _publish_feedback(mqtt_client, payload: Dict[str, Any]) -> None:
    """Publishes a command's result. The listener's connection can be dead by then (a stalled loop misses its keepalive), so a
    failure retries on a fresh connection instead of losing the result, and never raises into an unobserved task."""
    body = json.dumps(payload)
    try:
        await mqtt_client.publish("jarvis/feedback", body)
    except Exception:
        try:
            async with aiomqtt.Client("localhost") as fresh:
                await fresh.publish("jarvis/feedback", body)
        except Exception as e:
            logging.error(f"Could not publish light feedback: {e}")


async def mqtt_service_listener(manager: LightManager) -> None:
    logging.info("Service Mode initialized. Listening on MQTT topics...")
    background_tasks: set = set()
    while True:
        try:
            async with aiomqtt.Client("localhost") as mqtt_client:
                await mqtt_client.subscribe("home/room/+/set") 
                await mqtt_client.subscribe("system/discovery")
                await mqtt_client.publish("jarvis/sys/module_ready", json.dumps({"module": "light"}))
                
                async for message in mqtt_client.messages:
                    try:
                        payload = json.loads(message.payload.decode('utf-8'))
                        logging.info(f"Incoming Request Loop Event: {payload}")
                        action = payload.get("action")
                        
                        if action == "discover":
                            await manager.discovery_mode(voice_mode=True)
                            continue
                            
                        if action == "refresh_lights":
                            manager.refresh_lights()
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
                                    await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "message": msg}))
                                else:
                                    msg = f"Could not find a light matching {old_name}."
                                    await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "error", "message": msg}))
                            else:
                                msg = "Please specify the current name and the new name, like 'rename desk light to ceiling light'."
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "error", "message": msg}))
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
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "message": msg}))
                            else:
                                match = _match_light(target_str, manager.lights)
                                if match:
                                    del manager.lights[match]
                                    manager._save_devices()
                                    msg = f"Removed {match.replace('_', ' ')}."
                                    await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "message": msg}))
                                else:
                                    msg = f"Could not find a light matching {target_str}."
                                    await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "error", "message": msg}))
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
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "message": msg}))
                            else:
                                msg = f"Could not find a light matching {target_str}."
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "error", "message": msg}))
                            continue
                        
                        # --- ISOLATED HARDWARE TASK ---
                        async def execute_hardware(payload_data, action_cmd):
                            is_silent = payload_data.get("silent", False)
                            light_target = payload_data.get("light_target", "")
                            try:
                                if action_cmd in ("brightness_up", "brightness_down"):
                                    await manager.adjust_brightness(
                                        direction="up" if action_cmd == "brightness_up" else "down",
                                        target_name=light_target
                                    )
                                else:
                                    await manager.control_bulb(
                                        on=(action_cmd == "on"), off=(action_cmd == "off"), toggle=(action_cmd == "toggle"),
                                        color=payload_data.get("color"), lum=payload_data.get("lum"), temp=payload_data.get("temp"),
                                        target_name=light_target, rgb=payload_data.get("rgb")
                                    )
                                feedback = {
                                    "device": "smart_lights", "status": "success",
                                    "message": f"Successfully shifted hardware targets to '{action_cmd}' state.",
                                    "action_cmd": action_cmd,
                                    "target": manager.last_target,
                                    "light_target": light_target,
                                    "silent": True
                                }
                            except Exception as e:
                                feedback = {
                                    "device": "smart_lights", "status": "error", "message": str(e),
                                    "action_cmd": action_cmd, "light_target": light_target, "silent": is_silent
                                }
                            manager.poll_trigger.set()           # success or not, the dashboard should show what the light really is
                            await _publish_feedback(mqtt_client, feedback)

                        task = asyncio.create_task(execute_hardware(payload, action))
                        background_tasks.add(task)           # a task nobody references can be garbage-collected mid-run
                        task.add_done_callback(background_tasks.discard)
                        
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
        logging.error("\nError: No colors found in entities.json matrix.\n")
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

    header = "\n" + "=" * 70 + "\n" + "{:^70}".format("AVAILABLE COLOR PRESETS & ALIASES") + "\n" + "=" * 70 + "\n"
    
    whites = "\n[ WHITES & TEMPERATURES ]\n\n"
    # Sort by temperature value to group logically
    for val, names in sorted(temps_grouped.items(), key=lambda x: x[0]):
        aliases = ", ".join(sorted(names))
        whites += f"  • {aliases}\n"

    colors = "\n[ COLORS (RGB) ]\n\n"
    # Sort alphabetically by the primary alias
    for rgb, names in sorted(colors_grouped.items(), key=lambda x: sorted(x[1])[0]):
        aliases = ", ".join(sorted(names))
        colors += f"  • {aliases}\n"
        
    footer = "\n" + "=" * 70 + "\n"
    
    logging.info(header + whites + colors + footer)

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
        try:
            asyncio.run(manager.control_bulb(
                toggle=args.toggle, on=args.on, off=args.off,
                color=args.color, lum=args.lum, temp=args.temp
            ))
        except LightCommandError as e:
            print(e)
            sys.exit(1)
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