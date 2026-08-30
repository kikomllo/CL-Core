import json
import os
import re
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect, QSize

class UIScaler:
    _instance = None
    
    def __init__(self):
        self.monitors = []
        self.base_res = (1920, 1080)
        
        self.active_monitor = self.get_primary_monitor_idx()
        self.refresh_monitors()
        
    def set_active_monitor(self, idx):
        self.active_monitor = idx
        
    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = UIScaler()
        return cls._instance
        
    def get_stable_screens(self):
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

    def refresh_monitors(self):
        self.monitors = []
        screens = self.get_stable_screens()
        for i, screen in enumerate(screens):
            geom = screen.geometry()
            # Calculate scale based on height/width relative to 1920x1080
            # Use the minimum of both to prevent bleeding off-screen while maintaining proportions
            scale_w = geom.width() / self.base_res[0]
            scale_h = geom.height() / self.base_res[1]
            scale = min(scale_w, scale_h)
            
            # Allow downscaling so the UI appears proportionally the same size
            # on smaller resolution monitors.
            pass
            
            self.monitors.append({
                "index": i,
                "width": geom.width(),
                "height": geom.height(),
                "scale": scale
            })
            
    def get_scale(self, monitor_idx=None):
        if monitor_idx is None:
            monitor_idx = self.active_monitor
        if monitor_idx < 0 or monitor_idx >= len(self.monitors):
            return 1.0
        return self.monitors[monitor_idx]["scale"]
        
    def scale(self, val, monitor_idx=None):
        scale = self.get_scale(monitor_idx)
        return int(round(val * scale))
        
    def scale_float(self, val, monitor_idx=None):
        scale = self.get_scale(monitor_idx)
        return val * scale
        
    def scale_rect(self, x, y, w, h, monitor_idx=None):
        scale = self.get_scale(monitor_idx)
        return QRect(
            int(round(x * scale)),
            int(round(y * scale)),
            int(round(w * scale)),
            int(round(h * scale))
        )
        
    def scale_size(self, w, h, monitor_idx=None):
        scale = self.get_scale(monitor_idx)
        return QSize(
            int(round(w * scale)),
            int(round(h * scale))
        )
        
    def scale_css(self, css, monitor_idx=None):
        scale = self.get_scale(monitor_idx)
        if scale == 1.0:
            return css
            
        def repl(match):
            val = float(match.group(1))
            unit = match.group(2)
            
            # Prevent 1px borders from scaling down to 0px, which makes them disappear
            scaled_val = int(round(val * scale))
            if scaled_val == 0 and val > 0:
                scaled_val = 1
                
            return f"{scaled_val}{unit}"
            
        # Matches any float or int followed by px or pt (ignoring spaces)
        return re.sub(r'(\d+(?:\.\d+)?)\s*(px|pt)', repl, css)
