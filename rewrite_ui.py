import re
import sys

with open("src/clUI.py", "r") as f:
    code = f.read()

# 1. Create refresh_layout at the end of __init__
refresh_layout_code = """
    def refresh_layout(self):
        from PyQt6.QtWidgets import QApplication
        from clUIScaler import UIScaler
        
        screens = QApplication.screens()
        idx = getattr(self, 'current_monitor_idx', 0)
        if idx >= len(screens):
            idx = 0
        target_screen = screens[idx]
        geom = target_screen.geometry()
        
        UIScaler.get().set_active_monitor(idx)
        s = UIScaler.get().scale

        # Row 1
        if hasattr(self, 'btn_media'): self.btn_media.setGeometry(s(30), geom.height() - s(65), s(120), s(35))
        if hasattr(self, 'btn_lights'): self.btn_lights.setGeometry(s(160), geom.height() - s(65), s(120), s(35))
        if hasattr(self, 'btn_reminders'): self.btn_reminders.setGeometry(s(290), geom.height() - s(65), s(120), s(35))
        if hasattr(self, 'btn_todos'): self.btn_todos.setGeometry(s(420), geom.height() - s(65), s(120), s(35))

        # Row 2
        if hasattr(self, 'btn_settings'): self.btn_settings.setGeometry(s(30), geom.height() - s(110), s(120), s(35))
        if hasattr(self, 'btn_updates'): self.btn_updates.setGeometry(s(160), geom.height() - s(110), s(120), s(35))
        if hasattr(self, 'btn_debug'): self.btn_debug.setGeometry(s(290), geom.height() - s(110), s(120), s(35))

        # Calendar button
        if hasattr(self, 'btn_calendar'): self.btn_calendar.setGeometry(geom.width() - s(30), int(geom.height() / 2) - s(40), s(30), s(80))

        drawer_width = s(400) if geom.width() >= 1920 else s(350)
        if hasattr(self, 'calendar_drawer'):
            self.calendar_drawer.setGeometry(geom.width(), 0, drawer_width, geom.height())
        
        if hasattr(self, 'drawer'):
            self.drawer.setGeometry(geom.right() - drawer_width - 20, 0, drawer_width, geom.height())

        rw_w = s(300)
        rw_h = s(150)
        if hasattr(self, 'reminder_widget'):
            self.reminder_widget.setGeometry(geom.width() - rw_w - 20, geom.height() - rw_h - 20, rw_w, rw_h)

        # Text Input
        box_width = s(600)
        box_x = geom.width() // 2 - (box_width // 2)
        box_y = geom.height() - s(80)
        if hasattr(self, 'text_input'): self.text_input.setGeometry(box_x, box_y, box_width, s(40))
"""

code = code.replace("    def _check_occlusion(self):", refresh_layout_code + "\n    def _check_occlusion(self):")

# 2. Modify set_fullscreen swap block
set_fullscreen_pattern = r'if is_monitor_swap:\s+self\.current_monitor_idx = .*?return\s+else:'
replacement = """if is_monitor_swap:
                self.current_monitor_idx = (getattr(self, 'current_monitor_idx', 0) + 1) % len(screens)
                self.save_ui_state()
                
                # Dynamic update
                from clUIScaler import UIScaler
                UIScaler.get().set_active_monitor(self.current_monitor_idx)
                
                target_screen = screens[self.current_monitor_idx]
                geom = target_screen.geometry()
                
                self.hide()
                QApplication.processEvents()
                
                if self.windowHandle():
                    self.windowHandle().setScreen(target_screen)
                
                self.setGeometry(geom)
                self.showNormal()
                QApplication.processEvents()
                
                self.refresh_layout()
                self.showFullScreen()
                self.activateWindow()
                self.setFocus()
                return
            else:"""

code = re.sub(set_fullscreen_pattern, replacement, code, flags=re.DOTALL)

# 3. In the remainder of set_fullscreen, REPLACE the manual setGeometry block with a call to refresh_layout
manual_geom_pattern = r'# Row 1.*?# Text Input.*?self\.text_input\.setGeometry[^\n]*\n'
code = re.sub(manual_geom_pattern, 'self.refresh_layout()\n', code, flags=re.DOTALL)

with open("src/clUI.py", "w") as f:
    f.write(code)

print("done")
