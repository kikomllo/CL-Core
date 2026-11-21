import json
import os
import subprocess
import sys

import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC)


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _picker(brand="tapo", state=None):
    from ui.clColourPicker import ColourPicker
    picker = ColourPicker("Bedroom", brand, state or {})
    got = []
    picker.chosen.connect(got.append)
    return picker, got


class TestWhiteTemperatureRanges:
    """The popup shows kelvin the same way clControl maps a 0-100 temperature onto the bulb."""

    @pytest.mark.parametrize("brand, temp, kelvin", [
        ("wiz", 0, 2700), ("wiz", 100, 6500), ("wiz", 50, 4600),
        ("tapo", 0, 2500), ("tapo", 100, 6500), ("tapo", 50, 4500), ("TAPO", 20, 3300),
    ])
    def test_temperature_to_kelvin(self, brand, temp, kelvin):
        from ui.clColourPicker import temp_to_kelvin
        assert temp_to_kelvin(brand, temp) == kelvin

    @pytest.mark.parametrize("brand", ["wiz", "tapo"])
    def test_kelvin_round_trips_and_is_clamped(self, brand):
        from ui.clColourPicker import kelvin_to_temp, temp_to_kelvin
        for temp in (0, 20, 50, 80, 100):
            assert kelvin_to_temp(brand, temp_to_kelvin(brand, temp)) == temp
        assert kelvin_to_temp(brand, 1000) == 0 and kelvin_to_temp(brand, 20000) == 100

    def test_the_formula_matches_what_clcontrol_sends(self):
        """clControl: WiZ kelvin = 2700 + t*(6500-2700)/100, Tapo = 2500 + t*(6500-2500)/100."""
        from ui.clColourPicker import temp_to_kelvin
        for t in range(0, 101, 5):
            assert temp_to_kelvin("wiz", t) == int(round(2700 + t * (6500 - 2700) / 100))
            assert temp_to_kelvin("tapo", t) == int(round(2500 + t * (6500 - 2500) / 100))

    def test_warm_is_amber_and_cool_is_blue(self):
        from ui.clColourPicker import white_tint
        warm, cool = white_tint(2500), white_tint(6500)
        assert warm[0] > warm[2] and cool[2] > cool[0]
        assert white_tint(100) == warm and white_tint(99999) == cool           # outside the range is clamped


class TestWhatTheButtonShows:
    def test_colour_mode_shows_the_lights_rgb(self):
        from ui.clColourPicker import display_colour
        assert display_colour([150, 60, 220], None) == (150, 60, 220)

    def test_white_mode_shows_the_tint_of_its_temperature(self):
        from ui.clColourPicker import display_colour, white_tint
        assert display_colour(None, 4600) == white_tint(4600)

    def test_a_light_that_reports_nothing_still_gets_a_warm_white(self):
        from ui.clColourPicker import display_colour, white_tint
        assert display_colour(None, None) == white_tint(3000)

    def test_colour_of_command_for_names_whites_rgb_and_temp(self):
        from ui.clColourPicker import colour_of_command, temp_to_kelvin, white_tint
        assert colour_of_command({"color": "purple"}, "tapo") == (128, 0, 128)
        assert colour_of_command({"color": "warm white"}, "wiz") == white_tint(temp_to_kelvin("wiz", 20))
        assert colour_of_command({"rgb": [1, 2, 3]}, "wiz") == (1, 2, 3)
        assert colour_of_command({"temp": 80}, "tapo") == white_tint(temp_to_kelvin("tapo", 80))
        assert colour_of_command({"lum": 40}, "tapo") is None                  # brightness alone changes no colour


class TestSwatchesAreVoiceColours:
    def test_every_swatch_name_and_rgb_is_in_the_colour_list(self):
        """A swatch sends its name as `color`; a name clControl does not know would do nothing."""
        from ui.clColourPicker import PALETTE, WHITES
        with open(os.path.join(SRC, "..", "config", "entities.json"), encoding="utf-8") as f:
            colors = json.load(f)["colors"]
        for name, rgb in PALETTE:
            entry = colors[name]
            assert entry["type"] == "rgb" and (entry["r"], entry["g"], entry["b"]) == rgb, name
        for name, temp, _ in WHITES:
            assert colors[name] == {"type": "temp", "val": temp}, name


