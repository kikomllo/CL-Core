import os
import sys
import json
import math
import random
import paho.mqtt.client as mqtt
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPointF, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QRadialGradient, QBrush, QLinearGradient

os.environ["QT_QPA_PLATFORM"] = "xcb"

ECOSYSTEM_STATE = "normal"
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "core.json"), "r") as f:
        ECOSYSTEM_STATE = json.load(f).get("settings", {}).get("ecosystem_state", "normal")
except Exception:
    pass

class MqttThread(QThread):
    state_signal = pyqtSignal(str)
    options_signal = pyqtSignal(list, str)
    vol_signal = pyqtSignal(float)
    state_change_signal = pyqtSignal(str)
    ui_mode_signal = pyqtSignal(str)
    media_status_signal = pyqtSignal(dict)
    light_status_signal = pyqtSignal(dict)
    feedback_signal = pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        self.tts_active = False
        self.mic_active = "IDLE"
        self.processing_active = False
        self.attention_active = False
        self.ui_state = "IDLE"
    
    def evaluate_state(self):
        if self.tts_active:
            new_state = "SPEAKING"
        elif self.mic_active == "RECORDING":
            new_state = "RECORDING"
        elif self.mic_active == "LISTENING":
            new_state = "LISTENING"
        elif self.processing_active:
            new_state = "PROCESSING"
        elif self.attention_active:
            new_state = "ATTENTION"
        else:
            new_state = "IDLE"
            
        if self.ui_state != new_state:
            self.ui_state = new_state
            self.state_signal.emit(new_state)
            
    def run(self):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        
        attempt = 0
        while True:
            try:
                client.connect("localhost", 1883, 60)
                client.loop_forever()
            except Exception as e:
                delay = min(60, 2 ** attempt)
                print(f"MQTT Error: {e}. Reconnecting in {delay}s...")
                import time
                time.sleep(delay)
                attempt += 1
            
    def on_connect(self, client, userdata, flags, reason_code, properties):
        client.subscribe("jarvis/sys/tts_state")
        client.subscribe("jarvis/sys/mic_control")
        client.subscribe("jarvis/sys/mic_state")
        client.subscribe("jarvis/sys/audio_process")
        client.subscribe("jarvis/sensor/mic_vol")
        client.subscribe("jarvis/sys/ui_options")
        client.subscribe("jarvis/sys/ui_control")
        client.subscribe("jarvis/sys/audio_vol")
        client.subscribe("jarvis/sys/volume")
        client.subscribe("jarvis/sys/state_change")
        client.subscribe("jarvis/sys/media_status")
        client.subscribe("jarvis/sys/light_status")
        client.subscribe("jarvis/feedback")
        
    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode()) if msg.payload else {}
        except Exception:
            payload = msg.payload.decode() if msg.payload else ""
            
        handlers = {
            "jarvis/sys/audio_vol": self._handle_vol,
            "jarvis/sys/volume": self._handle_vol,
            "jarvis/sys/tts_state": self._handle_tts_state,
            "jarvis/sys/mic_control": self._handle_mic_control,
            "jarvis/sys/mic_state": self._handle_mic_state,
            "jarvis/sys/audio_process": self._handle_processing,
            "jarvis/sys/ui_options": self._handle_options,
            "jarvis/sys/ui_control": self._handle_ui_control,
            "jarvis/sys/state_change": self._handle_state_change,
            "jarvis/sys/media_status": self._handle_media_status,
            "jarvis/sys/light_status": self._handle_light_status,
            "jarvis/feedback": self._handle_feedback
        }
        
        handler = handlers.get(topic)
        if handler:
            handler(payload)

    def _handle_light_status(self, payload):
        if isinstance(payload, dict):
            self.light_status_signal.emit(payload)

    def _handle_media_status(self, payload):
        self.media_status_signal.emit(payload)

    def _handle_feedback(self, payload):
        self.feedback_signal.emit(payload)

    def _handle_vol(self, payload):
        if isinstance(payload, dict) and "rms" in payload:
            self.vol_signal.emit(float(payload["rms"]))

    def _handle_tts_state(self, payload):
        if isinstance(payload, dict):
            state = payload.get("state")
            if state == "active":
                self.tts_active = True
                self.processing_active = False
            elif state == "idle":
                self.tts_active = False
            self.evaluate_state()

    def _handle_mic_control(self, payload):
        if not isinstance(payload, dict): return
        action = payload.get("action")
        
        if action == "attention_on":
            self.attention_active = True
        elif action in ["request_reply", "open_window"]:
            self.mic_active = "LISTENING"
            self.processing_active = False
        elif action == "attention_off":
            self.attention_active = False
            self.mic_active = "IDLE"
            self.processing_active = False
        elif action == "cancel":
            self.mic_active = "IDLE"
            self.processing_active = False
            
        self.evaluate_state()

    def _handle_mic_state(self, payload):
        if isinstance(payload, dict):
            state = payload.get("state")
            if state == "recording":
                self.mic_active = "RECORDING"
            elif state == "listening":
                self.mic_active = "LISTENING"
            elif state == "idle":
                self.mic_active = "IDLE"
                
            self.evaluate_state()

    def _handle_processing(self, payload):
        if isinstance(payload, dict):
            state = payload.get("state", "active")
            if state == "active":
                self.processing_active = True
                self.mic_active = "IDLE"
            elif state == "idle":
                self.processing_active = False
                # Reset mic_active too — prevents stuck-in-LISTENING when a
                # request_reply was pending but the audio pipeline has finished
                if self.mic_active == "LISTENING" and not self.attention_active:
                    self.mic_active = "IDLE"
        else:
            self.processing_active = True
            self.mic_active = "IDLE"
            
        self.evaluate_state()

    def _handle_options(self, payload):
        if isinstance(payload, dict) and "options" in payload:
            self.options_signal.emit(payload["options"], payload.get("title", "Options"))

    def _handle_ui_control(self, payload):
        if isinstance(payload, dict) and "action" in payload:
            self.ui_mode_signal.emit(payload["action"])

    def _handle_state_change(self, payload):
        if isinstance(payload, dict) and "action" in payload:
            new_state = payload.get("action")
            if new_state in ["debug", "normal", "background"]:
                self.state_change_signal.emit(new_state)

