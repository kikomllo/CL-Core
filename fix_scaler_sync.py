import re

with open("src/clUI.py", "r") as f:
    code = f.read()

pattern = r"""    def refresh_layout\(self\):
        screens = UIScaler.get\(\).get_stable_screens\(\)
        idx = getattr\(self, 'current_monitor_idx', 0\)
        if idx >= len\(screens\):
            idx = 0

        UIScaler.get\(\).set_active_monitor\(idx\)"""

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
        UIScaler.get().set_active_monitor(idx)"""

if re.search(pattern, code):
    code = re.sub(pattern, replacement, code)
    print("Replaced refresh_layout sync")
else:
    print("Could not find pattern")

with open("src/clUI.py", "w") as f:
    f.write(code)

