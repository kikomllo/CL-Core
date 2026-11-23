"""The web dashboard's Lights and Spotify Settings tabs: the `.env` credentials and `config/devices.json`, behind an allow-list.

Pure file logic (no aiohttp), so it is testable on its own; clWebUI.py only wraps it in HTTP. Three rules:
  * `.env` keys are a fixed list and every value is plain one-line text (a newline in a value would write a second variable);
  * the two secrets (the Tapo password, the Spotify client secret) are never sent to the browser: a read only says whether one is set,
    and a write replaces it;
  * `devices.json` changes go through a few named operations (add/remove a network or light, edit one field, move a light), each
    validated, never a free-form write.
Mirrors what the Qt Settings widget does (Lights and Spotify tabs); the services read both files at start, so the page offers a restart.
"""
import ipaddress
import os
import re
from typing import Any, Dict

from dotenv import dotenv_values, set_key

ENV_PUBLIC = ("LIGHT_TYPE", "LIGHT_IP", "LIGHT_MAC", "TAPO_EMAIL", "SPOTIPY_CLIENT_ID", "SPOTIPY_REDIRECT_URI")
ENV_SECRET = ("TAPO_PASSWORD", "SPOTIPY_CLIENT_SECRET")
LIGHT_TYPES = ("wiz", "tapo")
DEFAULT_LIGHT = {"ip": "192.168.1.100", "mac": "", "type": "wiz"}            # what the Qt widget gives a new light
MAX_VALUE = 300

_PLAIN = re.compile(r"^[^\x00-\x1f\x7f]*$")
_MAC = re.compile(r"^([0-9A-Fa-f]{2}[:\-]?){5}[0-9A-Fa-f]{2}$")
_LIGHT_NAME = re.compile(r"^[a-z0-9_\-]{1,40}$")


class BadValue(ValueError):
    pass


def _plain(value: Any, what: str, limit: int = MAX_VALUE) -> str:
    if not isinstance(value, str) or len(value) > limit or not _PLAIN.match(value):
        raise BadValue(f"{what} must be short single-line text")
    return value.strip()


# ---------------------------------------------------------------- .env
def check_env(key: str, value: Any) -> str:
    """The value to store for an allow-listed `.env` key, or BadValue."""
    if key not in ENV_PUBLIC and key not in ENV_SECRET:
        raise BadValue(f"unknown setting {key!r}")
    value = _plain(value, key)
    if key == "LIGHT_TYPE" and value not in ("",) + LIGHT_TYPES:
        raise BadValue(f"LIGHT_TYPE must be one of {', '.join(LIGHT_TYPES)}")
    if key == "LIGHT_IP" and value:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise BadValue("LIGHT_IP must be an IP address")
    if key == "LIGHT_MAC" and value and not _MAC.match(value):
        raise BadValue("LIGHT_MAC must be a MAC address like aa:bb:cc:dd:ee:ff")
    if key == "SPOTIPY_REDIRECT_URI" and value and not value.lower().startswith(("http://", "https://")):
        raise BadValue("the redirect URI must start with http:// or https://")
    return value


def env_snapshot(path: str) -> Dict[str, Any]:
    """{values: the non-secret keys, secrets: {key: is it set}}. The secrets' values never leave this function."""
    try:
        file_values = dotenv_values(path)
    except OSError:
        file_values = {}
    return {"values": {k: file_values.get(k) or "" for k in ENV_PUBLIC}, "secrets": {k: bool(file_values.get(k)) for k in ENV_SECRET}}


def apply_env(path: str, key: str, value: Any) -> Dict[str, Any]:
    """Validate and write one `.env` value (python-dotenv keeps the other lines and quotes correctly). Returns what the page may know:
    the key, whether it is a secret, and the stored value only when it is not."""
    value = check_env(key, value)
    if not os.path.exists(path):
        open(path, "a", encoding="utf-8").close()
    set_key(path, key, value)
    secret = key in ENV_SECRET
    return {"key": key, "secret": secret, "set": bool(value), **({} if secret else {"value": value})}


# ---------------------------------------------------------------- devices.json
def _name(value: Any, what: str) -> str:
    value = _plain(value, what, 64)
    if not value:
        raise BadValue(f"{what} cannot be empty")
    return value


def _light_name(value: Any) -> str:
    """How the Qt widget cleans a light's name (lower case, spaces to underscores), then checks it is safe to use as a key."""
    name = _plain(value, "light name", 64).lower().replace(" ", "_")
    if not _LIGHT_NAME.match(name):
        raise BadValue("a light name is 1-40 letters, digits, - or _")
    return name


def _field(field: str, value: Any) -> str:
    value = _plain(value, field)
    if field == "type" and value not in LIGHT_TYPES:
        raise BadValue(f"type must be one of {', '.join(LIGHT_TYPES)}")
    if field == "ip" and value:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise BadValue("ip must be an IP address")
    if field == "mac" and value and not _MAC.match(value):
        raise BadValue("mac must be a MAC address like aa:bb:cc:dd:ee:ff")
    return value


def _networks(data: dict) -> dict:
    """The `networks` dict of a devices.json document, converting the old `{lights: {...}}` shape in place like the Qt widget."""
    if "networks" not in data:
        data["networks"] = {"Home Network": data.pop("lights", {})}
    return data["networks"]


def devices_snapshot(loader) -> Dict[str, Any]:
    try:
        data = loader.load_json("devices.json")
    except Exception:
        data = {}
    return {"networks": {net: {n: dict(info) for n, info in lights.items()} for net, lights in _networks(dict(data)).items() if isinstance(lights, dict)}}


def apply_device_op(loader, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """One validated change to devices.json; returns the new snapshot. Raises BadValue (nothing is written then)."""
    get = args.get

    def change(data: dict) -> None:
        nets = _networks(data)

        def net(name_key="network") -> dict:
            n = get(name_key)
            if not isinstance(n, str) or n not in nets:
                raise BadValue(f"no WiFi network called {n!r}")
            return nets[n]

        if op == "add_network":
            name = _name(get("network"), "network name")
            if name in nets:
                raise BadValue(f"{name!r} already exists")
            nets[name] = {}
        elif op == "remove_network":
            net()
            del nets[get("network")]
        elif op == "add_light":
            lights, name = net(), _light_name(get("name"))
            if name in lights:
                raise BadValue(f"{name!r} already exists on this network")
            lights[name] = dict(DEFAULT_LIGHT)
        elif op == "remove_light":
            lights, name = net(), get("name")
            if name not in lights:
                raise BadValue(f"no light called {name!r}")
            del lights[name]
        elif op == "update_light":
            lights, name, field = net(), get("name"), get("field")
            if name not in lights:
                raise BadValue(f"no light called {name!r}")
            if field == "name":
                new = _light_name(get("value"))
                if new == name:
                    return
                if new in lights:
                    raise BadValue(f"{new!r} already exists on this network")
                renamed = {(new if k == name else k): v for k, v in lights.items()}      # keeps the order
                lights.clear(); lights.update(renamed)
            elif field in ("type", "ip", "mac"):
                lights[name][field] = _field(field, get("value"))
            else:
                raise BadValue(f"unknown field {field!r}")
        elif op == "move_light":
            src, dst, name = net(), net("to"), get("name")
            if name not in src:
                raise BadValue(f"no light called {name!r}")
            if src is dst:
                return
            if name in dst:
                raise BadValue(f"{get('to')!r} already has a light called {name!r}")
            dst[name] = src.pop(name)
        else:
            raise BadValue(f"unknown operation {op!r}")

    loader.update_json_atomic("devices.json", change)
    return devices_snapshot(loader)
