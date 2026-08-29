import sys
try:
    from evdev import ecodes
except ImportError:
    print("no evdev")
    sys.exit(0)

MODIFIER_MAP = {
    "<ctrl>": [ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL],
    "<alt>": [ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT],
    "<shift>": [ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT],
}
def parse_evdev_hotkey(hotkey_str):
    parts = hotkey_str.lower().split("+")
    modifiers = []
    main_key = None
    for part in parts:
        if part in MODIFIER_MAP:
            modifiers.append(MODIFIER_MAP[part])
        else:
            key_name = f"KEY_{part.upper()}"
            main_key = getattr(ecodes, key_name, None)
    return modifiers, main_key

print(parse_evdev_hotkey("<ctrl>+<alt>+<shift>+f"))
print(parse_evdev_hotkey("<ctrl>+<alt>+<shift>+a"))
