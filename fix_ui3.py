import re

with open("src/clUI.py", "r") as f:
    text = f.read()

# Fix 1: save_ui_state - include is_fullscreen
target1 = """                "active_widgets": active_widgets_data,
                "current_monitor_idx": getattr(self, 'current_monitor_idx', 0)
            }"""
repl1 = """                "active_widgets": active_widgets_data,
                "current_monitor_idx": getattr(self, 'current_monitor_idx', 0),
                "is_fullscreen": getattr(self, 'is_fullscreen', False)
            }"""
text = text.replace(target1, repl1)

# Fix 2: load_ui_state - read is_fullscreen and apply it
target2 = """                else:
                    self.reminder_widget.hide()"""
repl2 = target2 + """
            
            if state.get("is_fullscreen", False):
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(100, lambda: self.set_ui_mode("set_fullscreen"))
"""
text = text.replace(target2, repl2)

# Fix 3: replace primary_screen hardcoding
target3 = """        primary_screen = QApplication.primaryScreen()
        screen_geom = primary_screen.availableGeometry()"""
repl3 = """        screens = QApplication.screens()
        idx = getattr(self, 'current_monitor_idx', 0)
        # Use UIScaler's active monitor if current_monitor_idx hasn't been set
        if not hasattr(self, 'current_monitor_idx'):
            try:
                from clUIScaler import UIScaler
                idx = UIScaler.get().active_monitor
                self.current_monitor_idx = idx
            except: pass
        target_screen = screens[idx] if idx < len(screens) else QApplication.primaryScreen()
        screen_geom = target_screen.availableGeometry()"""
text = text.replace(target3, repl3)

target4 = """            primary_screen = QApplication.primaryScreen()
            screen_geom = primary_screen.availableGeometry()"""
repl4 = """            screens = QApplication.screens()
            idx = getattr(self, 'current_monitor_idx', 0)
            target_screen = screens[idx] if idx < len(screens) else QApplication.primaryScreen()
            screen_geom = target_screen.availableGeometry()"""
text = text.replace(target4, repl4)

with open("src/clUI.py", "w") as f:
    f.write(text)
