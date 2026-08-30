import re

with open("src/clUI.py", "r") as f:
    code = f.read()

pattern = r"""    def refresh_layout\(self\):
        screens = UIScaler.get\(\).get_stable_screens\(\)
        
        # Dynamically determine which screen the window is ACTUALLY on, as Wayland/user can move it independently
        current_screen = self.screen\(\)
        idx = 0
        if current_screen:
            screen_name = current_screen.name\(\)
            for i, s in enumerate\(screens\):
                if s.name\(\) == screen_name:
                    idx = i
                    break
                    
        self.current_monitor_idx = idx
        UIScaler.get\(\).set_active_monitor\(idx\)
        s = UIScaler.get\(\).scale

        # Use actual window dimensions instead of target screen geometry to prevent Wayland scaling/cropping bugs
        win_w = self.width\(\)
        win_h = self.height\(\)"""

replacement = """    def refresh_layout(self):
        screens = UIScaler.get().get_stable_screens()
        
        # Dynamically determine which screen the window is ACTUALLY on, as Wayland/user can move it independently
        current_screen = self.screen()
        idx = 0
        if current_screen:
            screen_name = current_screen.name()
            for i, s in enumerate(screens):
                if s.name() == screen_name:
                    idx = i
                    break
                    
        self.current_monitor_idx = idx
        UIScaler.get().set_active_monitor(idx)
        s = UIScaler.get().scale

        # Use actual window dimensions instead of target screen geometry to prevent Wayland scaling/cropping bugs
        win_w = self.width()
        win_h = self.height()
        
        import logging
        logging.info(f"[DEBUG LAYOUT] Physical Screen: {current_screen.name() if current_screen else 'Unknown'} (idx: {idx})")
        logging.info(f"[DEBUG LAYOUT] Window Size: {win_w}x{win_h}")
        logging.info(f"[DEBUG LAYOUT] Applied Scale: {s(100)/100.0}")"""

if re.search(pattern, code):
    code = re.sub(pattern, replacement, code)
    print("Added debug to refresh_layout")
else:
    print("Could not find pattern for debug")

with open("src/clUI.py", "w") as f:
    f.write(code)

