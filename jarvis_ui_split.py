import re

with open("src/clUI_original.py", "r") as f:
    original = f.read()

pre_jarvis, jarvis_class_body = original.split("class JarvisUI(QWidget):", 1)

def extract_section(body, start_str, end_str=None):
    start_idx = body.find(start_str)
    # Find the preceding newline
    start_idx = body.rfind("\n", 0, start_idx) + 1
    if end_str:
        end_idx = body.find(end_str, start_idx)
        end_idx = body.rfind("\n", 0, end_idx) + 1
        return body[start_idx:end_idx]
    return body[start_idx:]

init_body = extract_section(jarvis_class_body, "def __init__(self", "def evaluate_state(self):")
resize_body = extract_section(jarvis_class_body, "def resizeEvent(self, event):", "def set_volume(self, vol):")
paint_body = extract_section(jarvis_class_body, "def paintEvent(self, event):", "def closeEvent(self, event):")
honey_body = extract_section(jarvis_class_body, "def _generate_honeycomb(self, w, h):", "def paintEvent(self, event):")

init_body = init_body.replace("def __init__(self, mode=\"overlay\"):", "def __init__(self, manager):\n        self.is_fullscreen = True")
init_body = init_body.replace("super().__init__()", "super().__init__()\n        self.manager = manager")
init_body = init_body.replace("        self.router = ActionRouter()\n", "")
init_body = init_body.replace("        self.state = \"IDLE\"\n", "")
init_body = init_body.replace("        self.active_widgets = {}\n", "")
init_body = init_body.replace("        self.timer = QTimer()\n", "")
init_body = init_body.replace("        self.timer.timeout.connect(self.update_animation)\n", "")
init_body = init_body.replace("        self.timer.start(1000 // 60)\n", "")
init_body = init_body.replace("        self.occlusion_timer = QTimer(self)\n", "")
init_body = init_body.replace("        self.occlusion_timer.timeout.connect(self._check_occlusion)\n", "")
init_body = init_body.replace("        self.occlusion_timer.start(250)\n", "")
init_body = init_body.replace("self.options_debounce_timer = QTimer(self)", "self.manager.options_debounce_timer = QTimer()")
init_body = init_body.replace("self.options_debounce_timer", "self.manager.options_debounce_timer")
init_body = init_body.replace("self.pending_options = None", "")

# Visualizer instantiation removed
init_body = re.sub(r"\s*# Core Visualizer.*?\n", "\n", init_body)
init_body = re.sub(r"\s*self\.visualizer = JarvisVisualizer\(self\)\n", "\n", init_body)
init_body = re.sub(r"\s*self\.visualizer\.resize\(self\.size\(\)\)\n", "\n", init_body)
init_body = re.sub(r"\s*self\.visualizer\.show\(\)\n", "\n", init_body)

# Callbacks
init_body = init_body.replace("self.submit_text_command", "self.manager.submit_text_command")
for btn in ["media", "lights", "reminders", "todos", "settings", "updates", "debug", "calendar"]:
    init_body = init_body.replace(f"self._toggle_{btn}", f"self.manager._toggle_{btn}")

# Text input no longer separate window
init_body = re.sub(r"\s*self\.text_input\.setWindowFlags\([^)]+\)\n", "\n", init_body)

resize_body = resize_body.replace("self.visualizer.resize(self.size())", "if getattr(self.manager, 'visualizer', None) and self.manager.visualizer.parent() == self:\n            self.manager.visualizer.resize(self.size())")

fullscreen_ui = f"""
class FullscreenUI(QWidget):
{init_body}
{honey_body}
{paint_body}
{resize_body}
"""

overlay_ui = """
class OverlayUI(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.is_fullscreen = False
        
        flags = (
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool
        )
        if sys.platform != "win32":
            flags |= Qt.WindowType.WindowTransparentForInput
            
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if sys.platform != "win32":
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        
    def position_in_corner(self, screen_idx):
        screens = QApplication.screens()
        target_screen = screens[screen_idx] if screen_idx < len(screens) else QApplication.primaryScreen()
        if self.windowHandle():
            self.windowHandle().setScreen(target_screen)
        
        screen_geom = target_screen.geometry()
        width, height = 200, 400
        x_pos = screen_geom.right() - width - 20
        y_pos = screen_geom.bottom() - height - 20
        self.setFixedSize(width, height)
        self.setGeometry(x_pos, y_pos, width, height)
"""

# Extract manager logic
# Everything BEFORE init
# Everything BETWEEN evaluate_state and _generate_honeycomb
# Everything AFTER paintEvent (closeEvent etc)
manager_pre = jarvis_class_body[:jarvis_class_body.find("def __init__(self")]
manager_mid = jarvis_class_body[jarvis_class_body.find("def evaluate_state(self):"):jarvis_class_body.find("def _generate_honeycomb(self, w, h):")]
manager_post = jarvis_class_body[jarvis_class_body.find("def closeEvent(self, event):"):]
manager_body = manager_pre + manager_mid + manager_post

manager_init = """
    def __init__(self):
        super().__init__()
        self.router = ActionRouter()
        self.state = "IDLE"
        self.active_widgets = {}
        self.is_fullscreen = False
        self.current_monitor_idx = 0
        
        self.overlay_ui = OverlayUI(self)
        self.fullscreen_ui = FullscreenUI(self)
        
        self.visualizer = JarvisVisualizer(self.overlay_ui)
        self.visualizer.resize(200, 400)
        self.visualizer.show()
        
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(1000 // 60)
        
        self.pending_options = None
        self.options_debounce_timer = QTimer()
        self.options_debounce_timer.setSingleShot(True)
        self.options_debounce_timer.timeout.connect(self._apply_pending_options)
"""

manager_body = manager_init + manager_body

for attr in ["btn_media", "btn_lights", "btn_reminders", "btn_todos", "btn_settings", "btn_updates", "btn_calendar", "btn_debug", "text_input", "calendar_drawer", "reminder_widget", "calendar_animation", "calendar_is_open", "honeycomb_pixmap"]:
    manager_body = manager_body.replace(f"self.{attr}", f"self.fullscreen_ui.{attr}")

manager_body = manager_body.replace("self.width()", "self.fullscreen_ui.width()")
manager_body = manager_body.replace("self.height()", "self.fullscreen_ui.height()")
manager_body = manager_body.replace("self.x()", "self.fullscreen_ui.x()")
manager_body = manager_body.replace("self.y()", "self.fullscreen_ui.y()")
manager_body = manager_body.replace("self.size()", "self.fullscreen_ui.size()")
manager_body = manager_body.replace("self.geometry()", "self.fullscreen_ui.geometry()")
manager_body = manager_body.replace("self.mapToGlobal", "self.fullscreen_ui.mapToGlobal")
manager_body = manager_body.replace("self.mapFromGlobal", "self.fullscreen_ui.mapFromGlobal")

old_set_ui_mode_start = manager_body.find("    def set_ui_mode(self, mode):")
old_set_ui_mode_end = manager_body.find("    def save_ui_state(self):")
    
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

manager_body = manager_body[:old_set_ui_mode_start] + new_set_ui_mode + manager_body[old_set_ui_mode_end:]

final_code = pre_jarvis + "\nfrom PyQt6.QtCore import QObject\n" + fullscreen_ui + "\n" + overlay_ui + "\nclass JarvisUI(QObject):\n" + manager_body

with open("src/clUI_new.py", "w") as f:
    f.write(final_code)

print("Generated src/clUI_new.py")
