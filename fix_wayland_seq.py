import re

with open("src/clUI.py", "r") as f:
    code = f.read()

pattern = r"""            logging.info\("\[DEBUG UI\] Calling showNormal\(\) to force Wayland mapping..."\)
            self.showNormal\(\)
            QApplication.processEvents\(\)
            logging.info\(f"\[DEBUG UI\] After showNormal\(\) -> isVisible: \{self.isVisible\(\)\}, isActiveWindow: \{self.isActiveWindow\(\)\}"\)

            self.is_fullscreen = True

            import time
            self._occlusion_disabled_until = time.time\(\) \+ 1.5

            target_screen = screens\[self.current_monitor_idx\]
            if self.windowHandle\(\):
                self.windowHandle\(\).setScreen\(target_screen\)

            geom = target_screen.geometry\(\)

            self.setMinimumSize\(0, 0\)
            self.setMaximumSize\(16777215, 16777215\)

            self.setGeometry\(geom\)"""

replacement = """            self.is_fullscreen = True

            import time
            self._occlusion_disabled_until = time.time() + 1.5

            # Force native window creation so windowHandle() becomes available without mapping the window yet
            self.winId()
            
            target_screen = screens[self.current_monitor_idx]
            if self.windowHandle():
                self.windowHandle().setScreen(target_screen)

            geom = target_screen.geometry()

            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)

            self.setGeometry(geom)

            logging.info("[DEBUG UI] Calling showNormal() to force Wayland mapping...")
            self.showNormal()
            QApplication.processEvents()
            logging.info(f"[DEBUG UI] After showNormal() -> isVisible: {self.isVisible()}, isActiveWindow: {self.isActiveWindow()}")"""

if re.search(pattern, code):
    code = re.sub(pattern, replacement, code)
    print("Match found and replaced.")
else:
    print("Match not found.")

with open("src/clUI.py", "w") as f:
    f.write(code)

