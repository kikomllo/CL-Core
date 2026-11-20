from clTheme import Theme
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QTextCursor
from ui.clZoomTextEdit import ZoomTextEdit
from utils import clLogTail
from utils.clLogTail import LogTailer, condense_text


class LogWidget(QWidget):
    """Live tail of logs/latest_run.log. The log is huge (many MB, with single lines of tens of KB: lyrics JSON), and all of this
    runs on the UI thread, so it never loads it whole: it opens with only the tail, bounds what one poll may read, caps the document,
    and does not poll while hidden. By default it is a condensed view (clLogTail.condense: heartbeat noise dropped, lines cut to
    one screen line); the Raw toggle shows everything but still cuts absurd lines."""

    INITIAL_TAIL_BYTES = 256 * 1024     # what is read on open (condensed, most of it is dropped)
    RAW_INITIAL_TAIL_BYTES = 96 * 1024  # raw keeps every line, so open it with less
    MAX_READ_BYTES = 256 * 1024         # one poll never reads more than this: a bigger burst shows its tail
    MAX_BLOCKS = 3000                   # the document keeps the last this many lines
    POLL_MS = 500

    def __init__(self, parent=None, log_path=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.condensed_btn = QPushButton("Condensed")
        self.condensed_btn.setCheckable(True)
        self.condensed_btn.setChecked(True)
        self.condensed_btn.setToolTip("Hide heartbeat noise and shorten long lines. Off shows the log as written.")
        self.condensed_btn.toggled.connect(self._reload)
        self.terminal_btn = QPushButton("Open terminal")
        self.terminal_btn.setToolTip("Follow this log in a separate terminal window")
        self.terminal_btn.clicked.connect(self.open_terminal)
        for b in (self.condensed_btn, self.terminal_btn):
            b.setStyleSheet(Theme.get_style("LogToolButton"))
        header.addWidget(self.condensed_btn)
        header.addStretch(1)
        header.addWidget(self.terminal_btn)
        self.layout.addLayout(header)

        self.text_edit = ZoomTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setUndoRedoEnabled(False)
        self.text_edit.document().setMaximumBlockCount(self.MAX_BLOCKS)

        # Match updater aesthetic
        self.text_edit.setStyleSheet(Theme.get_style("LogViewer"))
        self.text_edit.document().setDefaultStyleSheet("p { margin-top: 3px; margin-bottom: 3px; }")

        self.layout.addWidget(self.text_edit)

        # Log tailing
        self.log_path = log_path or clLogTail.LOG_PATH
        self.tailer = LogTailer(self.log_path, self.INITIAL_TAIL_BYTES, self.MAX_READ_BYTES)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.read_logs)
        self.timer.start(self.POLL_MS)

        self.read_logs()

    @property
    def condensed(self):
        return self.condensed_btn.isChecked()

    def _reload(self, _checked=None):
        """The view mode changed: show the tail again in the new mode."""
        self.text_edit.clear()
        self.tailer.initial_bytes = self.INITIAL_TAIL_BYTES if self.condensed else self.RAW_INITIAL_TAIL_BYTES
        self.tailer.reset()
        self.read_logs()

    def open_terminal(self):
        """The terminal follows in the same mode as this view."""
        if not clLogTail.open_terminal(raw=not self.condensed):
            self.terminal_btn.setText("No terminal found")
            QTimer.singleShot(2500, self._restore_terminal_label)

    def _restore_terminal_label(self):
        self.terminal_btn.setText("Open terminal")

    # A hidden widget (the dashboard hides it instead of destroying it) must not keep reading and inserting; on show it catches up
    # with the tail, so nothing is lost beyond what one poll may read.
    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.timer.isActive():
            self.timer.start(self.POLL_MS)
            self.read_logs()

    def _scroll_to_bottom(self):
        scrollbar = self.text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def read_logs(self):
        try:
            text, note, restarted = self.tailer.read()
            if restarted:                                     # a new run rewrote the log
                self.text_edit.clear()
            shown = note + condense_text(text, self.condensed)
            if not shown:
                return
            scrollbar = self.text_edit.verticalScrollBar()
            at_bottom = scrollbar.value() >= scrollbar.maximum() - 2

            cursor = QTextCursor(self.text_edit.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(shown)

            if at_bottom:
                scrollbar.setValue(scrollbar.maximum())
                QTimer.singleShot(0, self._scroll_to_bottom)      # the range only grows once the text is laid out
        except Exception:
            pass
