import re

with open("src/clUI.py", "r") as f:
    code = f.read()

# The current code has the flags setup inside the `else:` block.
# We want to dedent it so it runs for both `if is_monitor_swap` and `else:`

pattern = r"""            else:
                if not hasattr\(self, 'current_monitor_idx'\):
                    primary = QApplication.primaryScreen\(\)
                    self.current_monitor_idx = screens.index\(primary\) if primary in screens else 0
                if self.current_monitor_idx >= len\(screens\):
                    primary = QApplication.primaryScreen\(\)
                    self.current_monitor_idx = screens.index\(primary\) if primary in screens else 0

                self.hide\(\)
                QApplication.processEvents\(\)
                flags = Qt.WindowType.Window \| Qt.WindowType.FramelessWindowHint
                if sys.platform == "win32":
                    flags \|= Qt.WindowType.WindowStaysOnTopHint
                self.setWindowFlags\(flags\)
                logging.info\("\[DEBUG UI\] Window flags set."\)

                self.setAttribute\(Qt.WidgetAttribute.WA_TranslucentBackground, True\)
                if sys.platform != "win32":
                    self.setAttribute\(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False\)
                self.clearMask\(\)
                self.setAttribute\(Qt.WidgetAttribute.WA_ShowWithoutActivating, False\)

                logging.info\("\[DEBUG UI\] Calling showNormal\(\) to force Wayland mapping..."\)
                self.showNormal\(\)
                QApplication.processEvents\(\)
                logging.info\(f"\[DEBUG UI\] After showNormal\(\) -> isVisible: \{self.isVisible\(\)\}, isActiveWindow: \{self.isActiveWindow\(\)\}"\)"""

replacement = """            else:
                if not hasattr(self, 'current_monitor_idx'):
                    primary = QApplication.primaryScreen()
                    self.current_monitor_idx = screens.index(primary) if primary in screens else 0
                if self.current_monitor_idx >= len(screens):
                    primary = QApplication.primaryScreen()
                    self.current_monitor_idx = screens.index(primary) if primary in screens else 0

                self.hide()
                QApplication.processEvents()
            
            flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            if sys.platform == "win32":
                flags |= Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            logging.info("[DEBUG UI] Window flags set.")

            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            if sys.platform != "win32":
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.clearMask()
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

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

