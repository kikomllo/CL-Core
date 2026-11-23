"""Settings used to make the monitor speak "No device is paired" every time it was opened: monitor.status is spoken, so it is asked once per
session, when the user opens the Presence tab (web/js/presence-model.js `once`, under node, and the wiring as source)."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "presence-model.js").replace("\\", "/")
node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return re.sub(r"//[^\n]*", "", f.read())


@node
def test_once_runs_the_function_the_first_time_only():
    script = f'import {{ once }} from "file:///{MODEL}"; let n = 0; const f = once(() => ++n); f(); f(); f(); console.log(JSON.stringify(n));'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip()) == 1


class TestWiring:
    tabs = read("web", "js", "settings-tabs.js")
    settings = read("web", "js", "settings.js")

    def test_building_the_presence_tab_does_not_ask_the_monitor(self):
        # the only monitor.status request is inside the once() wrapper, never in presenceTab's body
        assert self.tabs.count('"monitor.status"') == 1
        assert re.search(r'askStatusOnce = once\(\(\) => bridge\.act\("monitor\.status"\)\)', self.tabs)

    def test_the_request_is_the_tabs_show_hook(self):
        assert "show: askStatusOnce" in self.tabs

    def test_settings_calls_the_hook_when_a_tab_is_opened_by_the_user(self):
        assert "customById.get(t.id)?.show?.()" in self.settings
        # ... on the tab pill's click, not while the window is built
        assert re.search(r'onclick: \(\) => \{ tab = t\.id; showTab\(\); customById\.get\(t\.id\)\?\.show\?\.\(\); \}', self.settings)
