"""Per-machine config: config/core.json is local (gitignored), config/core.default.json is the tracked template.

core.json holds machine values (audio devices, `hardware`, `stt_model`) and is rewritten at runtime by several services,
so it must not be shared through git. `ensure_core()` creates it from the template and adds keys the template gained
since, never overwriting a value the machine already has. `apply_profile()` sets the values of a hardware tier from
config/profiles.json ("lite" = integrated graphics / 12 GB, "full" = a GPU desktop).

    python src/utils/clLocalConfig.py               # create / top up core.json
    python src/utils/clLocalConfig.py --profile lite
"""

import argparse
import copy
import json
import logging
import os
from typing import Any, Dict, Optional

CONFIG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "config"))


def _read(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fill_missing(local: Dict[str, Any], default: Dict[str, Any]) -> bool:
    """Adds keys that `default` has and `local` lacks, recursing into dicts. Never replaces an existing value
    (lists and scalars are the machine's own). Returns True if anything was added."""
    changed = False
    for key, value in default.items():
        if key not in local:
            local[key] = copy.deepcopy(value)
            changed = True
        elif isinstance(local[key], dict) and isinstance(value, dict):
            changed |= fill_missing(local[key], value)
    return changed


def _deep_set(target: Dict[str, Any], values: Dict[str, Any]) -> None:
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_set(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _update(core_path: str, callback) -> None:
    """Read-modify-write under the same lock ConfigLoader.update_json_atomic uses, so a running service's write is not lost."""
    from filelock import FileLock
    with FileLock(core_path + ".lock", timeout=5):
        data = _read(core_path) if os.path.exists(core_path) else {}
        if callback(data):
            tmp = core_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            os.replace(tmp, core_path)


def ensure_core(config_dir: Optional[str] = None) -> bool:
    """Creates core.json from core.default.json if missing, else tops it up with new template keys. True if it wrote."""
    config_dir = config_dir or CONFIG_DIR
    default_path = os.path.join(config_dir, "core.default.json")
    core_path = os.path.join(config_dir, "core.json")
    if not os.path.exists(default_path):
        return False
    default = _read(default_path)
    wrote = {"v": False}

    def cb(data: Dict[str, Any]) -> bool:
        if not data:
            data.update(copy.deepcopy(default))
            logging.info("[CONFIG] Created config/core.json from core.default.json (this machine's own settings).")
            wrote["v"] = True
            return True
        if fill_missing(data, default):
            logging.info("[CONFIG] Added new settings from core.default.json to config/core.json.")
            wrote["v"] = True
            return True
        return False

    _update(core_path, cb)
    return wrote["v"]


def apply_profile(name: str, config_dir: Optional[str] = None) -> Dict[str, Any]:
    """Writes the profile's values into core.json's `settings` and records the profile name. Returns the values applied."""
    config_dir = config_dir or CONFIG_DIR
    profiles = _read(os.path.join(config_dir, "profiles.json"))
    if name not in profiles:
        raise KeyError(f"Unknown profile '{name}' (have: {', '.join(profiles)})")
    values = profiles[name]
    ensure_core(config_dir)

    def cb(data: Dict[str, Any]) -> bool:
        settings = data.setdefault("settings", {})
        _deep_set(settings, values)
        settings["performance_profile"] = name
        return True

    _update(os.path.join(config_dir, "core.json"), cb)
    return values


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Create or update this machine's config/core.json.")
    parser.add_argument("--profile", help="apply a hardware tier from config/profiles.json (lite | full)")
    args = parser.parse_args()
    if args.profile:
        for key, value in apply_profile(args.profile).items():
            print(f"  {key} = {json.dumps(value)}")
        print(f"Applied profile '{args.profile}' to config/core.json.")
    else:
        print("core.json written." if ensure_core() else "core.json is up to date.")
