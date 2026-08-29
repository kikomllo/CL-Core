import json
import logging
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import QTimer
from utils.clActionRouter import ActionRouter

class LightControlWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.layout.setSpacing(8)
        self.setMinimumWidth(340)
        
        # Title and Refresh
        top_layout = QHBoxLayout()
        self.title_lbl = QLabel("Smart Lights")
        self.title_lbl.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 13pt;")
        
        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedSize(30, 30)
        refresh_btn.setStyleSheet("QPushButton { text-align: center; padding-bottom: 4px; background-color: rgba(255, 150, 0, 20); color: #ffaa00; border-radius: 15px; border: 1px solid rgba(255,150,0,80); font-size: 14pt; } QPushButton:hover { background-color: rgba(255, 150, 0, 60); }")
        refresh_btn.clicked.connect(lambda: self.send_cmd("refresh_lights", "all"))
        
        top_layout.addWidget(self.title_lbl)
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
        btn.setStyleSheet("QPushButton { text-align: center; padding-bottom: 4px; background-color: rgba(255, 50, 0, 40); color: #ffaa00; border-radius: 5px; padding: 5px; border: 1px solid rgba(255,100,0,80); } QPushButton:hover { background-color: rgba(255, 50, 0, 80); }")
        btn.clicked.connect(lambda: self.send_cmd("off", "all", silent=True))
        self.layout.addWidget(btn)
        
        self.light_rows = {}

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self.window(), 'is_fullscreen', False):
            try:
                self.router.dispatch("light.set", action="refresh_lights", light_target="all")
            except Exception as e:
                pass

    def send_cmd(self, action, target, silent=False):
        import paho.mqtt.publish as publish
        try:
            self.router.dispatch("light.set", action=action, light_target=target, silent=silent)
        except Exception as e:
            logging.error(f"Failed to publish light control: {e}")

    def _delete_light(self, target_name):
        import paho.mqtt.publish as publish
        try:
            self.router.dispatch("system.discovery", action="intent_remove_light", target_str=target_name)
        except Exception as e:
            logging.error(f"Failed to publish delete: {e}")
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
        network_name = data.get("network", "")
        if network_name:
            self.title_lbl.setText(f"Smart Lights ({network_name})")
            
        lights = data.get("lights", [])
        
        # Remove loading placeholder once real data arrives
        if self._loading_lbl is not None:
            self._loading_lbl.deleteLater()
            self._loading_lbl = None
        
        if not lights:
            for r in list(self.light_rows.values()):
                r["widget"].deleteLater()
            self.light_rows.clear()
            
            if getattr(self, '_empty_lbl', None) is None:
                self._empty_lbl = QLabel("No lights configured for this network.")
                self._empty_lbl.setStyleSheet("color: rgba(150, 150, 150, 255); font-style: italic; font-size: 9.5pt;")
                self.lights_layout.addWidget(self._empty_lbl)
            return
        elif getattr(self, '_empty_lbl', None) is not None:
            self._empty_lbl.deleteLater()
            self._empty_lbl = None

        new_targets = {l.get("name", "").lower().replace(" ", "_") for l in lights}
        for old_target in list(self.light_rows.keys()):
            if old_target not in new_targets:
                self.light_rows[old_target]["widget"].deleteLater()
                del self.light_rows[old_target]
            
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
                
                btn_style = "QPushButton { text-align: center; background-color: rgba(255, 150, 0, 20); color: #ffaa00; border-radius: 5px; font-size: 10pt; border: 1px solid rgba(255,150,0,50); padding: 2px 8px; padding-bottom: 4px; } QPushButton:hover { background-color: rgba(255, 150, 0, 60); }"
                
                toggle_btn = QPushButton("Toggle")
                toggle_btn.setMinimumHeight(24)
                toggle_btn.setStyleSheet(btn_style)
                toggle_btn.clicked.connect(lambda checked, t=target_name: self.send_cmd("toggle", t, silent=True))
                
                delete_btn = QPushButton("X")
                delete_btn.setFixedSize(26, 24)
                delete_btn.setToolTip("Remove light")
                delete_btn.setStyleSheet("QPushButton { text-align: center; padding-bottom: 4px; background-color: rgba(255, 150, 0, 20); color: #ffaa00; border-radius: 5px; border: 1px solid rgba(255,150,0,50); font-size: 11pt; font-weight: bold; font-family: monospace; } QPushButton:hover { background-color: rgba(255, 150, 0, 60); color: #ffffff; }")
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
        QTimer.singleShot(0, self.adjustSize)
        QTimer.singleShot(50, self.adjustSize)
