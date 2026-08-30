import sys
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
primary = QApplication.primaryScreen()
print(f"Primary screen: {primary.name()} with geometry {primary.geometry()}")
for s in QApplication.screens():
    print(f"Screen {s.name()} geometry {s.geometry()}")
