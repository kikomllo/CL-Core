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


class TestReloadKeys:
    """The shell has no menu, which is where Electron keeps Ctrl+R / F5: they did nothing until the shell handled them itself."""

    @pytest.mark.parametrize("input_, expected", [
        ({"type": "keyDown", "key": "r", "control": True}, "reload"),
        ({"type": "keyDown", "key": "R", "control": True}, "reload"),
        ({"type": "keyDown", "key": "F5"}, "reload"),
        ({"type": "keyDown", "key": "R", "control": True, "shift": True}, "hard"),
        ({"type": "keyDown", "key": "F5", "control": True}, "hard"),
        ({"type": "keyDown", "key": "F5", "shift": True}, "hard"),
        ({"type": "keyUp", "key": "r", "control": True}, None),
        ({"type": "keyDown", "key": "r"}, None),
        ({"type": "keyDown", "key": "r", "control": True, "alt": True}, None),
        ({"type": "keyDown", "key": "w", "control": True}, None),
    ])
    def test_which_keys_reload(self, input_, expected):
        assert run(f"console.log(JSON.stringify(g.reloadKind({json.dumps(input_)})))") == expected


class TestScale:
    def test_one_unit_at_1080p_whichever_way_up_and_it_follows_the_resolution(self):
        assert run("console.log(JSON.stringify([g.scaleFor(1920,1080), g.scaleFor(1080,1920), g.scaleFor(3840,2160)]))") == [1, 1, 2]


class TestOverlayBounds:
    def test_a_200px_square_twenty_pixels_in_from_the_bottom_right_corner_of_the_work_area(self):
        # the work area is the screen minus the taskbar (1080 - 48)
        r = run("console.log(JSON.stringify(g.overlayBounds({width:1920,height:1080}, {x:0,y:0,width:1920,height:1032})))")
        assert r == {"x": 1700, "y": 812, "width": 200, "height": 200}

    def test_it_scales_with_the_monitor(self):
        r = run("console.log(JSON.stringify(g.overlayBounds({width:3840,height:2160}, {x:0,y:0,width:3840,height:2160})))")
        assert (r["width"], r["height"]) == (400, 400)

    def test_it_follows_a_monitor_that_is_not_at_the_origin(self):
        r = run("console.log(JSON.stringify(g.overlayBounds({width:1920,height:1080}, {x:1920,y:0,width:1920,height:1080})))")
        assert (r["x"], r["y"]) == (1920 + 1920 - 200 - 20, 1080 - 200 - 20)


class TestStartMode:
    def test_every_start_reopens_the_saved_mode(self):
        # a dashboard left fullscreen opens fullscreen on a cold start too (the startup cover is only seen there)
        assert run('console.log(JSON.stringify([g.startMode({mode:"fullscreen"}), g.startMode({mode:"overlay"})]))') == ["fullscreen", "overlay"]

    def test_a_missing_or_broken_state_file_means_the_overlay(self):
        assert run('console.log(JSON.stringify([g.startMode(null), g.startMode({mode:"windowed"}), g.startMode(5)]))') == ["overlay"] * 3

    def test_normalize_keeps_a_good_state_and_drops_a_bad_display_id(self):
        r = run('console.log(JSON.stringify([g.normalizeState({mode:"fullscreen", displayId: 7}), g.normalizeState({mode:"overlay", displayId:"x"})]))')
        assert r == [{"mode": "fullscreen", "displayId": 7, "floating": {}}, {"mode": "overlay", "displayId": None, "floating": {}}]


