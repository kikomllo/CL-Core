"""The full terminal in the Claude window: how the pieces are wired (read as text: importing clJarvis starts the supervisor, the page needs a browser)."""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


def test_xterm_is_vendored_so_the_terminal_works_offline():
    for name in ("xterm.js", "xterm.css", "addon-fit.js", "LICENSE"):
        assert os.path.exists(os.path.join(ROOT, "web", "vendor", "xterm", name))


def test_the_supervisor_starts_the_console_service_and_it_can_be_switched_off():
    assert re.search(r'NATIVE_SERVICES\.append\(\("Console",\s*"src/clConsole\.py"\)\)', read("clJarvis.py"))
    assert json.loads(read("config", "modules.json"))["native"]["Console"] is True
    assert '"clConsole.py"' in read("boot.py")                  # a stale one is swept before the ecosystem starts


def test_the_service_says_it_is_ready_so_the_boot_count_completes():
    assert '"module": "console"' in read("src", "clConsole.py")


def test_the_web_bridge_never_replays_a_stale_terminal_chunk():
    assert '"jarvis/console/out"' in re.search(r"NO_REPLAY = \{[^}]*\}", read("src", "clWebUI.py")).group(0)


def test_the_page_talks_to_the_service_by_the_same_topics():
    page = read("web", "js", "console.js")
    for topic in ("jarvis/console/input", "jarvis/console/resize", "jarvis/console/attach", "jarvis/console/out"):
        assert topic in page and topic in read("src", "clConsole.py")


def test_the_claude_window_switches_to_it_locally_and_never_sends_the_slash_commands_as_text():
    js = read("web", "js", "claude.js")
    assert 'import { loadXterm, createConsole, SHELL, CLAUDE } from "./console.js"' in js
    assert 'value.trim() === "/terminal"' in js and "showTerm(true)" in js
    assert "onclick: () => showTerm(!termView)" in js


def test_copy_and_paste_are_handled_in_the_terminal():
    page = read("web", "js", "console.js")
    assert "attachCustomKeyEventHandler" in page and "navigator.clipboard" in page and 'k === "v"' in page


def test_claude_gets_at_least_40_rows_because_it_draws_no_input_box_in_a_shorter_grid():
    assert "const MIN_ROWS = 40, MIN_COLS = 40;" in read("web", "js", "claude.js")
    page = read("web", "js", "console.js")
    assert "minRows: 40" in page and "MIN_FONT_PX" in page      # the terminal view shrinks its text to fit them in the window


def test_the_claude_session_is_drawn_by_a_terminal_when_the_bridge_streams_it():
    js = read("web", "js", "claude.js")
    assert "if (m.raw && !raw) { raw = true; layout(); }" in js and "createConsole(claudeHost, CLAUDE)" in js
    page = read("web", "js", "console.js")
    for topic in ("jarvis/claude/input", "jarvis/claude/attach", "jarvis/claude/out"):
        assert topic in page and topic in read("src", "clClaudeBridge.py")


def test_a_terminal_whose_service_is_not_running_says_so():
    page = read("web", "js", "console.js")
    assert "service.missing" in page and "python boot.py" in page
