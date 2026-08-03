import sys, os
os.environ["QT_QPA_PLATFORM"] = "xcb" # same as Jarvis
from PyQt6.QtWidgets import QApplication, QWidget, QLabel
from PyQt6.QtCore import QTimer

app = QApplication(sys.argv)
w = QWidget()
w.setGeometry(100, 100, 400, 400)
l = QLabel("Test Expose", w)
w.show()

def check_expose():
    wh = w.windowHandle()
    exposed = wh.isExposed() if wh else False
    print(f"Is exposed? {exposed}")
    sys.stdout.flush()

t = QTimer()
t.timeout.connect(check_expose)
t.start(1000)

QTimer.singleShot(5000, app.quit)
sys.exit(app.exec())
