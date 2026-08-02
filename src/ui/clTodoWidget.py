import json
import paho.mqtt.publish as publish
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QScrollArea, QFrame, QLineEdit, QPushButton, QCheckBox
from PyQt6.QtCore import Qt

class TodoWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.layout.setSpacing(10)
        
        # Title
        self.title_lbl = QLabel("My To-Do List")
        self.title_lbl.setStyleSheet("color: #ffaa00; font-weight: 800; font-size: 11pt; letter-spacing: 0.5px;")
        self.layout.addWidget(self.title_lbl)
        
        # Scroll Area for tasks
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollBar:vertical { width: 6px; background: rgba(0,0,0,40); border-radius: 3px; } QScrollBar::handle:vertical { background: rgba(255,170,0,120); border-radius: 3px; }")
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(8)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.scroll.setWidget(self.scroll_content)
        self.layout.addWidget(self.scroll)
        
        # Add task input (Floating Window)
        self.task_input = QLineEdit()
        self.task_input.setWindowFlags(
            Qt.WindowType.Window | 
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.task_input.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.task_input.setPlaceholderText("New task...")
        self.task_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(25, 12, 3, 240);
                color: #ffe6cc;
                border: 1px solid rgba(255, 180, 0, 150);
                border-radius: 8px;
                padding: 6px;
                font-size: 10pt;
            }
        """)
        self.task_input.returnPressed.connect(self.submit_task)
        self.task_input.hide()
        
        self.add_btn = QPushButton("+ Add Task")
        self.add_btn.setFixedHeight(32)
        self.add_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 150, 0, 0.25), stop:1 rgba(255, 100, 0, 0.25));
                color: #ffbb33;
                border-radius: 8px;
                border: 1px solid rgba(255, 160, 0, 0.5);
                font-weight: bold;
                font-size: 10pt;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 160, 0, 0.45), stop:1 rgba(255, 110, 0, 0.45));
                color: #ffffff;
                border: 1px solid #ffaa00;
            }
        """)
        self.add_btn.clicked.connect(self.open_task_input)
        
        self.layout.addWidget(self.add_btn)
        
        self.setMinimumSize(250, 300)
        
    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self.window(), 'is_fullscreen', False):
            publish.single("jarvis/sys/todo/request", json.dumps({"action": "list"}), hostname="localhost", qos=0)

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'task_input'):
            self.task_input.hide()

    def on_drag_start(self):
        if hasattr(self, 'task_input') and self.task_input.isVisible():
            self.task_input.hide()

    def open_task_input(self):
        pos = self.mapToGlobal(self.add_btn.pos())
        self.task_input.setGeometry(pos.x(), pos.y() - 40, 220, 35)
        self.task_input.show()
        self.task_input.activateWindow()
        self.task_input.raise_()
        self.task_input.setFocus()

    def submit_task(self):
        task_text = self.task_input.text().strip()
        if task_text:
            publish.single("jarvis/sys/todo/create", json.dumps({"task": task_text}), hostname="localhost", qos=0)
            self.task_input.clear()
        self.task_input.hide()

    def update_status(self, data):
        todos = data.get("todos", [])
        
        # Clear existing
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget: widget.deleteLater()
            
        if not todos:
            lbl = QLabel("No pending tasks.")
            lbl.setStyleSheet("color: rgba(255, 170, 0, 150); font-style: italic; font-size: 9pt;")
            self.scroll_layout.addWidget(lbl)
            return
            
        for t in todos:
            chk = QCheckBox(t["task"])
            chk.setStyleSheet("""
                QCheckBox {
                    color: #ffe6cc;
                    font-size: 10pt;
                    font-weight: 500;
                    padding: 2px 0px;
                }
                QCheckBox::indicator {
                    width: 16px;
                    height: 16px;
                    border-radius: 4px;
                    border: 1px solid rgba(255, 170, 0, 0.5);
                    background: rgba(35, 18, 5, 0.8);
                }
                QCheckBox::indicator:hover {
                    border: 1px solid #ffaa00;
                    background: rgba(255, 150, 0, 0.3);
                }
                QCheckBox::indicator:checked {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff8c00, stop:1 #e65c00);
                    border: 1px solid #ffbb33;
                }
            """)
            is_completed = t.get("completed", False)
            chk.setChecked(is_completed)
            if is_completed:
                chk.setStyleSheet(chk.styleSheet() + " QCheckBox { color: rgba(255, 200, 150, 0.45); text-decoration: line-through; }")
            
            # Connect the state change to MQTT
            chk.stateChanged.connect(lambda state, tid=t["id"]: self.toggle_task(tid, state))
            self.scroll_layout.addWidget(chk)
            
    def toggle_task(self, todo_id, state):
        if state == 2: # Checked
            publish.single("jarvis/sys/todo/control", json.dumps({"action": "complete", "id": todo_id}), hostname="localhost", qos=0)
        else: # Note: unchecking is not currently supported by backend, but we'll leave it
            publish.single("jarvis/sys/todo/control", json.dumps({"action": "delete", "id": todo_id}), hostname="localhost", qos=0)
