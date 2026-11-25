"""Zoom of the whole UI (web/js/zoom-model.js under node): a factor on the fluid unit, so windows, text and the orb are laid out again at the
new size. Per-window zoom comes in a later part of the same feature."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "zoom-model.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def call(fn, *args):
    script = f'import * as M from "file:///{MODEL}";\nconsole.log(JSON.stringify(M.{fn}(...{json.dumps(list(args))})))'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestClamp:
    @pytest.mark.parametrize("z, expected", [(1, 1), (0.1, 0.6), (5, 2), (1.234, 1.23), ("1.5", 1.5), (None, 1), ("x", 1), (-1, 1), (0, 1)])
    def test_global(self, z, expected):
        assert call("clampZoom", z) == expected

    def test_a_widget_may_go_further(self):
        assert call("clampZoom", 0.5, [0.5, 2.5]) == 0.5 and call("clampZoom", 9, [0.5, 2.5]) == 2.5


class TestSteps:
    def test_ten_percent_a_step_inside_the_range(self):
        assert call("stepZoom", 1, 1) == 1.1 and call("stepZoom", 1, -1) == 0.9

    def test_no_float_drift_over_many_steps(self):
        script = f'import * as M from "file:///{MODEL}"; let z = 1; for (let i = 0; i < 7; i++) z = M.stepZoom(z, 1); console.log(JSON.stringify(z))'
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
        assert json.loads(out.stdout) == 1.7

    def test_it_stops_at_the_ends(self):
        assert call("stepZoom", 2, 1) == 2 and call("stepZoom", 0.6, -1) == 0.6


class TestWheel:
    def test_a_notch_up_zooms_in_one_step(self):
        assert call("wheelSteps", 0, -100) == {"steps": 1, "acc": -20}

    def test_a_notch_down_zooms_out(self):
        assert call("wheelSteps", 0, 100)["steps"] == -1

    def test_a_trackpad_adds_up_small_turns(self):
        steps, acc = 0, 0
        for _ in range(10):
            r = call("wheelSteps", acc, -10)
            steps += r["steps"]; acc = r["acc"]
        assert steps == 1 and acc == -20

    def test_nothing_in_a_tiny_turn(self):
        assert call("wheelSteps", 0, -5)["steps"] == 0


class TestKeys:
    @pytest.mark.parametrize("event, expected", [
        ({"type": "keydown", "key": "=", "ctrlKey": True}, "in"), ({"type": "keydown", "key": "+", "ctrlKey": True, "shiftKey": True}, "in"),
        ({"type": "keydown", "key": "-", "ctrlKey": True}, "out"), ({"type": "keydown", "key": "0", "ctrlKey": True}, "reset"),
        ({"type": "keydown", "key": "=", "ctrlKey": False}, None), ({"type": "keyup", "key": "=", "ctrlKey": True}, None),
        ({"type": "keydown", "key": "-", "ctrlKey": True, "altKey": True}, None), ({"type": "keydown", "key": "a", "ctrlKey": True}, None),
    ])
    def test_which_keys_zoom(self, event, expected):
        assert call("zoomKey", event) == expected


class TestSavedState:
    def test_anything_stored_becomes_a_valid_state(self):
        assert call("parseZoomState", None) == {"global": 1, "widgets": {}}
        assert call("parseZoomState", {"global": 9, "widgets": {"widget_todo": 1.3, "bad id": 2, "widget_x": 1, "widget_y": "no"}}) == {"global": 2, "widgets": {"widget_todo": 1.3}}


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def test_the_unit_carries_the_zoom_and_the_overlay_is_not_zoomed():
    main = _read("web", "js", "main.js")
    assert '* (currentMode() === "overlay" ? 1 : getGlobal())' in main
    assert 'addEventListener("jarvis-zoom", setUnit)' in main and "startZoomInput()" in main
    assert 'window.dispatchEvent(new Event("jarvis-unit"))' in main


def test_windows_follow_a_changed_unit_and_terminals_measure_again():
    assert 'addEventListener("jarvis-unit", () => { refit();' in _read("web", "js", "ui.js")
    assert 'window.addEventListener("jarvis-unit", onUnit)' in _read("web", "js", "claude.js")


class TestPerWindowZoom:
    """A window's own zoom (Ctrl+wheel over it): its content scales, its frame does not; it multiplies the global zoom."""

    def test_the_range_is_wider_for_a_window_than_for_the_whole_ui(self):
        assert call("clampZoom", 0.5, [0.5, 2.5]) == 0.5 and call("clampZoom", 0.5) == 0.6
        assert call("stepZoom", 2.5, 1, [0.5, 2.5]) == 2.5 and call("stepZoom", 2, 1) == 2

    def test_saved_window_zooms_keep_only_real_ones(self):
        got = call("parseZoomState", {"global": 1.2, "widgets": {"widget_todo": 1.6, "widget_logs": 1, "bad id": 2, "widget_x": 99}})
        assert got == {"global": 1.2, "widgets": {"widget_todo": 1.6, "widget_x": 2.5}}


def test_a_window_zoom_is_a_unit_of_its_own_and_never_changes_its_frame():
    ui, zoom = _read("web", "js", "ui.js"), _read("web", "js", "zoom.js")
    assert "el.style.setProperty(\"--u\", `calc(var(--ug) * ${z})`)" in ui          # content only: the frame's px size is untouched
    assert "export function fitMin(el, u = unitOf(el))" in ui                        # what must fit is measured in the window's own unit
    assert "const unitOf = (el) => unit() * getWidget(el.dataset.id);" in ui
    assert "toLayout(r, unit()," in ui, "saved frames stay in the global unit, so a window zoom never reaches the layout"
    assert "--ug: 1px;" in _read("web", "style.css"), "calc(var(--ug) * z) must never be invalid"
    assert 'addEventListener("jarvis-widget-zoom"' in ui and '"jarvis-widget-zoom"' in zoom


def test_ctrl_wheel_over_a_window_is_its_zoom_and_with_alt_the_whole_uis():
    zoom = _read("web", "js", "zoom.js")
    assert "const win = !e.altKey && (" in zoom
    assert "setWidget(win.dataset.id, z); showWidgetBadge(" in zoom
    assert 'document.documentElement.dataset.mode === "widget" && document.querySelector(".win")' in zoom      # a floating window is one window
