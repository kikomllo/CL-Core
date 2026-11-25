import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clColour import dark_brightness

# The same table is in tests/test_webSwatches.py for web/js/swatch-model.js: the two must agree.
CASES = [
    ((255, 0, 0), 1.0, None), ((0, 255, 0), 2.2, None), ((255, 255, 255), 2.2, None), ((254, 0, 0), 1.0, None),
    ((0, 100, 0), 1.0, 39), ((0, 20, 0), 1.0, 8), ((0, 100, 0), 2.2, 13), ((0, 20, 0), 2.2, 1),
    ((128, 0, 128), 1.0, 50), ((116, 10, 200), 2.2, 59), ((0, 0, 0), 2.2, 1), ((1, 1, 1), 1.0, 1),
]


class TestDarkBrightness:
    @pytest.mark.parametrize("rgb, gamma, expected", CASES)
    def test_the_brightness_a_mix_asks_for(self, rgb, gamma, expected):
        assert dark_brightness(rgb, gamma) == expected

    def test_the_darker_the_mix_the_darker_the_light(self):
        values = [dark_brightness((0, g, 0), 2.2) or 100 for g in range(0, 256, 5)]
        assert values == sorted(values)

    def test_the_result_is_always_a_valid_tapo_brightness(self):
        for v in range(0, 256, 3):
            for gamma in (1.0, 1.8, 2.2, 3.0):
                b = dark_brightness((v, 0, 0), gamma)
                assert b is None or 1 <= b <= 100

    def test_the_hue_does_not_decide_it_only_the_brightest_channel(self):
        assert dark_brightness((0, 100, 0), 2.2) == dark_brightness((100, 0, 0), 2.2) == dark_brightness((100, 100, 100), 2.2) == dark_brightness((100, 50, 20), 2.2)
