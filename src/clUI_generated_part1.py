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


class FullscreenUI(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        if sys.platform == "win32":
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if sys.platform != "win32":
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 62)
        
        self.text_input = QLineEdit(self)
        self.text_input.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.text_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(20, 10, 0, 150);
                color: rgba(255, 200, 0, 255);
                font-family: 'Courier New'; font-size: 14pt; font-weight: bold;
                border: 1px solid rgba(255, 150, 0, 100);
                border-radius: 15px; padding: 5px 15px;
            }
            QLineEdit:focus { border: 1px solid rgba(255, 200, 0, 200); background-color: rgba(30, 15, 0, 180); }
        """)
        self.text_input.returnPressed.connect(self.manager.submit_text_command)
        self.text_input.setFixedWidth(500)
        self.text_input.setFixedHeight(30)
        
        self.layout.addStretch()
        self.layout.addWidget(self.text_input, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        
        self.btn_media = self.create_nav_button("♫ MEDIA", self.manager._toggle_media)
        self.btn_lights = self.create_nav_button("☼ LIGHTS", self.manager._toggle_lights)
        self.btn_reminders = self.create_nav_button("◷ REMINDERS", self.manager._toggle_reminders)
        self.btn_todos = self.create_nav_button("☑ TO-DO", self.manager._toggle_todos)
        self.btn_settings = self.create_nav_button("⚙ SETTINGS", self.manager._toggle_settings)
        self.btn_updates = self.create_nav_button("↓ UPDATES", self.manager._toggle_updates)
        self.btn_calendar = self.create_nav_button("❮", self.manager._toggle_calendar)
        self.btn_debug = self.create_nav_button("DEBUG", self.manager._toggle_debug)
        self.btn_debug.setStyleSheet("QPushButton { background-color: rgba(255,0,0,100); color: white; font-weight: bold; border-radius: 10px; }")
        self.btn_debug.hide()
        
        self.honeycomb_pixmap = None
        self.calendar_is_open = False
        
        self.calendar_drawer = CalendarDrawer(self)
        self.calendar_drawer.hide()
        self.reminder_widget = ReminderWidget(self)
        self.reminder_widget.hide()

    def create_nav_button(self, text, callback):
        btn = QPushButton(text, self)
        btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 150);
                color: rgba(255, 150, 0, 150);
                font-family: 'Courier New';
                font-size: 10pt;
                font-weight: bold;
                border: 1px solid rgba(255, 150, 0, 50);
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: rgba(255, 150, 0, 50);
                color: rgba(255, 200, 0, 255);
                border: 1px solid rgba(255, 150, 0, 200);
            }
        """)
        btn.clicked.connect(callback)
        btn.hide()
        return btn

    def resizeEvent(self, event):
        super().resizeEvent(event)
        geom = self.geometry()
        
        self.btn_media.setGeometry(30, geom.height() - 65, 120, 35)
        self.btn_lights.setGeometry(145, geom.height() - 65, 120, 35)
        self.btn_reminders.setGeometry(30, geom.height() - 110, 120, 35)
        self.btn_todos.setGeometry(145, geom.height() - 110, 120, 35)
        self.btn_settings.setGeometry(30, geom.height() - 155, 120, 35)
        self.btn_updates.setGeometry(geom.width() - 150, 20, 120, 35)
        self.btn_debug.setGeometry(geom.width() // 2 - 60, geom.height() - 150, 120, 35)
        
        self.btn_calendar.setGeometry(geom.width() - 30, (geom.height() - 80) // 2, 30, 80)
        
        if hasattr(self, 'calendar_drawer'):
            self.calendar_drawer.resizeEvent(event)
            drawer_width = 350
            if self.calendar_is_open:
                self.calendar_drawer.setGeometry(geom.width() - drawer_width, 0, drawer_width, geom.height())
                self.btn_calendar.setGeometry(geom.width() - drawer_width - 30, (geom.height() - 80) // 2, 30, 80)
            else:
                self.calendar_drawer.setGeometry(geom.width(), 0, drawer_width, geom.height())
                self.btn_calendar.setGeometry(geom.width() - 30, (geom.height() - 80) // 2, 30, 80)

        if hasattr(self, 'reminder_widget') and self.reminder_widget.isVisible():
            rw, rh = 300, 400
            self.reminder_widget.setGeometry((geom.width() - rw) // 2, (geom.height() - rh) // 2 - 50, rw, rh)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        painter.setBrush(QColor(0, 0, 0, 240))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())
        
        if not self.honeycomb_pixmap or self.honeycomb_pixmap.width() != self.width():
            self.honeycomb_pixmap = self._generate_honeycomb(self.width(), self.height())
            
        painter.drawPixmap(0, 0, self.honeycomb_pixmap)
        
    def _generate_honeycomb(self, w, h):
        import math
        from PyQt6.QtGui import QPixmap, QPolygonF, QPainter, QColor, QPen
        from PyQt6.QtCore import Qt, QPointF
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

class OverlayUI(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        
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
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
    def position_in_corner(self, screen_idx):
        screens = QApplication.screens()
        target_screen = screens[screen_idx] if screen_idx < len(screens) else QApplication.primaryScreen()
        
        if self.windowHandle():
            self.windowHandle().setScreen(target_screen)
            
        screen_geom = target_screen.geometry()
        width, height = 200, 400
        x_pos = screen_geom.right() - width - 20
        y_pos = screen_geom.bottom() - height - 20
        
        self.setFixedSize(width, height)
        self.setGeometry(x_pos, y_pos, width, height)
