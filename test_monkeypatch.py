from PyQt6.QtWidgets import QApplication, QWidget, QLabel
import sys

app = QApplication(sys.argv)

original_set_style_sheet = QWidget.setStyleSheet

def custom_set_style_sheet(self, css):
    print("MONKEYPATCH CALLED WITH CSS:", css)
    original_set_style_sheet(self, css + " color: red;")

QWidget.setStyleSheet = custom_set_style_sheet

w = QWidget()
w.setStyleSheet("background: black;")

lbl = QLabel("Test", w)
lbl.setStyleSheet("font-size: 20px;")

print("Widget stylesheet:", w.styleSheet())
print("Label stylesheet:", lbl.styleSheet())
