import sys
import os
import time
from PyQt6.QtWidgets import QApplication, QWidget, QLabel
from PyQt6.QtCore import Qt, QTimer

os.environ["QT_QPA_PLATFORM"] = "xcb"

app = QApplication(sys.argv)
screens = app.screens()

if len(screens) < 2:
    print("Need 2 screens to test.")
    sys.exit()

print(f"Screens: {[s.name() for s in screens]}")

w = QWidget()
w.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
w.setStyleSheet("background-color: red;")
l = QLabel("MONITOR 1", w)
l.setStyleSheet("color: white; font-size: 50px;")
l.move(100, 100)

w.setGeometry(screens[0].geometry())
w.showFullScreen()
print(f"Fullscreen on {screens[0].name()}")

def swap():
    print(f"Swapping to {screens[1].name()}")
    w.showNormal()
    # app.processEvents()
    
    w.windowHandle().setScreen(screens[1])
    geom = screens[1].geometry()
    w.move(geom.topLeft())
    w.resize(geom.width(), geom.height())
    
    l.setText("MONITOR 2")
    w.setStyleSheet("background-color: blue;")
    
    w.showFullScreen()
    print("Done")
    
    QTimer.singleShot(2000, app.quit)

QTimer.singleShot(2000, swap)
sys.exit(app.exec())
