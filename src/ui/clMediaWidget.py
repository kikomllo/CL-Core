import json
import logging
from clTheme import Theme
from utils.clActionRouter import ActionRouter
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import QTimer

class MediaWidget(QWidget):
    def __init__(self, parent=None, grid_mode=False):
        super().__init__(parent)
        self.router = ActionRouter()
        self.grid_mode = grid_mode
        self.setMinimumSize(250, 170)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 10, 15, 15)
        self.layout.setSpacing(6)
        
        # Header Badge
        header_layout = QHBoxLayout()
        self.badge_lbl = QLabel("SPOTIFY PLAYER")
        self.badge_lbl.setStyleSheet(Theme.get_style("BadgeLabel"))
        header_layout.addWidget(self.badge_lbl)
        header_layout.addStretch()
        self.layout.addLayout(header_layout)
        
        # Title and Artist
        self.title_lbl = QLabel("No Media Playing")
        self.title_lbl.setStyleSheet(Theme.get_style("TitleLabel"))
        self.title_lbl.setWordWrap(True)
        
        self.artist_lbl = QLabel("Unknown Artist")
        self.artist_lbl.setStyleSheet(Theme.get_style("SubtitleLabel"))
        self.artist_lbl.setWordWrap(True)
        
        self.layout.addWidget(self.title_lbl)
        self.layout.addWidget(self.artist_lbl)
        
        # Time label
        time_layout = QHBoxLayout()
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setStyleSheet(Theme.get_style("DimLabel"))
        time_layout.addWidget(self.time_lbl)
        time_layout.addStretch()
        self.layout.addLayout(time_layout)
        
        self.layout.addStretch()
        
        # Playback Controls
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(18)
        
        self.prev_btn = QPushButton("⏮")
        self.prev_btn.setFixedSize(32, 32)
        self.prev_btn.setStyleSheet(Theme.get_style("MediaSmallBtn"))
        self.prev_btn.clicked.connect(lambda: self.send_cmd("prev", silent=True))
        
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(40, 40)
        self.play_btn.setStyleSheet(Theme.get_style("MediaPlayBtn"))
        self.play_btn.clicked.connect(self.toggle_optimistic)
        
        self.next_btn = QPushButton("⏭")
        self.next_btn.setFixedSize(32, 32)
        self.next_btn.setStyleSheet(Theme.get_style("MediaSmallBtn"))
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
            self.router.dispatch("spotify.control", action=action, silent=silent)
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

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
