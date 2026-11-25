import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _widget(mocker):
    import ui.clLightControlWidget as clLightControlWidget
    mocker.patch.object(clLightControlWidget.ActionRouter, "dispatch", return_value=True)
    return clLightControlWidget.LightControlWidget()


class TestWidgetSizeSourceOfTruth:
    """Live bug: this widget is a layout-managed child of DraggableWidget,
    not a free-floating window -- calling self.adjustSize() anywhere in its
    refresh path snaps it straight to its own natural sizeHint, discarding
    a manual drag-resize or a size restored from ui_state.json. Found via
    the identical, already-fixed issue in the todo/notes widgets: three
    separate self.adjustSize() calls had survived here (in update_status(),
    _delete_light(), and even _force_resize() itself, contradicting that
    method's own comment) despite the pattern already being established
    elsewhere."""

    def test_update_status_does_not_call_adjustSize(self, qapp, mocker):
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w.update_status({"network": "TestNet", "lights": [
            {"name": "Lamp", "is_on": True, "offline": False},
        ]})

        adjust_spy.assert_not_called()

    def test_delete_light_does_not_call_adjustSize(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"network": "TestNet", "lights": [
            {"name": "Lamp", "is_on": True, "offline": False},
        ]})
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w._delete_light("lamp")

        adjust_spy.assert_not_called()

    def test_force_resize_does_not_call_adjustSize(self, qapp, mocker):
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w._force_resize()

        adjust_spy.assert_not_called()

    def test_force_resize_delegates_to_the_wrappers_grow_only_update_scaling(self, qapp, mocker):
        w = _widget(mocker)
        parent = mocker.MagicMock()
        mocker.patch.object(type(w), "parentWidget", return_value=parent)

        w._force_resize()

        parent.update_scaling.assert_called_once()
        parent.adjustSize.assert_not_called()

    def test_update_scaling_does_not_call_adjustSize_on_self(self, qapp, mocker):
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w.update_scaling()

        adjust_spy.assert_not_called()


class TestNetworkUnderTheTitle:
    def test_the_title_stays_plain_and_the_network_goes_below_it(self, qapp, mocker):
        w = _widget(mocker)
        w.resize(340, 300)
        w.show()
        w.update_status({"network": "MEO-76C2D0", "lights": [{"name": "Lamp", "is_on": True, "offline": False}]})
        qapp.processEvents()

        assert w.title_lbl.text() == "Smart Lights"
        assert w.network_lbl.text() == "MEO-76C2D0"
        assert w.network_lbl.y() >= w.title_lbl.y() + w.title_lbl.height() - 1
        assert w.network_lbl.height() < w.title_lbl.height()

    def test_the_network_line_stays_hidden_until_a_network_is_known(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"network": "", "lights": []})
        assert w.network_lbl.isHidden()
        w.update_status({"network": "Home", "lights": []})
        assert not w.network_lbl.isHidden()


class TestStatusDotGlowHasRoom:
    def test_the_dot_is_centred_in_a_holder_wider_than_the_dot(self, qapp, mocker):
        """The dot used to sit flush against its holder's left edge, so its glow was cut off hard there."""
        from PyQt6.QtCore import Qt
        w = _widget(mocker)
        w.update_status({"network": "N", "lights": [{"name": "Lamp", "is_on": True, "offline": False}]})
        row = w.light_rows["lamp"]
        indicator, holder = row["indicator"], row["indicator_holder"]

        assert indicator.alignment() & Qt.AlignmentFlag.AlignHCenter
        assert indicator._glow_halo.alignment() & Qt.AlignmentFlag.AlignHCenter
        assert holder.width() >= indicator.fontMetrics().horizontalAdvance("●") + 2 * 6



def _flush_deletes():
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _status(**overrides):
    base = {"network": "TestNet", "lights": [
        {"name": "Living room", "is_on": True, "offline": False, "brand": "wiz", "colour": True, "rgb": None, "kelvin": 3000, "brightness": 80},
        {"name": "Bedroom", "is_on": True, "offline": False, "brand": "tapo", "colour": True, "rgb": [150, 60, 220], "kelvin": None, "brightness": 60},
        {"name": "Kitchen", "is_on": False, "offline": False, "brand": "wiz", "colour": False, "rgb": None, "kelvin": 4000, "brightness": 100},
    ]}
    base.update(overrides)
    return base


