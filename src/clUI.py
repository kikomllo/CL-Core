import os
import sys
import json
import math
import random
import paho.mqtt.client as mqtt
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPointF, QPoint, QFileSystemWatcher, QPropertyAnimation, QEasingCurve, QRect
from datetime import datetime
import paho.mqtt.publish as publish
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QRadialGradient, QBrush, QLinearGradient
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QRadialGradient, QBrush, QLinearGradient
from utils.clActionRouter import ActionRouter

from ui.clMediaWidget import MediaWidget
from ui.clLightControlWidget import LightControlWidget
from ui.clReminderWidget import ReminderWidget
from ui.clTodoWidget import TodoWidget
from ui.clDashboardDrawer import DashboardDrawer
from ui.clSettingsWidget import SettingsWidget
from ui.clUpdateWidget import UpdateWidget
from ui.clLogWidget import LogWidget

import platform
if platform.system() != "Windows":
    os.environ["QT_QPA_PLATFORM"] = "xcb"
else:
    import ctypes
    from ctypes import wintypes
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

import logging
from utils.clLogging import setup_logging
setup_logging('UI')
from utils.clConfigLoader import ConfigLoader
ECOSYSTEM_STATE = "normal"
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "core.json"), "r") as f:
        ECOSYSTEM_STATE = json.load(f).get("settings", {}).get("ecosystem_state", "normal")
except Exception:
    pass

STATE_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "ui_state.json"))

class MqttThread(QThread):
    state_signal = pyqtSignal(str)
    options_signal = pyqtSignal(list, str)
    vol_signal = pyqtSignal(float)
    state_change_signal = pyqtSignal(str)
    ui_mode_signal = pyqtSignal(str)
    media_status_signal = pyqtSignal(dict)
    light_status_signal = pyqtSignal(dict)
    feedback_signal = pyqtSignal(dict)
    todo_status_signal = pyqtSignal(dict)
    calendar_status_signal = pyqtSignal(dict)
    
    def __init__(self, mode="overlay"):
        super().__init__()
        self.router = ActionRouter()
        self.mode = mode
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
                logging.error(f"MQTT Error: {e}. Reconnecting in {delay}s...")
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
        client.subscribe("jarvis/sys/todo/status")
        client.subscribe("jarvis/sys/calendar/status")
        client.publish("jarvis/sys/module_ready", json.dumps({"module": "ui"}), retain=False)
        
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
            "jarvis/feedback": self._handle_feedback,
            "jarvis/sys/todo/status": self._handle_todo_status,
            "jarvis/sys/calendar/status": self._handle_calendar_status
        }
        
        handler = handlers.get(topic)
        if handler:
            handler(payload)

    def _handle_todo_status(self, payload):
        if isinstance(payload, dict):
            self.todo_status_signal.emit(payload)

    def _handle_calendar_status(self, payload):
        if isinstance(payload, dict):
            self.calendar_status_signal.emit(payload)

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
            elif state == "processing":
                self.processing_active = True
                self.mic_active = "IDLE"
            elif state == "idle":
                self.mic_active = "IDLE"
                self.processing_active = False
                
            self.evaluate_state()

    def _handle_processing(self, payload):
        if isinstance(payload, dict):
            state = payload.get("state", "active")
            if state == "active":
                self.processing_active = True
                self.mic_active = "IDLE"
            elif state == "idle":
                self.processing_active = False
                if self.mic_active == "LISTENING" and not self.attention_active:
                    self.mic_active = "IDLE"
        else:
            self.processing_active = False
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
                try:
                    def update_cb(core):
                        if "settings" not in core: core["settings"] = {}
                        if "ecosystem" not in core: core["ecosystem"] = {}
                        core["settings"]["ecosystem_state"] = new_state.lower()
                        core["ecosystem"]["mode"] = new_state.upper()
                    ConfigLoader().update_json_atomic("core.json", update_cb)
                except Exception as e:
                    logging.error(f"Failed to persist state_change to core.json: {e}")

