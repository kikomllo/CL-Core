import re

with open("src/clUI.py", "r") as f:
    code = f.read()

# Completely rewrite the is_monitor_swap logic to avoid double calls
# We will find from "is_monitor_swap = getattr(self, 'is_fullscreen', False)"
# to "flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint"
# and replace it with a clean unified flow.

pattern = r"            is_monitor_swap = getattr\(self, 'is_fullscreen', False\).*?flags = Qt\.WindowType\.Window \| Qt\.WindowType\.FramelessWindowHint"

replacement = """            is_monitor_swap = getattr(self, 'is_fullscreen', False)
            old_geom = None

            if is_monitor_swap:
                self.current_monitor_idx = (getattr(self, 'current_monitor_idx', 0) + 1) % len(screens)
                self.save_ui_state()
                UIScaler.get().set_active_monitor(self.current_monitor_idx)

                # Save widget visibility before hiding
                widget_visibility = {wid: w.isVisible() for wid, w in self.active_widgets.items()}
                
                # Wayland requires hide() before setScreen() to correctly map
                self.hide()
                QApplication.processEvents()
            else:
                if not hasattr(self, 'current_monitor_idx'):
                    primary = QApplication.primaryScreen()
                    self.current_monitor_idx = screens.index(primary) if primary in screens else 0
                if self.current_monitor_idx >= len(screens):
                    primary = QApplication.primaryScreen()
                    self.current_monitor_idx = screens.index(primary) if primary in screens else 0
                
                self.hide()
                QApplication.processEvents()

            flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint"""

if re.search(pattern, code, re.DOTALL):
    code = re.sub(pattern, replacement, code, flags=re.DOTALL)
    print("Replaced monitor swap block.")
else:
    print("Could not find monitor swap block!")

with open("src/clUI.py", "w") as f:
    f.write(code)
