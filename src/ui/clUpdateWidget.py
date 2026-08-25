from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QFrame, QTextEdit, QProgressBar
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent
from PyQt6.QtGui import QFont
import json
from utils.clActionRouter import ActionRouter
import paho.mqtt.client as mqtt

class ZoomTextEdit(QTextEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.viewport().installEventFilter(self)

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

class UpdateWidget(QWidget):
    status_signal = pyqtSignal(str, object)
    log_signal = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        self.list_font_size = 8.5
        self.setMinimumSize(400, 350)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 10)
        self.layout.setSpacing(10)
        
        self.header = QLabel("System Updates")
        self.header.setStyleSheet("color: rgba(255, 170, 0, 220); font-weight: bold; font-size: 11pt; padding: 5px;")
        self.layout.addWidget(self.header)
        
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color: #ffe6cc; font-size: 9pt; padding-left: 5px;")
        self.layout.addWidget(self.status_lbl)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setStyleSheet("QProgressBar { border: 1px solid rgba(255, 150, 0, 50); border-radius: 2px; text-align: center; } QProgressBar::chunk { background-color: #ffaa00; }")
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.hide()
        self.layout.addWidget(self.progress_bar)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: 1px solid rgba(255, 150, 0, 50); border-radius: 4px; }")
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        self.layout.addWidget(self.scroll)
        self.scroll.viewport().installEventFilter(self)
        
        self.log_viewer = ZoomTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setStyleSheet("background: rgba(10, 5, 0, 200); color: #ffaa00; border: 1px solid rgba(255, 150, 0, 50);")
        
        font = QFont("monospace")
        font.setPointSize(8)
        self.log_viewer.setFont(font)
        
        self.log_viewer.hide()
        self.layout.addWidget(self.log_viewer)
        
        self.btn_layout = QHBoxLayout()
        self.btn_layout.setContentsMargins(15, 5, 15, 5)
        self.btn_layout.setSpacing(10)
        
        self.btn_check = QPushButton("Check for Updates")
        self.btn_check.setStyleSheet(self._btn_style())
        self.btn_check.clicked.connect(self._check_updates)
        
        self.btn_update_all = QPushButton("Update All")
        self.btn_update_all.setStyleSheet(self._btn_style())
        self.btn_update_all.clicked.connect(self._update_all)
        
        self.btn_layout.addWidget(self.btn_check)
        self.btn_layout.addWidget(self.btn_update_all)
        self.layout.addLayout(self.btn_layout)
        
        self.status_signal.connect(self._on_status_update)
        self.log_signal.connect(self._on_log_message)
        
        # Start MQTT listener for status updates
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_message = self._on_message
        try:
            self.client.connect("localhost", 1883, 60)
            self.client.subscribe("jarvis/sys/updates/status")
            self.client.loop_start()
        except:
            pass
            
    def _btn_style(self):
        return """
            QPushButton {
                background: rgba(255, 120, 0, 40);
                color: #ffaa00;
                border: 1px solid rgba(255, 150, 0, 80);
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 150, 0, 80);
                color: #ffffff;
            }
        """

    def _check_updates(self):
        self.status_lbl.setText("Checking for updates...")
        try:
            self.router.dispatch("updates.check")
        except Exception as e:
            self.status_lbl.setText(f"Error: {e}")

    def _update_all(self):
        self.status_lbl.setText("Starting Update All...")
        try:
            self.router.dispatch("updates.force_all")
        except Exception as e:
            self.status_lbl.setText(f"Error: {e}")
        
    def _update_individual(self, update_id):
        self.status_lbl.setText(f"Updating {update_id}...")
        try:
            self.router.dispatch("updates.individual", id=update_id)
        except Exception as e:
            self.status_lbl.setText(f"Error: {e}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            action = payload.get("action")
            if action == "log":
                self.log_signal.emit(payload.get("text", ""))
            else:
                status = payload.get("status", "")
                details = payload.get("details", [])
                self.status_signal.emit(status, details)
        except:
            pass
            
    def _on_log_message(self, text):
        self.log_viewer.append(text)
        # auto-scroll
        scrollbar = self.log_viewer.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
            
    def _on_status_update(self, status, details):
        if status == "checking":
            self.status_lbl.setText("Checking for updates... Please wait.")
            self.log_viewer.hide()
            self.progress_bar.show()
            self.scroll.show()
            self._clear_list()
        elif status == "updating":
            self.status_lbl.setText("Installing updates... Please wait.")
            self.progress_bar.show()
            self.scroll.hide()
            self.log_viewer.show()
            self.log_viewer.clear()
        elif status == "ready":
            self.progress_bar.hide()
            self.log_viewer.hide()
            self.scroll.show()
            self.status_lbl.setText(f"Found {len(details)} updates.")
            self._populate_list(details)

    def _clear_list(self):
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
    def eventFilter(self, obj, event):
        if obj == self.scroll.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self.list_font_size += 0.5
                elif delta < 0:
                    self.list_font_size = max(4.0, self.list_font_size - 0.5)
                self._update_list_fonts()
                return True
        return super().eventFilter(obj, event)

    def _update_list_fonts(self):
        for i in range(self.scroll_layout.count()):
            item = self.scroll_layout.itemAt(i)
            if item.widget():
                w = item.widget()
                if isinstance(w, QLabel):
                    w.setStyleSheet(f"color: #aaaaaa; padding: 10px; font-size: {self.list_font_size}pt;")
                else:
                    lay = w.layout()
                    if lay:
                        lbl_item = lay.itemAt(0)
                        btn_item = lay.itemAt(1)
                        if lbl_item and lbl_item.widget():
                            lbl_item.widget().setStyleSheet(f"color: #ffe6cc; font-size: {self.list_font_size}pt;")
                        if btn_item and btn_item.widget():
                            btn = btn_item.widget()
                            btn.setStyleSheet(f"QPushButton {{ background: rgba(50, 150, 50, 60); color: #88ff88; border: 1px solid rgba(50, 150, 50, 100); border-radius: 3px; font-weight: bold; font-size: {max(4.0, self.list_font_size - 0.5)}pt; padding: 2px 5px; }} QPushButton:hover {{ background: rgba(70, 200, 70, 100); color: #ffffff; }}")

                
    def _populate_list(self, details):
        self._clear_list()
        if not details:
            lbl = QLabel("No updates available.")
            lbl.setStyleSheet(f"color: #aaaaaa; padding: 10px; font-size: {self.list_font_size}pt;")
            self.scroll_layout.addWidget(lbl)
            return
            
        for update in details:
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 5)
            
            t = update.get("type", "Unknown").upper()
            n = update.get("name", "Unknown")
            uid = update.get("id", "")
            
            lbl = QLabel(f"[{t}] {n}")
            lbl.setStyleSheet(f"color: #ffe6cc; font-size: {self.list_font_size}pt;")
            lbl.setWordWrap(True)
            lay.addWidget(lbl, 1)
            
            btn = QPushButton("Install")
            btn.setStyleSheet(f"QPushButton {{ background: rgba(50, 150, 50, 60); color: #88ff88; border: 1px solid rgba(50, 150, 50, 100); border-radius: 3px; font-weight: bold; font-size: {max(4.0, self.list_font_size - 0.5)}pt; padding: 2px 5px; }} QPushButton:hover {{ background: rgba(70, 200, 70, 100); color: #ffffff; }}")

            btn.clicked.connect(lambda _, x=uid: self._update_individual(x))
            lay.addWidget(btn)
            
            self.scroll_layout.addWidget(w)
