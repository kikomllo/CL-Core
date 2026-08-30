from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QFont
from clUIScaler import UIScaler

def inject_scaler():
    # Style Sheet
    _orig_setStyleSheet = QWidget.setStyleSheet
    def _scaled_setStyleSheet(self, css):
        if not isinstance(css, str):
            return _orig_setStyleSheet(self, css)
        _orig_setStyleSheet(self, UIScaler.get().scale_css(css))
    QWidget.setStyleSheet = _scaled_setStyleSheet

    # Fixed Size
    _orig_setFixedSize = QWidget.setFixedSize
    def _scaled_setFixedSize(self, *args):
        scaler = UIScaler.get()
        if len(args) == 1:
            _orig_setFixedSize(self, scaler.scale_size(args[0].width(), args[0].height()))
        else:
            _orig_setFixedSize(self, scaler.scale(args[0]), scaler.scale(args[1]))
    QWidget.setFixedSize = _scaled_setFixedSize

    # Fixed Height
    _orig_setFixedHeight = QWidget.setFixedHeight
    def _scaled_setFixedHeight(self, h):
        _orig_setFixedHeight(self, UIScaler.get().scale(h))
    QWidget.setFixedHeight = _scaled_setFixedHeight

    # Fixed Width
    _orig_setFixedWidth = QWidget.setFixedWidth
    def _scaled_setFixedWidth(self, w):
        _orig_setFixedWidth(self, UIScaler.get().scale(w))
    QWidget.setFixedWidth = _scaled_setFixedWidth

    # Minimum Size
    _orig_setMinimumSize = QWidget.setMinimumSize
    def _scaled_setMinimumSize(self, *args):
        scaler = UIScaler.get()
        if len(args) == 1:
            _orig_setMinimumSize(self, scaler.scale_size(args[0].width(), args[0].height()))
        else:
            _orig_setMinimumSize(self, scaler.scale(args[0]), scaler.scale(args[1]))
    QWidget.setMinimumSize = _scaled_setMinimumSize

    # Maximum Size
    _orig_setMaximumSize = QWidget.setMaximumSize
    def _scaled_setMaximumSize(self, *args):
        scaler = UIScaler.get()
        if len(args) == 1:
            _orig_setMaximumSize(self, scaler.scale_size(args[0].width(), args[0].height()))
        else:
            # Check for Qt's QWIDGETSIZE_MAX which is 16777215
            w, h = args[0], args[1]
            if w < 16777215: w = scaler.scale(w)
            if h < 16777215: h = scaler.scale(h)
            _orig_setMaximumSize(self, w, h)
    QWidget.setMaximumSize = _scaled_setMaximumSize

    # Resize
    _orig_resize = QWidget.resize
    def _scaled_resize(self, *args):
        scaler = UIScaler.get()
        if len(args) == 1:
            _orig_resize(self, scaler.scale_size(args[0].width(), args[0].height()))
        else:
            _orig_resize(self, scaler.scale(args[0]), scaler.scale(args[1]))
    QWidget.resize = _scaled_resize

    # Font
    _orig_setFont = QWidget.setFont
    def _scaled_setFont(self, font):
        scaler = UIScaler.get()
        f = QFont(font)
        if f.pointSize() > 0:
            f.setPointSize(scaler.scale(f.pointSize()))
        elif f.pixelSize() > 0:
            f.setPixelSize(scaler.scale(f.pixelSize()))
        _orig_setFont(self, f)
    QWidget.setFont = _scaled_setFont

