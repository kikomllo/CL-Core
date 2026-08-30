with open("src/clUIScaler.py", "r") as f:
    text = f.read()

patch = """    def __init__(self):
        self.monitors = []
        self.base_res = (1920, 1080)
        self.active_monitor = 0
        self.refresh_monitors()
        
    def set_active_monitor(self, idx):
        self.active_monitor = idx"""
        
text = text.replace("    def __init__(self):\n        self.monitors = []\n        self.base_res = (1920, 1080)\n        self.refresh_monitors()", patch)

patch2 = """    def get_scale(self, monitor_idx=None):
        if monitor_idx is None:
            monitor_idx = self.active_monitor
        if monitor_idx < 0 or monitor_idx >= len(self.monitors):"""
        
text = text.replace("    def get_scale(self, monitor_idx=0):\n        if monitor_idx < 0 or monitor_idx >= len(self.monitors):", patch2)

with open("src/clUIScaler.py", "w") as f:
    f.write(text.replace("monitor_idx=0", "monitor_idx=None"))
