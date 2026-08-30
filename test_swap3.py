import sys
import os
from PyQt6.QtWidgets import QApplication, QWidget, QLabel
from PyQt6.QtCore import Qt, QTimer

os.environ["QT_QPA_PLATFORM"] = "xcb"

app = QApplication(sys.argv)
screens = app.screens()

if len(screens) < 2:
    print("Need 2 screens to test.")
    sys.exit()

w = QWidget()
w.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
w.setStyleSheet("background-color: red;")
l = QLabel("MONITOR 1", w)
l.setStyleSheet("color: white; font-size: 50px;")
l.move(100, 100)

w.setGeometry(screens[0].geometry())
w.showFullScreen()

def swap():
    w.setWindowOpacity(0.0)
    w.showNormal()
    w.windowHandle().setScreen(screens[1])
    geom = screens[1].geometry()
    w.move(geom.topLeft())
    w.resize(geom.width(), geom.height())
    l.setText("MONITOR 2")
    w.setStyleSheet("background-color: blue;")
    w.showFullScreen()
    QTimer.singleShot(150, lambda: w.setWindowOpacity(1.0))
    QTimer.singleShot(2000, app.quit)

QTimer.singleShot(2000, swap)
sys.exit(app.exec())
