import json
import logging
from clTheme import Theme
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import QTimer
from utils.clActionRouter import ActionRouter
from clUIScaler import UIScaler

def s(val):
    return UIScaler.get().scale(val)

class LightControlWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(s(15), s(15), s(15), s(15))
        self.layout.setSpacing(s(8))
        
        # Title and Refresh
        top_layout = QHBoxLayout()
        self.title_lbl = QLabel("Smart Lights")
        self.title_lbl.setStyleSheet(Theme.get_style("TitleLabel"))
        
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setFixedSize(30, 30)
        self.refresh_btn.setStyleSheet(Theme.get_style("RefreshButton"))
        self.refresh_btn.clicked.connect(lambda: self.send_cmd("refresh_lights", "all"))
        
        top_layout.addWidget(self.title_lbl)
        top_layout.addStretch()
        top_layout.addWidget(self.refresh_btn)
        self.layout.addLayout(top_layout)
        

        
        # Lights container
        self.lights_container = QWidget()
        self.lights_layout = QVBoxLayout(self.lights_container)
        self.lights_layout.setContentsMargins(0, 0, 0, 0)
        self.lights_layout.setSpacing(s(5))
        
        # Loading placeholder shown until first real data arrives
        self._loading_lbl = QLabel("⏳ Loading lights...")
        self._loading_lbl.setStyleSheet(Theme.get_style("DimLabel"))
        self.lights_layout.addWidget(self._loading_lbl)
        
        self.layout.addWidget(self.lights_container)
        
        # All off button
        btn = QPushButton("Toggle All Off")
        btn.setStyleSheet(Theme.get_style("LightDangerButton"))
        btn.clicked.connect(lambda: self.send_cmd("off", "all", silent=True))
        self.layout.addWidget(btn)
        
        self.light_rows = {}

    def update_scaling(self):
        self.layout.setContentsMargins(s(15), s(15), s(15), s(15))
        self.layout.setSpacing(s(8))
        if hasattr(self, 'refresh_btn'):
            self.refresh_btn.setFixedSize(30, 30)
        if hasattr(self, 'lights_layout'):
            self.lights_layout.setSpacing(s(5))
        for row_data in self.light_rows.values():
            if "layout" in row_data:
                row_data["layout"].setSpacing(s(6))
            if "indicator" in row_data:
                row_data["indicator"].setFixedWidth(22)
            if "toggle_btn" in row_data:
                row_data["toggle_btn"].setMinimumHeight(26)
            if "delete_btn" in row_data:
                row_data["delete_btn"].setFixedSize(26, 26)
        if hasattr(self, 'lights_container'):
            self.lights_container.adjustSize()
        self.adjustSize()

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
                self._empty_lbl.setStyleSheet(Theme.get_style("DimLabel"))
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
                row_layout.setSpacing(s(6))
                
                indicator = QLabel("●")
                indicator.setStyleSheet("color: rgba(100, 100, 100, 255); font-size: 16pt;")
                indicator.setFixedWidth(22)
                self._update_indicator(indicator, is_on, is_offline)
                
                name_lbl = QLabel(l.get("name", "Unknown"))
                name_lbl.setStyleSheet(Theme.get_style("SubtitleLabel"))
                
                toggle_btn = QPushButton("Toggle")
                toggle_btn.setMinimumHeight(26)
                toggle_btn.setStyleSheet(Theme.get_style("SecondaryButton"))
                toggle_btn.clicked.connect(lambda checked, t=target_name: self.send_cmd("toggle", t, silent=True))
                
                delete_btn = QPushButton("X")
                delete_btn.setFixedSize(26, 26)
                delete_btn.setToolTip("Remove light")
                delete_btn.setStyleSheet(Theme.get_style("SmallDangerButton"))
                delete_btn.clicked.connect(lambda checked, t=target_name: self._delete_light(t))
                
                row_layout.addWidget(indicator)
                row_layout.addWidget(name_lbl)
                row_layout.addStretch()
                row_layout.addWidget(toggle_btn)
                row_layout.addWidget(delete_btn)
                
                self.lights_layout.addWidget(row)
                self.light_rows[target_name] = {
                    "widget": row,
                    "layout": row_layout,
                    "indicator": indicator,
                    "toggle_btn": toggle_btn,
                    "delete_btn": delete_btn,
                    "is_on": is_on,
                    "is_offline": is_offline
                }
            
        self.lights_container.adjustSize()
        self.adjustSize()
        # Deferred resize ensures the parent wrapper picks up the new layout geometry
        QTimer.singleShot(0, lambda: self.window().adjustSize())
        QTimer.singleShot(50, lambda: self.window().adjustSize())

    def get_standalone_min_size(self):
        return 340, 300