class DraggableWidget(QWidget):
    def __init__(self, widget_id, title, content_widget, closable=True, parent=None):
        super().__init__(parent)
        self.widget_id = widget_id
        self.closable = closable
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        
        if title or closable:
            self.title_bar = QWidget(self)
            self.title_bar.setFixedHeight(24)
            self.title_bar.setObjectName("TitleBar")
            self.title_bar.setStyleSheet("#TitleBar { background-color: rgba(255, 120, 0, 60); border-top-left-radius: 12px; border-top-right-radius: 12px; border-bottom: 1px solid rgba(255, 150, 0, 80); }")
            title_layout = QHBoxLayout(self.title_bar)
            title_layout.setContentsMargins(10, 0, 5, 0)
            
            if title:
                lbl = QLabel(title)
                lbl.setStyleSheet("color: rgba(255, 200, 0, 200); font-family: 'Courier New'; font-size: 9pt; font-weight: bold; background: transparent; border: none;")
                title_layout.addWidget(lbl)
            else:
                title_layout.addStretch()
                
            if closable:
                btn = QPushButton("X")
                btn.setFixedSize(18, 18)
                btn.setStyleSheet("""
                    QPushButton { background-color: transparent; color: rgba(255,150,0,180); font-weight: bold; border: none; font-size: 10pt; }
                    QPushButton:hover { color: #ff5500; }
                """)
                btn.clicked.connect(self.close_widget)
                title_layout.addWidget(btn)
                
            self.layout.addWidget(self.title_bar)
            
        self.content_widget = content_widget
        self.layout.addWidget(self.content_widget)
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            DraggableWidget {
                background-color: rgba(20, 10, 0, 180);
                border: 1px solid rgba(255, 120, 0, 100);
                border-radius: 12px;
            }
        """)
        
        self._dragging = False
        self._drag_start_pos = QPoint()

    def close_widget(self):
        if hasattr(self.parent(), 'close_draggable_widget'):
            self.parent().close_draggable_widget(self.widget_id)
        else:
            self.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_pos = event.pos()
            self.raise_()
        super().mousePressEvent(event)
        
    def mouseMoveEvent(self, event):
        if self._dragging:
            # Prevent dragging out of parent bounds
            new_pos = self.pos() + event.pos() - self._drag_start_pos
            if self.parent():
                new_pos.setX(max(0, min(new_pos.x(), self.parent().width() - self.width())))
                new_pos.setY(max(0, min(new_pos.y(), self.parent().height() - self.height())))
            self.move(new_pos)
            
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
        super().mouseReleaseEvent(event)

class MediaWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.layout.setSpacing(10)
        
        # Title and Artist
        self.title_lbl = QLabel("No Media Playing")
        self.title_lbl.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 11pt;")
        self.artist_lbl = QLabel("Unknown Artist")
        self.artist_lbl.setStyleSheet("color: rgba(255, 170, 0, 180); font-size: 9pt;")
        
        self.layout.addWidget(self.title_lbl)
        self.layout.addWidget(self.artist_lbl)
        
        # Time and Progress Bar container
        time_layout = QHBoxLayout()
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setStyleSheet("color: rgba(255, 170, 0, 200); font-size: 8pt;")
        time_layout.addWidget(self.time_lbl)
        time_layout.addStretch()
        self.layout.addLayout(time_layout)
        
        # Controls
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(15)
        
        btn_style = "QPushButton { background-color: rgba(255, 150, 0, 20); color: #ffaa00; border-radius: 15px; border: 1px solid rgba(255,150,0,80); font-size: 12pt; } QPushButton:hover { background-color: rgba(255, 150, 0, 60); }"
        
        self.prev_btn = QPushButton("⏮")
        self.prev_btn.setFixedSize(30, 30)
        self.prev_btn.setStyleSheet(btn_style)
        self.prev_btn.clicked.connect(lambda: self.send_cmd("prev", silent=True))
        
        # --- FIX: Re-added the button creation lines ---
        self.play_btn = QPushButton("⏯")
        self.play_btn.setFixedSize(30, 30)
        self.play_btn.setStyleSheet(btn_style)
        self.play_btn.clicked.connect(self.toggle_optimistic)
        
        self.next_btn = QPushButton("⏭")
        self.next_btn.setFixedSize(30, 30)
        self.next_btn.setStyleSheet(btn_style)
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
        
        # --- NEW: Tracks seconds for automatic polling ---
        self.poll_counter = 0  
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        
    def toggle_optimistic(self):
        # Instantly flip the UI state for immediate visual feedback
        self.status = "Paused" if self.status == "Playing" else "Playing"
        self.play_btn.setText("⏸" if self.status == "Playing" else "▶")
        self.send_cmd("toggle", silent=True)

    def _tick(self):
        if self.status == "Playing" and self.duration > 0:
            self.position += 1.0
            if self.position > self.duration:
                self.position = self.duration
            self._update_time_label()
            
        # --- NEW: Automatic Polling Loop (Conditional) ---
        self.poll_counter += 1
        if self.poll_counter >= 8:  # Fetch fresh data every 8 seconds
            self.poll_counter = 0
            
            # Check if the widget is actually visible on screen
            if self.isVisible():
                # Check if the main window (JarvisUI) is in fullscreen mode
                main_window = self.window()
                if getattr(main_window, 'is_fullscreen', False):
                    self.send_cmd("status", silent=True)

    def _update_time_label(self):
        def fmt_time(secs):
            m = int(secs // 60)
            s = int(secs % 60)
            return f"{m}:{s:02d}"
        self.time_lbl.setText(f"{fmt_time(self.position)} / {fmt_time(self.duration)}")
        
    def send_cmd(self, action, silent=False):
        import json
        import paho.mqtt.publish as publish
        try:
            publish.single("pc/spotify/control", json.dumps({"action": action, "silent": silent}), hostname="localhost", qos=0)
        except Exception as e:
            print(f"Failed to publish media control: {e}")

    def update_status(self, data):
        title = data.get("title", "Unknown")
        artist = data.get("artist", "Unknown")
        self.position = data.get("position", 0.0)
        self.duration = data.get("duration", 0.0)
        self.status = data.get("status", "Paused")
        
        self.title_lbl.setText(title[:30] + ("..." if len(title) > 30 else ""))
        self.artist_lbl.setText(artist[:30] + ("..." if len(artist) > 30 else ""))
        
        self._update_time_label()
        self.play_btn.setText("⏸" if self.status == "Playing" else "▶")

class LightControlWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.layout.setSpacing(8)
        self.setMinimumWidth(340)
        
        # Title and Refresh
        top_layout = QHBoxLayout()
        title_lbl = QLabel("Smart Lights")
        title_lbl.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 14pt;")
        
        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedSize(30, 30)
        refresh_btn.setStyleSheet("QPushButton { background-color: rgba(255, 150, 0, 20); color: #ffaa00; border-radius: 15px; border: 1px solid rgba(255,150,0,80); font-size: 14pt; } QPushButton:hover { background-color: rgba(255, 150, 0, 60); }")
        refresh_btn.clicked.connect(lambda: self.send_cmd("refresh_lights", "all"))
        
        top_layout.addWidget(title_lbl)
        top_layout.addStretch()
        top_layout.addWidget(refresh_btn)
        self.layout.addLayout(top_layout)
        
        # Lights container
        self.lights_container = QWidget()
        self.lights_layout = QVBoxLayout(self.lights_container)
        self.lights_layout.setContentsMargins(0, 0, 0, 0)
        self.lights_layout.setSpacing(5)
        
        # Loading placeholder shown until first real data arrives
        self._loading_lbl = QLabel("⏳ Loading lights...")
        self._loading_lbl.setStyleSheet("color: rgba(255, 170, 0, 140); font-style: italic; font-size: 10pt;")
        self.lights_layout.addWidget(self._loading_lbl)
        
        self.layout.addWidget(self.lights_container)
        
        # All off button
        btn = QPushButton("Toggle All Off")
        btn.setStyleSheet("QPushButton { background-color: rgba(255, 50, 0, 40); color: #ffaa00; border-radius: 5px; padding: 5px; border: 1px solid rgba(255,100,0,80); } QPushButton:hover { background-color: rgba(255, 50, 0, 80); }")
        btn.clicked.connect(lambda: self.send_cmd("off", "all", silent=True))
        self.layout.addWidget(btn)
        
        self.light_rows = {}

    def send_cmd(self, action, target, silent=False):
        import paho.mqtt.publish as publish
        try:
            publish.single("home/room/all/set", json.dumps({"action": action, "light_target": target, "silent": silent}), hostname="localhost", qos=0)
        except Exception as e:
            print(f"Failed to publish light control: {e}")

    def _delete_light(self, target_name):
        import paho.mqtt.publish as publish
        try:
            publish.single("home/room/all/set", json.dumps({"action": "intent_remove_light", "target_str": target_name}), hostname="localhost", qos=0)
        except Exception as e:
            print(f"Failed to publish delete: {e}")
        # Remove from UI immediately
        if target_name in self.light_rows:
            row_data = self.light_rows.pop(target_name)
            row_data["widget"].deleteLater()
            self.lights_container.adjustSize()
            self.adjustSize()

    def handle_feedback(self, data):
        if data.get("status") == "success" and data.get("action_cmd") == "toggle":
            target = data.get("target", "")
            if target in self.light_rows:
                row_data = self.light_rows[target]
                current_is_on = row_data["is_on"]
                new_state = not current_is_on
                row_data["is_on"] = new_state
                self._update_indicator(row_data["indicator"], new_state, False)

    def _update_indicator(self, indicator, is_on, is_offline):
        if is_offline:
            color = "rgba(120, 120, 120, 255)"
        elif is_on:
            color = "rgba(50, 255, 50, 255)"
        else:
            color = "rgba(255, 50, 50, 255)"
        indicator.setStyleSheet(f"color: {color}; font-size: 16pt;")

    def update_status(self, data):
        lights = data.get("lights", [])
        
        # Remove loading placeholder once real data arrives
        if self._loading_lbl is not None:
            self._loading_lbl.deleteLater()
            self._loading_lbl = None
        
        if not lights:
            if not self.light_rows:
                lbl = QLabel("No lights configured.")
                lbl.setStyleSheet("color: rgba(150, 150, 150, 255); font-style: italic;")
                self.lights_layout.addWidget(lbl)
            return
            
        for l in lights:
            target_name = l.get("name", "").lower().replace(" ", "_")
            is_on = l.get("is_on", False)
            is_offline = l.get("offline", False)
            
            if target_name in self.light_rows:
                row_data = self.light_rows[target_name]
                row_data["is_on"] = is_on
                row_data["is_offline"] = is_offline
                self._update_indicator(row_data["indicator"], is_on, is_offline)
            else:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(6)
                
                indicator = QLabel("●")
                indicator.setStyleSheet("color: rgba(100, 100, 100, 255); font-size: 16pt;")
                indicator.setFixedWidth(22)
                self._update_indicator(indicator, is_on, is_offline)
                
                name_lbl = QLabel(l.get("name", "Unknown"))
                name_lbl.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 11pt;")
                
                btn_style = "QPushButton { background-color: rgba(255, 150, 0, 20); color: #ffaa00; border-radius: 5px; padding: 2px 8px; font-size: 10pt; border: 1px solid rgba(255,150,0,50); } QPushButton:hover { background-color: rgba(255, 150, 0, 60); }"
                
                toggle_btn = QPushButton("Toggle")
                toggle_btn.setMinimumHeight(24)
                toggle_btn.setStyleSheet(btn_style)
                toggle_btn.clicked.connect(lambda checked, t=target_name: self.send_cmd("toggle", t, silent=True))
                
                delete_btn = QPushButton("✕")
                delete_btn.setFixedSize(26, 24)
                delete_btn.setToolTip("Remove light")
                delete_btn.setStyleSheet("QPushButton { background-color: rgba(200, 50, 0, 30); color: rgba(255, 80, 60, 255); border-radius: 5px; border: 1px solid rgba(200,80,50,80); font-size: 11pt; font-weight: bold; font-family: monospace; } QPushButton:hover { background-color: rgba(200, 50, 0, 90); color: #ff4030; }")
                delete_btn.clicked.connect(lambda checked, t=target_name: self._delete_light(t))
                
                row_layout.addWidget(indicator)
                row_layout.addWidget(name_lbl)
                row_layout.addStretch()
                row_layout.addWidget(toggle_btn)
                row_layout.addWidget(delete_btn)
                
                self.lights_layout.addWidget(row)
                self.light_rows[target_name] = {
                    "widget": row,
                    "indicator": indicator,
                    "is_on": is_on,
                    "is_offline": is_offline
                }
            
        self.lights_container.adjustSize()
        self.adjustSize()
        # Deferred resize ensures the parent wrapper picks up the new layout geometry
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self.adjustSize)
        QTimer.singleShot(50, self.adjustSize)

class JarvisVisualizer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "IDLE"
        self.target_opacity = 0.0
        self.current_opacity = 0.0
        self.target_scale = 0.5
        self.current_scale = 0.5
        self.time_offset = 0.0
        self.options = []
        self.particles = []
        
        self.target_amplitude = 10
        self.amplitude = 10
        self.frequency = 0.05
        self.speed = 0.1
        self.is_fullscreen = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_volume(self, vol):
        max_amp = self.height() / 4.0 if self.height() > 100 else 150
        
        if self.state == "SPEAKING":
            # TTS vol is 0-100
            norm_vol = min(1.0, vol / 100.0) * 0.7
            self.target_amplitude = max(10, norm_vol * max_amp)
        elif self.state in ["RECORDING", "LISTENING", "ATTENTION"]:
            # Mic vol is raw RMS (usually 0 - 3000)
            norm_vol = min(1.0, vol / 3000.0) * 0.8
            self.target_amplitude = max(10, norm_vol * max_amp)
            if self.state == "RECORDING":
                self.target_opacity = min(1.0, max(0.6, norm_vol + 0.6))

    def set_state(self, state, is_fullscreen):
        self.state = state
        self.is_fullscreen = is_fullscreen
        if state == "IDLE":
            self.target_opacity = 0.8 if is_fullscreen else 0.0
            self.options = []
            self.target_scale = 0.8 if is_fullscreen else 0.5
            self.target_amplitude = 10
            self.frequency = 0.015
            self.speed = 0.02
        elif state == "SPEAKING":
            self.target_opacity = 1.0
            self.target_amplitude = 10
            self.frequency = 0.03
            self.speed = 0.3
            self.target_scale = 1.0
        elif state == "LISTENING":
            self.target_opacity = 1.0
            self.target_amplitude = 15
            self.frequency = 0.015
            self.speed = 0.05
            self.target_scale = 1.0
        elif state == "ATTENTION":
            self.target_opacity = 0.3
            self.target_amplitude = 15
            self.frequency = 0.015
            self.speed = 0.05
            self.target_scale = 0.5
        elif state == "RECORDING":
            self.target_opacity = 0.8
            self.target_amplitude = 20
            self.frequency = 0.02
            self.speed = 0.1
            self.target_scale = 1.0
        elif state == "PROCESSING":
            self.target_opacity = 1.0
            self.target_amplitude = 25
            self.frequency = 0.08
            self.speed = 0.15
            self.target_scale = 0.5

    def set_options(self, options):
        self.options = options
        if options:
            self.target_scale = 1.0
        self.target_opacity = 1.0

    def update_animation(self):
        if abs(self.current_opacity - self.target_opacity) > 0.01:
            self.current_opacity += (self.target_opacity - self.current_opacity) * 0.1
        else:
            self.current_opacity = self.target_opacity

        self.amplitude += (self.target_amplitude - self.amplitude) * 0.4
            
        if abs(self.current_scale - self.target_scale) > 0.01:
            self.current_scale += (self.target_scale - self.current_scale) * 0.15
        else:
            self.current_scale = self.target_scale
            
        self.time_offset += self.speed
        
        if self.target_opacity > 0 and self.amplitude > 5:
            if random.random() < (self.amplitude / 30.0):
                self.particles.append({
                    "x": random.randint(0, self.width()),
                    "y_offset": random.uniform(-self.amplitude, self.amplitude),
                    "speed_x": random.uniform(-2, 2),
                    "speed_y": random.uniform(-1, 1),
                    "life": 1.0,
                    "size": random.randint(1, 3)
                })
                
        for p in self.particles:
            p["x"] += p["speed_x"]
            p["y_offset"] += p["speed_y"]
            p["life"] -= 0.03
            
        self.particles = [p for p in self.particles if p["life"] > 0]
        self.update()

    def paintEvent(self, event):
        if self.width() == 0 or self.height() == 0:
            return
            
        if self.current_opacity <= 0.01 and not self.is_fullscreen:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width() / 2
        cy = self.height() / 2 if self.is_fullscreen else self.height() - 100

        painter.save()
        painter.translate(cx, cy)
        painter.scale(self.current_scale, self.current_scale)
        painter.translate(-cx, -cy)
        
        painter.setOpacity(self.current_opacity)
        
        gradient = QRadialGradient(cx, cy, 100)
        gradient.setColorAt(0.0, QColor(255, 150, 0, 60))
        gradient.setColorAt(0.5, QColor(255, 100, 0, 30))
        gradient.setColorAt(1.0, QColor(200, 50, 0, 0))
        
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(int(cx - 500), int(cy - 500), 1000, 1000)
        
        painter.save()
        painter.translate(cx, cy)
        
        painter.save()
        painter.rotate(self.time_offset * 15)
        pen_ring1 = QPen(QColor(255, 180, 0, int(60 * self.current_opacity)))
        pen_ring1.setWidth(2)
        pen_ring1.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen_ring1)
        r1 = 80
        painter.drawEllipse(QPointF(0, 0), r1, r1)
        painter.restore()
        
        painter.save()
        painter.rotate(-self.time_offset * 25)
        pen_ring2 = QPen(QColor(255, 120, 0, int(90 * self.current_opacity)))
        pen_ring2.setWidth(1)
        pen_ring2.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(pen_ring2)
        r2 = 50
        painter.drawEllipse(QPointF(0, 0), r2, r2)
        painter.restore()
        
        painter.restore()
        
        colors = [QColor(255, 120, 0, 100), QColor(255, 180, 0, 180), QColor(255, 230, 100, 255)]
        phases = [0, 2, 4]
        amplitudes = [self.amplitude, self.amplitude * 0.6, self.amplitude * 0.3]
        
        for i in range(3):
            path = QPainterPath()
            path.moveTo(0, cy)
            step = 6
            for x in range(0, self.width() + step, step):
                envelope = math.pow(math.sin(math.pi * x / self.width()), 3)
                y = cy + math.sin(x * self.frequency + self.time_offset + phases[i]) * amplitudes[i] * envelope
                path.lineTo(x, y)
                
            wave_grad = QLinearGradient(0, cy, self.width(), cy)
            base_color = colors[i]
            transparent_color = QColor(base_color.red(), base_color.green(), base_color.blue(), 0)
            
            wave_grad.setColorAt(0.0, transparent_color)
            wave_grad.setColorAt(0.20, base_color)
            wave_grad.setColorAt(0.80, base_color)
            wave_grad.setColorAt(1.0, transparent_color)
            
            pen = QPen(QBrush(wave_grad), 2 if i < 2 else 1)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            
        painter.setPen(Qt.PenStyle.NoPen)
        for p in self.particles:
            envelope = math.sin(math.pi * p["x"] / self.width())
            base_y = cy + math.sin(p["x"] * self.frequency + self.time_offset) * self.amplitude * envelope
            y = base_y + p["y_offset"]
            alpha = int(255 * p["life"] * self.current_opacity * envelope)
            if alpha > 0:
                painter.setBrush(QColor(255, 200, 50, alpha))
                painter.drawEllipse(int(p["x"]), int(y), p["size"], p["size"])
                
        painter.restore()
        
        if ECOSYSTEM_STATE == "debug":
            painter.save()
            painter.setPen(QColor(255, 255, 255, 150))
            painter.drawText(10, 20, f"State: {self.state}")
            painter.restore()

class JarvisUI(QWidget):
    def __init__(self):
        super().__init__()
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool
        )
        
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        
        primary_screen = QApplication.primaryScreen()
        screen_geom = primary_screen.availableGeometry()
        
        width, height = 200, 400
        x_pos = screen_geom.right() - width - 20
        y_pos = screen_geom.bottom() - height - 20
        
        self.setGeometry(x_pos, y_pos, width, height)
        self.setFixedSize(width, height)
        
        self.is_fullscreen = False
        self.state = "IDLE"
        
        # Dashboard Management
        self.active_widgets = {}
        
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(1000 // 60)
        
        self.pending_options = None
        self.options_debounce_timer = QTimer(self)
        self.options_debounce_timer.setSingleShot(True)
        self.options_debounce_timer.timeout.connect(self._apply_pending_options)
        
        # Core Visualizer is now permanently attached to the background
        self.visualizer = JarvisVisualizer(self)
        self.visualizer.resize(self.size())
        self.visualizer.show()
        
        # Text Input Workaround
        self.text_input = QLineEdit(self)
        self.text_input.setWindowFlags(
            Qt.WindowType.Window | 
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        

        self.text_input.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.text_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(20, 10, 0, 150);
                color: rgba(255, 200, 0, 180);
                border: 1px solid rgba(255, 180, 0, 50);
                border-radius: 8px;
                font-family: 'Courier New';
                font-size: 10pt;
                padding: 5px;
            }
        """)
        self.text_input.returnPressed.connect(self.submit_text_command)
        self.text_input.hide()
        
        self.btn_media = QPushButton("🎵", self)
        self.btn_media.setStyleSheet("QPushButton { background-color: rgba(255, 120, 0, 40); color: #ffaa00; border-radius: 25px; font-size: 18pt; border: 1px solid rgba(255, 150, 0, 80); } QPushButton:hover { background-color: rgba(255, 120, 0, 80); }")
        self.btn_media.setFixedSize(50, 50)
        self.btn_media.clicked.connect(self._toggle_media)
        self.btn_media.hide()
        
        self.btn_lights = QPushButton("💡", self)
        self.btn_lights.setStyleSheet("QPushButton { background-color: rgba(255, 120, 0, 40); color: #ffaa00; border-radius: 25px; font-size: 18pt; border: 1px solid rgba(255, 150, 0, 80); } QPushButton:hover { background-color: rgba(255, 120, 0, 80); }")
        self.btn_lights.setFixedSize(50, 50)
        self.btn_lights.clicked.connect(self._toggle_lights)
        self.btn_lights.hide()
        
        QApplication.instance().applicationStateChanged.connect(self._on_app_state_changed)
        
        self.mqtt_thread = MqttThread()
        self.mqtt_thread.state_signal.connect(self.set_state)
        self.mqtt_thread.options_signal.connect(self.set_options)
        self.mqtt_thread.vol_signal.connect(self.set_volume)
        self.mqtt_thread.state_change_signal.connect(self.update_ecosystem_state)
        self.mqtt_thread.ui_mode_signal.connect(self.set_ui_mode)
        self.mqtt_thread.media_status_signal.connect(self._handle_media_status)
        self.mqtt_thread.light_status_signal.connect(self._handle_light_status)
        self.mqtt_thread.feedback_signal.connect(self._handle_feedback)
        self.mqtt_thread.start()

    def _handle_feedback(self, data):
        device = data.get("device")
        if device == "smart_lights":
            widget_id = "widget_light_controls"
            if widget_id in self.active_widgets:
                wrapper = self.active_widgets[widget_id]
                if isinstance(wrapper.content_widget, LightControlWidget):
                    wrapper.content_widget.handle_feedback(data)
                if self.is_fullscreen:
                    wrapper.show()
                    wrapper.raise_()

    def _toggle_media(self):
        if getattr(self, 'is_fullscreen', False):
            widget_id = "widget_media_controls"
            if widget_id not in self.active_widgets:
                media_widget = MediaWidget()
                self.spawn_widget(widget_id, "Media Controls", media_widget)
                
                # Fetch instantly on spawn
                media_widget.send_cmd("status", silent=True) 
            else:
                w = self.active_widgets[widget_id]
                if w.isHidden():
                    w.show()
                    w.raise_()
                    
                    # Fetch instantly when brought back from hidden state
                    w.content_widget.send_cmd("status", silent=True)
                else:
                    self.close_draggable_widget(widget_id)

    def _toggle_lights(self):
        if getattr(self, 'is_fullscreen', False):
            widget_id = "widget_light_controls"
            if widget_id not in self.active_widgets:
                light_widget = LightControlWidget()
                self.spawn_widget(widget_id, "Smart Lights", light_widget)
                import paho.mqtt.publish as publish
                import json
                try:
                    publish.single("home/room/all/set", json.dumps({"action": "refresh_lights", "light_target": "all"}), hostname="localhost", qos=0)
                except:
                    pass
            else:
                w = self.active_widgets[widget_id]
                if w.isHidden():
                    w.show()
                    w.raise_()
                else:
                    self.close_draggable_widget(widget_id)

    def _handle_light_status(self, data):
        widget_id = "widget_light_controls"
        if widget_id in self.active_widgets:
            wrapper = self.active_widgets[widget_id]
            if isinstance(wrapper.content_widget, LightControlWidget):
                wrapper.content_widget.update_status(data)
                # Immediate + deferred resize so wrapper tracks new content height
                wrapper.adjustSize()
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, wrapper.adjustSize)
                QTimer.singleShot(60, wrapper.adjustSize)

    def _handle_media_status(self, data):
        widget_id = "widget_media_controls"
        if widget_id not in self.active_widgets:
            media_widget = MediaWidget()
            # Wrap and spawn it
            self.spawn_widget(widget_id, "Media Controls", media_widget)
        else:
            if self.is_fullscreen:
                self.active_widgets[widget_id].show()
                self.active_widgets[widget_id].raise_()
        # Update the widget contents
        wrapper = self.active_widgets[widget_id]
        if isinstance(wrapper.content_widget, MediaWidget):
            wrapper.content_widget.update_status(data)
            wrapper.adjustSize()

    def _on_app_state_changed(self, state):
        if not getattr(self, 'is_fullscreen', False) or getattr(self, 'text_input', None) is None:
            return
            
        if state != Qt.ApplicationState.ApplicationActive:
            self.text_input.hide()
        else:
            self.text_input.show()
            self.text_input.activateWindow()
            self.text_input.raise_()
            self.text_input.setFocus()

    def update_ecosystem_state(self, new_state):
        global ECOSYSTEM_STATE
        ECOSYSTEM_STATE = new_state
        self.update()
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'visualizer'):
            self.visualizer.resize(self.size())

    def set_volume(self, vol):
        self.visualizer.set_volume(vol)

    def set_state(self, state):
        self.state = state
        self.visualizer.set_state(state, self.is_fullscreen)
        if state == "IDLE":
            if not self.is_fullscreen:
                for w_id in list(self.active_widgets.keys()):
                    self.close_draggable_widget(w_id)
            else:
                pass # Keep logic consistent

    def set_options(self, options, title="Options"):
        self.pending_options = (options, title)
        self.options_debounce_timer.start(100)  # 100ms debounce

    def _apply_pending_options(self):
        if not self.pending_options:
            return
        options, title = self.pending_options
        self.pending_options = None
        
        self.visualizer.set_options(options)
        
        widget_id = f"list_{title.replace(' ', '_').lower()}"
        
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        
        from PyQt6.QtGui import QFont, QFontMetrics
        
        opt_font = QFont("Courier New", 10, QFont.Weight.Bold)
        fm = QFontMetrics(opt_font)
        
        title_font = QFont("Courier New", 9, QFont.Weight.Bold)
        title_fm = QFontMetrics(title_font)
        
        # Calculate target width based on title and options
        max_opt_width = max([fm.horizontalAdvance(opt) for opt in options] + [0]) if options else 0
        target_width = max(200, title_fm.horizontalAdvance(title) + 80, max_opt_width + 40)
        
        if not options:
            lbl = QLabel("List empty.")
            lbl.setFont(opt_font)
            lbl.setStyleSheet("color: rgba(255, 100, 100, 180);")
            layout.addWidget(lbl)
        else:
            for opt in reversed(options[:5]):
                truncated_opt = fm.elidedText(opt, Qt.TextElideMode.ElideRight, target_width - 20)
                lbl = QLabel(truncated_opt)
                lbl.setFont(opt_font)
                lbl.setStyleSheet("color: rgba(255, 200, 0, 180);")
                layout.addWidget(lbl)
            
        content.setStyleSheet("background-color: transparent;")
        
        if widget_id in self.active_widgets:
            # Update existing widget in-place without touching its position
            w = self.active_widgets[widget_id]
            w.layout.removeWidget(w.content_widget)
            w.content_widget.deleteLater()
            w.content_widget = content
            w.layout.addWidget(w.content_widget)
            w.content_widget.show()
            w.adjustSize()
        else:
            self.spawn_widget(widget_id, title, content)
            w = self.active_widgets[widget_id]
            
            if self.is_fullscreen:
                if hasattr(w, "title_bar"):
                    w.title_bar.show()
                w.adjustSize()
                # Offset position slightly based on number of active widgets to prevent complete overlap
                offset = len(self.active_widgets) * 30
                w.move(self.width() - w.width() - 100, 100 + offset)
            else:
                if hasattr(w, "title_bar"):
                    w.title_bar.hide()
                w.adjustSize()
                cx = (self.width() - w.width()) // 2
                cy = (self.height() // 2) - w.height() - 120
                w.move(cx, cy)

    def spawn_widget(self, widget_id, title, content_widget):
        """API to spawn or bring-to-front a dashboard widget"""
        if widget_id in self.active_widgets:
            w = self.active_widgets[widget_id]
            if self.is_fullscreen:
                w.show()
                w.raise_()
            return
            
        wrapper = DraggableWidget(widget_id, title, content_widget, closable=True, parent=self)
        
        # Position in center of screen by default
        cx = (self.width() - content_widget.sizeHint().width()) // 2
        cy = (self.height() - content_widget.sizeHint().height()) // 2
        wrapper.move(cx, cy)
        
        self.active_widgets[widget_id] = wrapper
        if self.is_fullscreen:
            if hasattr(wrapper, "title_bar"):
                wrapper.title_bar.show()
            wrapper.show()
            wrapper.raise_()
        else:
            if widget_id.startswith("list_"):
                if hasattr(wrapper, "title_bar"):
                    wrapper.title_bar.hide()
                wrapper.adjustSize()
                new_cx = (self.width() - wrapper.width()) // 2
                new_cy = (self.height() // 2) - wrapper.height() - 120
                wrapper.move(new_cx, new_cy)
                wrapper.show()
                wrapper.raise_()
            else:
                wrapper.hide()

    def close_draggable_widget(self, widget_id):
        if widget_id in self.active_widgets:
            w = self.active_widgets.pop(widget_id)
            w.deleteLater()

    def update_animation(self):
        self.visualizer.update_animation()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if getattr(self, 'is_fullscreen', False) and getattr(self, 'text_input', None) is not None:
            self.text_input.show()
            self.text_input.activateWindow()
            self.text_input.raise_()
            self.text_input.setFocus()

    def submit_text_command(self):
        if getattr(self, 'text_input', None) is None: return
        text = self.text_input.text().strip()
        if text:
            import paho.mqtt.publish as publish
            try:
                publish.single("jarvis/sensor/voice", text, hostname="localhost", qos=1)
            except Exception as e:
                print(f"Failed to publish text command: {e}")
            self.text_input.clear()
            self.text_input.setFocus()

    def set_ui_mode(self, mode):
        if mode == "set_fullscreen":
            self.is_fullscreen = True
            
            self.hide()
            QApplication.processEvents()
            
            self.setWindowFlags(
                Qt.WindowType.Window | 
                Qt.WindowType.FramelessWindowHint
            )
            
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.clearMask()
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            
            primary_screen = QApplication.primaryScreen()
            geom = primary_screen.geometry()
            
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            
            self.setGeometry(geom)
            
            # Reset visualizer to IDLE before transitioning — prevents stale RECORDING state
            # from a previous TTS+mic cycle being inherited by the fullscreen view
            self.state = "IDLE"
            self.visualizer.set_state("IDLE", True)
            
            self.btn_media.setGeometry(30, geom.height() - 80, 50, 50)
            self.btn_lights.setGeometry(90, geom.height() - 80, 50, 50)
            self.btn_media.show()
            self.btn_lights.show()
            
                
            # Show dashboard widgets
            for wid, w in self.active_widgets.items():
                if hasattr(w, "title_bar"):
                    w.title_bar.show()
                w.show()
            
            self.showFullScreen()
            self.raise_()
            self.activateWindow() 
            self.setFocus()
            
            box_width = min(800, geom.width() - 40)
            box_x = geom.x() + int((geom.width() - box_width) / 2)
            box_y = geom.y() + geom.height() - 50
            self.text_input.setGeometry(box_x, box_y, box_width, 30)
            
            if QApplication.applicationState() == Qt.ApplicationState.ApplicationActive:
                self._on_app_state_changed(Qt.ApplicationState.ApplicationActive)
            
        elif mode == "set_overlay":
            self.is_fullscreen = False
            
            self.hide()
            QApplication.processEvents()
            
            if getattr(self, 'text_input', None) is not None:
                self.text_input.hide()
                
            # Reset visualizer to IDLE on overlay transition
            self.state = "IDLE"
            self.visualizer.set_state("IDLE", False)
            
            self.btn_media.hide()
            self.btn_lights.hide()
                
            # Hide all dashboard widgets except options_list
            for wid, w in self.active_widgets.items():
                if wid != "options_list":
                    w.hide()
                else:
                    if hasattr(w, "title_bar"):
                        w.title_bar.hide()
                    w.adjustSize()
                    cx = (self.width() - w.width()) // 2
                    w.move(int(cx), 10)
            
            self.hide()
            
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | 
                Qt.WindowType.WindowStaysOnTopHint | 
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            
            primary_screen = QApplication.primaryScreen()
            screen_geom = primary_screen.availableGeometry()
            width, height = 200, 400
            x_pos = screen_geom.right() - width - 20
            y_pos = screen_geom.bottom() - height - 20
            
            self.setFixedSize(width, height)
            self.setGeometry(x_pos, y_pos, width, height)
            self.showNormal()

    def _generate_honeycomb(self, w, h):
        from PyQt6.QtGui import QPixmap, QPolygonF
        pix = QPixmap(w, h)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(30, 40, 50, 40), 1))
        
        r = 25
        dx = r * 1.5
        dy = r * math.sqrt(3)
        
        for row in range(-1, int(h / dy) + 2):
            for col in range(-1, int(w / dx) + 2):
                x = col * dx
                y = row * dy
                if col % 2 == 1:
                    y += dy / 2
                
                poly = QPolygonF()
                for i in range(6):
                    angle = i * math.pi / 3
                    px = x + r * math.cos(angle)
                    py = y + r * math.sin(angle)
                    poly.append(QPointF(px, py))
                p.drawPolygon(poly)
                
        p.end()
        return pix

    def paintEvent(self, event):
        if not self.is_fullscreen:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        cx = self.width() / 2
        cy = self.height() / 2
        
        # Invisible background to prevent alpha-click-passthrough on Linux (alpha=1 is enough to catch clicks)
        painter.setBrush(QColor(0, 0, 0, 240))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())
        
        if not hasattr(self, 'honeycomb_pixmap') or self.honeycomb_pixmap.width() != self.width():
            self.honeycomb_pixmap = self._generate_honeycomb(self.width(), self.height())
            
        painter.setOpacity(1.0)
        painter.drawPixmap(0, 0, self.honeycomb_pixmap)
        
        v_grad = QRadialGradient(cx, cy, max(self.width(), self.height()) / 1.5)
        v_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        v_grad.setColorAt(1.0, QColor(0, 0, 0, 220))
        painter.setBrush(v_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())
        if ECOSYSTEM_STATE == "debug":
            pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = JarvisUI()
    window.show()
    sys.exit(app.exec())
