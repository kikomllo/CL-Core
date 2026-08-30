import re

with open("src/clUI.py", "r") as f:
    code = f.read()

pattern = r"""        if mode == "set_fullscreen":
            logging.info\(f"\[DEBUG UI\] set_fullscreen triggered. is_fullscreen: \{getattr\(self, 'is_fullscreen', False\)\}"\)
            screens = UIScaler.get\(\).get_stable_screens\(\)
            is_monitor_swap = getattr\(self, 'is_fullscreen', False\)
            old_geom = None
            if is_monitor_swap:
                self.current_monitor_idx = \(getattr\(self, 'current_monitor_idx', 0\) \+ 1\) % len\(screens\)"""

replacement = """        if mode == "set_fullscreen":
            logging.info(f"[DEBUG UI] set_fullscreen triggered. is_fullscreen: {getattr(self, 'is_fullscreen', False)}")
            screens = UIScaler.get().get_stable_screens()
            is_monitor_swap = getattr(self, 'is_fullscreen', False)
            old_geom = None
            if is_monitor_swap:
                logging.info(f"[DEBUG UI] SWAP TRIGGERED. Old idx: {self.current_monitor_idx}")
                self.current_monitor_idx = (getattr(self, 'current_monitor_idx', 0) + 1) % len(screens)
                logging.info(f"[DEBUG UI] Target swap idx: {self.current_monitor_idx} (Screen: {screens[self.current_monitor_idx].name()})")"""

if re.search(pattern, code):
    code = re.sub(pattern, replacement, code)
    print("Added debug to set_ui_mode")
else:
    print("Could not find pattern for set_ui_mode")

with open("src/clUI.py", "w") as f:
    f.write(code)

