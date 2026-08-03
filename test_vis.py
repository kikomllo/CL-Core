import sys
from PyQt6.QtWidgets import QApplication, QWidget, QLabel
from PyQt6.QtCore import QTimer

app = QApplication(sys.argv)
w = QWidget()
w.setGeometry(100, 100, 400, 400)
l = QLabel("Test Window", w)
w.show()

def check_vis():
    print(f"Is active: {w.isActiveWindow()}, visibleRegion empty: {w.visibleRegion().isEmpty()}")
    sys.stdout.flush()

t = QTimer()
t.timeout.connect(check_vis)
t.start(2000)

QTimer.singleShot(6000, app.quit)
sys.exit(app.exec())
