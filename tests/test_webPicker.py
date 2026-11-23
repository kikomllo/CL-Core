"""The lights widget: the bulb disc is the colour button (no separate swatch), and the popover lines up with it (web/js/picker-spot.js under node)."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPOT = os.path.join(ROOT, "web", "js", "picker-spot.js").replace("\\", "/")
node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(body):
    script = f'import {{ pickerSpot }} from "file:///{SPOT}";\n' + body
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


@node
class TestPickerSpot:
    VIEW = "{width: 1920, height: 1080}"
    SIZE = "{width: 250, height: 300}"

    def test_its_left_edge_lines_up_with_the_control_and_it_opens_below(self):
        r = run(f"console.log(JSON.stringify(pickerSpot({{left: 100, top: 200, right: 136, bottom: 236}}, {self.SIZE}, {self.VIEW})))")
        assert r == {"left": 100, "top": 242}

    def test_it_is_kept_inside_the_viewport_on_the_right(self):
        r = run(f"console.log(JSON.stringify(pickerSpot({{left: 1900, top: 200, right: 1920, bottom: 236}}, {self.SIZE}, {self.VIEW})))")
        assert r["left"] == 1920 - 250 - 8

    def test_a_control_at_the_very_left_still_leaves_the_margin(self):
        assert run(f"console.log(pickerSpot({{left: 0, top: 200, right: 36, bottom: 236}}, {self.SIZE}, {self.VIEW}).left)") == 8

    def test_it_opens_above_when_there_is_no_room_below(self):
        r = run(f"console.log(JSON.stringify(pickerSpot({{left: 100, top: 900, right: 136, bottom: 936}}, {self.SIZE}, {self.VIEW})))")
        assert r["top"] == 900 - 300 - 6


class TestLightRow:
    js = re.sub(r"//[^\n]*", "", read("web", "js", "lights.js"))
    css = read("web", "style.css")

    def test_the_separate_colour_swatch_is_gone(self):
        assert "swatch-btn" not in self.js and "swatch-btn" not in self.css

    def test_the_bulb_disc_is_the_button_that_opens_the_colour_popover(self):
        assert re.search(r'h\("button", \{ class: discClass \+ " pick"[\s\S]*?onclick: \(e\) => openPicker\(l, e\.currentTarget\)', self.js)

    def test_a_light_without_colour_keeps_a_plain_indicator(self):
        assert 'l.colour === false' in self.js and re.search(r'l\.colour === false\s*\? h\("span"', self.js)

    def test_clicking_the_disc_again_closes_the_popover_instead_of_reopening_it(self):
        # the outside-click handler must ignore the disc, or it would close the popover and the click would open it again
        assert '!e.target.closest(".disc.pick")' in self.js

    def test_the_hover_ring_is_inside_the_disc_so_the_scrolling_list_cannot_crop_it(self):
        # .mod-list is overflow: auto and the disc sits at its left edge: a ring drawn outside the disc is clipped there
        rule = re.search(r"button\.disc:not\(:disabled\):hover[^{]*\{[^}]*\}", self.css).group(0)
        assert re.search(r"outline-offset:\s*-\d+px", rule)
