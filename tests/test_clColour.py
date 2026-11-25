import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clColour import brightness_for, compose_brightness, value_factor

# The same tables are in tests/test_webSwatches.py for web/js/swatch-model.js: the two must agree.
FACTORS = [((255, 0, 0), 1.0, 1.0), ((0, 100, 0), 1.0, 0.392), ((0, 100, 0), 2.2, 0.128), ((0, 20, 0), 2.2, 0.004), ((128, 0, 128), 1.0, 0.502), ((0, 0, 0), 2.2, 0.0)]
COMPOSED = [(100, 1.0, 100), (100, 0.996, 100), (100, 0.128, 13), (50, 0.128, 6), (100, 0.004, 1), (100, 0.0, 1), (1, 0.5, 1), (80, 0.5, 40)]


class TestValueFactor:
    @pytest.mark.parametrize("rgb, gamma, expected", FACTORS)
    def test_how_much_of_full_brightness_a_colour_is(self, rgb, gamma, expected):
        assert value_factor(rgb, gamma) == pytest.approx(expected, abs=0.001)

    def test_only_the_brightest_channel_decides_not_the_hue(self):
        assert value_factor((0, 100, 0), 2.2) == value_factor((100, 0, 0), 2.2) == value_factor((100, 100, 100), 2.2) == value_factor((100, 50, 20), 2.2)


class TestComposeBrightness:
    @pytest.mark.parametrize("master, factor, expected", COMPOSED)
    def test_the_overall_brightness_scaled_by_the_colour(self, master, factor, expected):
        assert compose_brightness(master, factor) == expected

    def test_the_result_is_always_a_valid_tapo_brightness(self):
        for master in (1, 20, 100):
            for v in range(0, 256, 5):
                for gamma in (1.0, 2.2, 3.0):
                    assert 1 <= compose_brightness(master, value_factor((v, 0, 0), gamma)) <= 100


class TestBrightnessFor:
    def test_a_brighter_colour_is_a_brighter_light_and_a_darker_one_a_dimmer_light(self):
        steps = [brightness_for((0, g, 0), 100, 2.2) for g in (20, 60, 100, 160, 255)]
        assert steps == sorted(steps) and steps[0] == 1 and steps[-1] == 100 and len(set(steps)) == 5

    def test_the_overall_brightness_scales_every_colour(self):
        assert brightness_for((0, 255, 0), 40, 2.2) == 40
        assert brightness_for((0, 100, 0), 50, 2.2) == 6

    def test_a_full_value_colour_leaves_the_brightness_alone_and_a_missing_one_missing(self):
        assert brightness_for((0, 255, 0), 42, 2.2) == 42 and brightness_for((0, 255, 0), None, 2.2) is None

    def test_a_dark_colour_with_no_brightness_given_starts_from_full(self):
        assert brightness_for((128, 0, 0), None, 1.0) == 50
