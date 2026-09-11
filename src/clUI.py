import os
import sys
import json
import math
import random
import paho.mqtt.client as mqtt
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QGraphicsDropShadowEffect, QStackedLayout
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPointF, QPoint, QSize, QFileSystemWatcher, QPropertyAnimation, QEasingCurve, QRect, pyqtProperty
from datetime import datetime
import paho.mqtt.publish as publish
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QRadialGradient, QBrush, QLinearGradient
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QRadialGradient, QBrush, QLinearGradient, QFontMetrics
from utils.clActionRouter import ActionRouter

from ui.clMediaWidget import MediaWidget
from ui.clLightControlWidget import LightControlWidget
from ui.clReminderWidget import ReminderWidget
from ui.clTodoWidget import TodoWidget
from ui.clDashboardDrawer import DashboardDrawer
from ui.clSettingsWidget import SettingsWidget
from ui.clUpdateWidget import UpdateWidget
from ui.clLogWidget import LogWidget
from ui.clMarqueeLabel import MarqueeLabel

from clUIScalerInjector import inject_scaler
from clUIScaler import UIScaler
from clTheme import Theme

inject_scaler()

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
    app_volumes_signal = pyqtSignal(dict)
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
        client.subscribe("jarvis/sys/app_volumes")
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
            "jarvis/sys/app_volumes": self._handle_app_volumes,
            "jarvis/sys/light_status": self._handle_light_status,
            "jarvis/feedback": self._handle_feedback,
            "jarvis/sys/todo/status": self._handle_todo_status,
            "jarvis/sys/calendar/status": self._handle_calendar_status,
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

    def _handle_app_volumes(self, payload):
        if isinstance(payload, dict):
            self.app_volumes_signal.emit(payload)

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
        self.setObjectName("PopupMain")
        self.widget_id = widget_id
        self.closable = closable
        self.main_window = parent
        self.is_unpinned = False
        
        print(f"[DEBUG DraggableWidget] widget_id={widget_id}, title={repr(title)}, closable={closable}")
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 15)
        self.layout.setSpacing(0)
        
        if title or closable:
            self.title_bar = QWidget(self)
            self.title_bar.setFixedHeight(24)
            self.title_bar.setObjectName("TitleBar")
            self.title_bar.setStyleSheet(Theme.get_style("NotificationTitleBar"))
            title_layout = QHBoxLayout(self.title_bar)
            title_layout.setContentsMargins(10, 0, 5, 0)

            # Small glowing accent dot -- QSS has no box-shadow equivalent, so
            # two labels stacked on the same spot approximate a layered CSS
            # glow (tight core + soft outer halo); a single
            # QGraphicsDropShadowEffect can only paint one layer.
            dot_holder = QWidget()
            dot_holder.setFixedSize(6, 6)
            dot_stack = QStackedLayout(dot_holder)
            dot_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
            dot_stack.setContentsMargins(0, 0, 0, 0)
            for blur, alpha, color in ((16, 82, (255, 140, 0)), (6, 166, (255, 170, 0))):
                dot = QLabel()
                dot.setStyleSheet(f"background: {Theme.C_PRIMARY}; border-radius: 3px;")
                glow_effect = QGraphicsDropShadowEffect(dot)
                glow_effect.setBlurRadius(blur)
                glow_effect.setOffset(0, 0)
                glow_effect.setColor(QColor(*color, alpha))
                dot.setGraphicsEffect(glow_effect)
                dot_stack.addWidget(dot)
            title_layout.addWidget(dot_holder)
            title_layout.addSpacing(7)

            if title:
                lbl = QLabel(title)
                lbl.setStyleSheet(Theme.get_style("NotificationTitle"))
                title_layout.addWidget(lbl)
            else:
                title_layout.addStretch()
                
            if closable:
                self.pin_btn = QPushButton()
                self.pin_btn.setFixedSize(22, 22)
                self.pin_btn.setIcon(Theme.get_icon("pin_off.svg", 13))
                self.pin_btn.setIconSize(QSize(13, 13))
                self.pin_btn.setStyleSheet(Theme.get_style("NotificationCloseBtn"))
                self.pin_btn.clicked.connect(lambda: self.toggle_pin())
                title_layout.addWidget(self.pin_btn)

                btn = QPushButton()
                btn.setFixedSize(22, 22)
                btn.setIcon(Theme.get_icon("close.svg", 13))
                btn.setIconSize(QSize(13, 13))
                btn.setStyleSheet(Theme.get_style("NotificationCloseBtn"))
                btn.clicked.connect(self.close_widget)
                title_layout.addWidget(btn)
                
            self.layout.addWidget(self.title_bar)
            
        self.content_widget = content_widget
        self.layout.addWidget(self.content_widget)
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(Theme.get_global_stylesheet() + "\n" + Theme.get_style("NotificationBody"))
        
        self._dragging = False
        self._resizing = False
        self._drag_start_pos = QPoint()
        self._resize_start_size = self.size()
        
    def update_scaling(self):
        if hasattr(self, 'content_widget') and hasattr(self.content_widget, 'update_scaling'):
            self.content_widget.update_scaling()

        if hasattr(self, 'title_bar'):
            self.title_bar.setFixedHeight(24)

        # Grow to fit the freshly-rescaled content if it no longer fits, but
        # never shrink -- adjustSize() unconditionally snapped back to the
        # natural minimum size here, and refresh_layout() (which calls this)
        # fires on every window resize, including an overlay transition or a
        # monitor swap that doesn't actually change this widget's content at
        # all -- silently discarding a user's manual drag-resize or a size
        # just restored from ui_state.json.
        hint = self.sizeHint()
        new_w = max(self.width(), hint.width())
        new_h = max(self.height(), hint.height())
        if (new_w, new_h) != (self.width(), self.height()):
            self.resize(new_w, new_h)

    def close_widget(self):
        parent_ui = self.main_window if self.is_unpinned else self.parent()
        if hasattr(parent_ui, 'close_draggable_widget'):
            parent_ui.close_draggable_widget(self.widget_id)
        else:
            self.hide()

    def toggle_pin(self, force_unpin=None):
        should_unpin = not self.is_unpinned if force_unpin is None else force_unpin
        if should_unpin == self.is_unpinned:
            return
            
        self.is_unpinned = should_unpin
        current_size = self.size()
        
        if self.is_unpinned:
            global_pos = self.mapToGlobal(QPoint(0, 0))
            # setParent(parent, flags) changes both atomically in one native-window
            # recreation -- doing it as two separate calls (setParent then
            # setWindowFlags) is what left Windows' native title bar/min/max/close
            # chrome in place instead of honoring the frameless Tool flags.
            self.setParent(None, Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            if hasattr(self, 'pin_btn'):
                self.pin_btn.setIcon(Theme.get_icon("pin.svg", 13))
            self.move(global_pos)
        else:
            global_pos = self.pos()
            if self.main_window:
                self.setParent(self.main_window, Qt.WindowType.Widget)
                local_pos = self.main_window.mapFromGlobal(global_pos)

                # Clamp to main window bounds so it pops into screen if pinned on another monitor
                max_x = max(0, self.main_window.width() - current_size.width())
                max_y = max(0, self.main_window.height() - current_size.height())
                clamped_x = max(0, min(local_pos.x(), max_x))
                clamped_y = max(0, min(local_pos.y(), max_y))

                self.move(clamped_x, clamped_y)
            else:
                self.setParent(None, Qt.WindowType.Widget)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            if hasattr(self, 'pin_btn'):
                self.pin_btn.setIcon(Theme.get_icon("pin_off.svg", 13))

        if hasattr(self, 'resizeUnscaled'):
            self.resizeUnscaled(current_size.width(), current_size.height())
        else:
            self.resize(current_size)
            
        self.show()

        # Skip saving mid-restore: load_ui_state() calls toggle_pin() to sync
        # pin state before it has applied this widget's saved visibility, so
        # saving here would persist that incomplete snapshot and clobber the
        # real saved state before load_ui_state() itself gets to read it.
        if hasattr(self.main_window, 'save_ui_state') and not getattr(self.main_window, '_restoring_ui_state', False):
            self.main_window.save_ui_state()

    def showEvent(self, event):
        super().showEvent(event)

    def paintEvent(self, event):
        from PyQt6.QtWidgets import QStyleOption, QStyle
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)
        p.end()
        
        super().paintEvent(event)
        s = UIScaler.get().scale
        margin = s(15)
        w, h = self.width(), self.height()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.moveTo(w, h - margin)
        path.lineTo(w - margin, h)
        path.lineTo(w, h)
        painter.fillPath(path, QColor(255, 120, 0, 80))
        painter.setPen(QPen(QColor(255, 120, 0, 180), s(3)))
        x, y = w, h
        painter.drawLine(x - s(14), y - s(4), x - s(4), y - s(14))
        painter.drawLine(x - s(9), y - s(4), x - s(4), y - s(9))
        painter.drawLine(x - s(4), y - s(4), x - s(4), y - s(4))

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
            if hasattr(self.parent(), "_enforce_z_order"):
                self.parent()._enforce_z_order()
        super().mousePressEvent(event)
        
    def mouseMoveEvent(self, event):
        if hasattr(self, '_resizing') and self._resizing:
            delta = event.globalPosition().toPoint() - self._resize_start_global
            
            min_w = self.minimumSizeHint().width()
            min_h = self.minimumSizeHint().height()
            
            new_w = max(min_w, self._resize_start_size.width() + delta.x())
            new_h = max(min_h, self._resize_start_size.height() + delta.y())
            
            if hasattr(self, 'resizeUnscaled'):
                self.resizeUnscaled(new_w, new_h)
            else:
                self.resize(new_w, new_h)
        elif self._dragging:
            delta = event.globalPosition().toPoint() - self._drag_start_global
            new_pos = self._drag_start_pos + delta
            if self.parent() and not self.is_unpinned:
                new_pos.setX(max(0, min(new_pos.x(), self.parent().width() - self.width())))
                new_pos.setY(max(0, min(new_pos.y(), self.parent().height() - self.height())))
            self.move(new_pos)
            
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._resizing = False
            if hasattr(self.main_window, 'save_ui_state'):
                self.main_window.save_ui_state()
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

        s = UIScaler.get().scale

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width() / 2
        cy = self.height() / 2 if self.is_fullscreen else self.height() - 100

        painter.save()
        painter.translate(cx, cy)
        painter.scale(self.current_scale, self.current_scale)
        painter.translate(-cx, -cy)
        
        painter.setOpacity(self.current_opacity)
        
        # Stronger than the original (was 60/30) -- no glow on the wave
        # lines this time, so the center bloom carries the glow instead.
        gradient = QRadialGradient(cx, cy, s(100))
        gradient.setColorAt(0.0, QColor(255, 150, 0, 110))
        gradient.setColorAt(0.5, QColor(255, 100, 0, 55))
        gradient.setColorAt(1.0, QColor(200, 50, 0, 0))
        
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(int(cx - s(500)), int(cy - s(500)), s(1000), s(1000))
        
        painter.save()
        painter.translate(cx, cy)
        
        painter.save()
        painter.rotate(self.time_offset * 15)
        pen_ring1 = QPen(QColor(255, 180, 0, int(60 * self.current_opacity)))
        pen_ring1.setWidth(s(2))
        pen_ring1.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen_ring1)
        r1 = s(80)
        painter.drawEllipse(QPointF(0, 0), r1, r1)
        painter.restore()
        
        painter.save()
        painter.rotate(-self.time_offset * 25)
        pen_ring2 = QPen(QColor(255, 120, 0, int(90 * self.current_opacity)))
        pen_ring2.setWidth(s(1))
        pen_ring2.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(pen_ring2)
        r2 = s(50)
        painter.drawEllipse(QPointF(0, 0), r2, r2)
        painter.restore()
        
        painter.restore()
        
        colors = [QColor(255, 120, 0, 100), QColor(255, 180, 0, 180), QColor(255, 230, 100, 255)]
        phases = [0, 2, 4]
        amplitudes = [s(self.amplitude), s(self.amplitude * 0.6), s(self.amplitude * 0.3)]

        for i in range(3):
            path = QPainterPath()
            path.moveTo(0, cy)
            step = max(1, s(6))
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