class DraggableWidget(QWidget):
    def __init__(self, widget_id, title, content_widget, closable=True, parent=None):
        super().__init__(parent)
        self.widget_id = widget_id
        self.closable = closable
        
        print(f"[DEBUG DraggableWidget] widget_id={widget_id}, title={repr(title)}, closable={closable}")
        
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
        
        # Enforce minimum size based on content to prevent squashing and clipping
        min_w = self.content_widget.minimumWidth()
        min_h = self.content_widget.minimumHeight() + (24 if title or closable else 0)
        self.setMinimumSize(max(150, min_w), max(50, min_h))
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            DraggableWidget {
                background-color: rgba(20, 10, 0, 180);
                border: 1px solid rgba(255, 120, 0, 100);
                border-radius: 12px;
            }
        """)
        
        self._dragging = False
        self._resizing = False
        self._drag_start_pos = QPoint()
        self._resize_start_size = self.size()

    def close_widget(self):
        if hasattr(self.parent(), 'close_draggable_widget'):
            self.parent().close_draggable_widget(self.widget_id)
        else:
            self.hide()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.setPen(QPen(QColor(255, 120, 0, 180), 3))
        x, y = rect.width(), rect.height()
        painter.drawLine(x - 14, y - 4, x - 4, y - 14)
        painter.drawLine(x - 9, y - 4, x - 4, y - 9)
        painter.drawLine(x - 4, y - 4, x - 4, y - 4)

    def mousePressEvent(self, event):
        rect = self.rect()
        bottom_right = QRect(rect.width() - 20, rect.height() - 20, 20, 20)
        
        if event.button() == Qt.MouseButton.LeftButton:
            if bottom_right.contains(event.pos()):
                self._resizing = True
                self._resize_start_global = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
            else:
                self._dragging = True
                self._drag_start_global = event.globalPosition().toPoint()
                self._drag_start_pos = self.pos()
                if hasattr(self.content_widget, "on_drag_start"):
                    self.content_widget.on_drag_start()
            self.raise_()
        super().mousePressEvent(event)
        
    def mouseMoveEvent(self, event):
        if hasattr(self, '_resizing') and self._resizing:
            delta = event.globalPosition().toPoint() - self._resize_start_global
            min_size = self.layout.minimumSize()
            new_w = max(min_size.width(), self._resize_start_size.width() + delta.x())
            new_h = max(min_size.height(), self._resize_start_size.height() + delta.y())
            self.resize(new_w, new_h)
        elif self._dragging:
            delta = event.globalPosition().toPoint() - self._drag_start_global
            new_pos = self._drag_start_pos + delta
            if self.parent():
                new_pos.setX(max(0, min(new_pos.x(), self.parent().width() - self.width())))
                new_pos.setY(max(0, min(new_pos.y(), self.parent().height() - self.height())))
            self.move(new_pos)
            
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._resizing = False
            if hasattr(self.parent(), 'save_ui_state'):
                self.parent().save_ui_state()
        super().mouseReleaseEvent(event)

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
        if self.current_opacity <= 0.01 and self.target_opacity <= 0.01 and not self.is_fullscreen and not self.particles:
            return

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
        self.router = ActionRouter()
        
        flags = (
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool
        )
        if sys.platform != "win32":
            flags |= Qt.WindowType.WindowTransparentForInput
            
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if sys.platform != "win32":
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
        
        self.occlusion_timer = QTimer(self)
        self.occlusion_timer.timeout.connect(self._check_occlusion)
        self.occlusion_timer.start(250)
        self._last_fg_hwnd = 0
        
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
        
        btn_style = """
            QPushButton {
                background-color: rgba(20, 10, 0, 180);
                color: #ffaa00;
                border-radius: 8px;
                font-size: 9pt;
                font-weight: bold;
                font-family: 'Courier New';
                border: 1px solid rgba(255, 150, 0, 80);
            }
            QPushButton:hover {
                background-color: rgba(255, 120, 0, 80);
            }
        """

        self.btn_media = QPushButton("MUSIC", self)
        self.btn_media.setStyleSheet(btn_style)
        self.btn_media.setFixedSize(100, 35)
        self.btn_media.clicked.connect(self._toggle_media)
        self.btn_media.hide()
        
        self.btn_lights = QPushButton("LIGHTS", self)
        self.btn_lights.setStyleSheet(btn_style)
        self.btn_lights.setFixedSize(100, 35)
        self.btn_lights.clicked.connect(self._toggle_lights)
        self.btn_lights.hide()
        
        self.btn_reminders = QPushButton("REMINDERS", self)
        self.btn_reminders.setStyleSheet(btn_style)
        self.btn_reminders.setFixedSize(100, 35)
        self.btn_reminders.clicked.connect(self._toggle_reminders)
        self.btn_reminders.hide()
        
        self.btn_todos = QPushButton("TODOS", self)
        self.btn_todos.setStyleSheet(btn_style)
        self.btn_todos.setFixedSize(100, 35)
        self.btn_todos.clicked.connect(self._toggle_todos)
        self.btn_todos.hide()
        
        self.btn_settings = QPushButton("SETTINGS", self)
        self.btn_settings.setStyleSheet(btn_style)
        self.btn_settings.setFixedSize(100, 35)
        self.btn_settings.clicked.connect(self._toggle_settings)
        self.btn_settings.hide()
        
        self.btn_updates = QPushButton("UPDATES", self)
        self.btn_updates.setStyleSheet(btn_style)
        self.btn_updates.setFixedSize(100, 35)
        self.btn_updates.clicked.connect(self._toggle_updates)
        self.btn_updates.hide()
        
        self.btn_debug = QPushButton("DEBUG", self)
        self.btn_debug.setStyleSheet(btn_style)
        self.btn_debug.setFixedSize(100, 35)
        self.btn_debug.clicked.connect(self._toggle_debug)
        self.btn_debug.hide()
        
        self.btn_calendar = QPushButton("❮", self)
        self.btn_calendar.setStyleSheet("QPushButton { background-color: rgba(255, 150, 0, 40); color: #ffaa00; border-radius: 5px; font-weight: bold; border: 1px solid rgba(255, 150, 0, 80); } QPushButton:hover { background-color: rgba(255, 150, 0, 80); }")
        self.btn_calendar.setFixedSize(30, 80)
        self.btn_calendar.clicked.connect(self._toggle_calendar)
        self.btn_calendar.hide()
        
        # Persistent Dashboard Drawer (hidden off-screen right initially)
        drawer_width = 380
        self.calendar_drawer = DashboardDrawer(self)
        self.calendar_drawer.setGeometry(screen_geom.width(), 0, drawer_width, screen_geom.height())
        self.calendar_drawer.hide()
        self.calendar_is_open = False
        
        self.reminder_widget = ReminderWidget(self)
        self.reminder_widget.hide()

        
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
        self.mqtt_thread.todo_status_signal.connect(self._handle_todo_status)
        self.mqtt_thread.calendar_status_signal.connect(self._handle_calendar_data)
        self.mqtt_thread.start()

    def _check_occlusion(self):
        if not getattr(self, 'is_fullscreen', False):
            return
            
        import time
        if time.time() < getattr(self, '_occlusion_disabled_until', 0):
            return
            
        if sys.platform == "win32":
            import ctypes
            user32 = ctypes.windll.user32
            
            hwnd = user32.GetForegroundWindow()
            if hwnd == 0:
                return
                
            if hwnd == int(self.winId()):
                self._last_fg_hwnd = hwnd
                return
                
            if hwnd == getattr(self, '_last_fg_hwnd', 0):
                return
                
            from ctypes.wintypes import RECT
            rect = RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                from PyQt6.QtCore import QRect
                fg_rect = QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                # Windows 10/11 maximized windows bleed ~8 pixels into adjacent monitors to hide borders.
                # We shrink the foreground rect by 15 pixels to prevent cross-monitor false positives.
                shrunk_rect = fg_rect.adjusted(15, 15, -15, -15)
                if self.geometry().intersects(shrunk_rect):
                    self._last_fg_hwnd = hwnd
                    self.set_ui_mode("set_overlay")
                else:
                    self._last_fg_hwnd = hwnd
        else:
            # On Linux/Wayland, activeWindow() can be None just because the compositor denied 
            # initial focus. We rely on the user explicitly closing the dashboard via keybinds 
            # (abort or ui_overlay) rather than auto-collapsing.
            pass

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
                
            else:
                w = self.active_widgets[widget_id]
                if w.isHidden():
                    w.show()
                    w.raise_()
                else:
                    self.close_draggable_widget(widget_id)

    def _toggle_lights(self):
        if getattr(self, 'is_fullscreen', False):
            widget_id = "widget_light_controls"
            if widget_id not in self.active_widgets:
                light_widget = LightControlWidget()
                self.spawn_widget(widget_id, "Smart Lights", light_widget)
                self.router.dispatch("light.set", action="refresh_lights", light_target="all")
            else:
                w = self.active_widgets[widget_id]
                if w.isHidden():
                    w.show()
                    w.raise_()
                else:
                    self.close_draggable_widget(widget_id)

    def _toggle_reminders(self):
        if hasattr(self, 'reminder_widget'):
            if self.reminder_widget.isVisible():
                self.reminder_widget.hide()
            else:
                self.reminder_widget.show()
                self.reminder_widget.raise_()
            self.save_ui_state()

    def _toggle_todos(self):
        if getattr(self, 'is_fullscreen', False):
            widget_id = "widget_todo_list"
            if widget_id not in self.active_widgets:
                todo_widget = TodoWidget()
                self.spawn_widget(widget_id, "To-Do List", todo_widget)
            else:
                w = self.active_widgets[widget_id]
                if w.isHidden():
                    w.show()
                    w.raise_()
                    self.save_ui_state()
                else:
                    self.close_draggable_widget(widget_id)

    def _toggle_settings(self):
        if getattr(self, 'is_fullscreen', False):
            widget_id = "widget_settings"
            if widget_id not in self.active_widgets:
                settings_widget = SettingsWidget()
                self.spawn_widget(widget_id, "System Settings", settings_widget)
            else:
                w = self.active_widgets[widget_id]
                if w.isHidden():
                    w.show()
                    w.raise_()
                    self.save_ui_state()
                else:
                    self.close_draggable_widget(widget_id)

    def _toggle_updates(self):
        if getattr(self, 'is_fullscreen', False):
            widget_id = "widget_updates"
            if widget_id not in self.active_widgets:
                update_widget = UpdateWidget()
                self.spawn_widget(widget_id, "System Updates", update_widget)
            else:
                w = self.active_widgets[widget_id]
                if w.isHidden():
                    w.show()
                    w.raise_()
                    self.save_ui_state()
                else:
                    self.close_draggable_widget(widget_id)

    def _toggle_debug(self):
        widget_id = "widget_debug_logs"
        if widget_id not in self.active_widgets:
            parent = self if getattr(self, 'is_fullscreen', False) else None
            log_widget = LogWidget(parent)
            self.spawn_widget(widget_id, "Live Logs", log_widget)
        else:
            w = self.active_widgets[widget_id]
            if w.isHidden():
                w.show()
                w.raise_()
                self.save_ui_state()
            else:
                self.close_draggable_widget(widget_id)

    def _toggle_calendar(self):
        if not getattr(self, 'is_fullscreen', False):
            return
            
        geom = self.geometry()
        drawer_width = 380
        
        # Stop existing animation
        if hasattr(self, 'calendar_animation') and self.calendar_animation.state() == QPropertyAnimation.State.Running:
            self.calendar_animation.stop()
            
        self.calendar_animation = QPropertyAnimation(self.calendar_drawer, b"geometry")
        self.calendar_animation.setDuration(400)
        self.calendar_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        btn_animation = QPropertyAnimation(self.btn_calendar, b"geometry")
        btn_animation.setDuration(400)
        btn_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        if not self.calendar_is_open:
            # Open drawer
            self.calendar_drawer.setGeometry(geom.width(), 0, drawer_width, geom.height())
            self.calendar_drawer.show()
            self.calendar_drawer.raise_()
            
            self.calendar_animation.setEndValue(QRect(geom.width() - drawer_width, 0, drawer_width, geom.height()))
            
            # Move button
            current_btn_geom = self.btn_calendar.geometry()
            btn_animation.setEndValue(QRect(geom.width() - drawer_width - 30, current_btn_geom.y(), 30, 80))
            self.btn_calendar.setText("❯")
            
            self.calendar_is_open = True
            
            try:
                self.router.dispatch("calendar.read")
            except: pass
        else:
            # Close drawer
            self.calendar_animation.setEndValue(QRect(geom.width(), 0, drawer_width, geom.height()))
            
            current_btn_geom = self.btn_calendar.geometry()
            btn_animation.setEndValue(QRect(geom.width() - 30, current_btn_geom.y(), 30, 80))
            self.btn_calendar.setText("❮")
            
            self.calendar_is_open = False
            
        self.calendar_animation.start()
        btn_animation.start()
        self.save_ui_state()
        
        # Keep reference to avoid garbage collection
        self._btn_anim = btn_animation

    def _handle_todo_status(self, data):
        widget_id = "widget_todo_list"
        if widget_id in self.active_widgets:
            wrapper = self.active_widgets[widget_id]
            if isinstance(wrapper.content_widget, TodoWidget):
                wrapper.content_widget.update_status(data)
                
        if hasattr(self, 'calendar_drawer'):
            self.calendar_drawer.carousel.todo_widget.update_status(data)

    def _handle_calendar_data(self, data):
        if hasattr(self, 'calendar_drawer'):
            self.calendar_drawer.calendar.load_events(data)
            self.calendar_drawer.up_next.load_events(data)

    def _handle_light_status(self, data):
        widget_id = "widget_light_controls"
        if widget_id in self.active_widgets:
            wrapper = self.active_widgets[widget_id]
            if isinstance(wrapper.content_widget, LightControlWidget):
                wrapper.content_widget.update_status(data)
        if hasattr(self, 'calendar_drawer'):
            self.calendar_drawer.carousel.lights_widget.update_status(data)

    def _handle_media_status(self, data):
        widget_id = "widget_media_controls"
        if widget_id in self.active_widgets:
            wrapper = self.active_widgets[widget_id]
            if isinstance(wrapper.content_widget, MediaWidget):
                wrapper.content_widget.update_status(data)
        if hasattr(self, 'calendar_drawer'):
            self.calendar_drawer.carousel.media_widget.update_status(data)

    def _on_app_state_changed(self, state):
        if not getattr(self, 'is_fullscreen', False) or getattr(self, 'text_input', None) is None:
            return
            
        if state != Qt.ApplicationState.ApplicationActive:
            # On Wayland, the app may frequently be marked as Inactive due to focus stealing prevention.
            # We must NOT hide the text inputs here, otherwise the user can't use the dashboard if Wayland denies focus.
            pass
                    
            if sys.platform == "win32":
                import ctypes
                from PyQt6.QtCore import QRect
                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                if hwnd != 0 and hwnd != int(self.winId()):
                    class RECT(ctypes.Structure):
                        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                    rect = RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        fg_rect = QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                        shrunk_rect = fg_rect.adjusted(15, 15, -15, -15)
                        if self.geometry().intersects(shrunk_rect):
                            self._last_fg_hwnd = hwnd
                            self.set_ui_mode("set_overlay")
        else:
            self.text_input.show()
            self.text_input.activateWindow()
            self.text_input.raise_()
            self.text_input.setFocus()

    def update_ecosystem_state(self, new_state):
        global ECOSYSTEM_STATE
        ECOSYSTEM_STATE = new_state
        if getattr(self, 'is_fullscreen', False) and hasattr(self, 'btn_debug'):
            if ECOSYSTEM_STATE == "debug":
                self.btn_debug.show()
            else:
                self.btn_debug.hide()
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

    def spawn_widget(self, widget_id, title, content_widget, closable=True):
        """API to spawn or bring-to-front a dashboard widget"""
        print(f"[DEBUG spawn_widget] widget_id={widget_id}, title={repr(title)}, closable={closable}")
        if widget_id in self.active_widgets:
            w = self.active_widgets[widget_id]
            if self.is_fullscreen:
                w.show()
                w.raise_()
            return
        is_standalone = not self.is_fullscreen
        parent = None if is_standalone else self
        wrapper = DraggableWidget(widget_id, title, content_widget, closable=closable, parent=parent)
        
        # Position in center of screen by default
        if is_standalone:
            wrapper.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
            wrapper.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            
            screen_geom = self.screen().geometry()
            cx = screen_geom.x() + (screen_geom.width() - content_widget.sizeHint().width()) // 2
            cy = screen_geom.y() + (screen_geom.height() - content_widget.sizeHint().height()) // 2
            wrapper.move(cx, cy)
        else:
            cx = (self.width() - content_widget.sizeHint().width()) // 2
            cy = (self.height() - content_widget.sizeHint().height()) // 2
            wrapper.move(cx, cy)
        
        self.active_widgets[widget_id] = wrapper
        self.save_ui_state()
        if self.is_fullscreen:
            if hasattr(wrapper, "title_bar"):
                wrapper.title_bar.show()
            wrapper.show()
            wrapper.raise_()
        else:
            if widget_id.startswith("list_") or widget_id == "widget_debug_logs":
                if hasattr(wrapper, "title_bar") and widget_id.startswith("list_"):
                    wrapper.title_bar.hide()
                wrapper.adjustSize()
                if widget_id.startswith("list_"):
                    new_cx = (self.width() - wrapper.width()) // 2
                    new_cy = (self.height() // 2) - wrapper.height() - 120
                    wrapper.move(new_cx, new_cy)
                wrapper.show()
                wrapper.raise_()
            else:
                wrapper.hide()

    def close_draggable_widget(self, widget_id):
        if widget_id in self.active_widgets:
            w = self.active_widgets[widget_id]
            w.hide()
            self.save_ui_state()

    def update_animation(self):
        self.visualizer.update_animation()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
    def submit_text_command(self):
        if getattr(self, 'text_input', None) is None: return
        text = self.text_input.text().strip()
        if text:
            if text:
                self.router.dispatch("voice.submit", text=text)
            self.text_input.clear()
            self.text_input.setFocus()

    def set_ui_mode(self, mode):
        if mode == "save_state":
            self.save_ui_state()
            return
            
        if mode == "show_logs":
            self._toggle_debug()
            return
            
        if mode == "set_fullscreen":
            logging.info(f"[DEBUG UI] set_fullscreen triggered. is_fullscreen: {getattr(self, 'is_fullscreen', False)}")
            screens = QApplication.screens()
            is_monitor_swap = getattr(self, 'is_fullscreen', False)
            old_geom = None
            
            if is_monitor_swap:
                old_geom = screens[getattr(self, 'current_monitor_idx', 0)].geometry()
                self.current_monitor_idx = (getattr(self, 'current_monitor_idx', 0) + 1) % len(screens)
            else:
                if not hasattr(self, 'current_monitor_idx'):
                    primary = QApplication.primaryScreen()
                    self.current_monitor_idx = screens.index(primary) if primary in screens else 0
                
                if self.current_monitor_idx >= len(screens):
                    primary = QApplication.primaryScreen()
                    self.current_monitor_idx = screens.index(primary) if primary in screens else 0

            if is_monitor_swap:
                widget_visibility = {wid: w.isVisible() for wid, w in self.active_widgets.items()}
                logging.info("[DEBUG UI] Executing monitor swap showNormal()")
                self.showNormal()
                QApplication.processEvents()
            else:
                logging.info("[DEBUG UI] Executing initial fullscreen setup.")
                self.hide()
                QApplication.processEvents()
                
                flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                if sys.platform == "win32":
                    flags |= Qt.WindowType.WindowStaysOnTopHint
                self.setWindowFlags(flags)
                logging.info("[DEBUG UI] Window flags set.")
                
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                if sys.platform != "win32":
                    self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
                self.clearMask()
                self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
                
                logging.info("[DEBUG UI] Calling showNormal() to force Wayland mapping...")
                self.showNormal()
                QApplication.processEvents()
                logging.info(f"[DEBUG UI] After showNormal() -> isVisible: {self.isVisible()}, isActiveWindow: {self.isActiveWindow()}")
            
            self.is_fullscreen = True
            
            import time
            self._occlusion_disabled_until = time.time() + 1.5
            
            target_screen = screens[self.current_monitor_idx]
            if self.windowHandle():
                self.windowHandle().setScreen(target_screen)
            
            geom = target_screen.geometry()
            
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            
            self.setGeometry(geom)
            
            # Reset visualizer to IDLE before transitioning — prevents stale RECORDING state
            # from a previous TTS+mic cycle being inherited by the fullscreen view
            self.state = "IDLE"
            self.visualizer.set_state("IDLE", True)
            
            self.visualizer.lower()
            
            # Row 1
            self.btn_media.setGeometry(30, geom.height() - 65, 120, 35)
            self.btn_lights.setGeometry(145, geom.height() - 65, 120, 35)
            
            self.btn_reminders.setGeometry(260, geom.height() - 65, 120, 35)
            self.btn_todos.setGeometry(375, geom.height() - 65, 120, 35)
            
            self.btn_settings.setGeometry(30, geom.height() - 115, 120, 35)
            self.btn_updates.setGeometry(145, geom.height() - 115, 120, 35)
            
            self.btn_debug.setGeometry(260, geom.height() - 115, 120, 35)
            
            # Position calendar toggle on right edge
            self.btn_calendar.setGeometry(geom.width() - 30, int(geom.height() / 2) - 40, 30, 80)
            self.btn_calendar.setText("❮")
            
            self.calendar_drawer.setGeometry(geom.width(), 0, 380, geom.height())
            self.calendar_is_open = False
            
            self.btn_media.show()
            self.btn_lights.show()
            self.btn_reminders.show()
            self.btn_todos.show()
            self.btn_settings.show()
            self.btn_updates.show()
            self.btn_calendar.show()
            
            if ECOSYSTEM_STATE == "debug":
                self.btn_debug.show()
            else:
                self.btn_debug.hide()
            
            rw_w, rw_h = 220, 135
            self.reminder_widget.setGeometry(geom.width() - rw_w - 20, geom.height() - rw_h - 20, rw_w, rw_h)
            if self.reminder_widget.reminders:
                self.reminder_widget.show()
            
            # Show dashboard widgets
            for wid, w in self.active_widgets.items():
                if hasattr(w, "title_bar"):
                    w.title_bar.show()
                if is_monitor_swap:
                    if widget_visibility.get(wid, False):
                        w.show()
                        if old_geom:
                            new_x = int((w.x() / old_geom.width()) * geom.width()) if old_geom.width() > 0 else w.x()
                            new_y = int((w.y() / old_geom.height()) * geom.height()) if old_geom.height() > 0 else w.y()
                            w.move(new_x, new_y)
                else:
                    w.show()
            
            if not is_monitor_swap:
                # Load and restore persistent UI state across sessions
                self.load_ui_state()

            # Refresh states for modules to sync UI
            if getattr(self, "calendar_is_open", False):
                self.router.dispatch("calendar.read")

            logging.info(f"[DEBUG UI] Calling showFullScreen(). Current focus: {self.hasFocus()}")
            self.showFullScreen()
            logging.info(f"[DEBUG UI] After showFullScreen() -> isVisible: {self.isVisible()}, isFullScreen: {self.isFullScreen()}")
            
            def force_focus():
                self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
                self.raise_()
                self.activateWindow() 
                self.setFocus()
                logging.info(f"[DEBUG UI] After delayed activate/focus -> isActiveWindow: {self.isActiveWindow()}, hasFocus: {self.hasFocus()}")
            
            # Delay focus grab slightly on Wayland to allow compositor to map the fullscreen surface
            QTimer.singleShot(150, force_focus)
            
            if sys.platform == "win32":
                import ctypes
                hwnd = int(self.winId())
                user32 = ctypes.windll.user32
                if sys.maxsize > 2**32:
                    GetWindowLong = user32.GetWindowLongPtrW
                    GetWindowLong.argtypes = [ctypes.c_void_p, ctypes.c_int]
                    GetWindowLong.restype = ctypes.c_void_p
                    SetWindowLong = user32.SetWindowLongPtrW
                    SetWindowLong.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
                    SetWindowLong.restype = ctypes.c_void_p
                else:
                    GetWindowLong = user32.GetWindowLongW
                    SetWindowLong = user32.SetWindowLongW
                
                style = GetWindowLong(hwnd, -20)
                if style is not None:
                    logging.info(f"[DEBUG UI] Before ctypes: GWL_EXSTYLE = {hex(style)}")
                    new_style = style & ~0x00000020
                    SetWindowLong(hwnd, -20, new_style)
                    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0023) # SWP_NOMOVE|SWP_NOSIZE|SWP_FRAMECHANGED
                    final_style = GetWindowLong(hwnd, -20)
                    logging.info(f"[DEBUG UI] After ctypes: GWL_EXSTYLE = {hex(final_style)}")
                
            self.raise_()
            self.activateWindow() 
            self.setFocus()
            
            box_width = min(650, geom.width() - 100)
            box_x = int((geom.width() - box_width) / 2)
            box_y = geom.height() - 62
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
            self.btn_reminders.hide()
            self.btn_todos.hide()
            self.btn_settings.hide()
            self.btn_updates.hide()
            self.btn_calendar.hide()
            self.calendar_drawer.hide()
            self.reminder_widget.hide()
                
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
            
            flags = (
                Qt.WindowType.FramelessWindowHint | 
                Qt.WindowType.WindowStaysOnTopHint | 
                Qt.WindowType.Tool
            )
            if sys.platform != "win32":
                flags |= Qt.WindowType.WindowTransparentForInput
                
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            if sys.platform != "win32":
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            
            primary_screen = QApplication.primaryScreen()
            screen_geom = primary_screen.availableGeometry()
            width, height = 200, 400
            x_pos = screen_geom.right() - width - 20
            y_pos = screen_geom.bottom() - height - 20
            
            self.showNormal()
            self.setFixedSize(width, height)
            self.setGeometry(x_pos, y_pos, width, height)
            
            if sys.platform == "win32":
                import ctypes
                hwnd = int(self.winId())
                user32 = ctypes.windll.user32
                if sys.maxsize > 2**32:
                    GetWindowLong = user32.GetWindowLongPtrW
                    GetWindowLong.argtypes = [ctypes.c_void_p, ctypes.c_int]
                    GetWindowLong.restype = ctypes.c_void_p
                    SetWindowLong = user32.SetWindowLongPtrW
                    SetWindowLong.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
                    SetWindowLong.restype = ctypes.c_void_p
                else:
                    GetWindowLong = user32.GetWindowLongW
                    SetWindowLong = user32.SetWindowLongW
                
                style = GetWindowLong(hwnd, -20)
                if style is not None:
                    SetWindowLong(hwnd, -20, style | 0x00000020)
                    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0023)

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
    def closeEvent(self, event):
        self.save_ui_state()
        super().closeEvent(event)

    def save_ui_state(self):
        try:
            active_widgets_data = {}
            for wid, wrapper in self.active_widgets.items():
                active_widgets_data[wid] = {
                    "visible": wrapper.isVisible(),
                    "pos": [wrapper.x(), wrapper.y()],
                    "size": [wrapper.width(), wrapper.height()]
                }
                
            reminder_data = {
                "visible": self.reminder_widget.isVisible() if hasattr(self, 'reminder_widget') else False,
                "pos": [self.reminder_widget.x(), self.reminder_widget.y()] if hasattr(self, 'reminder_widget') else [0, 0]
            }
            
            carousel_idx = self.calendar_drawer.carousel.stack.currentIndex() if hasattr(self, 'calendar_drawer') else 0
            
            state_payload = {
                "drawer_open": getattr(self, 'calendar_is_open', False),
                "carousel_tab": carousel_idx,
                "reminder_widget": reminder_data,
                "active_widgets": active_widgets_data,
                "current_monitor_idx": getattr(self, 'current_monitor_idx', 0)
            }
            
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state_payload, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save UI state: {e}")

    def load_ui_state(self):
        if not os.path.exists(STATE_FILE):
            return
            
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                
            # 1. Carousel tab
            carousel_tab = state.get("carousel_tab", 0)
            if hasattr(self, 'calendar_drawer'):
                if 0 <= carousel_tab < self.calendar_drawer.carousel.stack.count():
                    self.calendar_drawer.carousel.stack.setCurrentIndex(carousel_tab)
                    self.calendar_drawer.carousel.update_indicator()
                    
            # 2. Drawer open/closed state
            drawer_state = state.get("drawer_open", False)
            if drawer_state and not self.calendar_is_open:
                self._toggle_calendar()
                
            self.current_monitor_idx = state.get("current_monitor_idx", 0)
                
            widgets_state = state.get("active_widgets", {})
            rem_state = state.get("reminder_widget", {})
            if hasattr(self, 'reminder_widget'):
                pos = rem_state.get("pos")
                if pos and len(pos) == 2:
                    self.reminder_widget.move(pos[0], pos[1])
                if rem_state.get("visible", False):
                    self.reminder_widget.show()
                    self.reminder_widget.raise_()
                else:
                    self.reminder_widget.hide()
                    
            # 4. Draggable Floating Widgets (Media, Lights, To-Do)
            active_state = state.get("active_widgets", {})
            for widget_id, info in active_state.items():
                is_visible = info.get("visible", False)
                pos = info.get("pos")
                size = info.get("size")
                
                if widget_id == "widget_media_controls":
                    if widget_id not in self.active_widgets:
                        media_widget = MediaWidget()
                        self.spawn_widget(widget_id, "Media Controls", media_widget)
                elif widget_id == "widget_light_controls":
                    if widget_id not in self.active_widgets:
                        light_widget = LightControlWidget()
                        self.spawn_widget(widget_id, "Smart Lights", light_widget)
                elif widget_id == "widget_todo_list":
                    if widget_id not in self.active_widgets:
                        todo_widget = TodoWidget()
                        self.spawn_widget(widget_id, "To-Do List", todo_widget)
                elif widget_id == "widget_settings":
                    if widget_id not in self.active_widgets:
                        settings_widget = SettingsWidget()
                        self.spawn_widget(widget_id, "System Settings", settings_widget)
                elif widget_id == "widget_updates":
                    if widget_id not in self.active_widgets:
                        update_widget = UpdateWidget()
                        self.spawn_widget(widget_id, "System Updates", update_widget)
                        
                if widget_id in self.active_widgets:
                    w = self.active_widgets[widget_id]
                    
                    if pos and len(pos) == 2:
                        w.move(pos[0], pos[1])
                        
                    if size and len(size) == 2:
                        w.resize(size[0], size[1])
                        
                    if is_visible:
                        w.show()
                        w.raise_()
                    else:
                        w.hide()
        except Exception as e:
            logging.error(f"Failed to load UI state: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = JarvisUI()
    window.show()
    sys.exit(app.exec())
