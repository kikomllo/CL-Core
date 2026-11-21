import json
import logging
from clTheme import Theme
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGraphicsDropShadowEffect, QStackedLayout
from PyQt6.QtCore import QTimer, QSize, Qt
from PyQt6.QtGui import QColor
from utils.clActionRouter import ActionRouter
from clUIScaler import UIScaler
from ui.clColourPicker import ColourButton, ColourPicker, colour_of_command, display_colour

def s(val):
    return UIScaler.get().scale(val)

class LightControlWidget(QWidget):
    # The status dot's glow is painted inside its holder, so the holder needs
    # room on every side or the glow is cut off hard at the widget's left edge.
    INDICATOR_WIDTH = 28
    HALO_BLUR = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(s(15), s(8), s(15), 0)
        self.layout.setSpacing(s(8))
        
        # Title and Refresh
        top_layout = QHBoxLayout()
        self.title_lbl = QLabel("Smart Lights")
        self.title_lbl.setStyleSheet(Theme.get_style("TitleLabel"))
        # The Wi-Fi network sits under the title, smaller and dim; hidden
        # until the first status names one.
        self.network_lbl = QLabel("")
        self.network_lbl.setStyleSheet(f"color: {Theme.C_TEXT_DIM}; font-size: {Theme.F_TINY}; border: none; background: transparent;")
        self.network_lbl.hide()
        title_column = QVBoxLayout()
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(0)
        title_column.addWidget(self.title_lbl)
        title_column.addWidget(self.network_lbl)
        
        self.refresh_btn = QPushButton()
        self.refresh_btn.setFixedSize(30, 30)
        self.refresh_btn.setIcon(Theme.get_icon("refresh.svg", 16))
        self.refresh_btn.setIconSize(QSize(16, 16))
        self.refresh_btn.setStyleSheet(Theme.get_style("RefreshButton"))
        self.refresh_btn.clicked.connect(lambda: self.send_cmd("refresh_lights", "all"))
        
        top_layout.addLayout(title_column)
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
        self._picker = None                  # the open colour popup and which light it is for
        self._picker_target = None

    def update_scaling(self):
        self.layout.setContentsMargins(s(15), s(8), s(15), 0)
        self.layout.setSpacing(s(8))
        if hasattr(self, 'refresh_btn'):
            self.refresh_btn.setFixedSize(30, 30)
        if hasattr(self, 'lights_layout'):
            self.lights_layout.setSpacing(s(5))
        for row_data in self.light_rows.values():
            if "layout" in row_data:
                row_data["layout"].setSpacing(s(6))
            if "indicator_holder" in row_data:
                row_data["indicator_holder"].setFixedWidth(self.INDICATOR_WIDTH)
            if "toggle_btn" in row_data:
                row_data["toggle_btn"].setFixedSize(26, 26)
            if "colour_btn" in row_data:
                row_data["colour_btn"].setFixedSize(26, 26)
            if "delete_btn" in row_data:
                row_data["delete_btn"].setFixedSize(26, 26)
        if hasattr(self, 'lights_container'):
            self.lights_container.adjustSize()
        # No self.adjustSize() here -- this widget is a layout-managed
        # child of DraggableWidget, not a free-floating window.
        # DraggableWidget.update_scaling() calls this method FIRST, before
        # computing its own grow-only resize off self.sizeHint() --
        # snapping this widget to its own sizeHint here would undo that
        # and fight the wrapper's actual (larger, grow-only) size.

    def hideEvent(self, event):
        self._close_colour_picker()                  # a popup must not outlive the widget it hangs from
        super().hideEvent(event)

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

    # --- colour button ---
    def _refresh_colour_button(self, row_data):
        """Shows the light's own colour on its button, hides the button for a light without colour, and disables it
        while the light is unreachable."""
        btn = row_data["colour_btn"]
        has_colour = row_data.get("colour", True)
        btn.setVisible(has_colour)
        btn.setEnabled(not row_data.get("is_offline"))
        btn.set_light(display_colour(row_data.get("rgb"), row_data.get("kelvin")), row_data.get("is_on", False))

    def _open_colour_picker(self, target_name):
        row_data = self.light_rows.get(target_name)
        if row_data is None:
            return
        reopen = self._picker_target != target_name          # a second click on the same button just closes it
        self._close_colour_picker()
        if not reopen:
            return
        picker = ColourPicker(row_data["name"], row_data.get("brand", ""), {
            "brightness": row_data.get("brightness"), "kelvin": row_data.get("kelvin"),
            "brand_label": {"wiz": "WiZ", "tapo": "Tapo"}.get(row_data.get("brand", ""), "")}, parent=self)
        picker.chosen.connect(lambda fields, t=target_name: self._send_colour(t, fields))
        picker.destroyed.connect(lambda _=None, t=target_name: self._picker_closed(t))
        self._picker, self._picker_target = picker, target_name
        row_data["colour_btn"].set_open(True)
        picker.show_near(row_data["colour_btn"])

    def _picker_closed(self, target_name):
        if self._picker_target == target_name:
            self._picker = self._picker_target = None
        row_data = self.light_rows.get(target_name)
        if row_data is not None:
            try:
                row_data["colour_btn"].set_open(False)
            except RuntimeError:                              # the row was deleted along with the popup
                pass

    def _close_colour_picker(self):
        picker, self._picker, self._picker_target = self._picker, None, None
        if picker is not None:
            try:
                picker.close()
            except RuntimeError:
                pass

    def _send_colour(self, target_name, fields):
        """A choice in the popup: an on command (a colour also turns the light on) with colour / temperature /
        brightness, the same light.set that voice sends. The button follows at once; the next status confirms."""
        try:
            self.router.dispatch("light.set", action="on", light_target=target_name, silent=True, **fields)
        except Exception as e:
            logging.error(f"Failed to publish light colour: {e}")
            return
        row_data = self.light_rows.get(target_name)
        if row_data is None:
            return
        row_data["is_on"] = True
        rgb = colour_of_command(fields, row_data.get("brand", ""))
        if rgb:                                               # a white shows as its tint; the status brings the real values
            row_data["rgb"], row_data["kelvin"] = list(rgb), None
        if "lum" in fields:
            row_data["brightness"] = fields["lum"]
        self._refresh_colour_button(row_data)

    def _delete_light(self, target_name):
        import paho.mqtt.publish as publish
        try:
            self.router.dispatch("system.discovery", action="intent_remove_light", target_str=target_name)
        except Exception as e:
            logging.error(f"Failed to publish delete: {e}")
        if self._picker_target == target_name:
            self._close_colour_picker()
        if target_name in self.light_rows:
            row_data = self.light_rows.pop(target_name)
            row_data["widget"].deleteLater()
            self.lights_container.adjustSize()
            self._force_resize()

    def handle_feedback(self, data):
        if data.get("status") == "success" and data.get("action_cmd") == "toggle":
            target = data.get("target", "")
            if target in self.light_rows:
                row_data = self.light_rows[target]
                current_is_on = row_data["is_on"]
                new_state = not current_is_on
                row_data["is_on"] = new_state
                self._update_indicator(row_data["indicator"], new_state, False)

    def _make_indicator(self):
        # Two labels stacked on the same spot approximate a layered CSS
        # glow (tight core + soft outer halo) -- a single
        # QGraphicsDropShadowEffect can only paint one layer.
        holder = QWidget()
        stack = QStackedLayout(holder)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.setContentsMargins(0, 0, 0, 0)
        halo = QLabel("●")
        front = QLabel("●")
        for dot in (halo, front):
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stack.addWidget(halo)
        stack.addWidget(front)
        front._glow_halo = halo
        return holder, front

    def _set_glow(self, widget, blur, color):
        glow = widget.graphicsEffect()
        if not isinstance(glow, QGraphicsDropShadowEffect):
            glow = QGraphicsDropShadowEffect(widget)
            glow.setOffset(0, 0)
            widget.setGraphicsEffect(glow)
        glow.setBlurRadius(blur)
        glow.setColor(color)

    def _update_indicator(self, indicator, is_on, is_offline):
        halo = getattr(indicator, '_glow_halo', None)
        if is_offline:
            color = "rgba(120, 120, 120, 255)"
        elif is_on:
            color = "rgba(50, 255, 50, 255)"
        else:
            color = "rgba(255, 50, 50, 255)"
        indicator.setStyleSheet(f"color: {color}; font-size: 16pt;")
        if halo is not None:
            halo.setStyleSheet(f"color: {color}; font-size: 16pt;")

        # Only a genuinely "on" light glows -- off/offline dots stay flat,
        # matching how a real light actually looks.
        if is_on and not is_offline:
            self._set_glow(indicator, 6, QColor(80, 255, 80, 191))
            if halo is not None:
                self._set_glow(halo, self.HALO_BLUR, QColor(80, 255, 80, 107))
        else:
            indicator.setGraphicsEffect(None)
            if halo is not None:
                halo.setGraphicsEffect(None)

    def update_status(self, data):
        network_name = data.get("network", "")
        if network_name:
            self.network_lbl.setText(network_name)
            self.network_lbl.show()
            
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
                if self._picker_target == old_target:
                    self._close_colour_picker()
                self.light_rows[old_target]["widget"].deleteLater()
                del self.light_rows[old_target]
            
        for l in lights:
            target_name = l.get("name", "").lower().replace(" ", "_")
            is_on = l.get("is_on", False)
            is_offline = l.get("offline", False)
            
            colour_fields = {"brand": l.get("brand", ""), "colour": l.get("colour", True), "rgb": l.get("rgb"),
                             "kelvin": l.get("kelvin"), "brightness": l.get("brightness")}
            if target_name in self.light_rows:
                row_data = self.light_rows[target_name]
                row_data["is_on"] = is_on
                row_data["is_offline"] = is_offline
                row_data.update(colour_fields)
                self._update_indicator(row_data["indicator"], is_on, is_offline)
                self._refresh_colour_button(row_data)
            else:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(s(6))
                
                indicator_holder, indicator = self._make_indicator()
                indicator_holder.setFixedWidth(self.INDICATOR_WIDTH)
                indicator.setStyleSheet("color: rgba(100, 100, 100, 255); font-size: 16pt;")
                self._update_indicator(indicator, is_on, is_offline)
                
                name_lbl = QLabel(l.get("name", "Unknown"))
                name_lbl.setStyleSheet(Theme.get_style("SubtitleLabel"))
                
                toggle_btn = QPushButton()
                toggle_btn.setFixedSize(26, 26)
                toggle_btn.setIcon(Theme.get_icon("power.svg", 14))
                toggle_btn.setIconSize(QSize(14, 14))
                toggle_btn.setToolTip("Toggle")
                toggle_btn.setStyleSheet(Theme.get_style("SecondaryButton") + " padding: 0;")
                toggle_btn.clicked.connect(lambda checked, t=target_name: self.send_cmd("toggle", t, silent=True))

                delete_btn = QPushButton()
                delete_btn.setFixedSize(26, 26)
                delete_btn.setIcon(Theme.get_icon("close.svg", 14))
                delete_btn.setIconSize(QSize(14, 14))
                delete_btn.setToolTip("Remove light")
                delete_btn.setStyleSheet(Theme.get_style("SmallDangerButton"))
                delete_btn.clicked.connect(lambda checked, t=target_name: self._delete_light(t))
                
                colour_btn = ColourButton()
                colour_btn.clicked.connect(lambda checked=False, t=target_name: self._open_colour_picker(t))

                row_layout.addWidget(indicator_holder)
                row_layout.addWidget(name_lbl)
                row_layout.addStretch()
                row_layout.addWidget(colour_btn)
                row_layout.addWidget(toggle_btn)
                row_layout.addWidget(delete_btn)

                self.lights_layout.addWidget(row)
                self.light_rows[target_name] = {
                    "widget": row,
                    "layout": row_layout,
                    "indicator": indicator,
                    "indicator_holder": indicator_holder,
                    "toggle_btn": toggle_btn,
                    "delete_btn": delete_btn,
                    "colour_btn": colour_btn,
                    "name": l.get("name", "Unknown"),
                    "is_on": is_on,
                    "is_offline": is_offline,
                    **colour_fields,
                }
                self._refresh_colour_button(self.light_rows[target_name])
            
        self.lights_container.adjustSize()
        # Deferred resize ensures the parent wrapper picks up the new layout geometry
        QTimer.singleShot(50, self._force_resize)

    def _force_resize(self):
        # No self.adjustSize() here -- this widget is a layout-managed
        # child of DraggableWidget, not a free-floating window.
        # DraggableWidget.update_scaling() -- NOT adjustSize() -- grows the
        # wrapper to fit if needed but never shrinks it, so a user's manual
        # drag-resize (or a size just restored from ui_state.json) survives
        # every refresh instead of being silently snapped back to the
        # content's natural minimum size on every light toggle.
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, 'update_scaling'):
            parent.update_scaling()

    def get_standalone_min_size(self):
        return 340, 300
