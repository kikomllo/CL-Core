"""ui_control actions that open a dashboard window (web/js/ui-control-model.js under node): "show the logs", "show my todo list"."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "ui-control-model.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def call(fn, *args):
    script = f'import * as M from "file:///{MODEL}";\nconsole.log(JSON.stringify(M.{fn}(...{json.dumps(list(args))})))'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_actions_name_their_windows():
    assert call("windowForAction", "show_logs") == "widget_logs"
    assert call("windowForAction", "show_todo") == "widget_todo"


def test_mode_switches_and_unknown_actions_open_nothing():
    for action in ("set_overlay", "set_fullscreen", "save_state", "toString", "constructor", None):
        assert call("windowForAction", action) is None


def test_in_the_overlay_a_window_floats_instead_of_taking_the_screen():
    """The overlay draws no windows and takes no clicks: a window shown from it is an unpinned one, on top of other apps (like the Qt widgets)."""
    assert call("showPlan", "overlay", True) == "float"
    assert call("showPlan", "fullscreen", True) == "open"
    assert call("showPlan", "overlay", False) == "open"          # a plain browser tab has no overlay and no floating windows


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_the_page_survives_a_shell_that_is_older_than_it():
    """A reload picks up new web files at once, the shell only when it is restarted. The page once called a shell method that the running
    shell did not have, died while loading and left the overlay without its orb: every use of the floating-window API is behind `canFloat`."""
    main, ui = _read("web", "js", "main.js"), _read("web", "js", "ui.js")
    assert "export const canFloat = !!(SHELL && typeof SHELL.float === \"function\")" in ui
    assert "new Set(canFloat ? SHELL.floating() : [])" in main
    assert "if (canFloat) {\n  SHELL.onFloating(" in main.replace("\r\n", "\n")
    assert "canFloat && floating.has(" in main and "showPlan(currentMode(), canFloat)" in main
    assert "hidden: !canFloat" in ui
