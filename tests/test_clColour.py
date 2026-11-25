import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clColour import colour_command, keep_value, value_brightness, value_of

# The same tables are in tests/test_webSwatches.py for web/js/swatch-model.js: the two must agree.
BRIGHTNESS = [((255, 0, 0), 1.0, 100), ((0, 255, 0), 2.2, 100), ((254, 0, 0), 1.0, 100), ((0, 100, 0), 1.0, 39), ((0, 20, 0), 1.0, 8), ((0, 100, 0), 2.2, 13),
              ((0, 20, 0), 2.2, 1), ((128, 0, 128), 1.0, 50), ((128, 0, 191), 2.2, 53), ((0, 0, 0), 2.2, 1), ((1, 1, 1), 1.0, 1)]
COMMANDS = [((255, 0, 0), 2.2, {"rgb": [255, 0, 0], "lum": 100}), ((0, 100, 0), 2.2, {"rgb": [0, 255, 0], "lum": 13}),
            ((128, 0, 191), 2.2, {"rgb": [171, 0, 255], "lum": 53}), ((128, 64, 0), 1.0, {"rgb": [255, 128, 0], "lum": 50}),
            ((0, 0, 0), 2.2, {"lum": 1}), ((10, 20, 30), 1.0, {"rgb": [85, 170, 255], "lum": 12})]


class TestValueBrightness:
    @pytest.mark.parametrize("rgb, gamma, expected", BRIGHTNESS)
    def test_the_brightness_a_colour_is_as_a_light(self, rgb, gamma, expected):
        assert value_brightness(rgb, gamma) == expected

    def test_a_brighter_colour_is_a_brighter_light(self):
        steps = [value_brightness((0, g, 0), 2.2) for g in (20, 60, 100, 160, 255)]
        assert steps == sorted(steps) and steps[0] == 1 and steps[-1] == 100 and len(set(steps)) == 5

    def test_only_the_brightest_channel_decides_not_the_hue(self):
        assert value_brightness((0, 100, 0), 2.2) == value_brightness((100, 0, 0), 2.2) == value_brightness((100, 100, 100), 2.2) == value_brightness((100, 50, 20), 2.2)

    def test_it_is_always_a_valid_tapo_brightness(self):
        for v in range(0, 256, 3):
            for gamma in (1.0, 1.8, 2.2, 3.0):
                assert 1 <= value_brightness((v, 0, 0), gamma) <= 100


class TestColourCommand:
    @pytest.mark.parametrize("rgb, gamma, expected", COMMANDS)
    def test_the_fields_that_make_a_bulb_show_a_colour(self, rgb, gamma, expected):
        assert colour_command(rgb, gamma) == expected

    def test_the_same_colour_is_always_the_same_light(self):
        assert colour_command((128, 0, 191), 2.2) == colour_command((128, 0, 191), 2.2) == colour_command([128, 0, 191], 2.2)

    def test_the_colour_is_sent_at_full_value_so_every_bulb_type_shows_it_the_same(self):
        for rgb in [(10, 20, 30), (200, 100, 50), (0, 40, 0), (255, 255, 255)]:
            assert max(colour_command(rgb, 2.2)["rgb"]) == 255

    def test_the_hue_and_saturation_survive_the_scaling(self):
        import colorsys
        for rgb in [(10, 20, 30), (200, 100, 50), (128, 0, 191)]:
            a = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
            b = colorsys.rgb_to_hsv(*(c / 255 for c in colour_command(rgb)["rgb"]))
            assert abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01

    def test_every_channel_stays_in_range(self):
        for v in range(1, 256, 7):
            assert all(0 <= c <= 255 for c in colour_command((v, v // 2, 3), 2.2)["rgb"])


class TestKeepValue:
    def test_a_wheel_colour_takes_the_value_of_the_colour_it_replaces(self):
        assert keep_value((255, 0, 0), (0, 100, 0)) == (100, 0, 0)               # the hue changes, the swatch stays as dark as it was
        assert value_of(keep_value((10, 200, 255), (0, 100, 0))) == pytest.approx(100 / 255, abs=0.01)

    def test_a_full_value_colour_stays_full(self):
        assert keep_value((10, 200, 255), (255, 0, 0)) == (10, 200, 255)

    def test_black_has_no_value_to_keep(self):
        assert keep_value((10, 200, 255), (0, 0, 0)) == (10, 200, 255)
