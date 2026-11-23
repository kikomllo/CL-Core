"""The web Updates window's pure logic (web/js/updates-model.js), run under node."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "updates-model.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(expr):
    script = f'import * as M from "file:///{MODEL}";\nconsole.log(JSON.stringify({expr}));'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestInterpret:
    def test_checking_shows_the_bar_and_no_list(self):
        v = run('M.interpret({status: "checking"})')
        assert v == {"kind": "view", "label": "Checking for updates... Please wait.", "busy": True, "mode": "list", "items": [], "clearLog": False}

    def test_updating_switches_to_the_install_log_and_clears_it(self):
        v = run('M.interpret({status: "updating"})')
        assert v["busy"] is True and v["mode"] == "log" and v["clearLog"] is True

    def test_ready_lists_the_updates_and_counts_them(self):
        v = run('M.interpret({status: "ready", details: [{type: "app", name: "Firefox", id: "APP:Mozilla.Firefox"}, {type: "driver", name: "GPU", id: "DRV:GPU"}]})')
        assert v["label"] == "Found 2 updates." and v["busy"] is False and v["mode"] == "list" and [u["id"] for u in v["items"]] == ["APP:Mozilla.Firefox", "DRV:GPU"]
        assert run('M.interpret({status: "ready", details: [{id: "APP:x", name: "x"}]})')["label"] == "Found 1 update."
        assert run('M.interpret({status: "ready", details: []})')["label"] == "Found 0 updates."

    def test_a_log_message_is_a_line_to_append(self):
        assert run('M.interpret({action: "log", text: "--- Starting App Update: X ---"})') == {"kind": "log", "text": "--- Starting App Update: X ---"}
        assert run('M.interpret({action: "log"})') == {"kind": "log", "text": ""}

    @pytest.mark.parametrize("msg", ["null", "5", '"ready"', "{}", '{"status": "weird"}', "[]"])
    def test_anything_else_is_ignored(self, msg):
        assert run(f"M.interpret({msg})") is None


class TestItems:
    def test_a_malformed_update_cannot_be_installed(self):
        got = run('M.cleanItems([{id: "APP:a", name: "A"}, null, 5, "x", {name: "no id"}, {id: ""}, {id: 7}, {id: "DRV:b"}])')
        assert [u["id"] for u in got] == ["APP:a", "DRV:b"]
        assert run("M.cleanItems(null)") == [] and run('M.cleanItems({id: "x"})') == []

    def test_the_label_matches_the_qt_widget(self):
        assert run('M.itemLabel({type: "app", name: "Firefox"})') == "[APP] Firefox"
        assert run("M.itemLabel({})") == "[UNKNOWN] Unknown"
        assert run("M.itemLabel(null)") == "[UNKNOWN] Unknown"
