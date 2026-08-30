import re

with open("src/clUI.py", "r") as f:
    text = f.read()

# Add UIScaler import if not present
if "from src.clUIScaler import UIScaler" not in text:
    text = text.replace("from src.clUIScalerInjector import inject_scaler", "from src.clUIScalerInjector import inject_scaler\nfrom src.clUIScaler import UIScaler")

# JarvisUI __init__ base size
text = text.replace("width, height = 200, 400", "s = UIScaler.get().scale\n        width, height = s(200), s(400)")

# OverlayWindow __init__ base size
text = text.replace("self.setFixedSize(200, 400)", "s = UIScaler.get().scale\n        self.setFixedSize(s(200), s(400))")

# Fullscreen button row 1
text = text.replace("self.btn_media.setGeometry(30, geom.height() - 65, 120, 35)", "s = UIScaler.get().scale\n            self.btn_media.setGeometry(s(30), geom.height() - s(65), s(120), s(35))")
text = text.replace("self.btn_lights.setGeometry(145, geom.height() - 65, 120, 35)", "self.btn_lights.setGeometry(s(145), geom.height() - s(65), s(120), s(35))")
text = text.replace("self.btn_reminders.setGeometry(260, geom.height() - 65, 120, 35)", "self.btn_reminders.setGeometry(s(260), geom.height() - s(65), s(120), s(35))")
text = text.replace("self.btn_todos.setGeometry(375, geom.height() - 65, 120, 35)", "self.btn_todos.setGeometry(s(375), geom.height() - s(65), s(120), s(35))")

# Fullscreen button row 2
text = text.replace("self.btn_settings.setGeometry(30, geom.height() - 115, 120, 35)", "self.btn_settings.setGeometry(s(30), geom.height() - s(115), s(120), s(35))")
text = text.replace("self.btn_updates.setGeometry(145, geom.height() - 115, 120, 35)", "self.btn_updates.setGeometry(s(145), geom.height() - s(115), s(120), s(35))")
text = text.replace("self.btn_debug.setGeometry(260, geom.height() - 115, 120, 35)", "self.btn_debug.setGeometry(s(260), geom.height() - s(115), s(120), s(35))")

# Fullscreen calendar button
text = text.replace("self.btn_calendar.setGeometry(geom.width() - 30, int(geom.height() / 2) - 40, 30, 80)", "self.btn_calendar.setGeometry(geom.width() - s(30), int(geom.height() / 2) - s(40), s(30), s(80))")

# Calendar toggle animation
text = text.replace("self.calendar_drawer.setGeometry(geom.width(), 0, drawer_width, geom.height())", "self.calendar_drawer.setGeometry(geom.width(), 0, drawer_width, geom.height())") # drawer_width is already scaled inside the method!
# Wait, drawer_width is 380. Where is it defined?
# _toggle_calendar:
# drawer_width = 380 -> s(380)
text = text.replace("drawer_width = 380", "s = UIScaler.get().scale\n        drawer_width = s(380)")
text = text.replace("30, 80))", "s(30), s(80)))")
text = text.replace("geom.width() - drawer_width - 30", "geom.width() - drawer_width - s(30)")

# text_input geometry
text = text.replace("self.text_input.setGeometry(box_x, box_y, box_width, 30)", "s = UIScaler.get().scale\n            self.text_input.setGeometry(box_x, box_y, box_width, s(30))")
text = text.replace("box_width = 600", "s = UIScaler.get().scale\n            box_width = s(600)")
text = text.replace("geom.height() - 200", "geom.height() - s(200)")
text = text.replace("geom.height() - 50", "geom.height() - s(50)")

with open("src/clUI.py", "w") as f:
    f.write(text)
