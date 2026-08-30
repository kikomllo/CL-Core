import re

with open("src/clUI.py", "r") as f:
    code = f.read()

top_block_pattern = r"""            if is_monitor_swap:
                self.current_monitor_idx = \(getattr\(self, 'current_monitor_idx', 0\) \+ 1\) % len\(screens\)
                self.save_ui_state\(\)

                # Dynamic update

                UIScaler.get\(\).set_active_monitor\(self.current_monitor_idx\)

                target_screen = screens\[self.current_monitor_idx\]
                geom = target_screen.geometry\(\)

                self.hide\(\)
                QApplication.processEvents\(\)

                if self.windowHandle\(\):
                    self.windowHandle\(\).setScreen\(target_screen\)

                self.setGeometry\(geom\)
                self.showNormal\(\)
                QApplication.processEvents\(\)

                self.refresh_layout\(\)
                self.showFullScreen\(\)
                self.activateWindow\(\)
                self.setFocus\(\)
                return"""

replacement = """            if is_monitor_swap:
                self.current_monitor_idx = (getattr(self, 'current_monitor_idx', 0) + 1) % len(screens)
                self.save_ui_state()

                # Dynamic update
                UIScaler.get().set_active_monitor(self.current_monitor_idx)

                target_screen = screens[self.current_monitor_idx]
                geom = target_screen.geometry()

                self.hide()
                QApplication.processEvents()

                if self.windowHandle():
                    self.windowHandle().setScreen(target_screen)

                self.setGeometry(geom)
                self.showNormal()
                QApplication.processEvents()"""

if re.search(top_block_pattern, code):
    code = re.sub(top_block_pattern, replacement, code)
    print("Match found and replaced.")
else:
    print("Match not found.")

with open("src/clUI.py", "w") as f:
    f.write(code)

print("done")
