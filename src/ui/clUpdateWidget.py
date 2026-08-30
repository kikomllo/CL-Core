from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QFrame, QTextEdit, QProgressBar
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent
from PyQt6.QtGui import QFont
import json
from clTheme import Theme
from utils.clActionRouter import ActionRouter
import paho.mqtt.client as mqtt
from clUIScaler import UIScaler
from ui.clZoomTextEdit import ZoomTextEdit

def s(val):
    return UIScaler.get().scale(val)

class UpdateWidget(QWidget):
    status_signal = pyqtSignal(str, object)
    log_signal = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        # self.setMinimumSize(400, 350)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, s(15), 0, 0)
        self.layout.setSpacing(s(10))
        
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet(Theme.get_style("DimLabel") + " padding-left: 5px;")
        self.layout.addWidget(self.status_lbl)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setStyleSheet(Theme.get_style("ProgressBar"))
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.hide()
        self.layout.addWidget(self.progress_bar)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent;")
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        self.layout.addWidget(self.scroll)
        
        self.log_viewer = ZoomTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setStyleSheet("background: rgba(10, 5, 0, 200);")
        
        self.log_viewer.hide()
        self.layout.addWidget(self.log_viewer)
        
        self.btn_layout = QHBoxLayout()
        self.btn_layout.setContentsMargins(s(15), s(5), s(15), 0)
        self.btn_layout.setSpacing(s(10))
        
        self.btn_check = QPushButton("Check for Updates")
        self.btn_check.setStyleSheet(Theme.get_style("SecondaryButton"))
        self.btn_check.clicked.connect(self._check_updates)
        
        self.btn_update_all = QPushButton("Update All")
        self.btn_update_all.setStyleSheet(Theme.get_style("SecondaryButton"))
        self.btn_update_all.clicked.connect(self._update_all)
        
        self.btn_layout.addWidget(self.btn_check)
        self.btn_layout.addWidget(self.btn_update_all)
        self.layout.addLayout(self.btn_layout)
        
        self.refresh_status()
        
    def update_scaling(self):
        # self.setMinimumSize(400, 350)
        self.layout.setContentsMargins(0, s(15), 0, 0)
        self.layout.setSpacing(s(10))
        if hasattr(self, 'progress_bar'): self.progress_bar.setFixedHeight(6)
        if hasattr(self, 'btn_layout'):
            self.btn_layout.setContentsMargins(s(15), s(5), s(15), 0)
            self.btn_layout.setSpacing(s(10))
        self.adjustSize()
        
    def refresh_status(self):
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
                

    def _populate_list(self, details):
        self._clear_list()
        if not details:
            lbl = QLabel("No updates available.")
            lbl.setStyleSheet(Theme.get_style("DimLabel") + " padding: 10px;")
            self.scroll_layout.addWidget(lbl)
            return
            
        for update in details:
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, s(5))
            
            t = update.get("type", "Unknown").upper()
            n = update.get("name", "Unknown")
            uid = update.get("id", "")
            
            lbl = QLabel(f"[{t}] {n}")
            lbl.setWordWrap(True)
            lay.addWidget(lbl, 1)
            
            btn = QPushButton("Install")
            btn.setStyleSheet(Theme.get_style("SuccessButton"))

            btn.clicked.connect(lambda _, x=uid: self._update_individual(x))
            lay.addWidget(btn)
            
            self.scroll_layout.addWidget(w)


    def get_standalone_min_size(self):
        return 400, 350
