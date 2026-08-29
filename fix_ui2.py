with open("src/clUI_new.py", "r") as f:
    lines = f.readlines()

new_set_ui_mode = """    def set_ui_mode(self, mode):
        if mode == "save_state":
            self.save_ui_state()
            return
            
        if mode == "show_logs":
            self._toggle_debug()
            return
            
        if mode == "set_fullscreen":
            if getattr(self, 'is_fullscreen', False):
                screens = QApplication.screens()
                self.current_monitor_idx = (getattr(self, 'current_monitor_idx', 0) + 1) % len(screens)
                target_screen = screens[self.current_monitor_idx]
                if self.fullscreen_ui.windowHandle():
                    self.fullscreen_ui.windowHandle().setScreen(target_screen)
                self.fullscreen_ui.hide()
                self.fullscreen_ui.setGeometry(target_screen.geometry())
                self.fullscreen_ui.showFullScreen()
                return

            self.is_fullscreen = True
            
            screens = QApplication.screens()
            if getattr(self, 'current_monitor_idx', 0) >= len(screens):
                self.current_monitor_idx = 0
            target_screen = screens[self.current_monitor_idx]
            
            if self.fullscreen_ui.windowHandle():
                self.fullscreen_ui.windowHandle().setScreen(target_screen)
            
            self.fullscreen_ui.setGeometry(target_screen.geometry())
            self.fullscreen_ui.showFullScreen()

            def force_focus():
                self.fullscreen_ui.setWindowState((self.fullscreen_ui.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
                self.fullscreen_ui.raise_()
                self.fullscreen_ui.activateWindow() 
                self.fullscreen_ui.setFocus()
            
            QTimer.singleShot(150, force_focus)
            
            self.visualizer.setParent(self.fullscreen_ui)
            self.visualizer.setGeometry(0, 0, self.fullscreen_ui.width(), self.fullscreen_ui.height())
            self.visualizer.show()
            
            self.overlay_ui.hide()
            
            self.fullscreen_ui.btn_media.show()
            self.fullscreen_ui.btn_lights.show()
            self.fullscreen_ui.btn_reminders.show()
            self.fullscreen_ui.btn_todos.show()
            self.fullscreen_ui.btn_settings.show()
            self.fullscreen_ui.btn_updates.show()
            self.fullscreen_ui.btn_calendar.show()
            self.fullscreen_ui.calendar_drawer.show()
            self.fullscreen_ui.reminder_widget.show()
            
            if getattr(self.fullscreen_ui, 'text_input', None) is not None:
                self.fullscreen_ui.text_input.show()
                self.fullscreen_ui.text_input.raise_()

            for wid, w in self.active_widgets.items():
                if w.isVisible():
                    if w.parent() is None:
                        local_pos = self.fullscreen_ui.mapFromGlobal(w.pos())
                        w.setParent(self.fullscreen_ui)
                        w.move(local_pos)
                    else:
                        w.setParent(self.fullscreen_ui)
                    w.show()
                    w.raise_()

        elif mode == "set_overlay":
            self.is_fullscreen = False
            
            if getattr(self.fullscreen_ui, 'text_input', None) is not None:
                self.fullscreen_ui.text_input.hide()
                
            self.state = "IDLE"
            self.visualizer.set_state("IDLE", False)
            
            self.fullscreen_ui.btn_media.hide()
            self.fullscreen_ui.btn_lights.hide()
            self.fullscreen_ui.btn_reminders.hide()
            self.fullscreen_ui.btn_todos.hide()
            self.fullscreen_ui.btn_settings.hide()
            self.fullscreen_ui.btn_updates.hide()
            self.fullscreen_ui.btn_calendar.hide()
            self.fullscreen_ui.calendar_drawer.hide()
            self.fullscreen_ui.reminder_widget.hide()
            
            for wid, w in self.active_widgets.items():
                if w.isVisible():
                    global_pos = self.fullscreen_ui.mapToGlobal(w.pos())
                    w.setParent(None)
                    w.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
                    w.move(global_pos)
                    w.show()
                elif wid == "options_list":
                    if hasattr(w, "title_bar"):
                        w.title_bar.hide()
                    w.adjustSize()
                    w.setParent(None)
                    w.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
                    cx = self.fullscreen_ui.x() + (self.fullscreen_ui.width() - w.width()) // 2
                    w.move(int(cx), self.fullscreen_ui.y() + 10)
                    w.show()
                    
            self.fullscreen_ui.hide()
            
            self.overlay_ui.position_in_corner(self.current_monitor_idx)
            self.overlay_ui.showNormal()
            
            self.visualizer.setParent(self.overlay_ui)
            self.visualizer.setGeometry(0, 0, self.overlay_ui.width(), self.overlay_ui.height())
            self.visualizer.show()
"""

out_lines = []
for line in lines:
    if line.startswith("    def save_ui_state(self):"):
        out_lines.append(new_set_ui_mode + "\n")
    out_lines.append(line)

with open("src/clUI_new.py", "w") as f:
    f.writelines(out_lines)
