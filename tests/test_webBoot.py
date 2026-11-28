"""The startup cover: the supervisor's retained boot state (src/utils/clBootState.py), the cover's rules (web/js/boot-model.js, under node) and how
the pieces are wired (read as text: importing clJarvis starts the supervisor, and the page needs a browser)."""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from webcss import web_css

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "utils"))
from clBootState import build_boot_state, module_key  # noqa: E402

MODEL = os.path.join(ROOT, "web", "js", "boot-model.js").replace("\\", "/")
node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(body):
    script = f'import * as M from "file:///{MODEL}";\n' + body
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def read(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


class TestBootState:
    NAMES = ["Web UI", "Desktop Shell", "Whisper", "Light"]

    def test_a_module_is_matched_by_the_name_it_reports_itself_by(self):
        assert module_key("web_ui") == module_key("Web UI") == "web ui"

    def test_it_lists_every_expected_module_in_start_order(self):
        state = build_boot_state(self.NAMES, {"web ui": 0.94}, False, 1000.4)
        assert [m["name"] for m in state["modules"]] == ["web ui", "desktop shell", "whisper", "light"]
        assert state["modules"][0] == {"name": "web ui", "ready": True, "at": 0.9}
        assert state["modules"][1] == {"name": "desktop shell", "ready": False, "at": None}
        assert (state["ready"], state["total"], state["online"], state["started"]) == (1, 4, False, 1000)

    def test_online_counts_everything_even_a_module_whose_name_never_matched(self):
        state = build_boot_state(self.NAMES, {"web ui": 1.0}, True, 5)
        assert state["ready"] == state["total"] == 4 and state["online"] is True


@node
class TestPhases:
    def phases(self, steps):
        """steps: [[state-or-null, now-ms], ...] -> the phase after each."""
        return run(f"""let p = null; const out = [];
for (const [s, t] of {json.dumps(steps)}) {{ p = M.nextPhase(p, s, t, 60000); out.push(p.phase); }}
console.log(JSON.stringify(out));""")

    BOOTING = {"started": 1, "online": False, "ready": 3, "total": 16, "modules": []}
    ONLINE = {"started": 1, "online": True, "ready": 16, "total": 16, "modules": []}

    def test_nothing_covers_until_a_state_arrives(self):
        assert self.phases([[None, 0], [None, 5000]]) == ["idle", "idle"]

    def test_a_cleared_or_foreign_message_changes_nothing(self):
        assert self.phases([[{}, 0], [{"online": False}, 100]]) == ["idle", "idle"]

    def test_a_boot_under_way_covers_then_announces_then_lifts(self):
        assert self.phases([[self.BOOTING, 0], [self.BOOTING, 5000], [self.ONLINE, 9000], [self.ONLINE, 10500], [self.ONLINE, 11100]]) == \
            ["loading", "loading", "online", "online", "done"]

    def test_a_page_that_opens_when_the_system_is_already_online_never_covers(self):
        assert self.phases([[self.ONLINE, 0], [self.ONLINE, 100]]) == ["done", "done"]

    def test_a_boot_that_stalls_lifts_after_the_timeout(self):
        assert self.phases([[self.BOOTING, 0], [self.BOOTING, 59000], [self.BOOTING, 60000], [self.BOOTING, 90000]]) == \
            ["loading", "loading", "done", "done"]

    def test_a_new_boot_covers_again(self):
        again = dict(self.BOOTING, started=2)
        assert self.phases([[self.ONLINE, 0], [again, 1000]]) == ["done", "loading"]

    def test_covering_is_loading_and_the_online_hold_only(self):
        assert run('console.log(JSON.stringify(["idle","loading","online","done"].map(M.covering)))') == [False, True, True, False]


@node
class TestWhatItSays:
    STATE = {"started": 1, "online": False, "ready": 2, "total": 3,
             "modules": [{"name": "web ui", "ready": True, "at": 1.04}, {"name": "mic", "ready": True, "at": 7.5}, {"name": "whisper", "ready": False, "at": None}]}

    def describe(self, phase="loading", since=3000):
        return run(f"console.log(JSON.stringify(M.describe({json.dumps(self.STATE)}, {json.dumps(phase)}, {since})))")

    def test_it_counts_and_lists_the_modules(self):
        d = self.describe()
        assert d["count"] == "02 of 3 modules" and d["title"] == "Starting JARVIS" and abs(d["progress"] - 2 / 3) < 1e-9
        assert [(r["name"], r["state"], r["time"]) for r in d["rows"]] == [("web ui", "done", "1.0 s"), ("mic", "done", "7.5 s"), ("whisper", "wait", "loading")]
        assert d["warn"] is False

    def test_a_slow_boot_names_what_it_is_waiting_for(self):
        d = self.describe(since=30000)
        assert d["warn"] is True and d["sub"] == "Waiting on whisper." and d["rows"][2]["state"] == "stuck"

    def test_the_hold_says_the_system_is_online(self):
        assert self.describe("online")["title"] == "System online"


class TestWiring:
    def test_the_supervisor_publishes_the_boot_state_retained_and_starts_a_new_one_on_every_boot(self):
        sup = read("clJarvis.py")
        assert re.search(r'client\.publish\("jarvis/sys/boot_state",.*retain=True', sup)
        assert "publish_boot_state(False)          # a new boot" in sup and "publish_boot_state(True)" in sup

    def test_the_dashboard_and_the_floating_windows_start_it(self):
        assert "startBoot();" in read("web", "js", "main.js")
        assert "startBoot({ readout: false })" in read("web", "js", "widget-main.js")

    def test_the_windows_and_the_dock_stay_off_the_screen_while_it_is_on(self):
        css = web_css()
        assert re.search(r'html\[data-boot="1"\] #widgets > \.win,[^{]*#dock[^{]*\{[^}]*opacity: 0', css)

    def test_the_orb_has_a_boot_look_that_the_overlay_does_not_use(self):
        orb = read("web", "js", "orb.js")
        assert "BOOTING:" in orb and 'dataset.boot === "1" && !overlay()' in orb

    def test_it_can_be_switched_off_in_core_json(self):
        assert "c.enabled !== false" in read("web", "js", "boot.js")


class TestWindowsOpenWhenTheSystemIsOnline:
    """Live 2026-11-28: the restored To-Do window asked for its list while the Utilities service was still starting, got no answer, and stayed empty
    until it was closed and opened again. Windows are restored once the boot says the system is online (or gave up waiting)."""

    def test_the_dashboard_restores_its_windows_after_the_boot(self):
        main = read("web", "js", "main.js")
        assert "boot.ready.then(() => {" in main and "windowsToRestore({ open: lastOpen, restoreAll: true })" in main
        assert main.index("boot.ready.then") < main.index("windowsToRestore({")

    def test_a_floating_window_opens_after_the_boot_too(self):
        js = read("web", "js", "widget-main.js")
        assert "boot.ready.then(() => openWindow(def))" in js and "await opened;" in js

    def test_ready_means_online_done_no_cover_or_no_supervisor(self):
        js = read("web", "js", "boot.js")
        assert 'if (!enabled || phase.phase === "online" || phase.phase === "done") settle();' in js
        assert "SETTLE_FALLBACK_MS" in js and "return { ready };" in js
