import json
import os

with open("src/clUIScaler.py", "r") as f:
    text = f.read()

target = """        self.monitors = []
        self.base_res = (1920, 1080)
        self.active_monitor = 0"""

replacement = """        self.monitors = []
        self.base_res = (1920, 1080)
        
        # Load active monitor from state
        state_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "ui_state.json"))
        active = 0
        try:
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    state = json.load(f)
                    active = state.get("current_monitor_idx", 0)
        except: pass
        self.active_monitor = active
"""
text = text.replace(target, replacement)

# Add import os if missing
if "import os" not in text:
    text = "import os\n" + text
if "import json" not in text:
    text = "import json\n" + text

with open("src/clUIScaler.py", "w") as f:
    f.write(text)
