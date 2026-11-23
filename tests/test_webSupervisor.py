"""The web dashboard server is a supervised service: started by clJarvis, switchable in modules.json, swept by boot.py.
Read as text because importing clJarvis redirects stdout and starts the supervisor."""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


def test_the_supervisor_starts_the_web_ui():
    assert re.search(r'NATIVE_SERVICES\.append\(\("Web UI",\s*"src/clWebUI\.py"\)\)', _read("clJarvis.py"))


def test_modules_json_can_switch_it_off():
    assert json.loads(_read("config", "modules.json"))["native"]["Web UI"] is True


def test_boot_sweeps_a_stale_server_so_the_port_is_free():
    assert '"clWebUI.py"' in _read("boot.py")


def test_the_desktop_shell_is_supervised_and_replaces_the_qt_dashboard_by_default():
    assert re.search(r'NATIVE_SERVICES\.append\(\("Desktop Shell",\s*"src/clShell\.py"\)\)', _read("clJarvis.py"))
    native = json.loads(_read("config", "modules.json"))["native"]
    assert native["Desktop Shell"] is True and native["UI"] is False


def test_boot_installs_the_shell_and_sweeps_a_stale_one():
    boot = _read("boot.py")
    assert "_ensure_desktop_shell(is_windows)" in boot
    assert '"clShell.py"' in boot


def test_it_reports_ready_so_the_supervisors_count_completes():
    # EXPECTED_MODULES counts every enabled service; one that never says it is ready would hold back "ALL SYSTEMS GO"
    assert "jarvis/sys/module_ready" in _read("src", "clWebUI.py")
