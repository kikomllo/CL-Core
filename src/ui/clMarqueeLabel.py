from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore import QTimer, Qt, QSize
from PyQt6.QtGui import QPainter, QFontMetrics

class MarqueeLabel(QLabel):
    def __init__(self, text="", force_single_line=False):
        super().__init__(text)
        self._full_text = text
        self._force_single_line = force_single_line
        self._marquee_active = False
        self._offset = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._scroll_text)
        # Holds the text still (shown elided, same as the non-hover state)
        # for a beat after hover starts, so the reader gets a moment with
        # the full start of the text before it begins moving.
        self._start_delay_timer = QTimer(self)
        self._start_delay_timer.setSingleShot(True)
        self._start_delay_timer.timeout.connect(lambda: self._timer.start(40))
        self.setMouseTracking(True)
        
    def setText(self, text):
        self._full_text = text
        self._offset = 0
        super().setText(self._full_text)
        self.update_layout_state()
        
    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(50, fm.height())
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_layout_state()
        
    def update_layout_state(self):
        fm = QFontMetrics(self.font())
        if self._force_single_line:
            rect = fm.boundingRect(self._full_text)
            if rect.width() <= self.width():
                self.setWordWrap(False)
                self._marquee_active = False
            else:
                self.setWordWrap(False)
                self._marquee_active = True
        else:
            # Calculate required height with word wrap
            rect = fm.boundingRect(0, 0, self.width(), 10000, Qt.TextFlag.TextWordWrap, self._full_text)
            if rect.height() <= self.height():
                self.setWordWrap(True)
                self._marquee_active = False
            else:
                self.setWordWrap(False)
                self._marquee_active = True
                
        if not self._marquee_active:
            self._offset = 0
            
    def enterEvent(self, event):
        self.start_scrolling()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.stop_scrolling()
        super().leaveEvent(event)

    def start_scrolling(self):
        # Callable directly by a parent that hosts this label behind a
        # mouse-transparent overlay, since a transparent label never gets
        # its own enterEvent -- the parent's hover has to trigger this.
        if self._marquee_active:
            self._start_delay_timer.start(800)

    def stop_scrolling(self):
        self._start_delay_timer.stop()
        self._timer.stop()
        self._offset = 0
        self.update()

    def _scroll_text(self):
        fm = QFontMetrics(self.font())
        text_width = fm.horizontalAdvance(self._full_text)
        self._offset -= 1
        if self._offset < -text_width:
            self._offset = self.width()
        self.update()
        
    def paintEvent(self, event):
        if not self._marquee_active:
            super().paintEvent(event)
            return
            
        painter = QPainter(self)
        fm = QFontMetrics(self.font())
        
        y = (self.height() + fm.ascent() - fm.descent()) // 2
        
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        
        if self._timer.isActive():
            painter.drawText(self._offset, y, self._full_text)
        else:
            elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, self.width())
            painter.drawText(0, y, elided)
