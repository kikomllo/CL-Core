import json
import paho.mqtt.publish as publish
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import QTimer

class MediaWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 5, 10, 5)
        self.layout.setSpacing(6)
        
        # Header Badge
        header_layout = QHBoxLayout()
        self.badge_lbl = QLabel("SPOTIFY PLAYER")
        self.badge_lbl.setStyleSheet("color: #ffaa00; font-size: 10px; font-weight: 800; letter-spacing: 1px; border: none; background: transparent;")
        header_layout.addWidget(self.badge_lbl)
        header_layout.addStretch()
        self.layout.addLayout(header_layout)
        
        # Title and Artist
        self.title_lbl = QLabel("No Media Playing")
        self.title_lbl.setStyleSheet("color: #ffaa00; font-weight: 800; font-size: 12pt; border: none; background: transparent;")
        self.title_lbl.setWordWrap(True)
        
        self.artist_lbl = QLabel("Unknown Artist")
        self.artist_lbl.setStyleSheet("color: #ffcc80; font-size: 10pt; font-weight: 500; border: none; background: transparent;")
        self.artist_lbl.setWordWrap(True)
        
        self.layout.addWidget(self.title_lbl)
        self.layout.addWidget(self.artist_lbl)
        
        # Time label
        time_layout = QHBoxLayout()
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setStyleSheet("color: rgba(255, 200, 150, 0.7); font-size: 9pt; font-weight: 600; border: none; background: transparent;")
        time_layout.addWidget(self.time_lbl)
        time_layout.addStretch()
        self.layout.addLayout(time_layout)
        
        self.layout.addStretch()
        
        # Playback Controls
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(18)
        
        small_btn_style = """
            QPushButton {
                background-color: rgba(35, 18, 5, 0.8);
                color: #ffaa00;
                border-radius: 16px;
                border: 1px solid rgba(255, 160, 0, 0.4);
                font-size: 11pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 150, 0, 0.35);
                color: #ffffff;
                border: 1px solid #ffaa00;
            }
        """
        
        play_btn_style = """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff8c00, stop:1 #e65c00);
                color: #ffffff;
                border-radius: 20px;
                border: 1px solid #ffcc66;
                font-size: 13pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffa01a, stop:1 #ff701a);
            }
        """
        
        self.prev_btn = QPushButton("⏮")
        self.prev_btn.setFixedSize(32, 32)
        self.prev_btn.setStyleSheet(small_btn_style)
        self.prev_btn.clicked.connect(lambda: self.send_cmd("prev", silent=True))
        
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(40, 40)
        self.play_btn.setStyleSheet(play_btn_style)
        self.play_btn.clicked.connect(self.toggle_optimistic)
        
        self.next_btn = QPushButton("⏭")
        self.next_btn.setFixedSize(32, 32)
        self.next_btn.setStyleSheet(small_btn_style)
        self.next_btn.clicked.connect(lambda: self.send_cmd("next", silent=True))
        
        controls_layout.addStretch()
        controls_layout.addWidget(self.prev_btn)
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.next_btn)
        controls_layout.addStretch()
        
        self.layout.addLayout(controls_layout)
        
        self.position = 0.0
        self.duration = 0.0
        self.status = "Paused"
        self._waiting_for_status = False
                
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        
    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self.window(), 'is_fullscreen', False):
            self.send_cmd("status", silent=True)
        
    def toggle_optimistic(self):
        self.status = "Paused" if self.status == "Playing" else "Playing"
        self.play_btn.setText("⏸" if self.status == "Playing" else "▶")
        self.send_cmd("toggle", silent=True)

    def _tick(self):
        if self.status == "Playing" and self.duration > 0:
            self.position += 1.0
            if self.position >= self.duration:
                self.position = self.duration
                if self.isVisible() and getattr(self.window(), 'is_fullscreen', False):
                    if not getattr(self, '_waiting_for_status', False):
                        self._waiting_for_status = True
                        self.send_cmd("status", silent=True)
            self._update_time_label()
            
    def _update_time_label(self):
        def fmt_time(secs):
            m = int(secs // 60)
            s = int(secs % 60)
            return f"{m}:{s:02d}"
        self.time_lbl.setText(f"{fmt_time(self.position)} / {fmt_time(self.duration)}")
        
    def send_cmd(self, action, silent=False):
        try:
            publish.single("pc/spotify/control", json.dumps({"action": action, "silent": silent}), hostname="localhost", qos=0)
        except Exception as e:
            print(f"Failed to publish media control: {e}")

    def update_status(self, data):
        self._waiting_for_status = False
        title = data.get("title", "Unknown")
        artist = data.get("artist", "Unknown")
        self.position = data.get("position", 0.0)
        self.duration = data.get("duration", 0.0)
        self.status = data.get("status", "Paused")
        
        self.title_lbl.setText(title[:30] + ("..." if len(title) > 30 else ""))
        self.artist_lbl.setText(artist[:30] + ("..." if len(artist) > 30 else ""))
        
        self._update_time_label()
        self.play_btn.setText("⏸" if self.status == "Playing" else "▶")