def load_recolored_svg_icon(path: str, color_hex: str, size: int):
    """Loads an SVG, swaps every fill="#hex" for color_hex, and renders it
    to a QIcon at size x size. Vector-based and rendered by Qt itself (not
    a font), so it looks identical on both machines regardless of what
    fonts/emoji sets are installed -- and unlike a font glyph, the color is
    just a string swap away, no font metrics/fallback involved. Icon files
    should use a single flat fill color (any hex works, it gets replaced)
    and a square viewBox; Illustrator's SVG export needs "Styling:
    Presentation Attributes" so fill="..." appears as a plain XML attribute
    rather than buried in an inline <style> block this substitution won't see."""
    import re
    from PyQt6.QtCore import QByteArray
    from PyQt6.QtGui import QIcon, QPainter, QPixmap
    from PyQt6.QtSvg import QSvgRenderer

    with open(path, "r", encoding="utf-8") as f:
        svg_text = f.read()
    svg_text = re.sub(r'fill="#[0-9a-fA-F]{3,8}"', f'fill="{color_hex}"', svg_text)

    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class IconPill(QWidget):
    """A small circular icon by default; hovering expands it into a pill
    that reveals a label via a MarqueeLabel (scrolling/carousel on hover if
    it overflows -- the same idiom the media widget uses for song titles).
    Clicking (icon or expanded area) calls _activate(). Subclasses supply
    the icon path and override _activate()/_label_text()."""

    # SVG, not emoji/font glyph: renders identically cross-platform and recolors trivially.

    def __init__(self, icon_path: str, grow_direction: str = "right", parent=None, icon_padding: int = 14):
        super().__init__(parent)
        self.grow_direction = grow_direction  # "left" or "right" -- which way it expands
        self._icon_path = icon_path
        self._icon_padding = icon_padding  # gap subtracted from diameter to get the icon's own size
        self.diameter = 35
        self.expanded_width = 140
        self._anchor_x = 0  # the edge that stays fixed on screen while (de)expanding
        self.on_width_changed = None  # optional: group-relayout hook, see WidgetTogglePill/_reflow_widget_dock
        self._anim_generation = 0  # see _animate_to/_settle_animation
        self._target_expanded = False  # intent, not instantaneous width -- an expand's first frame is also AT diameter

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # plain QWidget needs this to paint QSS background/border at all

        # Real layout margin, not CSS padding -- MarqueeLabel paints its own text and ignores CSS.
        self._edge_gap = 10
        self._layout_spacing = 6
        self._collapsed_margins = (0, 0, 0, 0)
        self._expanded_margins = (self._edge_gap, 0, 0, 0) if grow_direction == "left" else (0, 0, self._edge_gap, 0)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(*self._collapsed_margins)
        layout.setSpacing(self._layout_spacing)

        self.icon_btn = QPushButton(self)
        self.icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.icon_btn.clicked.connect(self._activate)

        self.label = MarqueeLabel("", force_single_line=True)
        self.label.setStyleSheet(f"color: {Theme.C_PRIMARY}; font-weight: bold; background: transparent; border: none;")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.hide()

        if grow_direction == "left":
            layout.addWidget(self.label, 1)
            layout.addWidget(self.icon_btn)
        else:
            layout.addWidget(self.icon_btn)
            layout.addWidget(self.label, 1)

        self._anim = QPropertyAnimation(self, b"pillWidth")
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_animation_finished)

        # Delay avoids expanding every pill a cursor merely sweeps past.
        self._hover_delay_timer = QTimer(self)
        self._hover_delay_timer.setSingleShot(True)
        self._hover_delay_timer.timeout.connect(self._begin_expand)

        self.setStyleSheet("background: transparent; border: none;")
        self.set_sizes(self.diameter, self.expanded_width)

    def set_sizes(self, diameter: int, expanded_width: int):
        self.diameter = diameter
        self.expanded_width = self._resolve_expanded_width(expanded_width)
        self.icon_btn.setFixedSize(diameter, diameter)
        self.icon_btn.setStyleSheet(Theme.get_style("IconPillCircle", radius=diameter // 2))

        icon_size = max(10, diameter - self._icon_padding)
        try:
            self.icon_btn.setIcon(load_recolored_svg_icon(self._icon_path, Theme.C_PRIMARY, icon_size))
            self.icon_btn.setIconSize(QSize(icon_size, icon_size))
        except Exception as e:
            logging.warning(f"Failed to load pill icon '{self._icon_path}': {e}")

        self._target_expanded = False
        self.setFixedHeight(diameter)
        self.setPillWidth(diameter)

    def set_anchor(self, anchor_x: int, y: int):
        """anchor_x is the edge that must stay put on screen as the pill
        expands/collapses -- the right edge if it grows left, else the left."""
        self._anchor_x = anchor_x
        self._apply_position(self.width())
        self.move(self.x(), y)

    def _apply_position(self, width: int):
        if self.grow_direction == "left":
            self.move(self._anchor_x - width, self.y())
        else:
            self.move(self._anchor_x, self.y())

    def getPillWidth(self) -> int:
        return self.width()

    def setPillWidth(self, w: int):
        self.setFixedWidth(w)
        if not self._target_expanded and w <= self.diameter and not self.label.isHidden():
            # Keyed off intent, not width -- an expand's own first frame is also at diameter.
            self.label.hide()
            self.label.stop_scrolling()
            self.layout().setContentsMargins(*self._collapsed_margins)
            self.setStyleSheet("background: transparent; border: none;")
        if self.on_width_changed:
            # Positioning is fully external (see _reflow_widget_dock) --
            # the anchor-based _apply_position below is only for a
            # standalone pill that owns its own fixed edge.
            self.on_width_changed()
        else:
            self._apply_position(w)

    pillWidth = pyqtProperty(int, getPillWidth, setPillWidth)

    def enterEvent(self, event):
        self._hover_delay_timer.start(80)
        super().enterEvent(event)

    def _begin_expand(self):
        self._target_expanded = True
        self.setStyleSheet(Theme.get_style("IconPill", radius=self.diameter // 2))
        self.layout().setContentsMargins(*self._expanded_margins)
        self.label.setText(self._label_text())
        self.label.show()
        self._animate_to(self.expanded_width)

    def leaveEvent(self, event):
        self._target_expanded = False
        self._hover_delay_timer.stop()
        self.label.stop_scrolling()
        self._animate_to(self.diameter)
        super().leaveEvent(event)

    def _animate_to(self, end_value: int):
        self._anim.stop()
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(end_value)
        self._anim.start()
        # Safety net for interrupted animations (see _settle_animation); generation guards stale checks.
        self._anim_generation += 1
        generation = self._anim_generation
        QTimer.singleShot(self._anim.duration() + 50, lambda: self._settle_animation(generation))

    def _settle_animation(self, generation: int):
        if generation != self._anim_generation:
            return
        self._target_expanded = self.underMouse()
        target = self.expanded_width if self._target_expanded else self.diameter
        if self.width() == target:
            return
        self._anim.stop()
        self.setPillWidth(target)
        self._on_animation_finished()

    def _on_animation_finished(self):
        if self.width() <= self.diameter:
            self.label.hide()
            self.label.stop_scrolling()
            self.layout().setContentsMargins(*self._collapsed_margins)
            self.setStyleSheet("background: transparent; border: none;")
        else:
            # The label's real (post-layout) width is only final once the
            # expand animation settles, since MarqueeLabel decides whether
            # to scroll by comparing text width to its own current width.
            self.label.start_scrolling()

    def mousePressEvent(self, event):
        # The label is mouse-transparent, so a click anywhere in the
        # expanded area that isn't the icon button itself lands here.
        self._activate()
        super().mousePressEvent(event)

    def _activate(self):
        raise NotImplementedError

    def _label_text(self) -> str:
        raise NotImplementedError

    def _resolve_expanded_width(self, expanded_width: int) -> int:
        return expanded_width


class AudioQuickSwitchPill(IconPill):
    """Clicking (icon or expanded area) opens a menu of every available
    device -- the full-fidelity dashboard equivalent of Settings' Audio tab,
    for switching without leaving the fullscreen view."""

    ICON_FILE = {"input": "mic.svg", "output": "speaker.svg"}

    def __init__(self, kind: str, grow_direction: str = "right", parent=None):
        self.kind = kind  # "input" or "output"
        self.router = ActionRouter()
        self.loader = ConfigLoader()
        icon_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "icons")
        icon_path = os.path.join(icon_dir, self.ICON_FILE[kind])
        super().__init__(icon_path, grow_direction, parent)
        self.refresh_label()

    def _activate(self):
        self._open_picker()

    def _label_text(self) -> str:
        return getattr(self, "_cached_display_name", None) or self._current_device()

    def _current_device(self) -> str:
        try:
            settings = self.loader.load_json("core.json").get("settings", {}).get("audio_settings", {})
        except Exception:
            settings = {}
        return settings.get(f"{self.kind}_device", "System Default")

    def _enumerate_options(self):
        from utils.clAudioDevices import list_input_device_names, list_output_device_names, SYSTEM_DEFAULT
        try:
            return [SYSTEM_DEFAULT] + (list_input_device_names() if self.kind == "input" else list_output_device_names())
        except Exception:
            return [SYSTEM_DEFAULT]

    def refresh_label(self):
        from utils.clAudioDevices import get_clean_display_names
        name = self._current_device()
        try:
            display = get_clean_display_names(self._enumerate_options(), self.kind).get(name, name)
        except Exception:
            display = name
        self._cached_display_name = display
        self.icon_btn.setToolTip(f"{'Microphone' if self.kind == 'input' else 'Speaker'}: {display}")
        if self.label.isVisible():
            self.label.setText(display)

    def _open_picker(self):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction
        from utils.clAudioDevices import get_clean_display_names

        options = self._enumerate_options()
        try:
            display_map = get_clean_display_names(options, self.kind)
        except Exception:
            display_map = {}

        current = self._current_device()
        menu = QMenu(self)
        menu.setStyleSheet(Theme.get_style("SettingsMenu"))
        for name in options:
            action = QAction(display_map.get(name, name), menu)
            action.setCheckable(True)
            action.setChecked(name == current)
            action.triggered.connect(lambda checked, n=name: self._select(n))
            menu.addAction(action)
        menu.exec(self.mapToGlobal(QPoint(0, self.height())))

    def _select(self, device_name: str):
        def update_cb(core):
            core.setdefault("settings", {}).setdefault("audio_settings", {})[f"{self.kind}_device"] = device_name
        self.loader.update_json_atomic("core.json", update_cb)

        if self.kind == "input":
            self.router.dispatch("mic.state", action="set_input_device", device_name=device_name)
        else:
            self.router.dispatch("tts.control", action="set_output_device", device_name=device_name)

        self.refresh_label()


class WidgetTogglePill(IconPill):
    """A dashboard shortcut restyled to match the audio quick-switch pills:
    collapsed to an icon, expanding on hover to show the widget's name.
    Clicking toggles that widget open/closed via the given callback."""

    def __init__(self, icon_file: str, label_text: str, callback, grow_direction: str = "right", parent=None):
        self._widget_label = label_text
        self._callback = callback
        icon_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "icons")
        icon_path = os.path.join(icon_dir, icon_file)
        super().__init__(icon_path, grow_direction, parent, icon_padding=20)

    def _activate(self):
        self._callback()

    def _label_text(self) -> str:
        return self._widget_label

    def _resolve_expanded_width(self, expanded_width: int) -> int:
        # max() with minimumSizeHint: MarqueeLabel always wants >=50px, so a short
        # label undershot here squeezed the icon-label spacing instead of the text.
        fm = QFontMetrics(self.label.font())
        text_width = fm.horizontalAdvance(self._widget_label)
        label_width = max(text_width, self.label.minimumSizeHint().width())
        return self.diameter + self._layout_spacing + label_width + self._edge_gap


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
        if sys.platform == "win32":
            # Make the overlay click-through from frame one (see _set_win32_click_through).
            self.winId()  # force native window handle creation
            self._set_win32_click_through(True)

        screens = UIScaler.get().get_stable_screens()
        idx = getattr(self, 'current_monitor_idx', 0)
        # Use UIScaler's active monitor if current_monitor_idx hasn't been set
        if not hasattr(self, 'current_monitor_idx'):
            try:
                
                idx = UIScaler.get().active_monitor
                self.current_monitor_idx = idx
            except: pass
        
        # Initial overlay spawn should always be on the primary monitor
        overlay_idx = UIScaler.get().get_primary_monitor_idx()
        target_screen = screens[overlay_idx] if overlay_idx < len(screens) else screens[0]
        screen_geom = target_screen.availableGeometry()
        
        s = UIScaler.get().scale
        width, height = s(200), s(400)
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
        self.timer.start(1000 // 15)

        self.pending_options = None
        self.options_debounce_timer = QTimer(self)
        self.options_debounce_timer.setSingleShot(True)
        self.options_debounce_timer.timeout.connect(self._apply_pending_options)
        
        # Core Visualizer is now permanently attached to the background
        self.visualizer = JarvisVisualizer(self)
        self.visualizer.setGeometry(0, 0, self.width(), self.height())
        self.visualizer.show()
        
        # Text Input Workaround
        self.text_input = QLineEdit(self)
        
        from PyQt6.QtCore import QObject, QEvent
        class FocusFilter(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.FocusIn:
                    import logging
                    logging.debug(f"[DEBUG FOCUS] text_input focusInEvent. Reason: {event.reason()}")
                elif event.type() == QEvent.Type.FocusOut:
                    import logging
                    logging.debug(f"[DEBUG FOCUS] text_input focusOutEvent. Reason: {event.reason()}")
                elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                    obj.clearFocus()
                    return True
                return False

        self.focus_filter = FocusFilter()
        self.text_input.installEventFilter(self.focus_filter)

        self.text_input.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.text_input.returnPressed.connect(self.submit_text_command)
        self.text_input.hide()
        
        # Dashboard shortcuts, stacked as a column of icon pills along the
        # far left edge (see refresh_layout) -- each grows rightward on
        # hover to reveal its name, matching the audio quick-switch pills.
        self.btn_media = WidgetTogglePill("music.svg", "Music", self._toggle_media, grow_direction="right", parent=self)
        self.btn_media.hide()

        self.btn_lights = WidgetTogglePill("lights.svg", "Lights", self._toggle_lights, grow_direction="right", parent=self)
        self.btn_lights.hide()

        self.btn_reminders = WidgetTogglePill("reminders.svg", "Reminders", self._toggle_reminders, grow_direction="right", parent=self)
        self.btn_reminders.hide()

        self.btn_todos = WidgetTogglePill("todos.svg", "To-Do List", self._toggle_todos, grow_direction="right", parent=self)
        self.btn_todos.hide()

        self.btn_settings = WidgetTogglePill("settings.svg", "Settings", self._toggle_settings, grow_direction="right", parent=self)
        self.btn_settings.hide()

        self.btn_updates = WidgetTogglePill("updates.svg", "Updates", self._toggle_updates, grow_direction="right", parent=self)
        self.btn_updates.hide()

        self.btn_debug = WidgetTogglePill("debug.svg", "Debug Logs", self._toggle_debug, grow_direction="right", parent=self)
        self.btn_debug.hide()

        self.widget_toggle_pills = [
            self.btn_media, self.btn_lights, self.btn_reminders,
            self.btn_todos, self.btn_settings, self.btn_updates, self.btn_debug,
        ]
        # Laid out as one horizontal, center-anchored row (see
        # _reflow_widget_dock) rather than each pill owning a fixed edge --
        # every pill needs to shift when ANY one of them resizes on hover.
        for pill in self.widget_toggle_pills:
            pill.on_width_changed = self._reflow_widget_dock

        # Mic (left) expands leftward, speaker (right) expands rightward --
        # each grows away from the other so they never collide mid-hover.
        self.pill_mic = AudioQuickSwitchPill("input", grow_direction="left", parent=self)
        self.pill_mic.hide()

        self.pill_speaker = AudioQuickSwitchPill("output", grow_direction="right", parent=self)
        self.pill_speaker.hide()

        self.btn_calendar = QPushButton(self)
        self.btn_calendar.setIcon(Theme.get_icon("chevron_left.svg", 14))
        self.btn_calendar.setIconSize(QSize(14, 14))
        self.btn_calendar.setStyleSheet(Theme.get_style("CalendarButton"))
        self.btn_calendar.clicked.connect(self._toggle_calendar)
        self.btn_calendar.hide()
        
        # Persistent Dashboard Drawer (hidden off-screen right initially)
        s = UIScaler.get().scale
        drawer_width = s(380)
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
        self.mqtt_thread.app_volumes_signal.connect(self._handle_app_volumes)
        self.mqtt_thread.light_status_signal.connect(self._handle_light_status)
        self.mqtt_thread.feedback_signal.connect(self._handle_feedback)
        self.mqtt_thread.todo_status_signal.connect(self._handle_todo_status)
        self.mqtt_thread.calendar_status_signal.connect(self._handle_calendar_data)
        self.mqtt_thread.start()

        # Only restore fullscreen mode -- and any dashboard widgets that were
        # open in it -- on a reboot/crash-recovery/restart (JARVIS_REBOOT=1,
        # set by clJarvis.py). Widgets are never restored from this early
        # call (restore_widgets=False): the window is still sized/positioned
        # as the tiny overlay box here, so spawn_widget's overlay path would
        # force every widget unpinned, and the toggle_pin() call that syncs
        # it back to its saved pinned state clamps its position against
        # main_window's CURRENT (still tiny) size -- crushing every restored
        # position toward (0,0). set_ui_mode("set_fullscreen") below does its
        # own full widget restore once the window is actually at real
        # fullscreen geometry, which is the only correct place to do it.
        is_reboot = os.environ.get("JARVIS_REBOOT") == "1"
        saved_state = self.load_ui_state(restore_widgets=False)

        if is_reboot and saved_state and saved_state.get("is_fullscreen", False):
            self.set_ui_mode("set_fullscreen")

    def _reflow_widget_dock(self):
        """Re-centers the widget-toggle pill row around self._widget_dock_center_x
        using each pill's CURRENT width -- called once to lay out the
        collapsed row, then again on every frame any pill's hover-expand
        animation reports a width change, so the row's midpoint never
        drifts as one pill grows or shrinks."""
        pills = self.widget_toggle_pills
        gap = self._widget_dock_gap
        total_width = sum(p.width() for p in pills) + gap * (len(pills) - 1)
        x = self._widget_dock_center_x - total_width // 2
        for p in pills:
            p.move(x, self._widget_dock_y)
            x += p.width() + gap

    def refresh_layout(self, force_monitor_idx=None):
        screens = UIScaler.get().get_stable_screens()
        
        if force_monitor_idx is not None:
            idx = force_monitor_idx
        else:
            current_screen = self.screen()
            idx = 0
            if current_screen:
                screen_name = current_screen.name()
                for i, s in enumerate(screens):
                    if s.name() == screen_name:
                        idx = i
                        break
                    
        self.current_monitor_idx = idx
        UIScaler.get().set_active_monitor(idx)
        s = UIScaler.get().scale

        # Use actual window dimensions instead of target screen geometry to prevent Wayland scaling/cropping bugs
        win_w = self.width()
        win_h = self.height()
        
        import logging
        target_screen_name = screens[idx].name() if idx < len(screens) else 'Unknown'
        logging.debug(f"[DEBUG LAYOUT] Physical Screen: {target_screen_name} (idx: {idx})")
        logging.debug(f"[DEBUG LAYOUT] Window Size: {win_w}x{win_h}")
        logging.debug(f"[DEBUG LAYOUT] Applied Scale: {s(100)/100.0}")

        # Re-apply stylesheets so the scaling dynamically updates font sizes and border radii
        self.setStyleSheet(Theme.get_global_stylesheet())
        self.btn_calendar.setStyleSheet(Theme.get_style("CalendarButton"))

        # Text Input (positioned here, ahead of its old spot below, so the
        # audio pills and widget dock can be laid out relative to it)
        box_width = s(600)
        box_x = win_w // 2 - (box_width // 2)
        box_y = win_h - s(80)
        box_height = s(40)
        self.text_input.setGeometry(box_x, box_y, box_width, box_height)

        # Horizontal row centered in the left margin, vertically centered on the text bar.
        widget_pill_diameter = s(48)
        widget_pill_expanded_width = s(170)  # unused: WidgetTogglePill fits its own label width instead
        widget_dock_left_margin = s(20)
        widget_dock_bar_gap = s(20)

        self._widget_dock_gap = s(12)
        self._widget_dock_y = box_y + box_height // 2 - widget_pill_diameter // 2
        self._widget_dock_center_x = (widget_dock_left_margin + (box_x - widget_dock_bar_gap)) // 2

        for pill in self.widget_toggle_pills:
            pill.set_sizes(widget_pill_diameter, widget_pill_expanded_width)
        self._reflow_widget_dock()

        # Audio quick-switch pills -- collapsed to small circles, centered as
        # a pair directly above the text bar; each expands outward on hover
        # (mic left, speaker right) so they never grow into each other's
        # space, and stay clear of the reminder widget's bottom-right corner.
        pill_diameter = s(35)
        pill_expanded_width = s(140)
        pill_circle_gap = s(10)
        pair_collapsed_width = pill_diameter * 2 + pill_circle_gap
        pair_left_x = win_w // 2 - pair_collapsed_width // 2
        pill_y = box_y - pill_diameter - s(10)

        self.pill_mic.set_sizes(pill_diameter, pill_expanded_width)
        self.pill_speaker.set_sizes(pill_diameter, pill_expanded_width)

        mic_right_edge = pair_left_x + pill_diameter
        speaker_left_edge = pair_left_x + pill_diameter + pill_circle_gap
        self.pill_mic.set_anchor(mic_right_edge, pill_y)
        self.pill_speaker.set_anchor(speaker_left_edge, pill_y)
        self.pill_mic.refresh_label()
        self.pill_speaker.refresh_label()

        # Calendar button
        self.btn_calendar.setGeometry(win_w - s(30), int(win_h / 2) - s(40), s(30), s(80))

        drawer_width = s(400) if win_w >= 1920 else s(350)
        if hasattr(self, 'calendar_drawer'):
            self.calendar_drawer.setGeometry(win_w, 0, drawer_width, win_h)
            if hasattr(self.calendar_drawer, 'update_scaling'):
                self.calendar_drawer.update_scaling()
        
        if hasattr(self, 'drawer'):
            self.drawer.setGeometry(win_w - drawer_width - 20, 0, drawer_width, win_h)
            if hasattr(self.drawer, 'update_scaling'):
                self.drawer.update_scaling()
                
        for wrapper in self.active_widgets.values():
            if hasattr(wrapper, 'update_scaling'):
                wrapper.update_scaling()
                
        # Universally force all active local CSS and fonts to rescale!
        from PyQt6.QtWidgets import QApplication
        for widget in QApplication.allWidgets():
            if hasattr(widget, '_unscaled_css'):
                widget.setStyleSheet(widget._unscaled_css)
            if hasattr(widget, '_unscaled_font'):
                widget.setFont(widget._unscaled_font)

        rw_w = s(300)
        rw_h = s(150)
        if hasattr(self, 'reminder_widget'):
            self.reminder_widget.setGeometry(win_w - rw_w - 20, win_h - rw_h - 20, rw_w, rw_h)

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
                    wrapper.raise_(); self._enforce_z_order()


    def _enforce_z_order(self):
        if getattr(self, 'is_fullscreen', False):
            if hasattr(self, 'calendar_drawer'):
                self.calendar_drawer.raise_()
            if getattr(self, 'text_input', None) is not None:
                self.text_input.raise_()

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
                    w.raise_(); self._enforce_z_order()
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
                    w.raise_(); self._enforce_z_order()
                else:
                    self.close_draggable_widget(widget_id)

    def _toggle_reminders(self):
        if hasattr(self, 'reminder_widget'):
            if self.reminder_widget.isVisible():
                self.reminder_widget.hide()
            else:
                self.reminder_widget.show()
                self.reminder_widget.raise_(); self._enforce_z_order()
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
                    w.raise_(); self._enforce_z_order()
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
                    w.raise_(); self._enforce_z_order()
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
                    w.raise_(); self._enforce_z_order()
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
                w.raise_(); self._enforce_z_order()
                self.save_ui_state()
            else:
                self.close_draggable_widget(widget_id)

    def _toggle_calendar(self):
        if not getattr(self, 'is_fullscreen', False):
            return
            
        geom = self.geometry()
        s = UIScaler.get().scale
        drawer_width = s(380)
        
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
            btn_animation.setEndValue(QRect(geom.width() - drawer_width - s(30), current_btn_geom.y(), s(30), s(80)))
            self.btn_calendar.setIcon(Theme.get_icon("chevron_right.svg", 14))
            
            self.calendar_is_open = True
            
            try:
                self.router.dispatch("calendar.read")
            except: pass
        else:
            # Close drawer
            self.calendar_animation.setEndValue(QRect(geom.width(), 0, drawer_width, geom.height()))
            
            current_btn_geom = self.btn_calendar.geometry()
            btn_animation.setEndValue(QRect(geom.width() - 30, current_btn_geom.y(), s(30), s(80)))
            self.btn_calendar.setIcon(Theme.get_icon("chevron_left.svg", 14))
            
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

    def _handle_app_volumes(self, data):
        apps = data.get("apps", [])
        widget_id = "widget_media_controls"
        if widget_id in self.active_widgets:
            wrapper = self.active_widgets[widget_id]
            if isinstance(wrapper.content_widget, MediaWidget):
                wrapper.content_widget.update_app_volumes(apps)
        if hasattr(self, 'calendar_drawer'):
            # Rebuilding real rows (icons, MarqueeLabels, sliders) on every
            # single app_volumes message even when this copy's own drawer
            # isn't open doubles the widget-construction work for nothing
            # the user can see.
            calendar_media_widget = self.calendar_drawer.carousel.media_widget
            if calendar_media_widget.app_volume_body.isVisible():
                calendar_media_widget.update_app_volumes(apps)

    def _on_app_state_changed(self, state):
        if not getattr(self, 'is_fullscreen', False) or getattr(self, 'text_input', None) is None:
            return
            
        if state != Qt.ApplicationState.ApplicationActive:
            # On Wayland, the app may frequently be marked as Inactive due to focus stealing prevention.
            # We must NOT hide the text inputs here, otherwise the user can't use the dashboard if Wayland denies focus.
            # No auto-collapse on focus loss -- closed explicitly via keybinds instead.
            pass
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
            self.visualizer.setGeometry(0, 0, self.width(), self.height())
        # Constantly recalculate coordinates if Wayland overrides our setGeometry
        self.refresh_layout()

    def set_volume(self, vol):
        self.visualizer.set_volume(vol)

    def set_state(self, state):
        self.state = state
        self.visualizer.set_state(state, self.is_fullscreen)
        
        # Low-Power Idle Mode: Drop to 15 FPS to save CPU, snap to 60 FPS when active
        if state == "IDLE":
            self.timer.setInterval(1000 // 15)
        else:
            self.timer.setInterval(1000 // 60)
            
        if state == "IDLE":
            if not self.is_fullscreen:
                for w_id in list(self.active_widgets.keys()):
                    if w_id.startswith("list_"):
                        continue  # keep options prompts open until answered
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
            lbl.setStyleSheet(Theme.get_style("HealthDanger"))
            layout.addWidget(lbl)
        else:
            for opt in reversed(options[:5]):
                truncated_opt = fm.elidedText(opt, Qt.TextElideMode.ElideRight, target_width - 20)
                lbl = QLabel(truncated_opt)
                lbl.setFont(opt_font)
                lbl.setStyleSheet(Theme.get_style("HealthWarning"))
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
            # Ensure it's visible even if it was previously hidden.
            w.show()
            w.raise_()
            if self.is_fullscreen:
                self._enforce_z_order()
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

        # Grab focus so a time-limited options prompt is actually seen.
        if self.is_fullscreen:
            self.raise_()
            self.activateWindow()
            w.raise_()

    def spawn_widget(self, widget_id, title, content_widget, closable=True):
        """API to spawn or bring-to-front a dashboard widget"""
        print(f"[DEBUG spawn_widget] widget_id={widget_id}, title={repr(title)}, closable={closable}")
        if widget_id in self.active_widgets:
            w = self.active_widgets[widget_id]
            if self.is_fullscreen:
                w.show()
                w.raise_(); self._enforce_z_order()
            return
        is_standalone = not self.is_fullscreen
        # Always construct with main_window=self, even when spawning standalone --
        # DraggableWidget.main_window is captured once at construction and never
        # updated again, so a widget built with parent=None here would have no
        # main_window to reparent into later. Without it, toggle_pin()'s re-pin
        # branch (e.g. after switching overlay -> fullscreen) falls through to
        # setParent(None, Widget), which leaves the widget both parentless AND
        # frameless-hint-free -- Windows then draws its full default decorated
        # chrome back onto it, exactly the "title bar came back" bug.
        wrapper = DraggableWidget(widget_id, title, content_widget, closable=closable, parent=self)

        # Position in center of screen by default
        if is_standalone:
            # No Qt parent, so is_unpinned must say so too (drives the drag clamp/pin label).
            wrapper.is_unpinned = True
            if hasattr(wrapper, "pin_btn"):
                wrapper.pin_btn.setIcon(Theme.get_icon("pin.svg", 13))
            # Atomic setParent(None, flags), matching toggle_pin()'s unpin branch --
            # detaches to a real top-level frameless window while main_window
            # (a plain Python attribute, untouched by setParent) still points at self.
            wrapper.setParent(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
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
        if not getattr(self, '_restoring_ui_state', False):
            # Skip mid-restore: would overwrite on-disk state with an incomplete snapshot.
            self.save_ui_state()
        if self.is_fullscreen:
            if hasattr(wrapper, "title_bar"):
                wrapper.title_bar.show()
            wrapper.show()
            wrapper.raise_(); self._enforce_z_order()
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
                wrapper.raise_(); self._enforce_z_order()
            else:
                wrapper.hide()

    def close_draggable_widget(self, widget_id):
        if widget_id in self.active_widgets:
            # Actually drop it, not just hide it -- every _toggle_* method
            # already treats "in active_widgets" as "currently open" to
            # decide whether to spawn fresh, and set_ui_mode("set_fullscreen")
            # unconditionally re-shows everything still in this dict on the
            # way back from overlay. Leaving a closed widget parked here
            # (merely hidden) meant it silently came back the next time the
            # user returned to fullscreen.
            w = self.active_widgets.pop(widget_id)
            w.hide()
            w.deleteLater()
            self.save_ui_state()

    def update_animation(self):
        self.visualizer.update_animation()

    def focusInEvent(self, event):
        import logging
        logging.debug(f"[DEBUG FOCUS] JarvisUI focusInEvent. Reason: {event.reason()}")
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        import logging
        logging.debug(f"[DEBUG FOCUS] JarvisUI focusOutEvent. Reason: {event.reason()}")
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        import logging
        logging.debug(f"[DEBUG KEY] JarvisUI keyPressEvent: key={event.key()} text={event.text()}")
        super().keyPressEvent(event)

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

    def _set_win32_click_through(self, transparent: bool) -> None:
        """Toggles WS_EX_TRANSPARENT -- the Win32 equivalent of Qt's WA_TransparentForMouseEvents."""
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

        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        style = GetWindowLong(hwnd, GWL_EXSTYLE)
        if style is None:
            return
        new_style = (style | WS_EX_TRANSPARENT) if transparent else (style & ~WS_EX_TRANSPARENT)
        SetWindowLong(hwnd, GWL_EXSTYLE, new_style)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0023)  # SWP_NOMOVE|SWP_NOSIZE|SWP_FRAMECHANGED

    def _screen_for_cursor(self):
        """Which screen the mouse cursor is currently on, falling back to
        the primary screen. Split out of set_ui_mode so tests can mock this
        one Python-level method instead of QCursor.pos() itself -- that's a
        sip/C++-bound static method, and patching it directly crashes the
        process rather than raising a normal Python exception."""
        from PyQt6.QtGui import QCursor
        screen = QApplication.screenAt(QCursor.pos())
        return screen or QApplication.primaryScreen()

    def set_ui_mode(self, mode):
        if mode == "save_state":
            self.save_ui_state()
            return
            
        if mode == "show_logs":
            self._toggle_debug()
            return
            
        if mode == "set_fullscreen":
            logging.debug(f"[DEBUG UI] set_fullscreen triggered. is_fullscreen: {getattr(self, 'is_fullscreen', False)}")
            screens = UIScaler.get().get_stable_screens()
            is_monitor_swap = getattr(self, 'is_fullscreen', False)
            old_geom = None

            if is_monitor_swap:
                self.current_monitor_idx = (getattr(self, 'current_monitor_idx', 0) + 1) % len(screens)
                self.save_ui_state()
                UIScaler.get().set_active_monitor(self.current_monitor_idx)

                # Save widget visibility before hiding
                widget_visibility = {wid: w.isVisible() for wid, w in self.active_widgets.items()}
                
                # To prevent the old 1366x768 buffer from flashing on the new monitor,
                # we temporarily hide all widgets and skip drawing the background.
                self._is_swapping_monitors = True
                self.visualizer.hide()
                for w in self.active_widgets.values():
                    w.hide()
                
                # To swap monitors in XWayland/X11, we must un-fullscreen the window first.
                # We DO NOT call QApplication.processEvents() here.
                self.hide()
            else:
                active_screen = self._screen_for_cursor()

                self.current_monitor_idx = 0
                active_name = active_screen.name() if active_screen else ""
                for i, s in enumerate(screens):
                    if s.name() == active_name:
                        self.current_monitor_idx = i
                        break
                
                UIScaler.get().set_active_monitor(self.current_monitor_idx)
                
                # Wayland maps new windows to where the cursor is, but for the initial
                # spawn we just ensure the surface is created.
                self.hide()
                QApplication.processEvents()

            # Set attributes BEFORE changing window flags, because setWindowFlags 
            # might recreate the native Wayland surface using the current attributes.
            # If WA_ShowWithoutActivating is True during recreation, Wayland denies focus permanently.
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            if sys.platform != "win32":
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.clearMask()
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

            # No WindowStaysOnTopHint: the dashboard no longer auto-collapses when occluded.
            flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            self.setWindowFlags(flags)
            logging.info("[DEBUG UI] Window flags set.")
            
            self.is_fullscreen = True

            # Force native window creation so windowHandle() becomes available without mapping the window yet
            self.winId()
            
            target_screen = screens[self.current_monitor_idx]
            if self.windowHandle():
                self.windowHandle().setScreen(target_screen)
            
            geom = target_screen.geometry()
            
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            
            # Using move and resize exactly as proven in the test script
            self.move(geom.topLeft())
            self.resize(geom.width(), geom.height())
            
            # Reset visualizer to IDLE before transitioning — prevents stale RECORDING state
            # from a previous TTS+mic cycle being inherited by the fullscreen view
            self.state = "IDLE"
            self.visualizer.set_state("IDLE", True)
            
            self.visualizer.lower()
            
            self.refresh_layout(force_monitor_idx=self.current_monitor_idx)
            self.btn_calendar.setIcon(Theme.get_icon("chevron_left.svg", 14))
            self.calendar_is_open = False
            
            self.btn_media.show()
            self.btn_lights.show()
            self.btn_reminders.show()
            self.btn_todos.show()
            self.btn_settings.show()
            self.btn_updates.show()
            self.btn_calendar.show()
            self.pill_mic.show()
            self.pill_speaker.show()

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

            logging.debug(f"[DEBUG UI] Calling showFullScreen(). Current focus: {self.hasFocus()}")
            self.showFullScreen()
            
            if is_monitor_swap:
                # Re-enable paint events, restore visibility, and force a repaint
                self._is_swapping_monitors = False
                self.setUpdatesEnabled(True)
                
                if getattr(self, 'state', 'IDLE') == 'IDLE':
                    self.visualizer.show()
                
                self.update()
                QApplication.processEvents()
                
            logging.debug(f"[DEBUG UI] After showFullScreen() -> isVisible: {self.isVisible()}, isFullScreen: {self.isFullScreen()}")
            
            def force_focus():
                self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
                self.raise_()
                self.activateWindow() 
                self.setFocus()
                logging.debug(f"[DEBUG UI] After delayed activate/focus -> isActiveWindow: {self.isActiveWindow()}, hasFocus: {self.hasFocus()}")
            
            # Delay focus grab slightly on Wayland to allow compositor to map the fullscreen surface
            QTimer.singleShot(150, force_focus)
            
            if sys.platform == "win32":
                self._set_win32_click_through(False)

            self.raise_()
            self.activateWindow() 
            self.setFocus()
            
            pass
            
            if QApplication.applicationState() == Qt.ApplicationState.ApplicationActive:
                self._on_app_state_changed(Qt.ApplicationState.ApplicationActive)

            # Persist is_fullscreen immediately rather than relying on the pre-kill save signal.
            self.save_ui_state()

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
            self.btn_debug.hide()
            self.btn_calendar.hide()
            self.pill_mic.hide()
            self.pill_speaker.hide()
            self.calendar_drawer.hide()
            
            if getattr(self, 'calendar_is_open', False):
                self.calendar_is_open = False
                self.btn_calendar.setIcon(Theme.get_icon("chevron_left.svg", 14))
                if hasattr(self, 'calendar_animation') and self.calendar_animation.state() == QPropertyAnimation.State.Running:
                    self.calendar_animation.stop()
                self.save_ui_state()
            
            self.reminder_widget.hide()
                
            # Hide all dashboard widgets except options prompts
            for wid, w in self.active_widgets.items():
                if not wid.startswith("list_"):
                    w.hide()
                else:
                    if hasattr(w, "title_bar"):
                        w.title_bar.hide()
                    w.adjustSize()
                    cx = (self.width() - w.width()) // 2
                    w.move(int(cx), 10)
            
            self.hide()
            
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            if sys.platform != "win32":
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

            flags = (
                Qt.WindowType.FramelessWindowHint | 
                Qt.WindowType.WindowStaysOnTopHint | 
                Qt.WindowType.Tool
            )
            if sys.platform != "win32":
                flags |= Qt.WindowType.WindowTransparentForInput
                
            self.setWindowFlags(flags)
            
            screens = UIScaler.get().get_stable_screens()
            idx = UIScaler.get().get_primary_monitor_idx()
            target_screen = screens[idx] if idx < len(screens) else screens[0]
            screen_geom = target_screen.availableGeometry()
            s = UIScaler.get().scale
            width, height = s(200), s(400)
            x_pos = screen_geom.right() - width - 20
            y_pos = screen_geom.bottom() - height - 20
            
            self.showNormal()
            self.setFixedSize(width, height)
            self.setGeometry(x_pos, y_pos, width, height)
            
            if sys.platform == "win32":
                self._set_win32_click_through(True)

            # Persist is_fullscreen=False immediately (see set_fullscreen branch above).
            self.save_ui_state()

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
        if getattr(self, '_is_swapping_monitors', False):
            # Skip drawing background during monitor swap to stay 100% transparent
            return
            
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

        # A faint warm orange tint radiating from the exact center, fading
        # to nothing well before the edges (which stay exactly as they were).
        # Many stops (not just 3) so the falloff reads as one smooth curve --
        # a single midpoint stop leaves two straight segments that meet at a
        # visible kink where their slopes change.
        ambient_grad = QRadialGradient(cx, cy, max(self.width(), self.height()) * 0.7)
        ambient_grad.setColorAt(0.0, QColor(255, 150, 0, 11))
        ambient_grad.setColorAt(0.15, QColor(255, 145, 0, 9))
        ambient_grad.setColorAt(0.3, QColor(255, 140, 0, 7))
        ambient_grad.setColorAt(0.45, QColor(255, 135, 0, 5))
        ambient_grad.setColorAt(0.6, QColor(255, 125, 0, 3))
        ambient_grad.setColorAt(0.8, QColor(255, 110, 0, 1))
        ambient_grad.setColorAt(1.0, QColor(255, 100, 0, 0))
        painter.setBrush(ambient_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())

    def closeEvent(self, event):
        self.save_ui_state()
        super().closeEvent(event)

    def save_ui_state(self):
        try:
            is_fullscreen = getattr(self, 'is_fullscreen', False)

            if is_fullscreen:
                active_widgets_data = {}
                for wid, wrapper in self.active_widgets.items():
                    active_widgets_data[wid] = {
                        "visible": wrapper.isVisible(),
                        "pos": [wrapper.x(), wrapper.y()],
                        "size": [wrapper.width(), wrapper.height()],
                        "is_unpinned": getattr(wrapper, "is_unpinned", False)
                    }

                reminder_data = {
                    "visible": self.reminder_widget.isVisible() if hasattr(self, 'reminder_widget') else False
                }

                drawer_open = getattr(self, 'calendar_is_open', False)
                carousel_idx = self.calendar_drawer.carousel.stack.currentIndex() if hasattr(self, 'calendar_drawer') else 0

                # The real monitor resolution, not self.width()/height() --
                # saving while collapsed to the tiny overlay box would
                # otherwise pair every widget's real (fullscreen) position
                # with a tiny "canvas" size, corrupting the scale math on the
                # next restore.
                target_screen = self.screen()
                screen_size = [target_screen.geometry().width(), target_screen.geometry().height()] if target_screen else [self.width(), self.height()]
                current_monitor_idx = getattr(self, 'current_monitor_idx', 0)
            else:
                # Overlay force-hides every widget/drawer -- that's a
                # transient view change, not the user closing anything, so a
                # save made here must carry the last real fullscreen layout
                # forward unchanged instead of overwriting it with "nothing
                # is visible, everything is at overlay's tiny geometry".
                existing = {}
                if os.path.exists(STATE_FILE):
                    try:
                        with open(STATE_FILE, "r", encoding="utf-8") as f:
                            existing = json.load(f)
                    except Exception:
                        existing = {}
                active_widgets_data = existing.get("active_widgets", {})
                reminder_data = existing.get("reminder_widget", {"visible": False})
                drawer_open = existing.get("drawer_open", False)
                carousel_idx = existing.get("carousel_tab", 0)
                screen_size = existing.get("screen_size", [self.width(), self.height()])
                current_monitor_idx = existing.get("current_monitor_idx", getattr(self, 'current_monitor_idx', 0))

            state_payload = {
                "drawer_open": drawer_open,
                "carousel_tab": carousel_idx,
                "reminder_widget": reminder_data,
                "active_widgets": active_widgets_data,
                "current_monitor_idx": current_monitor_idx,
                "screen_size": screen_size,
                "is_fullscreen": is_fullscreen
            }

            # Write via a temp file + atomic replace -- stop_native() kills this
            # process outright (TerminateProcess on Windows) shortly after
            # requesting this save, and a write caught mid-flush would leave
            # truncated/corrupt JSON that silently fails to load on the next
            # boot, discarding the whole saved session back to a cold overlay.
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            tmp_path = STATE_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state_payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, STATE_FILE)
        except Exception as e:
            logging.error(f"Failed to save UI state: {e}")

    def load_ui_state(self, restore_widgets=True):
        if not os.path.exists(STATE_FILE):
            return None

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
                if rem_state.get("visible", False):
                    self.reminder_widget.show()
                    self.reminder_widget.raise_(); self._enforce_z_order()
                else:
                    self.reminder_widget.hide()


            # 4. Draggable Floating Widgets (Media, Lights, To-Do)
            if restore_widgets:
                self._restoring_ui_state = True
                active_state = state.get("active_widgets", {})
                for widget_id, info in active_state.items():
                    is_visible = info.get("visible", False)
                    pos = info.get("pos")
                    size = info.get("size")
                    # Guards visibility only (not pos/size/pin) against a stale on-disk snapshot below.
                    already_existed = widget_id in self.active_widgets

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

                        # Scale against the real target monitor's resolution, not
                        # self.width()/height() -- on a reboot this runs while the
                        # window is still sized as the tiny overlay box (pre-
                        # fullscreen transition), which used to crush every saved
                        # position toward (0,0).
                        target_screen = self.screen()
                        canvas_w = target_screen.geometry().width() if target_screen else self.width()
                        canvas_h = target_screen.geometry().height() if target_screen else self.height()

                        if pos and len(pos) == 2:
                            prev_screen = state.get("screen_size", [1920, 1080]) # Fallback for old states
                            scale_x = canvas_w / max(1, prev_screen[0])
                            scale_y = canvas_h / max(1, prev_screen[1])

                            p_x = int(pos[0] * scale_x)
                            p_y = int(pos[1] * scale_y)

                            # Clamp to current screen bounds
                            p_x = max(0, min(p_x, canvas_w - 50))
                            p_y = max(0, min(p_y, canvas_h - 50))

                            w.move(p_x, p_y)

                        if size and len(size) == 2:
                            # Scale the saved size to match the current monitor proportions just like we do for position
                            prev_screen = state.get("screen_size", [1920, 1080])
                            scale_x = canvas_w / max(1, prev_screen[0])
                            scale_y = canvas_h / max(1, prev_screen[1])
                            s_w = int(size[0] * scale_x)
                            s_h = int(size[1] * scale_y)
                            w.resize(s_w, s_h)

                        # Bidirectional: also re-pin a widget saved as pinned but
                        # currently unpinned (e.g. forced so by an overlay-mode spawn).
                        saved_unpinned = info.get("is_unpinned", False)
                        if hasattr(w, "toggle_pin") and w.is_unpinned != saved_unpinned:
                            w.toggle_pin(force_unpin=saved_unpinned)

                        if w.is_unpinned:
                            # toggle_pin()'s unpin branch turns the already-clamped
                            # local position above into a global one via
                            # mapToGlobal() -- if that position was saved while on
                            # a different monitor arrangement, it can land outside
                            # any real screen. Clamp it back onto the current one.
                            screen_geom = self.screen().geometry()
                            clamped_x = max(screen_geom.x(), min(w.x(), screen_geom.x() + screen_geom.width() - 50))
                            clamped_y = max(screen_geom.y(), min(w.y(), screen_geom.y() + screen_geom.height() - 50))
                            if (clamped_x, clamped_y) != (w.x(), w.y()):
                                w.move(clamped_x, clamped_y)

                        if not already_existed:
                            if is_visible:
                                w.show()
                                w.raise_(); self._enforce_z_order()
                            else:
                                w.hide()
                self._restoring_ui_state = False

            return state
        except Exception as e:
            logging.error(f"Failed to load UI state: {e}")
            return None
        finally:
            self._restoring_ui_state = False


def _load_bundled_fonts():
    """DejaVu Sans Mono isn't preinstalled on Windows, so it's bundled here
    (assets/fonts/) and registered via Qt's font database -- guarantees the
    same font renders identically on both machines regardless of what's
    already installed on either."""
    from PyQt6.QtGui import QFontDatabase
    fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "fonts")
    for filename in ["DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf", "DejaVuSansMono-Oblique.ttf", "DejaVuSansMono-BoldOblique.ttf"]:
        path = os.path.join(fonts_dir, filename)
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)
        else:
            logging.warning(f"Bundled font not found: {path}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    _load_bundled_fonts()
    window = JarvisUI()
    window.show()
    sys.exit(app.exec())
