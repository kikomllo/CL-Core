with open("src/clUI.py", "r") as f:
    text = f.read()

target = """        if mode == "set_fullscreen":
            if is_monitor_swap:
                self.current_monitor_idx = (getattr(self, 'current_monitor_idx', 0) + 1) % len(screens)"""

replacement = """        if mode == "set_fullscreen":
            if is_monitor_swap:
                self.current_monitor_idx = (getattr(self, 'current_monitor_idx', 0) + 1) % len(screens)
                self.save_ui_state()
                import json
                import paho.mqtt.publish as publish
                try:
                    publish.single("jarvis/sys/manager", json.dumps({"action": "restart_module", "target": "ui"}), hostname="localhost")
                except Exception: pass
                return"""

text = text.replace(target, replacement)
with open("src/clUI.py", "w") as f:
    f.write(text)
