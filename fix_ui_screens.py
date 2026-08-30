import re

with open("src/clUI.py", "r") as f:
    code = f.read()

# Replace all simple occurrences
code = code.replace("screens = QApplication.screens()", "screens = UIScaler.get().get_stable_screens()")

# Replace the fallback logic in set_ui_mode
old_fallback = """            else:
                if not hasattr(self, 'current_monitor_idx'):
                    primary = QApplication.primaryScreen()
                    self.current_monitor_idx = screens.index(primary) if primary in screens else 0
                if self.current_monitor_idx >= len(screens):
                    primary = QApplication.primaryScreen()
                    self.current_monitor_idx = screens.index(primary) if primary in screens else 0"""

new_fallback = """            else:
                if not hasattr(self, 'current_monitor_idx'):
                    self.current_monitor_idx = UIScaler.get().get_primary_monitor_idx()
                if self.current_monitor_idx >= len(screens):
                    self.current_monitor_idx = UIScaler.get().get_primary_monitor_idx()"""

code = code.replace(old_fallback, new_fallback)

# Replace the fallback logic in Overlay window (line 1496)
old_overlay = "target_screen = screens[idx] if idx < len(screens) else QApplication.primaryScreen()"
new_overlay = "target_screen = screens[idx] if idx < len(screens) else screens[UIScaler.get().get_primary_monitor_idx()]"
code = code.replace(old_overlay, new_overlay)

with open("src/clUI.py", "w") as f:
    f.write(code)

print("done")
