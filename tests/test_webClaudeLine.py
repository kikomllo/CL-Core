"""The web Claude window's local line editing (web/js/claude-line.js), run under node with a fake clock."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINE = os.path.join(ROOT, "web", "js", "claude-line.js").replace("\\", "/")
MODEL = os.path.join(ROOT, "web", "js", "claude-model.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

PRELUDE = f"""
import {{ LocalLine }} from "file:///{LINE}";
import * as M from "file:///{MODEL}";
let t = 0; const L = new LocalLine(() => t);
const rule = "─".repeat(40);
// Claude's framed input box: a ❯ row between two rules (the bridge trims trailing spaces, so an empty prompt is a bare "❯")
const claude = (line = "", cursor) => ({{ mode: "claude", text: ["● hi", "", rule, "❯" + (line ? " " + line : ""), rule, "  ? for shortcuts"].join("\\n"), cursor }});
const shell = (line = "", idle = true) => ({{ mode: "terminal", idle, cursor: [4 + line.length, 0], text: "C:> " + line }});
const press = (key, mods = {{}}) => L.key({{ key, ...mods }});
const type = (s) => [...s].forEach((c) => press(c));
const sent = [];
const go = (key, mods) => {{ const r = press(key, mods); if (r) sent.push(...r.effects); return r; }};
"""


def run(body):
    script = PRELUDE + "\n" + body
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestWhenItIsActive:
    def test_claude_box_is_edited_locally(self):
        r = run('L.setScreen(claude()); console.log(JSON.stringify([L.active, L.kind, L.view.start, L.view.row]))')
        assert r == [True, "claude", 2, 3]

    def test_a_menu_or_busy_screen_is_a_pure_mirror(self):
        assert run('L.setScreen({mode: "claude", text: "Do you trust this folder?\\n❯ 1. Yes\\n  2. No"}); console.log(JSON.stringify([L.active, press("a")]))') == [False, None]

    def test_cmd_exe_and_a_running_program_are_pure_mirrors(self):
        assert run('L.setScreen({mode: "terminal-basic", text: "C:> ", cursor: [4, 0]}); console.log(JSON.stringify(L.active))') is False
        assert run('L.setScreen(shell("", false)); console.log(JSON.stringify(L.active))') is False

    def test_an_idle_readline_prompt_is_edited_locally_from_the_cursor_column(self):
        # the start is the cursor column of a fresh prompt (the prompt's own length); it is re-measured on every snapshot until the
        # user touches the line, then fixed, so what is typed after that is the line and never part of the prompt
        assert run('L.setScreen(shell()); const a = L.view.start; type("ls"); L.setScreen(shell("ls")); console.log(JSON.stringify([L.kind, a, L.view.start, L.buf]))') == ["shell", 4, 4, "ls"]


class TestTypingNeverLeavesUntilEnter:
    def test_typing_and_editing_send_nothing(self):
        r = run('L.setScreen(claude()); "hello".split("").forEach((c) => go(c)); go("Backspace"); go("ArrowLeft"); go("x"); console.log(JSON.stringify([L.buf, L.pos, sent]))')
        assert r == ["helxl", 4, []]          # hello, Backspace -> hell, Left -> before the l, type x

    def test_enter_overwrites_the_clis_line_and_presses_enter_in_one_go(self):
        sent = run('L.setScreen(claude()); type("hi"); go("Enter"); console.log(JSON.stringify(sent))')
        assert sent == [{"keys": "\x05\x15hi"}, {"control": "ENTER"}]

    def test_enter_on_an_unchanged_line_only_presses_enter(self):
        assert run('L.setScreen(claude("same")); go("Enter"); console.log(JSON.stringify(sent))') == [{"control": "ENTER"}]

    def test_the_line_is_empty_after_enter(self):
        assert run('L.setScreen(claude()); type("hi"); go("Enter"); console.log(JSON.stringify([L.buf, L.pos]))') == ["", 0]

    def test_a_typed_mode_switch_is_sent_as_text_and_never_typed_into_the_cli(self):
        assert run('L.setScreen(claude()); type("/terminal"); go("Enter"); console.log(JSON.stringify(sent))') == [{"text": "/terminal"}]

    def test_the_ide_chip_is_not_part_of_the_line(self):
        r = run('const s = claude(); s.text = s.text.replace("❯", "❯\\u00a0[⧉ In clWebUI.py]"); L.setScreen(s); type("hey"); console.log(JSON.stringify([L.buf, L.view.start]))')
        assert r == ["hey", 2 + len("[⧉ In clWebUI.py]")]

    def test_a_paste_is_one_line(self):
        assert run('L.setScreen(claude()); L.insert("a\\nb\\r\\nc"); console.log(JSON.stringify(L.buf))') == "a b c"


class TestEditingKeys:
    def test_home_end_and_word_moves(self):
        r = run('L.setScreen(claude()); type("one two three"); go("Home"); const a = L.pos; go("ArrowRight", {ctrlKey: true}); const b = L.pos; go("End"); go("ArrowLeft", {ctrlKey: true}); console.log(JSON.stringify([a, b, L.pos]))')
        assert r == [0, 4, 8]

    def test_ctrl_w_u_k(self):
        assert run('L.setScreen(claude()); type("one two"); go("w", {ctrlKey: true}); console.log(JSON.stringify(L.buf))') == "one "
        assert run('L.setScreen(claude()); type("one two"); go("ArrowLeft"); go("u", {ctrlKey: true}); console.log(JSON.stringify(L.buf))') == "o"
        assert run('L.setScreen(claude()); type("one two"); go("Home"); go("ArrowRight"); go("k", {ctrlKey: true}); console.log(JSON.stringify(L.buf))') == "o"

    def test_ctrl_backspace_and_ctrl_delete_remove_a_word(self):
        assert run('L.setScreen(claude()); type("one two"); go("Backspace", {ctrlKey: true}); console.log(JSON.stringify(L.buf))') == "one "
        assert run('L.setScreen(claude()); type("one two"); go("Home"); go("Delete", {ctrlKey: true}); console.log(JSON.stringify(L.buf))') == "two"

    def test_shift_arrows_select_and_typing_replaces(self):
        r = run('L.setScreen(claude()); type("hello"); go("ArrowLeft", {shiftKey: true}); go("ArrowLeft", {shiftKey: true}); const s = L.selection; type("Y"); console.log(JSON.stringify([s, L.buf]))')
        assert r == [[3, 5], "helY"]

    def test_ctrl_a_selects_all_in_claude_but_goes_home_in_a_shell(self):
        assert run('L.setScreen(claude()); type("abc"); go("a", {ctrlKey: true}); console.log(JSON.stringify(L.selection))') == [0, 3]
        assert run('L.setScreen(shell()); type("abc"); go("a", {ctrlKey: true}); console.log(JSON.stringify([L.pos, L.selection]))') == [0, None]

    def test_ctrl_c_copies_a_selection_and_otherwise_interrupts_and_clears(self):
        assert run('L.setScreen(claude()); type("abc"); go("a", {ctrlKey: true}); go("c", {ctrlKey: true}); console.log(JSON.stringify(sent))') == [{"copy": "abc"}]
        r = run('L.setScreen(claude()); type("abc"); go("c", {ctrlKey: true}); console.log(JSON.stringify([sent, L.buf]))')
        assert r == [[{"keys": "\x03"}], ""]

    def test_ctrl_x_cuts_the_selection(self):
        r = run('L.setScreen(claude()); type("abc"); go("a", {ctrlKey: true}); go("x", {ctrlKey: true}); console.log(JSON.stringify([sent, L.buf]))')
        assert r == [[{"copy": "abc"}], ""]

    def test_ctrl_v_asks_the_page_to_paste(self):
        assert run('L.setScreen(claude()); console.log(JSON.stringify(press("v", {ctrlKey: true})))') == {"effects": [], "paste": True}

    def test_ctrl_z_and_meta_keys_are_left_alone(self):
        assert run('L.setScreen(claude()); console.log(JSON.stringify([press("z", {ctrlKey: true}), press("a", {metaKey: true})]))') == [None, None]


class TestKeysThatNeedTheClisOwnLine:
    def test_tab_syncs_the_line_first(self):
        assert run('L.setScreen(claude()); type("/he"); go("Tab"); console.log(JSON.stringify(sent))') == [{"keys": "\x05\x15/he"}, {"keys": "\t"}]

    def test_up_and_down_sync_then_send_the_arrow(self):
        assert run('L.setScreen(claude()); go("ArrowUp"); go("ArrowDown"); console.log(JSON.stringify(sent))') == [{"control": "UP"}, {"control": "DOWN"}]

    def test_other_ctrl_letters_sync_then_go_as_their_control_byte(self):
        assert run('L.setScreen(claude()); type("x"); go("r", {ctrlKey: true}); console.log(JSON.stringify(sent))') == [{"keys": "\x05\x15x"}, {"keys": "\x12"}]

    def test_escape_and_shift_tab_go_straight_through(self):
        assert run('L.setScreen(claude()); type("x"); go("Escape"); go("Tab", {shiftKey: true}); console.log(JSON.stringify(sent))') == [{"control": "ESCAPE"}, {"control": "SHIFT_TAB"}]

    def test_the_line_the_cli_ends_up_with_is_adopted_after_history_recall(self):
        r = run('L.setScreen(claude("old")); go("ArrowUp"); t += 0.3; L.setScreen(claude("recalled")); console.log(JSON.stringify(L.buf))')
        assert r == "recalled"


class TestSnapshotsAndHold:
    def test_an_unsent_edit_is_never_overwritten_by_a_snapshot(self):
        assert run('L.setScreen(claude()); type("typing"); L.setScreen(claude("something else")); console.log(JSON.stringify(L.buf))') == "typing"

    def test_a_snapshot_with_a_new_line_is_adopted_when_nothing_is_unsent(self):
        assert run('L.setScreen(claude("a")); L.setScreen(claude("b")); console.log(JSON.stringify(L.buf))') == "b"

    def test_after_enter_the_stale_line_is_not_readopted_until_the_hold_is_over(self):
        r = run('L.setScreen(claude("")); type("hi"); go("Enter"); L.setScreen(claude("hi")); const during = L.buf; t += 1; L.refresh(); console.log(JSON.stringify([during, L.buf, L.holdLeft]))')
        assert r[0] == "" and r[1] == "hi"          # while the hold runs the line stays empty; after it the CLI's own line is shown again

    def test_hold_left_counts_down(self):
        r = run('L.setScreen(claude()); type("a"); go("Enter"); const a = L.holdLeft; t += 0.5; const b = L.holdLeft; t += 1; console.log(JSON.stringify([a > 0.7, b > 0.2 && b < a, L.holdLeft]))')
        assert r == [True, True, 0]


class TestLineSpans:
    def test_the_prompt_keeps_its_spans_and_the_local_text_follows(self):
        r = run('const spans = M.screenRows({text: "❯"})[0]; console.log(JSON.stringify(M.lineSpans(spans, 2, "hi").map((s) => s.text)))')
        assert r == ["❯", " ", "hi"]

    def test_the_selection_is_its_own_span(self):
        r = run('console.log(JSON.stringify(M.lineSpans([], 0, "hello", [1, 3]).map((s) => [s.text, !!s.sel])))')
        assert r == [["h", False], ["el", True], ["lo", False]]
