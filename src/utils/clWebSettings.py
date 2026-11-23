"""The settings the web dashboard may read and change in core.json: a fixed allow-list with a type or a set of choices per
key, so the page (or anything that can reach its endpoint) can never write an arbitrary value or an arbitrary key. Pure
file logic, no aiohttp, so it is testable on its own; clWebUI.py only wraps it in HTTP.

Mirrors what the Qt Settings widget writes (System, Updates and Debug tabs). Dispatching the live action that goes with a
change (silent mode, follow-ups, debug flags ...) stays with the page, exactly as the Qt widget does it.
"""
import re
from typing import Any, Dict, List

# key -> list of allowed strings, or bool
SETTINGS: Dict[str, Any] = {
    "silent_mode": bool,
    "enable_followup": bool,
    "theme": ["jarvis", "ultron"],
    "ecosystem_state": ["normal", "debug", "background"],
    "stt_model": ["tiny", "base", "small", "medium", "large"],
    "stt_processing_mode": ["parallel", "sequential"],
    "hardware": ["cpu", "cuda"],
    "update_mode": ["confirm", "direct"],
}
DEBUG_FLAGS = ("wakeword_debug_logging", "wakeword_debug_saving", "capture_wakeword_positive", "capture_stt_training_data")
DEFAULTS = {"silent_mode": False, "enable_followup": False, "theme": "jarvis", "ecosystem_state": "normal", "stt_model": "small",
            "stt_processing_mode": "parallel", "hardware": "cpu", "update_mode": "confirm"}
START_ON_LOGIN = "start_on_login"
AUDIO_KEYS = ("audio_settings.input_device", "audio_settings.output_device")    # a device name: any short non-empty string
WIFI_GATE_KEY, WIFI_MAC_KEY = "monitor_settings.wifi_gate_enabled", "monitor_settings.wifi_mac"
PRESENCE_ENABLED_KEY, PRESENCE_LIGHT_PREFIX = "automation_settings.presence_lights.enabled", "automation_settings.presence_lights.light."
_LIGHT_NAME = re.compile(r"^[A-Za-z0-9_\- ]{1,64}$")
_PLAIN_TEXT = re.compile(r"^[^\x00-\x1f\x7f]*$")


class BadSetting(ValueError):
    pass


def check(key: str, value: Any) -> Any:
    """The value to store for `key`, or BadSetting. Bools must be real booleans (a string "false" is truthy)."""
    if key in (WIFI_GATE_KEY, PRESENCE_ENABLED_KEY) or key.startswith(PRESENCE_LIGHT_PREFIX):
        if key.startswith(PRESENCE_LIGHT_PREFIX) and not _LIGHT_NAME.match(key[len(PRESENCE_LIGHT_PREFIX):]):
            raise BadSetting(f"{key!r} is not a light name")
        if not isinstance(value, bool):
            raise BadSetting(f"{key} must be true or false")
        return value
    if key == WIFI_MAC_KEY:                                  # the phone's Wi-Fi MAC; empty clears it
        if not isinstance(value, str) or len(value) > 64 or not _PLAIN_TEXT.match(value):
            raise BadSetting(f"{key} must be short plain text")
        return value.strip().lower() or None
    if key in AUDIO_KEYS:
        if not isinstance(value, str) or not value.strip() or len(value) > 300:
            raise BadSetting(f"{key} must be a device name")
        return value
    if key in (START_ON_LOGIN,) or key.startswith("debug_flags."):
        if key.startswith("debug_flags.") and key.split(".", 1)[1] not in DEBUG_FLAGS:
            raise BadSetting(f"unknown debug flag {key!r}")
        if not isinstance(value, bool):
            raise BadSetting(f"{key} must be true or false")
        return value
    kind = SETTINGS.get(key)
    if kind is None:
        raise BadSetting(f"unknown setting {key!r}")
    if kind is bool:
        if not isinstance(value, bool):
            raise BadSetting(f"{key} must be true or false")
        return value
    if value not in kind:
        raise BadSetting(f"{key} must be one of {', '.join(kind)}")
    return value


def apply(loader, key: str, value: Any) -> Any:
    """Validate, then write one setting to core.json atomically. Returns the stored value. start_on_login is not a core.json
    setting (it is an OS startup entry): the caller handles it."""
    value = check(key, value)

    def update(core):
        settings = core.setdefault("settings", {})
        if key.startswith("debug_flags."):
            settings.setdefault("debug_flags", {})[key.split(".", 1)[1]] = value
            return
        if key in AUDIO_KEYS:
            settings.setdefault("audio_settings", {})[key.split(".", 1)[1]] = value
            return
        if key in (WIFI_GATE_KEY, WIFI_MAC_KEY):
            settings.setdefault("monitor_settings", {})[key.split(".", 1)[1]] = value
            return
        if key == PRESENCE_ENABLED_KEY:
            settings.setdefault("automation_settings", {}).setdefault("presence_lights", {})["enabled"] = value
            return
        if key.startswith(PRESENCE_LIGHT_PREFIX):
            name = key[len(PRESENCE_LIGHT_PREFIX):]
            if name not in light_names(loader):
                raise BadSetting(f"no saved light called {name!r}")
            chosen = settings.setdefault("automation_settings", {}).setdefault("presence_lights", {}).setdefault("lights", [])
            if value and name not in chosen:
                chosen.append(name)
            elif not value and name in chosen:
                chosen.remove(name)
            return
        settings[key] = value
        if key == "ecosystem_state":           # the Qt widget keeps the supervisor's mode in step
            core.setdefault("ecosystem", {})["mode"] = value.upper()

    loader.update_json_atomic("core.json", update)
    return value


