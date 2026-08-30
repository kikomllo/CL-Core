import json
import sys
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
screens = QApplication.screens()

data = []
for s in screens:
    geom = s.geometry()
    data.append({
        "name": s.name(),
        "geometry": [geom.x(), geom.y(), geom.width(), geom.height()],
        "logical_dpi": s.logicalDotsPerInch(),
        "physical_dpi": s.physicalDotsPerInch()
    })

print(json.dumps(data, indent=2))