class TestColourButton:
    """A round button on each light that has colour: it shows the light's own colour and opens the colour popup."""

    def test_lights_with_colour_get_the_button_and_white_only_ones_do_not(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        assert not w.light_rows["bedroom"]["colour_btn"].isHidden()
        assert w.light_rows["kitchen"]["colour_btn"].isHidden()

    def test_a_status_without_the_new_fields_still_shows_the_button(self, qapp, mocker):
        """An older light service (or an offline light) reports no capability: assume colour."""
        w = _widget(mocker)
        w.update_status({"network": "N", "lights": [{"name": "Lamp", "is_on": True, "offline": False}]})
        assert not w.light_rows["lamp"]["colour_btn"].isHidden()

    def test_the_capability_can_change_between_statuses(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        lights = _status()["lights"]
        lights[1]["colour"] = False
        w.update_status({"network": "TestNet", "lights": lights})
        assert w.light_rows["bedroom"]["colour_btn"].isHidden()

    def test_the_button_shows_the_lights_rgb_or_the_tint_of_its_temperature(self, qapp, mocker):
        from ui.clColourPicker import white_tint
        w = _widget(mocker)
        w.update_status(_status())
        assert w.light_rows["bedroom"]["colour_btn"]._rgb == (150, 60, 220)
        assert w.light_rows["living_room"]["colour_btn"]._rgb == white_tint(3000)

    def test_an_off_light_dims_its_dot_and_an_offline_one_disables_the_button(self, qapp, mocker):
        w = _widget(mocker)
        lights = _status()["lights"]
        lights[0]["is_on"] = False
        lights[1]["offline"] = True
        w.update_status({"network": "TestNet", "lights": lights})
        assert w.light_rows["living_room"]["colour_btn"]._on is False
        assert not w.light_rows["bedroom"]["colour_btn"].isEnabled()

    def test_a_later_status_updates_the_colour_on_the_button(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        lights = _status()["lights"]
        lights[1]["rgb"] = [255, 0, 0]
        w.update_status({"network": "TestNet", "lights": lights})
        assert w.light_rows["bedroom"]["colour_btn"]._rgb == (255, 0, 0)


class TestColourPopup:
    def _open(self, w, target="bedroom"):
        w.light_rows[target]["colour_btn"].click()

    def test_clicking_the_button_opens_the_popup_for_that_light(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        self._open(w)
        assert w._picker is not None and w._picker_target == "bedroom"
        assert w._picker.name == "Bedroom" and w._picker.brand == "tapo"
        assert w._picker.lum_slider.value() == 60                     # starts from the light's own brightness
        w._close_colour_picker()

    def test_a_second_click_on_the_same_button_closes_it(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        self._open(w)
        self._open(w)
        assert w._picker is None

    def test_opening_another_light_replaces_the_popup(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        self._open(w, "bedroom")
        first = w._picker
        self._open(w, "living_room")
        assert w._picker is not first and w._picker_target == "living_room"
        w._close_colour_picker()

    def test_the_button_looks_pressed_while_its_popup_is_open(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        self._open(w)
        assert w.light_rows["bedroom"]["colour_btn"]._open is True
        self._open(w)
        _flush_deletes()
        assert w.light_rows["bedroom"]["colour_btn"]._open is False

    def test_a_swatch_dispatches_a_light_set_on_with_the_colour_and_brightness(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        self._open(w)
        next(s for s in w._picker.swatches if s.name == "blue").click()
        w.router.dispatch.assert_called_with("light.set", action="on", light_target="bedroom", silent=True, color="blue", lum=60)
        w._close_colour_picker()

    def test_a_slider_move_dispatches_brightness_only(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        self._open(w)
        w._picker.lum_slider.setValue(25)
        w._picker._flush()
        w.router.dispatch.assert_called_with("light.set", action="on", light_target="bedroom", silent=True, lum=25)
        w._close_colour_picker()

    def test_the_button_follows_the_choice_before_the_next_status(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        self._open(w, "living_room")
        next(s for s in w._picker.swatches if s.name == "red").click()
        row = w.light_rows["living_room"]
        assert row["colour_btn"]._rgb == (255, 0, 0) and row["is_on"] is True and row["brightness"] == 80
        w._close_colour_picker()

    def test_a_failed_dispatch_is_logged_and_changes_nothing(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        w.router.dispatch.side_effect = RuntimeError("broker down")
        w._send_colour("bedroom", {"color": "red", "lum": 60})
        assert w.light_rows["bedroom"]["colour_btn"]._rgb == (150, 60, 220)

    def test_a_light_removed_while_its_popup_is_open_closes_it(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        self._open(w)
        w._delete_light("bedroom")
        assert w._picker is None

    def test_a_status_without_that_light_closes_its_popup(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        self._open(w)
        w.update_status({"network": "TestNet", "lights": _status()["lights"][:1]})
        assert w._picker is None

    def test_hiding_the_widget_closes_the_popup(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        w.show()
        self._open(w)
        w.hide()
        assert w._picker is None

    def test_a_popup_for_a_gone_row_does_not_break_the_cleanup(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        self._open(w)
        w._delete_light("bedroom")
        _flush_deletes()                                              # destroyed fires after the row is gone
        assert w._picker is None

    def test_update_scaling_keeps_the_button_size(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status(_status())
        w.update_scaling()
        btn = w.light_rows["bedroom"]["colour_btn"]
        assert btn.width() == btn.height()


@pytest.fixture(autouse=True)
def _own_config(tmp_path, monkeypatch):
    """The popup reads and writes the user's colour swatches in core.json: these tests use a config of their own."""
    import json
    from utils.clConfigLoader import ConfigLoader
    import ui.clLightControlWidget as module
    (tmp_path / "core.json").write_text(json.dumps({"settings": {}}))
    monkeypatch.setattr(module, "ConfigLoader", lambda: ConfigLoader(config_dir=str(tmp_path)))
    return tmp_path


class TestCustomSwatchesAreKept:
    def test_a_changed_swatch_is_saved_and_the_next_popup_shows_it(self, qapp, mocker, _own_config):
        import json
        w = _widget(mocker)
        w.update_status(_status())
        w.light_rows["bedroom"]["colour_btn"].click()
        w._picker.swatchChanged.emit("red", [10, 20, 30])
        assert json.loads((_own_config / "core.json").read_text())["settings"]["light_swatches"] == {"red": [10, 20, 30]}
        w._close_colour_picker()
        w.light_rows["bedroom"]["colour_btn"].click()
        red = next(s for s in w._picker.swatches if s.name == "red")
        assert red.custom and red.rgb == (10, 20, 30)
        w._picker.swatchChanged.emit("red", None)                          # Reset
        assert "light_swatches" not in json.loads((_own_config / "core.json").read_text())["settings"]
        w._close_colour_picker()

    def test_a_refused_save_is_logged_and_does_not_break_the_picker(self, qapp, mocker, _own_config, caplog):
        w = _widget(mocker)
        w._save_swatch("magenta", [1, 2, 3])
        assert "Failed to save the colour swatch" in caplog.text
