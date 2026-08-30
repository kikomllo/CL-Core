import re

with open("src/clUIScaler.py", "r") as f:
    code = f.read()

pattern = r"""        # Load active monitor from state
        state_file = os\.path\.abspath\(os\.path\.join\(os\.path\.dirname\(__file__\), "..", "data", "ui_state.json"\)\)
        active = 0
        try:
            if os\.path\.exists\(state_file\):
                with open\(state_file, "r"\) as f:
                    state = json\.load\(f\)
                    active = state\.get\("current_monitor_idx", 0\)
        except: pass

        self\.active_monitor = active
        self\.refresh_monitors\(\)

        # If active monitor wasn't in state \(e\.g\. active is 0 but we want OS primary\)
        if not os\.path\.exists\(state_file\):
            self\.active_monitor = self\.get_primary_monitor_idx\(\)"""

replacement = """        self.active_monitor = self.get_primary_monitor_idx()
        self.refresh_monitors()"""

if re.search(pattern, code):
    code = re.sub(pattern, replacement, code)
    print("Fixed clUIScaler boot monitor")
else:
    print("Could not find pattern for scaler boot")

with open("src/clUIScaler.py", "w") as f:
    f.write(code)

