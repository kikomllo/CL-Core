import re

with open("src/clUI.py", "r") as f:
    text = f.read()

# Replace set_fullscreen transition
fullscreen_search = """        if mode == "set_fullscreen":
            if getattr(self, 'is_fullscreen', False):"""

fullscreen_replace = """        if mode == "set_fullscreen":
            self.overlay_window.hide()
            self.visualizer.setParent(self)
            self.visualizer.resize(self.size())
            self.visualizer.show()
            
            if getattr(self, 'is_fullscreen', False):"""

text = text.replace(fullscreen_search, fullscreen_replace)

fullscreen_flags_search = """            self.is_fullscreen = True
            
            flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            if sys.platform == "win32":
                flags |= Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            
            # Remove minimum size constraints temporarily for transition
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)"""

fullscreen_flags_replace = """            self.is_fullscreen = True
            
            flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            if sys.platform == "win32":
                flags |= Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)"""

text = text.replace(fullscreen_flags_search, fullscreen_flags_replace)

overlay_search = """        elif mode == "set_overlay":
            self.is_fullscreen = False"""

overlay_replace = """        elif mode == "set_overlay":
            self.is_fullscreen = False"""

overlay_flags_search = """            self.hide()
            
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
            
            primary_screen = QApplication.primaryScreen()
            screen_geom = primary_screen.availableGeometry()
            width, height = 200, 400
            x_pos = screen_geom.right() - width - 20
            y_pos = screen_geom.bottom() - height - 20
            
            self.showNormal()
            self.setFixedSize(width, height)
            self.setGeometry(x_pos, y_pos, width, height)"""

overlay_flags_replace = """            self.hide()
            
            primary_screen = QApplication.primaryScreen()
            screen_geom = primary_screen.availableGeometry()
            width, height = 200, 400
            x_pos = screen_geom.right() - width - 20
            y_pos = screen_geom.bottom() - height - 20
            
            self.overlay_window.setGeometry(x_pos, y_pos, width, height)
            self.overlay_window.showNormal()
            
            self.visualizer.setParent(self.overlay_window)
            self.visualizer.resize(width, height)
            self.visualizer.show()"""

text = text.replace(overlay_flags_search, overlay_flags_replace)

with open("src/clUI.py", "w") as f:
    f.write(text)
    
