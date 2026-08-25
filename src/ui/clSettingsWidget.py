import json
import os
from utils.clActionRouter import ActionRouter
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame, QCheckBox, QComboBox, QPushButton, QTabWidget, QLineEdit, QInputDialog
from PyQt6.QtCore import Qt, QTimer
from utils.clConfigLoader import ConfigLoader
from utils.clEnvLoader import EnvLoader

class CollapsibleBlock(QWidget):
    """Code editor style collapsible container widget for individual smart lights."""
    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.is_expanded = False
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # --- HEADER BUTTON ---
        self.header_btn = QPushButton()
        self.header_btn.setCheckable(True)
        self.header_btn.setChecked(False)
        self.header_btn.setFixedHeight(30)
        self.header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(35, 18, 5, 220);
                color: #ffe6cc;
                border: 1px solid rgba(255, 180, 0, 80);
                border-radius: 4px;
                text-align: left;
                padding-left: 8px;
                font-family: 'Courier New';
                font-size: 9pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(55, 30, 10, 255);
                border: 1px solid #ffaa00;
            }
        """)
        self.header_btn.clicked.connect(self.toggle_collapse)
        self.main_layout.addWidget(self.header_btn)
        
        # --- BODY CONTAINER ---
        self.body_widget = QWidget()
        self.body_widget.setStyleSheet("""
            QWidget {
                background-color: rgba(20, 10, 2, 180);
                border-left: 2px solid rgba(255, 170, 0, 120);
                border-bottom: 1px solid rgba(255, 170, 0, 40);
                border-right: 1px solid rgba(255, 170, 0, 40);
                border-bottom-left-radius: 4px;
                border-bottom-right-radius: 4px;
            }
        """)
        self.body_layout = QVBoxLayout(self.body_widget)
        self.body_layout.setContentsMargins(12, 10, 12, 10)
        self.body_layout.setSpacing(10)
        
        self.main_layout.addWidget(self.body_widget)
        self.body_widget.hide()
        
        self.title = title
        self.subtitle = subtitle
        self.update_header_text()

    def update_header_text(self, new_title=None, new_subtitle=None):
        if new_title: self.title = new_title
        if new_subtitle is not None: self.subtitle = new_subtitle
        arrow = "▼" if self.is_expanded else "▶"
        sub = f"  [{self.subtitle}]" if self.subtitle else ""
        self.header_btn.setText(f"{arrow}  {self.title}{sub}")

    def toggle_collapse(self):
        self.is_expanded = not self.is_expanded
        self.body_widget.setVisible(self.is_expanded)
        self.update_header_text()


class SettingsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        self.setMinimumSize(325, 385)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 10)
        self.layout.setSpacing(0)
        
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: none; background: transparent; }
            QTabWidget::tab-bar { alignment: left; }
            QTabBar {
                background-color: rgba(255, 120, 0, 30);
                border-bottom: 1px solid rgba(255, 150, 0, 60);
                min-height: 24px;
                max-height: 24px;
            }
            QTabBar::tab { 
                background: transparent; 
                color: rgba(255, 200, 0, 180); 
                padding: 0px 12px; 
                height: 24px;
                border: none;
                font-family: 'Courier New';
                font-weight: bold;
                font-size: 8.5pt;
            }
            QTabBar::tab:hover {
                color: #ffffff;
                background-color: rgba(255, 150, 0, 40);
            }
            QTabBar::tab:selected { 
                background-color: rgba(255, 150, 0, 70); 
                color: #ffaa00; 
                border-bottom: 2px solid #ffaa00; 
            }
            QTabBar::scroller { width: 20px; height: 24px; }
            QTabBar QToolButton { background: transparent; border: none; color: #ffaa00; height: 24px; }
        """)
        
        self.layout.addWidget(self.tabs)
        
        self.loader = ConfigLoader()
        self.env_loader = EnvLoader()
        self.core_json_path = os.path.join(self.loader.config_dir, "core.json")
        self.devices_json_path = os.path.join(self.loader.config_dir, "devices.json")
        self.last_mtime = 0
        
        self.ui_elements = {}
        self.needs_reboot = False
        
        self._build_ui()
        
        # Reboot Button at bottom
        self.reboot_btn = QPushButton("Save & Reboot Ecosystem")
        self.reboot_btn.setFixedHeight(32)
        self.reboot_btn.setStyleSheet("""
            QPushButton {
                text-align: center;
                padding-bottom: 4px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(200, 30, 0, 0.4), stop:1 rgba(150, 10, 0, 0.4));
                color: #ffcccc;
                border-radius: 6px;
                border: 1px solid rgba(255, 50, 0, 0.5);
                font-weight: bold;
                font-size: 9pt;
                margin-left: 15px;
                margin-right: 15px;
                margin-bottom: 10px;
                margin-top: 5px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(220, 50, 0, 0.6), stop:1 rgba(180, 20, 0, 0.6));
                color: #ffffff;
                border: 1px solid #ff4400;
            }
        """)
        self.reboot_btn.clicked.connect(self._reboot_ecosystem)
        self.reboot_btn.hide()
        self.layout.addWidget(self.reboot_btn)
        
        self.tabs.currentChanged.connect(self._on_tab_changed)
        
        self._check_for_updates()
        
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._check_for_updates)
        self.update_timer.start(2000)

    def _create_scroll_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollBar:vertical { width: 6px; background: rgba(0,0,0,40); border-radius: 3px; } QScrollBar::handle:vertical { background: rgba(255,170,0,120); border-radius: 3px; }")
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(15, 12, 15, 30)
        scroll_layout.setSpacing(12)
        scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(scroll_content)
        
        return scroll, scroll_layout

    def _create_section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: rgba(255, 170, 0, 180); font-weight: bold; font-size: 9.5pt; border-bottom: 1px solid rgba(255, 150, 0, 50); padding-bottom: 2px;")
        return lbl

    def _create_checkbox(self, key, label_text, is_checked, callback):
        widget = QWidget()
        lay = QHBoxLayout(widget)
        lay.setContentsMargins(0,0,0,0)
        
        chk = QCheckBox()
        chk.setStyleSheet("""
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid rgba(255, 170, 0, 0.5);
                background: rgba(35, 18, 5, 0.8);
            }
            QCheckBox::indicator:hover {
                border: 1px solid #ffaa00;
                background: rgba(255, 150, 0, 0.3);
            }
            QCheckBox::indicator:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff8c00, stop:1 #e65c00);
                border: 1px solid #ffbb33;
            }
        """)
        chk.setChecked(is_checked)
        chk.stateChanged.connect(callback)
        
        self.ui_elements[key] = chk
        
        lbl = QLabel(label_text)
        lbl.setStyleSheet("color: #ffe6cc; font-size: 9.5pt;")
        
        lay.addWidget(chk)
        lay.addWidget(lbl, 1)
        
        widget.mouseReleaseEvent = lambda e, c=chk: c.setChecked(not c.isChecked()) if e.button() == Qt.MouseButton.LeftButton else None
        return widget

    def _create_dropdown(self, key, label_text, options, current_val, callback):
        widget = QWidget()
        lay = QHBoxLayout(widget)
        lay.setContentsMargins(0,0,0,0)
        
        lbl = QLabel(label_text)
        lbl.setStyleSheet("color: #ffe6cc; font-size: 9.5pt;")
        
        combo = QComboBox()
        combo.addItems(options)
        if current_val in options:
            combo.setCurrentText(current_val)
        combo.setStyleSheet("""
            QComboBox {
                background-color: rgba(25, 12, 3, 240);
                color: #ffe6cc;
                border: 1px solid rgba(255, 180, 0, 100);
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 9pt;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: rgba(20, 10, 0, 240);
                color: #ffe6cc;
                selection-background-color: rgba(255, 150, 0, 100);
            }
        """)
        combo.currentTextChanged.connect(callback)
        
        self.ui_elements[key] = combo
        
        lay.addWidget(lbl, 1)
        lay.addWidget(combo, 1)
        return widget

    def _create_line_edit(self, key, label_text, current_val, is_password, callback):
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(0,0,0,0)
        lay.setSpacing(3)
        
        lbl = QLabel(label_text)
        lbl.setStyleSheet("color: rgba(255, 230, 204, 200); font-size: 8.5pt;")
        
        line_edit = QLineEdit()
        if current_val is not None:
            line_edit.setText(str(current_val))
            
        if is_password:
            line_edit.setEchoMode(QLineEdit.EchoMode.Password)
            
        line_edit.setStyleSheet("""
            QLineEdit {
                background-color: rgba(25, 12, 3, 240);
                color: #ffe6cc;
                border: 1px solid rgba(255, 180, 0, 80);
                border-radius: 4px;
                padding: 5px;
                font-size: 9pt;
            }
            QLineEdit:focus {
                border: 1px solid #ffaa00;
                background-color: rgba(35, 18, 5, 255);
            }
        """)
        
        line_edit.editingFinished.connect(lambda: callback(key, line_edit.text()))
        self.ui_elements[key] = line_edit
        
        lay.addWidget(lbl)
        lay.addWidget(line_edit)
        return widget

    def _build_ui(self):
        # --- TAB 1: SYSTEM ---
        system_scroll, sys_layout = self._create_scroll_tab()
        self.tabs.addTab(system_scroll, "System")
        
        sys_layout.addWidget(self._create_section_label("Behavior"))
        sys_layout.addWidget(self._create_checkbox("silent_mode", "Silent Mode (No Voice Output)", False, self._toggle_silent_mode))
        sys_layout.addWidget(self._create_checkbox("enable_followup", "Follow-ups", False, self._toggle_followup))
        
        sys_layout.addWidget(self._create_section_label("System Core"))
        sys_layout.addWidget(self._create_dropdown("ecosystem_state", "Ecosystem Mode", ["normal", "debug", "background"], "normal", self._change_ecosystem_mode))
        
        sys_layout.addWidget(self._create_section_label("Inference Engine"))
        sys_layout.addWidget(self._create_dropdown("stt_model", "STT Model Size", ["tiny", "base", "small", "medium", "large"], "small", self._change_stt_model))
        sys_layout.addWidget(self._create_dropdown("hardware", "Hardware Acceleration", ["cpu", "cuda"], "cpu", self._change_hardware))
        sys_layout.addSpacing(20)

        # --- TAB 2: SMART LIGHTS ---
        lights_scroll, lights_layout = self._create_scroll_tab()
        self.tabs.addTab(lights_scroll, "Smart Lights")
        self.lights_layout = lights_layout
        
        lights_layout.addWidget(self._create_section_label("Global Light Settings"))
        lights_layout.addWidget(self._create_line_edit("LIGHT_TYPE", "Default Light Type (wiz/tapo)", self.env_loader.get("LIGHT_TYPE"), False, self._update_env))
        lights_layout.addWidget(self._create_line_edit("LIGHT_IP", "Default IP Address", self.env_loader.get("LIGHT_IP"), False, self._update_env))
        lights_layout.addWidget(self._create_line_edit("LIGHT_MAC", "Default MAC Address", self.env_loader.get("LIGHT_MAC"), False, self._update_env))
        
        lights_layout.addWidget(self._create_section_label("Tapo Account Credentials"))
        lights_layout.addWidget(self._create_line_edit("TAPO_EMAIL", "Email", self.env_loader.get("TAPO_EMAIL"), False, self._update_env))
        lights_layout.addWidget(self._create_line_edit("TAPO_PASSWORD", "Password", self.env_loader.get("TAPO_PASSWORD"), True, self._update_env))

        # Dynamic WiFi Network Light Groups
        self.networks_container = QWidget()
        self.networks_layout = QVBoxLayout(self.networks_container)
        self.networks_layout.setContentsMargins(0, 5, 0, 0)
        self.networks_layout.setSpacing(15)
        lights_layout.addWidget(self.networks_container)
        
        self._populate_network_lights()

        # Button to add new WiFi Network Group
        add_net_btn = QPushButton("+ Add WiFi Network")
        add_net_btn.setFixedHeight(28)
        add_net_btn.setStyleSheet("""
            QPushButton {
                text-align: center;
                padding-bottom: 4px;
                background-color: rgba(40, 25, 10, 200);
                color: #ffaa00;
                border: 1px dashed rgba(255, 170, 0, 120);
                border-radius: 4px;
                font-weight: bold;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background-color: rgba(70, 45, 15, 255);
                border: 1px solid #ffaa00;
            }
        """)
        add_net_btn.clicked.connect(self._add_wifi_network)
        lights_layout.addWidget(add_net_btn)
        lights_layout.addSpacing(25)

        # --- TAB 3: SPOTIFY ---
        spotify_scroll, spot_layout = self._create_scroll_tab()
        self.tabs.addTab(spotify_scroll, "Spotify")
        
        spot_layout.addWidget(self._create_section_label("API Credentials"))
        spot_layout.addWidget(self._create_line_edit("SPOTIPY_CLIENT_ID", "Client ID", self.env_loader.get("SPOTIPY_CLIENT_ID"), False, self._update_env))
        spot_layout.addWidget(self._create_line_edit("SPOTIPY_CLIENT_SECRET", "Client Secret", self.env_loader.get("SPOTIPY_CLIENT_SECRET"), True, self._update_env))
        spot_layout.addWidget(self._create_line_edit("SPOTIPY_REDIRECT_URI", "Redirect URI", self.env_loader.get("SPOTIPY_REDIRECT_URI"), False, self._update_env))
        spot_layout.addSpacing(20)

        # --- TAB 4: UPDATES ---
        updates_scroll, updates_layout = self._create_scroll_tab()
        self.tabs.addTab(updates_scroll, "Updates")
        
        updates_layout.addWidget(self._create_section_label("Update Settings"))
        
        # Load update mode from core.json
        current_mode = "confirm"
        try:
            cfg = self.loader.load("core.json")
            current_mode = cfg.get("settings", {}).get("update_mode", "confirm")
        except:
            pass
            
        updates_layout.addWidget(self._create_dropdown("update_mode", "Update Mode", ["confirm", "direct"], current_mode, self._change_update_mode))
        updates_layout.addSpacing(20)

    def _load_devices_json(self):
        if os.path.exists(self.devices_json_path):
            try:
                with open(self.devices_json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if "networks" in data:
                        return data
                    elif "lights" in data:
                        return {"networks": {"Home Network": data["lights"]}}
            except Exception:
                pass
        return {"networks": {"Home Network": {}}}

    def _save_devices_json(self, data):
        try:
            with open(self.devices_json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self._flag_reboot()
        except Exception:
            pass

    def _populate_network_lights(self):
        # Clear existing widgets in network container
        while self.networks_layout.count():
            item = self.networks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        devices_data = self._load_devices_json()
        networks = devices_data.get("networks", {})
        all_net_names = list(networks.keys())
        
        for net_name, lights in networks.items():
            net_section = QWidget()
            net_lay = QVBoxLayout(net_section)
            net_lay.setContentsMargins(0, 0, 0, 0)
            net_lay.setSpacing(8)
            
            # Network Header Bar
            header_widget = QWidget()
            header_lay = QHBoxLayout(header_widget)
            header_lay.setContentsMargins(0, 0, 0, 0)
            
            net_lbl = QLabel(f"{net_name}")
            net_lbl.setStyleSheet("color: rgba(255, 180, 0, 220); font-weight: bold; font-size: 10pt;")
            
            del_net_btn = QPushButton("Remove Network")
            del_net_btn.setFixedHeight(20)
            del_net_btn.setStyleSheet("""
                QPushButton { text-align: center; padding-bottom: 4px; background-color: rgba(180, 30, 30, 120); color: #ffaaaa; border-radius: 3px; font-size: 7.5pt; font-weight: bold; padding: 2px 6px; }
                QPushButton:hover { background-color: rgba(220, 40, 40, 200); color: #ffffff; }
            """)
            del_net_btn.clicked.connect(lambda _, n=net_name: self._remove_wifi_network(n))
            
            header_lay.addWidget(net_lbl)
            header_lay.addStretch()
            header_lay.addWidget(del_net_btn)
            
            divider = QFrame()
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setStyleSheet("color: rgba(255, 150, 0, 80);")
            
            net_lay.addWidget(header_widget)
            net_lay.addWidget(divider)
            
            # Collapsible Blocks for each light
            for l_name, l_info in lights.items():
                l_type = l_info.get("type", "wiz").upper()
                l_ip = l_info.get("ip", "")
                subtitle_str = f"{l_type} - {l_ip}" if l_ip else l_type
                
                block = CollapsibleBlock(title=l_name, subtitle=subtitle_str)
                
                # Fields inside collapsible body
                b_name = self._create_line_edit_raw("Name", l_name, lambda val, n=net_name, old_n=l_name: self._update_light_field(n, old_n, "name", val))
                b_net = self._create_dropdown_raw("WiFi Network", all_net_names, net_name, lambda val, n=net_name, ln=l_name: self._move_light_network(n, ln, val))
                b_type = self._create_dropdown_raw("Type", ["wiz", "tapo"], l_info.get("type", "wiz"), lambda val, n=net_name, ln=l_name: self._update_light_field(n, ln, "type", val))
                b_ip = self._create_line_edit_raw("IP Address", l_info.get("ip", ""), lambda val, n=net_name, ln=l_name: self._update_light_field(n, ln, "ip", val))
                b_mac = self._create_line_edit_raw("MAC Address", l_info.get("mac", ""), lambda val, n=net_name, ln=l_name: self._update_light_field(n, ln, "mac", val))
                
                # Delete Light Button
                del_btn = QPushButton("Remove Light")
                del_btn.setFixedHeight(24)
                del_btn.setStyleSheet("""
                    QPushButton { text-align: center; padding-bottom: 4px; background-color: rgba(180, 30, 30, 150); color: #ffcccc; border-radius: 4px; font-size: 8pt; font-weight: bold; }
                    QPushButton:hover { background-color: rgba(220, 40, 40, 220); color: #ffffff; }
                """)
                del_btn.clicked.connect(lambda _, n=net_name, ln=l_name: self._remove_light(n, ln))
                
                block.body_layout.addWidget(b_name)
                block.body_layout.addWidget(b_net)
                block.body_layout.addWidget(b_type)
                block.body_layout.addWidget(b_ip)
                block.body_layout.addWidget(b_mac)
                block.body_layout.addWidget(del_btn)
                
                net_lay.addWidget(block)
                
            # Button to add new light to this network
            add_l_btn = QPushButton(f"+ Add Light to {net_name}")
            add_l_btn.setFixedHeight(24)
            add_l_btn.setStyleSheet("""
                QPushButton { text-align: center; padding-bottom: 4px; background-color: rgba(30, 20, 5, 180); color: #ffbb44; border: 1px solid rgba(255, 170, 0, 60); border-radius: 4px; font-size: 8pt; }
                QPushButton:hover { background-color: rgba(60, 40, 10, 220); color: #ffffff; border: 1px solid #ffaa00; }
            """)
            add_l_btn.clicked.connect(lambda _, n=net_name: self._add_light(n))
            net_lay.addWidget(add_l_btn)
            
            self.networks_layout.addWidget(net_section)

    def _move_light_network(self, old_net, light_name, new_net):
        if old_net == new_net:
            return
        data = self._load_devices_json()
        if old_net in data["networks"] and light_name in data["networks"][old_net]:
            light_info = data["networks"][old_net].pop(light_name)
            if new_net not in data["networks"]:
                data["networks"][new_net] = {}
            data["networks"][new_net][light_name] = light_info
            self._save_devices_json(data)
            self._populate_network_lights()

    def _remove_wifi_network(self, net_name):
        data = self._load_devices_json()
        if net_name in data["networks"]:
            del data["networks"][net_name]
            self._save_devices_json(data)
            self._populate_network_lights()

    def _create_line_edit_raw(self, label_text, current_val, callback):
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(0,0,0,0)
        lay.setSpacing(2)
        
        lbl = QLabel(label_text)
        lbl.setStyleSheet("color: rgba(255, 230, 204, 180); font-size: 8pt;")
        
        line_edit = QLineEdit()
        line_edit.setText(str(current_val) if current_val else "")
        line_edit.setStyleSheet("""
            QLineEdit {
                background-color: rgba(15, 8, 2, 220);
                color: #ffe6cc;
                border: 1px solid rgba(255, 180, 0, 60);
                border-radius: 3px;
                padding: 4px;
                font-size: 8.5pt;
            }
            QLineEdit:focus { border: 1px solid #ffaa00; }
        """)
        line_edit.editingFinished.connect(lambda: callback(line_edit.text()))
        
        lay.addWidget(lbl)
        lay.addWidget(line_edit)
        return widget

    def _create_dropdown_raw(self, label_text, options, current_val, callback):
        widget = QWidget()
        lay = QHBoxLayout(widget)
        lay.setContentsMargins(0,0,0,0)
        
        lbl = QLabel(label_text)
        lbl.setStyleSheet("color: rgba(255, 230, 204, 180); font-size: 8pt;")
        
        combo = QComboBox()
        combo.addItems(options)
        if current_val in options: combo.setCurrentText(current_val)
        combo.setStyleSheet("""
            QComboBox {
                background-color: rgba(15, 8, 2, 220);
                color: #ffe6cc;
                border: 1px solid rgba(255, 180, 0, 60);
                border-radius: 3px;
                padding: 2px 5px;
                font-size: 8.5pt;
            }
            QComboBox::drop-down { border: none; }
        """)
        combo.currentTextChanged.connect(callback)
        
        lay.addWidget(lbl, 1)
        lay.addWidget(combo, 1)
        return widget

    def _update_light_field(self, net_name, light_name, field, new_val):
        data = self._load_devices_json()
        if net_name in data["networks"] and light_name in data["networks"][net_name]:
            if field == "name":
                new_val_clean = new_val.strip().lower().replace(" ", "_")
                if new_val_clean and new_val_clean != light_name:
                    data["networks"][net_name][new_val_clean] = data["networks"][net_name].pop(light_name)
            else:
                data["networks"][net_name][light_name][field] = new_val.strip()
            self._save_devices_json(data)
            self._populate_network_lights()

    def _add_light(self, net_name):
        name, ok = QInputDialog.getText(self, "Add Light", f"Enter light name for {net_name}:")
        if ok and name.strip():
            clean_name = name.strip().lower().replace(" ", "_")
            data = self._load_devices_json()
            if net_name in data["networks"]:
                data["networks"][net_name][clean_name] = {"ip": "192.168.1.100", "mac": "", "type": "wiz"}
                self._save_devices_json(data)
                self._populate_network_lights()

    def _remove_light(self, net_name, light_name):
        data = self._load_devices_json()
        if net_name in data["networks"] and light_name in data["networks"][net_name]:
            del data["networks"][net_name][light_name]
            self._save_devices_json(data)
            self._populate_network_lights()

    def _add_wifi_network(self):
        net_name, ok = QInputDialog.getText(self, "Add WiFi Network", "Enter WiFi Network Name (SSID):")
        if ok and net_name.strip():
            clean_net = net_name.strip()
            data = self._load_devices_json()
            if clean_net not in data["networks"]:
                data["networks"][clean_net] = {}
                self._save_devices_json(data)
                self._populate_network_lights()

    def _update_env(self, key, new_value):
        old_value = self.env_loader.get(key)
        if str(old_value) != str(new_value):
            self.env_loader.update(key, new_value)
            self._flag_reboot()

    def _check_for_updates(self):
        try:
            if not os.path.exists(self.core_json_path):
                return
            mtime = os.stat(self.core_json_path).st_mtime
            if mtime > self.last_mtime:
                self.last_mtime = mtime
                self.core_data = self.loader.load_json("core.json")
                settings = self.core_data.get("settings", {})
                
                if "silent_mode" in self.ui_elements:
                    chk = self.ui_elements["silent_mode"]
                    chk.blockSignals(True)
                    chk.setChecked(settings.get("silent_mode", False))
                    chk.blockSignals(False)
                    
                if "enable_followup" in self.ui_elements:
                    chk = self.ui_elements["enable_followup"]
                    chk.blockSignals(True)
                    chk.setChecked(settings.get("enable_followup", False))
                    chk.blockSignals(False)
                    
                if "ecosystem_state" in self.ui_elements:
                    combo = self.ui_elements["ecosystem_state"]
                    combo.blockSignals(True)
                    val = settings.get("ecosystem_state", "normal")
                    if combo.currentText() != val:
                        combo.setCurrentText(val)
                    combo.blockSignals(False)
                    
                if "stt_model" in self.ui_elements:
                    combo = self.ui_elements["stt_model"]
                    combo.blockSignals(True)
                    val = settings.get("stt_model", "small")
                    if combo.currentText() != val:
                        combo.setCurrentText(val)
                    combo.blockSignals(False)
                    
                if "hardware" in self.ui_elements:
                    combo = self.ui_elements["hardware"]
                    combo.blockSignals(True)
                    val = settings.get("hardware", "cpu")
                    if combo.currentText() != val:
                        combo.setCurrentText(val)
                    combo.blockSignals(False)
        except Exception as e:
            pass

    def _update_core_json(self, key, value, category="settings"):
        try:
            def update_cb(core):
                if category not in core: core[category] = {}
                core[category][key] = value
            self.loader.update_json_atomic("core.json", update_cb)
            if os.path.exists(self.core_json_path):
                self.last_mtime = os.stat(self.core_json_path).st_mtime
        except Exception as e:
            pass

    def _toggle_silent_mode(self, state):
        is_silent = (state == 2)
        self._update_core_json("silent_mode", is_silent)
        action = "silent_mode_on" if is_silent else "silent_mode_off"
        self.router.dispatch("state.daemon", action=action)

    def _change_update_mode(self, value):
        def update_cb(core):
            if "settings" not in core: core["settings"] = {}
            core["settings"]["update_mode"] = value
        self.loader.update_json_atomic("core.json", update_cb)

    def _toggle_followup(self, state):
        is_followup = (state == 2)
        self._update_core_json("enable_followup", is_followup)
        action = "followup_on" if is_followup else "followup_off"
        self.router.dispatch("state.daemon", action=action)

    def _change_ecosystem_mode(self, value):
        self._update_core_json("ecosystem_state", value)
        self._update_core_json("mode", value.upper(), category="ecosystem")
        self.router.dispatch("state.change", action=value)

    def _change_stt_model(self, value):
        self._update_core_json("stt_model", value)
        self._flag_reboot()

    def _change_hardware(self, value):
        self._update_core_json("hardware", value)
        self._flag_reboot()

    def _flag_reboot(self):
        self.needs_reboot = True
        self.reboot_btn.show()

    def _on_tab_changed(self, index):
        if index == 1:
            self.reboot_btn.setText("Save & Reboot Smart Lights")
        elif index == 2:
            self.reboot_btn.setText("Save & Reboot Spotify")
        else:
            self.reboot_btn.setText("Save & Reboot Ecosystem")

    def _reboot_ecosystem(self):
        # Save UI state before we potentially get terminated by the supervisor
        p = self.parent()
        while p is not None:
            if hasattr(p, 'save_ui_state'):
                p.save_ui_state()
                break
            p = p.parent()
            
        btn_text = self.reboot_btn.text()
        if btn_text == "Save & Reboot Smart Lights":
            self.router.dispatch("system.restart_module", target="light")
        elif btn_text == "Save & Reboot Spotify":
            self.router.dispatch("system.restart_module", target="music")
        else:
            self.router.dispatch("system.restart_all")
            
        self.reboot_btn.hide()
        self.needs_reboot = False
