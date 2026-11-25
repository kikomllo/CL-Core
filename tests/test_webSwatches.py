"""The colour wheel and the customisable swatches of the light picker (web/js/swatch-model.js under node)."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "swatch-model.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def call(fn, *args):
    script = f'import * as M from "file:///{MODEL}";\nconsole.log(JSON.stringify(M.{fn}(...{json.dumps(list(args))})))'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


DEFAULTS = [["red", [255, 0, 0]], ["blue", [0, 0, 255]], ["hot pink", [255, 105, 180]]]


class TestColourMaths:
    @pytest.mark.parametrize("h, s, expected", [
        (0, 1, [255, 0, 0]), (60, 1, [255, 255, 0]), (120, 1, [0, 255, 0]), (180, 1, [0, 255, 255]), (240, 1, [0, 0, 255]), (300, 1, [255, 0, 255]),
        (0, 0, [255, 255, 255]), (360, 1, [255, 0, 0]), (-120, 1, [0, 0, 255]), (0, 0.5, [255, 128, 128]),
    ])
    def test_hsv_to_rgb(self, h, s, expected):
        assert call("hsvToRgb", h, s) == expected

    @pytest.mark.parametrize("rgb", [[255, 0, 0], [0, 255, 0], [12, 200, 90], [255, 105, 180], [128, 0, 128], [255, 255, 255]])
    def test_a_colour_goes_round_trip_through_hue_and_saturation(self, rgb):
        hsv = call("rgbToHsv", rgb)
        back = call("hsvToRgb", hsv["h"], hsv["s"], hsv["v"])
        assert all(abs(a - b) <= 1 for a, b in zip(back, rgb))

    def test_a_grey_has_no_saturation(self):
        assert call("rgbToHsv", [90, 90, 90])["s"] == 0


class TestWheel:
    def test_hue_runs_clockwise_from_the_right(self):
        assert call("wheelColour", 50, 0, 50)["rgb"] == [255, 0, 0]               # right: red
        assert call("wheelColour", 0, 50, 50)["h"] == 90                          # y grows downwards: down is 90 degrees, a yellow-green
        assert call("wheelColour", -50, 0, 50)["rgb"] == [0, 255, 255]            # left: cyan
        assert call("wheelColour", 0, -50, 50)["h"] == 270

    def test_the_centre_is_white_and_the_rim_is_fully_saturated(self):
        assert call("wheelColour", 0, 0, 50)["rgb"] == [255, 255, 255]
        assert call("wheelColour", 25, 0, 50)["s"] == 0.5

    def test_beyond_the_rim_is_the_rims_colour(self):
        assert call("wheelColour", 500, 0, 50) == call("wheelColour", 50, 0, 50)

    def test_the_knob_sits_where_the_colour_is(self):
        for dx, dy in [(30, 20), (-40, 10), (0, -25)]:
            c = call("wheelColour", dx, dy, 50)
            k = call("knobOffset", c["h"], c["s"], 50)
            assert abs(k["x"] - dx) < 1e-6 and abs(k["y"] - dy) < 1e-6


class TestSwatches:
    def test_nothing_saved_is_all_defaults(self):
        assert call("mergeSwatches", DEFAULTS, {}) == [{"name": n, "rgb": c, "custom": False} for n, c in DEFAULTS]
        assert call("mergeSwatches", DEFAULTS, None)[0]["custom"] is False

    def test_a_saved_colour_replaces_only_its_own_slot(self):
        got = call("mergeSwatches", DEFAULTS, {"blue": [1, 2, 3]})
        assert got == [{"name": "red", "rgb": [255, 0, 0], "custom": False}, {"name": "blue", "rgb": [1, 2, 3], "custom": True},
                       {"name": "hot pink", "rgb": [255, 105, 180], "custom": False}]

    @pytest.mark.parametrize("bad", [[1, 2], [1, 2, 3, 4], "red", [256, 0, 0], [-1, 0, 0], [1.5, 0, 0], None, [1, 2, "3"]])
    def test_a_broken_saved_colour_falls_back_to_the_default(self, bad):
        assert call("mergeSwatches", DEFAULTS, {"red": bad})[0] == {"name": "red", "rgb": [255, 0, 0], "custom": False}

    def test_a_default_swatch_sends_its_voice_name_and_a_changed_one_its_rgb(self):
        assert call("swatchCommand", {"name": "red", "rgb": [255, 0, 0], "custom": False}, 80) == {"color": "red", "lum": 80}
        assert call("swatchCommand", {"name": "red", "rgb": [10, 20, 30], "custom": True}, 80) == {"rgb": [10, 20, 30], "lum": 80}


class TestLiveSender:
    def run(self, script):
        full = (f'import * as M from "file:///{MODEL}";\nlet t = 0, sent = [], timers = [];\n'
                'const s = M.liveSender((v) => sent.push([t, v]), 100, () => t, (fn, d) => { timers.push([t + d, fn]); return timers.length; }, (id) => { timers[id - 1] = null; });\n'
                'const tick = (to) => { while (true) { const due = timers.map((x, i) => [x, i]).filter(([x]) => x && x[0] <= to).sort((a, b) => a[0][0] - b[0][0])[0]; if (!due) break; t = due[0][0]; const fn = due[0][1]; timers[due[1]] = null; fn(); } t = to; };\n'
                + script + '\nconsole.log(JSON.stringify(sent));')
        out = subprocess.run(["node", "--input-type=module", "-e", full], capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_the_first_value_goes_at_once(self):
        assert self.run('s.push("a");') == [[0, "a"]]

    def test_a_burst_is_limited_to_one_message_per_window_and_the_last_value_still_arrives(self):
        got = self.run('s.push("a"); t = 10; s.push("b"); t = 20; s.push("c"); t = 30; s.push("d"); tick(300);')
        assert got == [[0, "a"], [100, "d"]]

    def test_flush_sends_the_waiting_value_now(self):
        assert self.run('s.push("a"); t = 10; s.push("b"); s.flush();') == [[0, "a"], [10, "b"]]

    def test_flush_with_nothing_waiting_sends_nothing(self):
        assert self.run('s.push("a"); s.flush(); s.flush();') == [[0, "a"]]


class TestWheelPaint:
    def test_the_wheel_has_no_border_because_the_gradient_repeats_into_it(self):
        """A border on the wheel painted the opposite side's colours as slivers at the top, bottom, left and right; the ring is a box-shadow."""
        import re
        css = open(os.path.join(ROOT, "web", "style.css"), encoding="utf-8").read()
        rule = re.search(r"(?m)^\.wh-disc\s*\{([^}]*)\}", css).group(1)
        rule = re.sub(r"/\*.*?\*/", "", rule, flags=re.S)
        assert not re.search(r"(?<![-\w])border\s*:", rule) and "box-shadow" in rule
