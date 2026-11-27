"""The orb's rules for the desktop shell's overlay window (web/js/orb-model.js, under node) and the page's stacking order."""
import json
from webcss import web_css
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "orb-model.js").replace("\\", "/")
node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(body):
    script = f'import * as M from "file:///{MODEL}";\n' + body
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@node
class TestRestingLook:
    def test_the_dashboard_shows_a_dim_orb_at_rest(self):
        assert run("console.log(JSON.stringify(M.restingLook(false)))") == {"op": 0.8, "scale": 0.8}

    def test_the_overlay_shows_nothing_at_rest(self):
        assert run("console.log(JSON.stringify(M.restingLook(true)))") == {"op": 0, "scale": 0.5}


@node
class TestBloomFitsTheWindow:
    def test_the_dashboard_bloom_is_the_full_size(self):
        assert run("console.log(M.bloomRadius(1, 200, 400, 1, false))") == 170

    def test_in_the_overlay_it_ends_inside_the_200px_wide_window_at_full_scale(self):
        # 170 px would reach past the window's edge and show the box; the bloom fades to zero at its rim so a smaller one has no edge
        assert run("console.log(M.bloomRadius(1, 200, 400, 1, true))") == 100

    def test_a_smaller_orb_scale_does_not_make_it_exceed_the_window(self):
        assert run("console.log(M.bloomRadius(1, 200, 400, 0.5, true))") == 100

    def test_a_scale_above_one_shrinks_it_further(self):
        assert run("console.log(M.bloomRadius(1, 200, 400, 1.25, true))") == 80

    def test_a_bigger_monitor_has_a_bigger_window_and_a_bigger_bloom(self):
        assert run("console.log(M.bloomRadius(2, 400, 800, 1, true))") == 200

    def test_a_window_already_big_enough_keeps_the_designed_size(self):
        assert run("console.log(M.bloomRadius(1, 1000, 1000, 1, true))") == 170


@node
class TestOverlayVisibility:
    def test_nothing_to_show_means_the_window_is_hidden(self):
        assert run("console.log(JSON.stringify([M.overlayVisible(0, 0), M.overlayVisible(0.005, 0)]))") == [False, False]

    def test_an_orb_on_its_way_up_or_still_fading_keeps_it_shown(self):
        assert run("console.log(JSON.stringify([M.overlayVisible(0, 1), M.overlayVisible(0.5, 0), M.overlayVisible(0.02, 0)]))") == [True, True, True]

    def test_the_head_stays_inside_the_overlay_width(self):
        assert run("console.log(JSON.stringify([M.headUnit(1, 200, true), M.headUnit(1, 200, false), M.headUnit(0.3, 200, true)]))") == [160 / 300, 1, 0.3]


def _css_z(selector):
    css = web_css()
    rule = re.search(re.escape(selector) + r"\s*\{[^}]*\}", css).group(0)
    return int(re.search(r"z-index:\s*(\d+)", rule).group(1))


class TestStackingOrder:
    """From the bottom: the lyrics, the windows, the calendar drawer."""

    def test_the_lyrics_are_under_every_window(self):
        ui = open(os.path.join(ROOT, "web", "js", "ui.js"), encoding="utf-8").read()
        first_window_z = int(re.search(r"let zTop = (\d+);", ui).group(1)) + 1           # focus() gives a window ++zTop
        assert _css_z("#lyrics") < first_window_z

    def test_the_calendar_drawer_is_over_the_windows_and_the_dock(self):
        assert _css_z(".cal-drawer") > _css_z("#dock") > _css_z("#lyrics")
