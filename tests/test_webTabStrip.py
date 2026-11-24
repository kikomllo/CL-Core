"""A row of tab pills wider than its window (web/js/tabstrip-model.js under node): it scrolls with the mouse wheel, shows a fade where tabs
are hidden, and keeps the selected tab in view. Settings' tabs were unreachable in a small window: the bar is hidden and a wheel only scrolls vertically."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "tabstrip-model.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(body):
    script = f'import * as M from "file:///{MODEL}";\n' + body
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def call(fn, *args):
    return run(f"console.log(JSON.stringify(M.{fn}(...{json.dumps(list(args))})))")


STRIP = {"scrollLeft": 0, "scrollWidth": 500, "clientWidth": 200}


class TestWheel:
    def test_a_vertical_wheel_scrolls_the_strip_sideways(self):
        assert call("wheelScroll", {"deltaX": 0, "deltaY": 100}, STRIP) == 100

    def test_it_stops_at_the_ends(self):
        assert call("wheelScroll", {"deltaX": 0, "deltaY": 900}, {**STRIP, "scrollLeft": 100}) == 300
        assert call("wheelScroll", {"deltaX": 0, "deltaY": -900}, {**STRIP, "scrollLeft": 100}) == 0

    def test_at_an_end_the_turn_is_left_to_the_page(self):
        assert call("wheelScroll", {"deltaX": 0, "deltaY": 50}, {**STRIP, "scrollLeft": 300}) is None
        assert call("wheelScroll", {"deltaX": 0, "deltaY": -50}, STRIP) is None

    def test_a_sideways_wheel_or_trackpad_is_left_to_the_browser(self):
        assert call("wheelScroll", {"deltaX": 80, "deltaY": 10}, STRIP) is None

    def test_a_strip_that_fits_is_left_alone(self):
        assert call("wheelScroll", {"deltaX": 0, "deltaY": 100}, {"scrollLeft": 0, "scrollWidth": 200, "clientWidth": 200}) is None


class TestEdges:
    def test_fades_only_where_tabs_are_hidden(self):
        assert call("edges", STRIP) == {"left": False, "right": True}
        assert call("edges", {**STRIP, "scrollLeft": 100}) == {"left": True, "right": True}
        assert call("edges", {**STRIP, "scrollLeft": 300}) == {"left": True, "right": False}
        assert call("edges", {"scrollLeft": 0, "scrollWidth": 200, "clientWidth": 200}) == {"left": False, "right": False}


class TestReveal:
    def test_a_tab_past_the_right_edge_is_scrolled_in_with_a_margin(self):
        assert call("revealOffset", STRIP, 350, 60, 10) == 220           # right edge of the tab at clientWidth - pad

    def test_a_tab_past_the_left_edge_is_scrolled_in(self):
        assert call("revealOffset", {**STRIP, "scrollLeft": 250}, -40, 60, 10) == 200

    def test_a_visible_tab_changes_nothing(self):
        assert call("revealOffset", {**STRIP, "scrollLeft": 50}, 60, 60, 10) == 50

    def test_it_never_scrolls_past_the_ends_or_when_everything_fits(self):
        assert call("revealOffset", STRIP, 900, 60, 10) == 300
        assert call("revealOffset", {"scrollLeft": 0, "scrollWidth": 200, "clientWidth": 200}, 150, 80, 10) == 0
