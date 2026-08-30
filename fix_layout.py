import re

with open("src/clUI.py", "r") as f:
    code = f.read()

# 1. Update refresh_layout to use self.width() and self.height()
refresh_pattern = r"""    def refresh_layout\(self\):
        screens = UIScaler.get\(\).get_stable_screens\(\)
        idx = getattr\(self, 'current_monitor_idx', 0\)
        if idx >= len\(screens\):
            idx = 0
        target_screen = screens\[idx\]
        geom = target_screen.geometry\(\)


        UIScaler.get\(\).set_active_monitor\(idx\)
        s = UIScaler.get\(\).scale

        # Row 1
        self.btn_media.setGeometry\(s\(30\), geom.height\(\) - s\(65\), s\(120\), s\(35\)\)
        self.btn_lights.setGeometry\(s\(160\), geom.height\(\) - s\(65\), s\(120\), s\(35\)\)
        self.btn_reminders.setGeometry\(s\(290\), geom.height\(\) - s\(65\), s\(120\), s\(35\)\)
        self.btn_todos.setGeometry\(s\(420\), geom.height\(\) - s\(65\), s\(120\), s\(35\)\)

        # Row 2
        self.btn_settings.setGeometry\(s\(30\), geom.height\(\) - s\(110\), s\(120\), s\(35\)\)
        self.btn_updates.setGeometry\(s\(160\), geom.height\(\) - s\(110\), s\(120\), s\(35\)\)
        self.btn_debug.setGeometry\(s\(290\), geom.height\(\) - s\(110\), s\(120\), s\(35\)\)

        # Calendar button
        self.btn_calendar.setGeometry\(geom.width\(\) - s\(30\), int\(geom.height\(\) / 2\) - s\(40\), s\(30\), s\(80\)\)

        drawer_width = s\(400\) if geom.width\(\) >= 1920 else s\(350\)
        if hasattr\(self, 'calendar_drawer'\):
            self.calendar_drawer.setGeometry\(geom.width\(\), 0, drawer_width, geom.height\(\)\)

        if hasattr\(self, 'drawer'\):
            self.drawer.setGeometry\(geom.right\(\) - drawer_width - 20, 0, drawer_width, geom.height\(\)\)

        rw_w = s\(300\)
        rw_h = s\(150\)
        if hasattr\(self, 'reminder_widget'\):
            self.reminder_widget.setGeometry\(geom.width\(\) - rw_w - 20, geom.height\(\) - rw_h - 20, rw_w, rw_h\)"""

refresh_replacement = """    def refresh_layout(self):
        screens = UIScaler.get().get_stable_screens()
        idx = getattr(self, 'current_monitor_idx', 0)
        if idx >= len(screens):
            idx = 0
            
        UIScaler.get().set_active_monitor(idx)
        s = UIScaler.get().scale

        # Use actual window dimensions instead of target screen geometry to prevent Wayland scaling/cropping bugs
        win_w = self.width()
        win_h = self.height()

        # Row 1
        self.btn_media.setGeometry(s(30), win_h - s(65), s(120), s(35))
        self.btn_lights.setGeometry(s(160), win_h - s(65), s(120), s(35))
        self.btn_reminders.setGeometry(s(290), win_h - s(65), s(120), s(35))
        self.btn_todos.setGeometry(s(420), win_h - s(65), s(120), s(35))

        # Row 2
        self.btn_settings.setGeometry(s(30), win_h - s(110), s(120), s(35))
        self.btn_updates.setGeometry(s(160), win_h - s(110), s(120), s(35))
        self.btn_debug.setGeometry(s(290), win_h - s(110), s(120), s(35))

        # Calendar button
        self.btn_calendar.setGeometry(win_w - s(30), int(win_h / 2) - s(40), s(30), s(80))

        drawer_width = s(400) if win_w >= 1920 else s(350)
        if hasattr(self, 'calendar_drawer'):
            self.calendar_drawer.setGeometry(win_w, 0, drawer_width, win_h)

        rw_w = s(300)
        rw_h = s(150)
        if hasattr(self, 'reminder_widget'):
            self.reminder_widget.setGeometry(win_w - rw_w - 20, win_h - rw_h - 20, rw_w, rw_h)"""

if re.search(refresh_pattern, code):
    code = re.sub(refresh_pattern, refresh_replacement, code)
    print("Replaced refresh_layout")
else:
    print("Could not find refresh_layout")

# 2. Update text_input to use win_w, win_h in refresh_layout (around line 830)
text_input_pattern = r"""        box_width = s\(600\)
        box_x = geom.width\(\) // 2 - \(box_width // 2\)
        box_y = geom.height\(\) - s\(80\)
        self.text_input.setGeometry\(box_x, box_y, box_width, s\(40\)\)"""

text_input_replacement = """        box_width = s(600)
        box_x = win_w // 2 - (box_width // 2)
        box_y = win_h - s(80)
        self.text_input.setGeometry(box_x, box_y, box_width, s(40))"""

if re.search(text_input_pattern, code):
    code = re.sub(text_input_pattern, text_input_replacement, code)
    print("Replaced text_input layout")
else:
    print("Could not find text_input layout")

# 3. Add refresh_layout to resizeEvent
resize_pattern = r"""    def resizeEvent\(self, event\):
        super\(\).resizeEvent\(event\)
        if hasattr\(self, 'visualizer'\):
            self.visualizer.resize\(self.size\(\)\)"""

resize_replacement = """    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'visualizer'):
            self.visualizer.resize(self.size())
        # Re-layout elements whenever the window size changes (e.g. Wayland forces a resize)
        self.refresh_layout()"""

if re.search(resize_pattern, code):
    code = re.sub(resize_pattern, resize_replacement, code)
    print("Replaced resizeEvent")
else:
    print("Could not find resizeEvent")

with open("src/clUI.py", "w") as f:
    f.write(code)

