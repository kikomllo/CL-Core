from PyQt6.QtWidgets import QApplication, QWidget, QPushButton
from PyQt6.QtCore import QSize
import sys

app = QApplication(sys.argv)

_orig_setFixedSize = QWidget.setFixedSize

def _custom_setFixedSize(self, *args):
    print("MONKEYPATCH CALLED:", args)
    if len(args) == 1:
        _orig_setFixedSize(self, QSize(args[0].width() * 2, args[0].height() * 2))
    else:
        _orig_setFixedSize(self, args[0] * 2, args[1] * 2)

QWidget.setFixedSize = _custom_setFixedSize

btn = QPushButton()
btn.setFixedSize(10, 20)
print("Button size after int:", btn.size())
btn.setFixedSize(QSize(10, 20))
print("Button size after QSize:", btn.size())