class TestPickerSendsFields:
    def test_a_colour_swatch_sends_its_name_and_the_current_brightness(self, qapp):
        picker, got = _picker(state={"brightness": 60})
        purple = next(s for s in picker.swatches if s.name == "purple")
        purple.click()
        assert got == [{"color": "purple", "lum": 60}]

    def test_a_white_swatch_sends_its_voice_name(self, qapp):
        picker, got = _picker()
        next(s for s in picker.swatches if s.name == "daylight").click()
        assert got == [{"color": "daylight", "lum": 100}]

    def test_only_one_swatch_is_marked_and_clicking_it_again_keeps_it(self, qapp):
        picker, _ = _picker()
        red, blue = (next(s for s in picker.swatches if s.name == n) for n in ("red", "blue"))
        red.click()
        blue.click()
        assert [s.name for s in picker.swatches if s.isChecked()] == ["blue"]
        blue.click()
        assert blue.isChecked()

    def test_the_brightness_slider_waits_for_the_value_to_settle(self, qapp):
        picker, got = _picker()
        picker.lum_slider.setValue(40)
        picker.lum_slider.setValue(35)
        assert got == [] and picker._debounce.isActive()                       # a drag does not flood the bulb
        picker._flush()
        assert got == [{"lum": 35}]

    def test_the_temperature_slider_sends_temp_with_brightness_and_clears_the_swatches(self, qapp):
        picker, got = _picker(state={"brightness": 70})
        next(s for s in picker.swatches if s.name == "red").click()
        got.clear()
        picker.temp_slider.setValue(80)
        picker._flush()
        assert got == [{"temp": 80, "lum": 70}]
        assert not any(s.isChecked() for s in picker.swatches)

    def test_closing_sends_a_slider_move_that_had_not_settled(self, qapp):
        picker, got = _picker()
        picker.lum_slider.setValue(20)
        picker.close()
        assert got == [{"lum": 20}]

    def test_the_sliders_start_from_the_lights_own_values(self, qapp):
        picker, got = _picker("wiz", {"brightness": 35, "kelvin": 4600})
        assert picker.lum_slider.value() == 35 and picker.temp_slider.value() == 50
        assert got == []                                                       # opening sends nothing

    def test_the_labels_show_percent_and_the_brands_kelvin(self, qapp):
        picker, _ = _picker("wiz", {"brightness": 35, "kelvin": 4600})
        assert picker.lum_value.text() == "35 %" and picker.temp_value.text() == "4600 K"

    def test_hovering_shows_the_voice_command_for_this_light(self, qapp):
        picker, _ = _picker()
        next(s for s in picker.swatches if s.name == "hot pink").hovered.emit("hot pink")
        assert 'make the bedroom hot pink' in picker.hint.text()
        picker._on_hover("coolwhite")
        assert "cool white" in picker.hint.text() and "coolwhite" not in picker.hint.text()


class TestGradientSlider:
    def _slider(self, qapp):
        from ui.clColourPicker import GradientSlider
        s = GradientSlider([(0.0, (0, 0, 0)), (1.0, (255, 255, 255))])
        s.setRange(0, 100)
        s.resize(209, 22)
        return s

    def test_the_value_follows_the_x_position(self, qapp):
        s = self._slider(qapp)
        assert s._value_at(s.PAD) == 0 and s._value_at(s.width() - s.PAD) == 100
        assert s._value_at(s.width() / 2) == 50
        assert s._value_at(-50) == 0 and s._value_at(9999) == 100              # outside the track is clamped

    def test_a_click_sets_the_value_directly(self, qapp):
        from PyQt6.QtCore import Qt, QPointF
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import QEvent
        s = self._slider(qapp)
        ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(s.width() / 2, 10), Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        s.mousePressEvent(ev)
        s.setSliderDown(False)
        assert s.value() == 50


class TestPopupPlacement:
    def test_it_opens_under_the_anchor_and_stays_on_the_screen(self, qapp):
        from PyQt6.QtWidgets import QWidget
        from PyQt6.QtGui import QGuiApplication
        from ui.clColourPicker import ColourPicker
        anchor = QWidget()
        anchor.resize(26, 26)
        anchor.move(10, 10)
        anchor.show()
        picker = ColourPicker("Bedroom", "wiz", {})
        picker.show_near(anchor)
        area = QGuiApplication.primaryScreen().availableGeometry()
        assert area.left() <= picker.x() and picker.x() + picker.width() <= area.right() + 1
        assert picker.y() >= area.top()
        picker.close()
        anchor.close()


class TestColoursIgnoreTheSkin:
    """The Ultron skin recolours warm hues in stylesheets and in clSkinQt.QColor. A swatch is a real bulb colour, so it
    is painted with the raw QColor: the orange one must stay orange there (checked in its own interpreter, since the
    skin is chosen once at import)."""

    SCRIPT = (
        "import sys\\n"
        "sys.path.insert(0, sys.argv[1])\\n"
        "from PyQt6.QtWidgets import QApplication\\n"
        "app = QApplication([])\\n"
        "import clSkin\\n"
        "assert clSkin.ACTIVE == 'ultron', clSkin.ACTIVE\\n"
        "from ui.clColourPicker import Swatch, ColourButton\\n"
        "sw = Swatch('orange', (255, 165, 0)); sw.resize(40, 28)\\n"
        "c = sw.grab().toImage().pixelColor(20, 14)\\n"
        "b = ColourButton(); b.set_light((255, 165, 0), True)\\n"
        "d = b.grab().toImage().pixelColor(b.width() // 2, b.height() // 2)\\n"
        "print((c.red(), c.green(), c.blue()), (d.red(), d.green(), d.blue()))\\n"
    )

    def test_the_orange_swatch_and_button_dot_stay_orange_under_ultron(self):
        env = dict(os.environ, JARVIS_THEME="ultron", QT_QPA_PLATFORM="offscreen")
        out = subprocess.run([sys.executable, "-c", self.SCRIPT.replace("\\n", "\n"), SRC], env=env,
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-600:]
        assert out.stdout.strip() == "(255, 165, 0) (255, 165, 0)"
