"""The web Claude window's pure logic (web/js/claude-model.js), run under node."""
import json
from webcss import web_css
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "claude-model.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(expr):
    script = f'import * as M from "file:///{MODEL}";\nconsole.log(JSON.stringify({expr}));'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestKeys:
    @pytest.mark.parametrize("key,want", [
        ("a", {"keys": "a"}), ("é", {"keys": "é"}), (" ", {"keys": " "}), ("/", {"keys": "/"}),
        ("Enter", {"control": "ENTER"}), ("Escape", {"control": "ESCAPE"}), ("ArrowUp", {"control": "UP"}),
        ("ArrowDown", {"control": "DOWN"}), ("ArrowLeft", {"control": "LEFT"}), ("Home", {"control": "HOME"}),
        ("End", {"control": "END"}), ("Delete", {"control": "DELETE"}), ("PageUp", {"control": "PAGEUP"}),
        ("PageDown", {"control": "PAGEDOWN"}), ("Tab", {"keys": "\t"}), ("Backspace", {"keys": "\x7f"}),
    ])
    def test_plain_keys(self, key, want):
        assert run(f'M.keyAction({{key: {json.dumps(key)}}})') == want

    def test_shift_tab_is_its_own_token(self):
        assert run('M.keyAction({key: "Tab", shiftKey: true})') == {"control": "SHIFT_TAB"}

    def test_ctrl_arrows_and_ctrl_backspace(self):
        assert run('M.keyAction({key: "ArrowLeft", ctrlKey: true})') == {"control": "CTRL_LEFT"}
        assert run('M.keyAction({key: "ArrowRight", ctrlKey: true})') == {"control": "CTRL_RIGHT"}
        assert run('M.keyAction({key: "Backspace", ctrlKey: true})') == {"keys": "\x17"}

    def test_ctrl_letter_is_its_control_byte(self):
        assert run('M.keyAction({key: "a", ctrlKey: true})') == {"keys": "\x01"}
        assert run('M.keyAction({key: "E", ctrlKey: true, shiftKey: true})') == {"keys": "\x05"}

    def test_ctrl_c_copies_a_selection_and_otherwise_interrupts(self):
        assert run('M.keyAction({key: "c", ctrlKey: true}, true)') == {"copy": True}
        assert run('M.keyAction({key: "c", ctrlKey: true}, false)') == {"keys": "\x03"}

    def test_ctrl_z_is_never_sent(self):
        assert run('M.keyAction({key: "z", ctrlKey: true})') is None

    def test_ctrl_v_is_a_paste(self):
        assert run('M.keyAction({key: "v", ctrlKey: true})') == {"paste": True}

    @pytest.mark.parametrize("ev", ['{key: "Shift"}', '{key: "Control", ctrlKey: true}', '{key: "F5"}', '{key: "a", metaKey: true}', '{key: "Dead"}', '{key: "a", altKey: true}'])
    def test_keys_that_mean_nothing_are_left_alone(self, ev):
        assert run(f"M.keyAction({ev})") is None


class TestShadow:
    def test_a_typed_mode_switch_is_erased_not_run(self):
        assert run('M.submitAction("/terminal")') == {"erase": 9, "text": "/terminal"}
        assert run('M.submitAction("  /Claude ")') == {"erase": 10, "text": "/claude"}      # every typed character goes, spaces included

    def test_anything_else_is_an_ordinary_enter(self):
        assert run('M.submitAction("ls /terminal")') is None
        assert run('M.submitAction("")') is None

    def test_shadow_follows_typing_backspace_and_control_keys(self):
        assert run('M.nextShadow("ab", {keys: "c"})') == "abc"
        assert run('M.nextShadow("abc", {keys: "\\x7f"})') == "ab"
        assert run('M.nextShadow("abc", {control: "UP"})') == ""
        assert run('M.nextShadow("abc", {keys: "\\t"})') == ""
        assert run('M.nextShadow("abc", {keys: "\\x05"})') == ""
        assert run('M.nextShadow("a", {keys: "paste me"})') == "apaste me"


