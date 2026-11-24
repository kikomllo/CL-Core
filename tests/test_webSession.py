"""What the dashboard puts back after a reload, a restart of the UI or a mode change (web/js/session-model.js under node): the windows that
were open (not only the pinned ones) and each window's own choice (Settings tab, To-Do list, Media drawer)."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "session-model.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def call(fn, *args):
    script = f'import * as M from "file:///{MODEL}";\nconsole.log(JSON.stringify(M.{fn}(...{json.dumps(list(args))})))'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestWindowsToRestore:
    def test_a_continuing_session_puts_back_everything_that_was_open_and_the_pinned(self):
        assert call("windowsToRestore", {"pinned": ["c"], "open": ["a", "b"], "restoreAll": True}) == ["a", "b", "c"]

    def test_a_cold_start_opens_only_the_pinned(self):
        assert call("windowsToRestore", {"pinned": ["c"], "open": ["a", "b"], "restoreAll": False}) == ["c"]

    def test_no_window_is_opened_twice(self):
        assert call("windowsToRestore", {"pinned": ["a"], "open": ["a", "b", "a"], "restoreAll": True}) == ["a", "b"]

    def test_nothing_saved_is_fine(self):
        assert call("windowsToRestore", {"restoreAll": True}) == []


class TestParseIds:
    @pytest.mark.parametrize("raw, expected", [
        ('["a","b"]', ["a", "b"]), (None, []), ("", []), ("not json", []), ('{"a":1}', []), ('["a", 3, "", null, "a"]', ["a"]),
    ])
    def test_whatever_was_stored(self, raw, expected):
        assert call("parseIds", raw) == expected


class TestSavedChoice:
    TABS = ["system", "audio", "presence"]

    def test_a_saved_tab_is_used(self):
        assert call("savedChoice", {"settings.tab": "audio"}, "settings.tab", self.TABS, "system") == "audio"

    def test_a_tab_that_no_longer_exists_falls_back(self):
        assert call("savedChoice", {"settings.tab": "gone"}, "settings.tab", self.TABS, "system") == "system"

    def test_nothing_saved_falls_back(self):
        assert call("savedChoice", {}, "settings.tab", self.TABS, "system") == "system"
        assert call("savedChoice", None, "settings.tab", self.TABS, "system") == "system"

    def test_without_an_allowed_list_any_value_counts(self):
        assert call("savedChoice", {"todo.list": "Groceries"}, "todo.list", None, "My To-Do List") == "Groceries"
