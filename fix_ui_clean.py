import re

with open("src/clUI.py", "r") as f:
    code = f.read()

# 1. Remove local imports of UIScaler
code = code.replace("from clUIScaler import UIScaler", "")

# We removed all of them, but we need the global one back at line 25!
code = code.replace("from clUIScalerInjector import inject_scaler", "from clUIScalerInjector import inject_scaler\nfrom clUIScaler import UIScaler")

# 2. Find and replace the manual setGeometry blocks
# For the main buttons:
old_buttons = """            # Row 1
            s = UIScaler.get().scale
            self.btn_media.setGeometry(s(30), geom.height() - s(65), s(120), s(35))
            self.btn_lights.setGeometry(s(145), geom.height() - s(65), s(120), s(35))

            self.btn_reminders.setGeometry(s(260), geom.height() - s(65), s(120), s(35))
            self.btn_todos.setGeometry(s(375), geom.height() - s(65), s(120), s(35))

            self.btn_settings.setGeometry(s(30), geom.height() - s(115), s(120), s(35))
            self.btn_updates.setGeometry(s(145), geom.height() - s(115), s(120), s(35))

            self.btn_debug.setGeometry(s(260), geom.height() - s(115), s(120), s(35))

            # Position calendar toggle on right edge
            self.btn_calendar.setGeometry(geom.width() - s(30), int(geom.height() / 2) - s(40), s(30), s(80))
            self.btn_calendar.setText("❮")

            self.calendar_drawer.setGeometry(geom.width(), 0, 380, geom.height())"""

new_buttons = """            self.refresh_layout()
            self.btn_calendar.setText("❮")"""

if old_buttons in code:
    code = code.replace(old_buttons, new_buttons)
else:
    print("Warning: old_buttons not found")

# For text input
old_text = """            box_width = min(650, geom.width() - 100)
            box_x = int((geom.width() - box_width) / 2)
            box_y = geom.height() - 62
            s = UIScaler.get().scale
            self.text_input.setGeometry(box_x, box_y, box_width, s(30))"""

new_text = """            pass"""

if old_text in code:
    code = code.replace(old_text, new_text)
else:
    print("Warning: old_text not found")

with open("src/clUI.py", "w") as f:
    f.write(code)

print("done")
