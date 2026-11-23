"""The desktop shell's pure window logic (shell/geometry.js), run under node."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEO = os.path.join(ROOT, "shell", "geometry.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(body):
    script = f'const g = require("{GEO}");\n' + body
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestScale:
    def test_one_unit_at_1080p_whichever_way_up_and_it_follows_the_resolution(self):
        assert run("console.log(JSON.stringify([g.scaleFor(1920,1080), g.scaleFor(1080,1920), g.scaleFor(3840,2160)]))") == [1, 1, 2]


class TestOverlayBounds:
    def test_200x400_twenty_pixels_in_from_the_bottom_right_of_the_work_area(self):
        # the work area is the screen minus the taskbar (1080 - 48)
        r = run("console.log(JSON.stringify(g.overlayBounds({width:1920,height:1080}, {x:0,y:0,width:1920,height:1032})))")
        assert r == {"x": 1700, "y": 612, "width": 200, "height": 400}

    def test_it_scales_with_the_monitor(self):
        r = run("console.log(JSON.stringify(g.overlayBounds({width:3840,height:2160}, {x:0,y:0,width:3840,height:2160})))")
        assert (r["width"], r["height"]) == (400, 800)

    def test_it_follows_a_monitor_that_is_not_at_the_origin(self):
        r = run("console.log(JSON.stringify(g.overlayBounds({width:1920,height:1080}, {x:1920,y:0,width:1920,height:1080})))")
        assert (r["x"], r["y"]) == (1920 + 1920 - 200 - 20, 1080 - 400 - 20)


class TestStartMode:
    def test_a_cold_start_is_always_the_overlay(self):
        assert run('console.log(JSON.stringify(g.startMode(false, {mode:"fullscreen"})))') == "overlay"

    def test_a_restart_reopens_the_saved_mode(self):
        assert run('console.log(JSON.stringify(g.startMode(true, {mode:"fullscreen"})))') == "fullscreen"

    def test_a_missing_or_broken_state_file_means_the_overlay(self):
        assert run('console.log(JSON.stringify([g.startMode(true, null), g.startMode(true, {mode:"windowed"}), g.startMode(true, 5)]))') == ["overlay"] * 3

    def test_normalize_keeps_a_good_state_and_drops_a_bad_display_id(self):
        r = run('console.log(JSON.stringify([g.normalizeState({mode:"fullscreen", displayId: 7}), g.normalizeState({mode:"overlay", displayId:"x"})]))')
        assert r == [{"mode": "fullscreen", "displayId": 7}, {"mode": "overlay", "displayId": None}]


class TestPickDisplay:
    DISPLAYS = 'const d = [{id:1, bounds:{x:0,y:0,width:1920,height:1080}}, {id:2, bounds:{x:1920,y:0,width:1920,height:1080}}];\n'

    def test_the_overlay_stays_on_the_primary_monitor(self):
        assert run(self.DISPLAYS + 'console.log(g.pickDisplay("overlay", d, 1, 2, {x:2500,y:100}).id)') == 1

    def test_the_dashboard_opens_where_the_pointer_is(self):
        assert run(self.DISPLAYS + 'console.log(g.pickDisplay("fullscreen", d, 1, null, {x:2500,y:100}).id)') == 2

    def test_without_a_pointer_it_uses_the_saved_display_then_the_primary(self):
        assert run(self.DISPLAYS + 'console.log(JSON.stringify([g.pickDisplay("fullscreen", d, 1, 2, null).id, g.pickDisplay("fullscreen", d, 1, 9, null).id]))') == [2, 1]

    def test_an_unknown_primary_falls_back_to_the_first_display(self):
        assert run(self.DISPLAYS + 'console.log(g.pickDisplay("overlay", d, 99, null, null).id)') == 1


class TestModeFromAction:
    def test_only_the_two_mode_switches_count(self):
        assert run('console.log(JSON.stringify(["set_overlay","set_fullscreen","show_logs",undefined].map(g.modeFromAction)))') == ["overlay", "fullscreen", None, None]
