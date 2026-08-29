import sys

with open("src/clUI.py", "r") as f:
    original = f.read()

overlay_class = """
class OverlayWindow(QWidget):
    def __init__(self, parent_ui):
        super().__init__()
        self.parent_ui = parent_ui
        
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
        self.setFixedSize(200, 400)

class JarvisUI(QWidget):
"""

original = original.replace("class JarvisUI(QWidget):", overlay_class)

with open("src/clUI.py", "w") as f:
    f.write(original)
