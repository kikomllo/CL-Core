import os
import sys
from clTheme import Theme
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTextEdit
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont, QColor, QTextCursor
from ui.clZoomTextEdit import ZoomTextEdit

class LogWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.text_edit = ZoomTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        
        # Match updater aesthetic
        self.text_edit.setStyleSheet(Theme.get_style("LogViewer"))
        self.text_edit.document().setDefaultStyleSheet("p { margin-top: 3px; margin-bottom: 3px; }")
        
        self.layout.addWidget(self.text_edit)
        
        # Log tailing
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.log_path = os.path.abspath(os.path.join(base_dir, "..", "..", "logs", "latest_run.log"))
        
        self.last_pos = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.read_logs)
        self.timer.start(500) # Check every 500ms
        
        self.read_logs()

    def read_logs(self):
        if not os.path.exists(self.log_path):
            return
            
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(self.last_pos)
                new_data = f.read()
                if new_data:
                    self.last_pos = f.tell()
                    
                    # Remove terminal color codes for basic display
                    import re
                    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                    clean_data = ansi_escape.sub('', new_data)
                    
                    scrollbar = self.text_edit.verticalScrollBar()
                    at_bottom = scrollbar.value() == scrollbar.maximum()
                    
                    self.text_edit.insertPlainText(clean_data)
                    
                    if at_bottom:
                        scrollbar.setValue(scrollbar.maximum())
        except Exception as e:
            pass