class TestPickDisplay:
    DISPLAYS = 'const d = [{id:1, bounds:{x:0,y:0,width:1920,height:1080}}, {id:2, bounds:{x:1920,y:0,width:1920,height:1080}}];\n'

    def test_the_overlay_stays_on_the_primary_monitor(self):
        assert run(self.DISPLAYS + 'console.log(g.pickDisplay("overlay", d, 1, 2, {x:2500,y:100}).id)') == 1

    def test_the_dashboard_opens_where_the_pointer_is(self):
        assert run(self.DISPLAYS + 'console.log(g.pickDisplay("fullscreen", d, 1, null, {x:2500,y:100}).id)') == 2

    def test_without_a_pointer_it_uses_the_saved_display_then_the_primary(self):
        assert run(self.DISPLAYS + 'console.log(JSON.stringify([g.pickDisplay("fullscreen", d, 1, 2, null).id, g.pickDisplay("fullscreen", d, 1, 9, null).id]))') == [2, 1]

    def test_the_first_placement_goes_back_to_the_saved_monitor_and_to_the_primary_when_it_is_gone(self):
        # no pointer is passed for it: the dashboard reopens where it was, not where the mouse happens to be
        assert run(self.DISPLAYS + 'console.log(JSON.stringify([g.pickDisplay("fullscreen", d, 1, 2, null).id, g.pickDisplay("fullscreen", d, 1, 42, null).id]))') == [2, 1]

    def test_an_unknown_primary_falls_back_to_the_first_display(self):
        assert run(self.DISPLAYS + 'console.log(g.pickDisplay("overlay", d, 99, null, null).id)') == 1


class TestModeFromAction:
    def test_only_the_two_mode_switches_count(self):
        assert run('console.log(JSON.stringify(["set_overlay","set_fullscreen","show_logs",undefined].map(g.modeFromAction)))') == ["overlay", "fullscreen", None, None]


class TestFloatingWindows:
    """Unpinned windows: windows of their own, always on top, that keep their place and can be on any monitor."""
    DISPLAYS = "[{id:1,bounds:{x:0,y:0,width:1920,height:1080}},{id:2,bounds:{x:1920,y:0,width:1920,height:1080}}]"
    WORK = "{x:0,y:0,width:1920,height:1032}"

    def bounds(self, req, index=0):
        return run(f"console.log(JSON.stringify(g.floatBounds({req}, {self.DISPLAYS}, {self.WORK}, {index})))")

    def test_unpinning_keeps_the_window_where_it_was_also_on_another_monitor(self):
        assert self.bounds("{x:2100,y:200,width:380,height:440}") == {"x": 2100, "y": 200, "width": 380, "height": 440}

    def test_a_window_whose_monitor_is_gone_comes_back_onto_the_primary_one(self):
        r = self.bounds("{x:5000,y:300,width:380,height:440}")
        assert 0 <= r["x"] <= 1920 - 380 and 0 <= r["y"] <= 1032 - 440

    def test_from_the_overlay_it_opens_above_the_overlay_box_in_the_corner(self):
        r = self.bounds("{width:380,height:440}")
        assert r["x"] + r["width"] <= 1920 - 40 + 1 and r["y"] + r["height"] <= 1032 - 200 - 20 - 40 + 1       # clear of the overlay's 200 px box
        assert r["x"] > 1920 / 2 and r["y"] > 0                                                                  # in the corner, not the middle

    def test_each_further_window_steps_up_and_left(self):
        a, b = self.bounds("{width:380,height:300}", 0), self.bounds("{width:380,height:300}", 1)
        assert b["x"] < a["x"] and b["y"] < a["y"]

    def test_it_is_never_smaller_than_the_minimum(self):
        r = self.bounds("{width:10,height:10}")
        assert (r["width"], r["height"]) == (160, 90)

    def test_the_saved_ones_keep_only_usable_entries(self):
        raw = '{widget_todo:{x:1,y:2,width:400,height:300},"bad id":{x:1,y:1,width:5,height:5},widget_x:{x:"a"},widget_small:{x:0,y:0,width:20,height:20}}'
        got = run(f"console.log(JSON.stringify(g.normalizeState({{mode:'overlay', floating: {raw}}}).floating))")
        assert got == {"widget_todo": {"x": 1, "y": 2, "width": 400, "height": 300}, "widget_small": {"x": 0, "y": 0, "width": 160, "height": 90}}

    def test_no_saved_state_means_none_floating(self):
        assert run("console.log(JSON.stringify(g.normalizeState(null).floating))") == {}