def light_names(loader) -> List[str]:
    """The saved lights' names (devices.json, every network, no repeats), as the Settings widget lists them."""
    try:
        networks = loader.load_json("devices.json").get("networks", {})
    except Exception:
        return []
    names: List[str] = []
    for lights in networks.values():
        for name in lights:
            if name not in names:
                names.append(name)
    return names


def snapshot(loader) -> Dict[str, Any]:
    """Every setting the page shows, with the defaults filled in for what core.json does not have."""
    try:
        settings = loader.load_json("core.json").get("settings", {})
    except Exception:
        settings = {}
    out = {key: settings.get(key, default) for key, default in DEFAULTS.items()}
    flags = settings.get("debug_flags", {}) or {}
    audio = settings.get("audio_settings", {}) or {}
    out["audio_settings"] = {k: audio.get(k) or "System Default" for k in ("input_device", "output_device")}
    out["debug_flags"] = {name: bool(flags.get(name, False)) for name in DEBUG_FLAGS}
    monitor = settings.get("monitor_settings", {}) or {}
    out["monitor_settings"] = {"wifi_gate_enabled": bool(monitor.get("wifi_gate_enabled", False)), "wifi_mac": monitor.get("wifi_mac") or ""}
    rule = (settings.get("automation_settings", {}) or {}).get("presence_lights", {}) or {}
    out["presence_lights"] = {"enabled": bool(rule.get("enabled", False)), "lights": [n for n in rule.get("lights", []) if isinstance(n, str)]}
    out["light_names"] = light_names(loader)
    return out


# ---------------------------------------------------------------- keybinds (config/keybinds.json)
PTT_ACTION = "push_to_talk"
PTT_KEYS = frozenset({"KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_CAPSLOCK",
                      "KEY_SPACE", *(f"KEY_F{n}" for n in range(13, 25))})            # what clKeybinds.PTT_KEY_MAP supports
_MODS = ("<ctrl>", "<alt>", "<shift>", "<super>")
KEYBIND_MODES = ("single", "continuous")


def check_keybind(action: str, field: str, value: Any) -> str:
    """The value to store for keybinds.json[action][field] (the action must already exist: the page cannot invent one)."""
    if field == "mode":
        if value not in KEYBIND_MODES:
            raise BadSetting(f"mode must be one of {', '.join(KEYBIND_MODES)}")
        return value
    if field != "key":
        raise BadSetting(f"unknown field {field!r}")
    if not isinstance(value, str):
        raise BadSetting("key must be text")
    if value == "":
        return ""                                                                         # Esc in the capture box clears the shortcut
    if action == PTT_ACTION:
        if value not in PTT_KEYS:
            raise BadSetting("push to talk takes one of Ctrl/Alt/Shift (left or right), CapsLock, Space or F13-F24")
        return value
    parts = value.split("+")
    mods, last = parts[:-1], parts[-1]
    # optional modifiers plus one letter or digit: that is what parses the same on both OSes (clKeybinds' hotkey parsers)
    if any(m not in _MODS for m in mods) or len(set(mods)) != len(mods) or not re.fullmatch(r"[a-z0-9]", last):
        raise BadSetting("a shortcut is optional modifiers plus one letter or digit, e.g. <ctrl>+<alt>+a")
    return value


def keybinds_snapshot(loader) -> Dict[str, Dict[str, str]]:
    """{action: {key, mode}} in the file's order; an old plain-string entry reads as a single-press key."""
    try:
        data = loader.load_json("keybinds.json")
    except Exception:
        return {}
    out = {}
    for action, info in data.items():
        if isinstance(info, dict):
            out[action] = {"key": str(info.get("key", "")), "mode": info.get("mode") if info.get("mode") in KEYBIND_MODES else "single"}
        else:
            out[action] = {"key": str(info), "mode": "single"}
    return out


def apply_keybind(loader, action: str, field: str, value: Any) -> str:
    if action not in keybinds_snapshot(loader):
        raise BadSetting(f"unknown shortcut {action!r}")
    value = check_keybind(action, field, value)

    def update(data):
        entry = data.get(action)
        if not isinstance(entry, dict):
            entry = data[action] = {"key": str(entry or ""), "mode": "single"}
        entry[field] = value

    loader.update_json_atomic("keybinds.json", update)
    return value
