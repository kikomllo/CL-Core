from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtCore import Qt, QEvent

class ZoomTextEdit(QTextEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.viewport().installEventFilter(self)
        
        self._current_font_size = 9
        self._base_style = ""

    def setStyleSheet(self, styleSheet):
        self._base_style = styleSheet
        super().setStyleSheet(self._base_style + f"; font-size: {self._current_font_size}pt;")

    def zoomIn(self, range=1):
        self._current_font_size += range
        super().setStyleSheet(self._base_style + f"; font-size: {self._current_font_size}pt;")
        
    def zoomOut(self, range=1):
        self._current_font_size -= range
        if self._current_font_size < 4:
            self._current_font_size = 4
        super().setStyleSheet(self._base_style + f"; font-size: {self._current_font_size}pt;")

    def eventFilter(self, obj, event):
        if obj == self.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self.zoomIn(1)
                elif delta < 0:
                    self.zoomOut(1)
                return True
        return super().eventFilter(obj, event)
