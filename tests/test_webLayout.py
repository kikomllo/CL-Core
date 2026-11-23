"""A window's saved layout (web/js/layout-model.js under node) and the rule that it, not the window's current rectangle, decides where it is drawn."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "layout-model.js").replace("\\", "/")
node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(body):
    script = f'import * as M from "file:///{MODEL}";\n' + body
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@node
class TestSavedLayout:
    def test_a_window_saved_and_restored_in_the_same_viewport_is_where_it_was(self):
        r = run("const l = M.toLayout({left: 768, top: 324, width: 500, height: 300}, 1, 1920, 1080); console.log(JSON.stringify(M.fromLayout(l, 1, 1920, 1080)))")
        assert r == {"x": 768, "y": 324, "w": 500, "h": 300}

    def test_the_position_is_relative_so_another_screen_puts_it_in_the_same_relative_spot(self):
        assert run("console.log(JSON.stringify(M.fromLayout({fx: 0.4, fy: 0.3, w: 500, h: 300}, 1, 1080, 1920)))") == {"x": 432, "y": 576, "w": 500, "h": 300}

    def test_the_size_follows_the_fluid_unit(self):
        r = run("console.log(JSON.stringify(M.fromLayout({fx: 0, fy: 0, w: 500, h: 300}, 2, 3840, 2160)))")
        assert (r["w"], r["h"]) == (1000, 600)

    def test_a_window_is_kept_inside_the_viewport(self):
        r = run("console.log(JSON.stringify(M.fromLayout({fx: 0.95, fy: 0.95, w: 500, h: 300}, 1, 1920, 1080)))")
        assert (r["x"], r["y"]) == (1420, 780)

    def test_a_window_bigger_than_the_viewport_sits_at_the_corner_not_off_screen(self):
        r = run("console.log(JSON.stringify(M.fromLayout({fx: 0.4, fy: 0.3, w: 500, h: 300}, 1, 200, 400)))")
        assert r["x"] == 0

    def test_a_squeezed_viewport_does_not_change_the_layout_it_comes_back_to(self):
        # the overlay is a 200x400 page: placing the window there must leave the saved layout alone, so the dashboard gets the same spot back
        r = run("""const l = {fx: 0.4, fy: 0.3, w: 500, h: 300};
          const before = M.fromLayout(l, 1, 1920, 1080); M.fromLayout(l, 0.185, 200, 400);
          console.log(JSON.stringify([before, M.fromLayout(l, 1, 1920, 1080), l]))""")
        assert r[0] == r[1] and r[2] == {"fx": 0.4, "fy": 0.3, "w": 500, "h": 300}


@node
class TestWindowsDrawn:
    def test_windows_are_not_drawn_in_the_overlay(self):
        assert run('console.log(JSON.stringify([M.windowsDrawn("overlay"), M.windowsDrawn("fullscreen"), M.windowsDrawn(undefined)]))') == [False, True, True]


class TestWindowManager:
    ui = open(os.path.join(ROOT, "web", "js", "ui.js"), encoding="utf-8").read()

    def test_the_viewport_listener_places_windows_from_the_saved_layout_not_from_where_they_are(self):
        # `place(el, r.left, r.top)` on resize read the rectangle of a window that is display: none in the overlay (0x0 at 0,0) and put it there for good
        assert "place(el, r.left, r.top)" not in self.ui
        assert 'addEventListener("resize", refit)' in self.ui and 'addEventListener("jarvis-mode", refit)' in self.ui

    def test_refit_does_nothing_while_the_windows_are_not_drawn(self):
        assert "if (!windowsDrawn(" in self.ui

    def test_every_window_gets_a_saved_layout_when_it_opens_and_nothing_is_saved_from_a_hidden_one(self):
        assert "if (!layout) persist(def.id, el);" in self.ui
        assert "if (!r.width || !r.height) return;" in self.ui