class TestScreen:
    def test_plain_text_rows(self):
        rows = run('M.screenRows({text: "one\\n\\nthree"})')
        assert [[s["text"] for s in r] for r in rows] == [["one"], [], ["three"]]

    def test_ansi_splits_into_styled_spans(self):
        rows = run('M.screenRows({text: "x", ansi: "\\u001b[1mbold\\u001b[0m plain"})')
        assert rows[0][0]["text"] == "bold" and rows[0][0]["bold"] is True
        assert rows[0][1]["text"] == " plain" and rows[0][1]["bold"] is False

    def test_style_carries_to_the_next_row_like_a_terminal(self):
        rows = run('M.ansiRows("\\u001b[3mone\\ntwo")')
        assert rows[1][0]["italic"] is True

    def test_a_dim_grey_suggestion_is_darker_than_typed_text(self):
        rows = run('M.ansiRows("\\u001b[38;5;250mtyped\\u001b[0m\\u001b[2m suggestion")')
        assert rows[0][1]["fgV"] < rows[0][0]["fgV"]

    def test_truecolour_and_256_colour(self):
        rows = run('M.ansiRows("\\u001b[38;2;255;0;0mred\\u001b[38;5;21mblue")')
        assert rows[0][0]["fgV"] == 1 and 0.5 < rows[0][1]["fgV"] <= 1

    def test_reverse_video(self):
        assert run('M.ansiRows("\\u001b[7mx")')[0][0]["reverse"] is True

    def test_garbage_is_harmless(self):
        assert run("M.screenRows(null)") == []
        assert run('M.ansiRows("\\u001b[999;38mx")')[0][0]["text"] == "x"

    def test_cursor_cell(self):
        assert run("M.cursorCell({cursor: [4, 2]})") == {"col": 4, "row": 2}
        assert run("M.cursorCell({cursor: [-1, 2]})") is None
        assert run('M.cursorCell({cursor: "x"})') is None
        assert run("M.cursorCell({})") is None

    def test_suggestion_is_hidden_only_at_an_idle_shell_prompt(self):
        base = '{mode: "terminal", idle: true, cursor: [2, 0]}'
        assert run(f"M.hidesSuggestion({base})") is True
        assert run('M.hidesSuggestion({mode: "terminal", idle: false, cursor: [2, 0]})') is False
        assert run('M.hidesSuggestion({mode: "claude", cursor: [2, 0]})') is False

    def test_cut_after_the_cursor_column(self):
        spans = run('M.cutAfter([{text: "$ ls"}, {text: " grey suggestion"}], 6)')
        assert "".join(s["text"] for s in spans) == "$ ls g"
        assert run('M.cutAfter([{text: "abc"}], 0)') == []


class TestGrid:
    def test_grid_from_pixels(self):
        assert run("M.gridFor(800, 400, 8, 16)") == {"cols": 100, "rows": 25}

    def test_never_below_the_bridges_minimum(self):
        assert run("M.gridFor(40, 20, 8, 16)") == {"cols": 20, "rows": 5}

    def test_a_row_floor_keeps_a_short_window_tall_enough_for_the_input_box(self):
        assert run("M.gridFor(800, 200, 8, 16, 40)") == {"cols": 100, "rows": 40}
        assert run("M.gridFor(800, 800, 8, 16, 40)") == {"cols": 100, "rows": 50}

    def test_a_column_floor_keeps_a_narrow_window_at_the_bridges_start_size(self):
        assert run("M.gridFor(400, 800, 8, 16, 40, 120)") == {"cols": 120, "rows": 50}
        assert run("M.gridFor(1200, 400, 8, 16, 40, 120)") == {"cols": 150, "rows": 40}


class TestQtParity:
    """The brightness a colour reduces to must match the Qt widget's, so both UIs draw the same terminal."""

    @pytest.mark.parametrize("n", [0, 1, 4, 7, 8, 12, 15, 21, 100, 196, 244, 250])
    def test_brightness_matches_the_qt_widget(self, n):
        pytest.importorskip("PyQt6")
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from ui import clClaudeWidget as q
        except Exception as e:                                           # a headless box without the full Qt stack
            pytest.skip(str(e))
        want = q._brightness(q._xterm_color(n))
        got = run(f"M.brightness(M.xtermColor({n}))")
        assert got == pytest.approx(want, abs=0.01)


class TestActivityAndCells:
    def test_claude_is_busy_while_it_offers_to_be_interrupted(self):
        assert run('M.isBusy({mode: "claude", text: "✻ Brewing… (esc to interrupt)"})') is True
        assert run('M.isBusy({mode: "claude", text: "❯ "})') is False

    def test_a_shell_is_busy_when_a_program_is_in_front(self):
        assert run('M.isBusy({mode: "terminal", idle: false})') is True
        assert run('M.isBusy({mode: "terminal", idle: true})') is False
        assert run('M.isBusy({mode: "terminal"})') is False
        assert run("M.isBusy(null)") is False

    def test_symbols_get_a_cell_each_and_text_stays_together(self):
        assert run('M.cellRuns("● Two plus two")') == [{"text": "●", "sym": True}, {"text": " Two plus two", "sym": False}]
        assert run('M.cellRuns("❯ hi ⎿ x")') == [
            {"text": "❯", "sym": True}, {"text": " hi ", "sym": False}, {"text": "⎿", "sym": True}, {"text": " x", "sym": False}]

    def test_accented_letters_and_plain_text_are_not_symbols(self):
        assert run('M.cellRuns("não é")') == [{"text": "não é", "sym": False}]
        assert run('M.cellRuns("")') == []


class TestNeverScrollsSideways:
    """The terminal's columns follow the window and a horizontal scrollbar is impossible (web/js/claude.js, web/style.css)."""

    def _read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_the_screen_hides_horizontal_overflow(self):
        css = web_css()
        rule = re.search(r"\.cl-screen \{[^}]*\}", css).group(0)
        assert "overflow-x: hidden" in rule and "overflow-y: auto" in rule

    def test_the_window_sends_the_grid_that_fits_it(self):
        js = self._read("web", "js", "claude.js")
        assert 'bridge.act("claude.resize", g)' in js and "gridFor(w, hh, cell.w, cell.h, MIN_ROWS, MIN_COLS)" in js

    def test_the_column_floor_is_below_the_windows_smallest_width(self):
        # the smallest Claude window is fit to its terminal floor (ui.js ELASTIC: 350 px minus the screen's padding, ~42 columns at 7.5 px a cell): a column floor above that would overflow it
        js = self._read("web", "js", "claude.js")
        assert int(re.search(r"MIN_COLS = (\d+)", js).group(1)) <= 44
