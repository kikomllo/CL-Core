import json
import logging
import time
import os
import sys
import threading
import select
from pynput import keyboard
from utils.clActionRouter import ActionRouter

# Optional Linux evdev support for Wayland background keylogging
try:
    import evdev
    from evdev import ecodes
    EVDEV_AVAILABLE = sys.platform.startswith('linux')
except ImportError:
    EVDEV_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [KEYBINDS] %(message)s", datefmt="%H:%M:%S")

def is_debug():
    config_path = os.path.join("config", "core.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            mode = cfg.get("ecosystem", {}).get("mode", "")
            return mode.upper() == "DEBUG"
    except Exception:
        return False

def load_keybinds():
    config_path = os.path.join("config", "keybinds.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load keybinds.json: {e}")
        return {
            "abort": {"key": "<ctrl>+<alt>+<shift>+a", "mode": "single"},
            "ui_fullscreen": {"key": "<ctrl>+<alt>+<shift>+f", "mode": "single"},
            "ui_overlay": {"key": "<ctrl>+<alt>+<shift>+o", "mode": "single"},
            "push_to_talk": {"key": "KEY_RIGHTALT", "mode": "continuous"}
        }

DEBUG_MODE = is_debug()
EVDEV_PTT_READY = False

def evdev_listener():
    """Reads raw hardware inputs from the Linux kernel to bypass Wayland security limits."""
    global EVDEV_PTT_READY
    
    keybinds = load_keybinds()
    ptt_key_str = keybinds.get("push_to_talk", {}).get("key", "KEY_RIGHTALT")
    ptt_ecode = getattr(ecodes, ptt_key_str, ecodes.KEY_RIGHTALT)
    ptt_active = False
    router = ActionRouter()
    
    while True:
        keyboards = []
        try:
            import glob
            paths = glob.glob('/dev/input/event*')
            for path in paths:
                try:
                    device = evdev.InputDevice(path)
                    if ecodes.EV_KEY in device.capabilities():
                        if ecodes.KEY_ENTER in device.capabilities()[ecodes.EV_KEY]:
                            keyboards.append(device)
                except Exception:
                    pass
        except PermissionError:
            logging.error("\n" + "="*80)
            logging.error("WAYLAND PUSH-TO-TALK PERMISSION DENIED!")
            logging.error("Wayland blocks background keyloggers. To use Push-to-Talk while unfocused,")
            logging.error("you must grant this user permission to read raw hardware inputs.")
            logging.error("Please run this command in your terminal:")
            logging.error(f"    sudo usermod -aG input {os.environ.get('USER', '$USER')}")
            logging.error("Then LOG OUT of your Linux session and LOG BACK IN.")
            logging.error("="*80 + "\n")
            return
            
        if not keyboards:
            time.sleep(3)
            continue

        if not EVDEV_PTT_READY:
            logging.info(f"Wayland Support Active: Monitoring {len(keyboards)} keyboard(s) via evdev.")
            EVDEV_PTT_READY = True

        try:
            while True:
                r, w, x = select.select(keyboards, [], [])
                for device in r:
                    for event in device.read():
                        if event.type == ecodes.EV_KEY:
                            if event.code == ptt_ecode:
                                if event.value == 1 and not ptt_active: # Press
                                    ptt_active = True
                                    logging.info("Push-to-Talk (Mic Opened via Hardware)")
                                    router.dispatch("mic.ptt_start")
                                elif event.value == 0 and ptt_active: # Release
                                    ptt_active = False
                                    logging.info("Push-to-Talk (Mic Closed via Hardware)")
                                    router.dispatch("mic.ptt_stop")
        except Exception as e:
            logging.warning(f"Evdev device disconnected or error ({e}). Rescanning devices...")
            time.sleep(2)

def main():
    keybinds = load_keybinds()
    router = ActionRouter()
    
    logging.info("Starting global keybind listener (Configs loaded from keybinds.json)...")
    logging.info("Active Keybinds:")
    for k, v in keybinds.items():
        if v: logging.info(f"  {v} -> {k}")
    
    if EVDEV_AVAILABLE:
        t = threading.Thread(target=evdev_listener, daemon=True)
        t.start()
        # Give evdev a moment to set EVDEV_PTT_READY
        time.sleep(0.5)
    
    ptt_active = False
    ptt_key_str = keybinds.get("push_to_talk", {}).get("key", "KEY_RIGHTALT")

    def _is_ptt_key(key):
        if "RIGHTALT" in ptt_key_str:
            if key == keyboard.Key.alt_r or key == keyboard.Key.alt_gr: return True
            if hasattr(key, 'vk') and key.vk == 65027: return True
        elif "LEFTALT" in ptt_key_str:
            if key == keyboard.Key.alt_l or key == keyboard.Key.alt: return True
        return False

    LEGACY_MAP = {
        "abort": "system.abort",
        "ui_fullscreen": "ui.fullscreen",
        "ui_overlay": "ui.overlay",
    }
    
    # Custom HotKey tracking
    hotkeys = []
    active_actions = set()
    last_triggered = {}

    for action_key, bind_info in keybinds.items():
        hotkey_str = bind_info.get("key", "")
        mode = bind_info.get("mode", "single")
        
        if not hotkey_str or action_key == "push_to_talk":
            continue
            
        actual_action = LEGACY_MAP.get(action_key, action_key)
        
        if router._get_action_def(actual_action):
            try:
                hk = keyboard.HotKey(keyboard.HotKey.parse(hotkey_str), lambda: None)
                hotkeys.append({
                    "hk": hk,
                    "action": actual_action,
                    "mode": mode
                })
            except Exception as e:
                logging.error(f"Failed to parse hotkey '{hotkey_str}': {e}")
        else:
            logging.warning(f"Unknown action '{action_key}' (mapped to '{actual_action}') in keybinds.json")

    def on_press(key):
        nonlocal ptt_active
        canonical_key = l.canonical(key)
        
        # PTT Logic
        if not EVDEV_PTT_READY and _is_ptt_key(key) and not ptt_active:
            ptt_active = True
            logging.info("Push-to-Talk (Mic Opened via pynput)")
            router.dispatch("mic.ptt_start")

        # Custom HotKey logic
        for hk_def in hotkeys:
            hk = hk_def["hk"]
            action = hk_def["action"]
            mode = hk_def["mode"]
            
            hk.press(canonical_key)
            if hk._state == hk._keys: # This indicates the hotkey combination is fully met
                if mode == "single":
                    if action not in active_actions:
                        now = time.time()
                        if now - last_triggered.get(action, 0) > 0.2:
                            active_actions.add(action)
                            router.dispatch(action)
                            last_triggered[action] = now
                else:
                    # continuous mode triggers every OS key-repeat
                    router.dispatch(action)

    def on_release(key):
        nonlocal ptt_active
        canonical_key = l.canonical(key)
        
        # PTT Logic
        if not EVDEV_PTT_READY and _is_ptt_key(key) and ptt_active:
            ptt_active = False
            logging.info("Push-to-Talk (Mic Closed via pynput)")
            router.dispatch("mic.ptt_stop")

        # Custom HotKey logic
        for hk_def in hotkeys:
            hk = hk_def["hk"]
            action = hk_def["action"]
            
            hk.release(canonical_key)
            if hk._state != hk._keys: # Combination broken
                if action in active_actions:
                    active_actions.remove(action)

    l = keyboard.Listener(on_press=on_press, on_release=on_release)
    l.start()
    
    import paho.mqtt.publish as publish
    publish.single("jarvis/sys/module_ready", json.dumps({"module": "keybinds"}), hostname="localhost")
    
    try:
        l.join()
    except KeyboardInterrupt:
        logging.info("Shutting down keybind listener.")
        l.stop()

if __name__ == '__main__':
    time.sleep(2)
    main()
