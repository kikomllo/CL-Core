import sys
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QLineEdit
from PyQt6.QtCore import Qt
import ctypes

class TestUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        
        self.btn = QPushButton("Click Me", self)
        self.btn.setGeometry(50, 50, 100, 50)
        self.btn.clicked.connect(lambda: print("Button clicked!", flush=True))
        
        self.text = QLineEdit(self)
        self.text.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.text.setGeometry(50, 150, 100, 30)

    def mousePressEvent(self, event):
        print("JarvisUI clicked!", flush=True)

    def go_fullscreen(self):
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.clearMask()
        self.showFullScreen()
        
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, style & ~0x00000020)
        
        self.text.show()
        
app = QApplication(sys.argv)
w = TestUI()
w.show()
import threading, time
def delay_fs():
    time.sleep(1)
    # Qt must execute UI updates in main thread, use QTimer instead
threading.Thread(target=delay_fs).start()
# Actually, just use QTimer
from PyQt6.QtCore import QTimer
QTimer.singleShot(1000, w.go_fullscreen)
QTimer.singleShot(5000, app.quit)
app.exec()
