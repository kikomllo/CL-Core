import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src', 'utils')))
from clModuleMatch import services_to_restart

SERVICES = [("UI", "src/clUI.py"), ("Keybinds", "src/clKeybinds.py"), ("Utilities", "src/clUtilities.py"), ("Updater", "src/clUpdater.py"),
            ("Tray Icon", "src/clTrayIcon.py"), ("Claude Bridge", "src/clClaudeBridge.py"), ("Monitor", "src/clMonitor.py"),
            ("Automation", "src/clAutomation.py"), ("Web UI", "src/clWebUI.py"), ("Desktop Shell", "src/clShell.py"),
            ("Light", "src/clControl.py")]
NATIVE = {"UI": False, "Web UI": True, "Desktop Shell": True}          # the Qt "UI" is off while the desktop shell is on


def names(target, cfg=NATIVE):
    return [d for d, _ in services_to_restart(target, SERVICES, cfg)]


def test_restarting_the_ui_restarts_the_server_and_the_window():
    """Live log 2026-11-24: "restart ui module" restarted only the Web UI server; the open page just reconnected, so nothing seemed to happen."""
    assert names("ui module") == ["Web UI", "Desktop Shell"]
    assert names("the dashboard") == ["Web UI", "Desktop Shell"]


def test_the_qt_dashboard_is_included_when_it_is_the_one_running():
    assert names("ui", {"UI": True, "Web UI": False, "Desktop Shell": False}) == ["UI"]


def test_a_named_ui_service_restarts_just_that_one():
    assert names("web ui") == ["Web UI"]
    assert names("desktop shell") == ["Desktop Shell"]
    assert names("shell module") == ["Desktop Shell"]


def test_other_targets_still_restart_their_first_match():
    assert names("light") == ["Light"]
    assert names("updates") == ["Updater"]
    assert names("utilities service") == ["Utilities"]


def test_an_unknown_target_matches_nothing():
    assert names("toaster") == []
