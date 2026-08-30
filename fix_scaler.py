import re

with open("src/clUIScaler.py", "r") as f:
    code = f.read()

# Add get_stable_screens to UIScaler
new_methods = """    def get_stable_screens(self):
        screens = QApplication.screens()
        reg_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "monitor_registry.json"))
        registry = []
        try:
            if os.path.exists(reg_file):
                with open(reg_file, "r") as f:
                    registry = json.load(f)
        except: pass

        # Create a map of name -> screen
        screen_map = {s.name(): s for s in screens}
        
        stable_screens = []
        # First add screens that are in the registry in the registered order
        for name in registry:
            if name in screen_map:
                stable_screens.append(screen_map.pop(name))
                
        # Then append any new screens and sort them to be deterministic
        new_names = sorted(list(screen_map.keys()))
        for name in new_names:
            stable_screens.append(screen_map[name])
            registry.append(name)
            
        # Save updated registry
        try:
            os.makedirs(os.path.dirname(reg_file), exist_ok=True)
            with open(reg_file, "w") as f:
                json.dump(registry, f)
        except: pass
        
        return stable_screens

    def get_primary_monitor_idx(self):
        screens = self.get_stable_screens()
        primary = QApplication.primaryScreen()
        if not primary:
            return 0
        primary_name = primary.name()
        for i, s in enumerate(screens):
            if s.name() == primary_name:
                return i
        return 0

    def refresh_monitors(self):"""

code = code.replace("    def refresh_monitors(self):", new_methods)

# Update refresh_monitors to use get_stable_screens
code = code.replace("screens = QApplication.screens()", "screens = self.get_stable_screens()")

# Update __init__ to fallback to get_primary_monitor_idx
init_pattern = r"""        except: pass
        self\.active_monitor = active

        self\.refresh_monitors\(\)"""

init_replacement = """        except: pass
        
        self.active_monitor = active
        self.refresh_monitors()
        
        # If active monitor wasn't in state (e.g. active is 0 but we want OS primary)
        if not os.path.exists(state_file):
            self.active_monitor = self.get_primary_monitor_idx()"""

code = re.sub(init_pattern, init_replacement, code)

with open("src/clUIScaler.py", "w") as f:
    f.write(code)

print("done")
