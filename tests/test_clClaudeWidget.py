"""Tests for the Claude dashboard widget's terminal-style input -- no separate
line edit, keys typed directly into the screen mirror are forwarded to the
live session, with plain typing/backspace echoed locally so it feels instant
despite the MQTT round trip. Covers both the claude and terminal backends
identically -- the widget doesn't know or care which is active; routing by
mode happens server-side in clClaudeBridge.py."""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QKeyEvent


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def make_key_event(key, modifiers=Qt.KeyboardModifier.NoModifier, text=""):
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text)


FRAMED_EMPTY = "\u2500" * 40 + "\n\u276f\n" + "\u2500" * 40


def _local_view(qapp, screen=FRAMED_EMPTY, cursor=None):
    from ui.clClaudeWidget import ClaudeTerminalView
    on_keys, on_control_key = MagicMock(), MagicMock()
    view = ClaudeTerminalView(on_keys, on_control_key, MagicMock(), local_line_editing=True)
    view.set_server_screen(screen, cursor)
    return view, on_keys, on_control_key


def _view(qapp):
    from ui.clClaudeWidget import ClaudeTerminalView
    on_keys = MagicMock()
    on_control_key = MagicMock()
    on_resize = MagicMock()
    view = ClaudeTerminalView(on_keys, on_control_key, on_resize)
    return view, on_keys, on_control_key


def _with_open_popup(view, mocker):
    fake_popup = MagicMock()
    fake_popup.isVisible.return_value = True
    mocker.patch.object(view, "completer", MagicMock(popup=lambda: fake_popup))
    return view


class TestGridResize:
    """Widget resize (or a font-size zoom) should reflow the live terminal's
    actual column/row count -- a real resize (ConPTY setwinsize / tmux
    resize-window), dispatched via claude.resize, not just a display clamp."""

    def test_grid_size_computed_from_viewport_and_font_metrics(self, qapp, mocker):
        view, _, _ = _view(qapp)
        metrics = MagicMock()
        metrics.horizontalAdvance.return_value = 8
        metrics.height.return_value = 16
        mocker.patch.object(view, "fontMetrics", return_value=metrics)
        viewport = MagicMock()
        viewport.width.return_value = 800
        viewport.height.return_value = 400
        mocker.patch.object(view, "viewport", return_value=viewport)

        assert view._grid_size() == (100, 25)

    def test_grid_size_never_goes_below_the_minimum(self, qapp, mocker):
        view, _, _ = _view(qapp)
        metrics = MagicMock()
        metrics.horizontalAdvance.return_value = 8
        metrics.height.return_value = 16
        mocker.patch.object(view, "fontMetrics", return_value=metrics)
        viewport = MagicMock()
        viewport.width.return_value = 10
        viewport.height.return_value = 10
        mocker.patch.object(view, "viewport", return_value=viewport)

        cols, rows = view._grid_size()
        assert cols == view.MIN_COLS
        assert rows == view.MIN_ROWS

    def test_dispatch_resize_calls_on_resize_with_grid_size(self, qapp, mocker):
        view, _, _ = _view(qapp)
        mocker.patch.object(view, "_grid_size", return_value=(100, 30))

        view._dispatch_resize()

        view._on_resize.assert_called_once_with(100, 30)

    def test_dispatch_resize_skips_an_unchanged_size(self, qapp, mocker):
        view, _, _ = _view(qapp)
        mocker.patch.object(view, "_grid_size", return_value=(100, 30))
        view._dispatch_resize()
        view._on_resize.reset_mock()

        view._dispatch_resize()

        view._on_resize.assert_not_called()

    def test_dispatch_resize_fires_again_after_a_real_size_change(self, qapp, mocker):
        view, _, _ = _view(qapp)
        mocker.patch.object(view, "_grid_size", return_value=(100, 30))
        view._dispatch_resize()
        view._on_resize.reset_mock()
        mocker.patch.object(view, "_grid_size", return_value=(80, 24))

        view._dispatch_resize()

        view._on_resize.assert_called_once_with(80, 24)

    def test_resize_event_starts_the_debounce_timer_not_an_immediate_dispatch(self, qapp):
        from PyQt6.QtGui import QResizeEvent
        from PyQt6.QtCore import QSize
        view, _, _ = _view(qapp)

        view.resizeEvent(QResizeEvent(QSize(300, 200), QSize(200, 150)))

        assert view._resize_timer.isActive()
        view._on_resize.assert_not_called()

    def test_zoom_in_also_starts_the_debounce_timer(self, qapp):
        view, _, _ = _view(qapp)
        view.zoomIn()
        assert view._resize_timer.isActive()

    def test_zoom_out_also_starts_the_debounce_timer(self, qapp):
        view, _, _ = _view(qapp)
        view.zoomOut()
        assert view._resize_timer.isActive()


class TestScrollPositionSurvivesCursorFollow:
    """setTextCursor() can itself auto-scroll to keep the cursor (on the
    prompt row) visible -- found live, this permanently hid anything below
    the prompt row (a footer line) and fought a manual scroll-up, because
    nothing overrode it afterward. _render() must always end with an
    explicit, intentional scroll target regardless of whatever setTextCursor
    did internally."""

    def test_stays_at_max_when_previously_at_bottom_even_if_cursor_follow_moved_it(self, qapp, mocker):
        view, _, _ = _view(qapp)
        scrollbar = MagicMock()
        scrollbar.value.return_value = 100
        scrollbar.maximum.return_value = 100  # at bottom before the render
        mocker.patch.object(view, "verticalScrollBar", return_value=scrollbar)

        view.set_server_screen("line1\nline2")

        scrollbar.setValue.assert_called_with(100)

    def test_restores_the_preserved_scroll_position_when_not_at_bottom(self, qapp, mocker):
        view, _, _ = _view(qapp)
        scrollbar = MagicMock()
        scrollbar.value.return_value = 40
        scrollbar.maximum.return_value = 100  # scrolled up, not at bottom
        mocker.patch.object(view, "verticalScrollBar", return_value=scrollbar)

        view.set_server_screen("line1\nline2")

        scrollbar.setValue.assert_called_with(40)


class TestPromptRowPlacement:
    """The Claude TUI's input box (a "❯" row bracketed by horizontal rules)
    isn't always the last row on screen -- a footer/status line (mode hint,
    update notice) can follow it. Found live: typed characters were landing
    after the footer instead of on the actual input line. Local echo must
    insert into the bracketed ❯ row specifically, not just the screen's last
    line."""

    SCREEN_WITH_FOOTER = "\n".join([
        "\u25cf I opened YouTube in your browser.",
        "",
        "\u2500" * 40,
        "\u276f ",
        "\u2500" * 40,
        "  \u23ed\u23ed accept edits on (shift+tab to cycle)",
    ])

    def test_typed_text_lands_on_the_prompt_row_not_after_the_footer(self, qapp):
        view, _, _ = _local_view(qapp, self.SCREEN_WITH_FOOTER)

        view._insert("h")
        view._insert("i")

        lines = view.toPlainText().split("\n")
        assert lines[3] == "\u276f hi"
        assert lines[5] == "  \u23ed\u23ed accept edits on (shift+tab to cycle)"

    def test_cursor_ends_up_on_the_prompt_row(self, qapp):
        view, _, _ = _local_view(qapp, self.SCREEN_WITH_FOOTER)

        view._insert("h")

        assert view.textCursor().blockNumber() == 3

    def test_bare_shell_prompt_shows_exactly_what_the_shell_reports(self, qapp):
        """/terminal mode is a pure mirror: typed characters go to the shell and appear only once
        the shell echoes them back in a snapshot."""
        view, on_keys, _ = _view(qapp)
        view.set_server_screen("C:\\repo>")

        for ch in "dir":
            view._insert(ch)

        assert view.toPlainText() == "C:\\repo>"
        assert [c.args[0] for c in on_keys.call_args_list] == ["d", "i", "r"]


class TestPureMirror:
    """Outside Claude's own input box (a shell, menus) the widget predicts nothing: every key is
    forwarded as a terminal would send it and only backend snapshots change the screen -- so
    things like a shell's autosuggestion or its own line editing are never second-guessed."""

    SHELL = "user@host:~$ cl"

    def _shell(self, qapp, cursor=None):
        view, on_keys, on_ctl = _view(qapp)
        view.set_server_screen("out\n" + self.SHELL, cursor)
        return view, on_keys, on_ctl

    def test_typing_is_forwarded_and_not_echoed_locally(self, qapp):
        view, on_keys, _ = self._shell(qapp)
        view._insert("x")
        assert view.toPlainText() == "out\n" + self.SHELL
        on_keys.assert_called_once_with("x")

    def test_backspace_forwards_del_and_changes_nothing_locally(self, qapp):
        view, on_keys, _ = self._shell(qapp)
        view._backspace()
        assert view.toPlainText() == "out\n" + self.SHELL
        on_keys.assert_called_once_with("\x7f")

    def test_paste_is_forwarded_as_one_chunk(self, qapp):
        from PyQt6.QtWidgets import QApplication
        view, on_keys, _ = self._shell(qapp)
        QApplication.clipboard().setText("pasted text")
        view._paste()
        on_keys.assert_called_once_with("pasted text")

    def test_the_screen_and_cursor_are_exactly_the_servers(self, qapp):
        view, _, _ = self._shell(qapp, cursor=[14, 1])
        assert view._cursor_cell() == (1, 14)
        view.set_server_screen("other\nlines", [3, 0])
        assert view.toPlainText() == "other\nlines"
        assert view._cursor_cell() == (0, 3)

    def test_ctrl_letters_go_to_the_shell_as_control_bytes(self, qapp):
        view, on_keys, _ = self._shell(qapp)
        for key, byte in ((Qt.Key.Key_A, "\x01"), (Qt.Key.Key_E, "\x05"), (Qt.Key.Key_C, "\x03"),
                          (Qt.Key.Key_R, "\x12"), (Qt.Key.Key_U, "\x15")):
            view.keyPressEvent(make_key_event(key, Qt.KeyboardModifier.ControlModifier))
            assert on_keys.call_args.args[0] == byte

    def test_ctrl_backspace_deletes_a_word_in_the_shell(self, qapp):
        view, on_keys, _ = self._shell(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Backspace, Qt.KeyboardModifier.ControlModifier))
        on_keys.assert_called_once_with("\x17")

    def test_ctrl_arrows_and_page_keys_are_forwarded(self, qapp):
        view, _, on_ctl = self._shell(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier))
        view.keyPressEvent(make_key_event(Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier))
        view.keyPressEvent(make_key_event(Qt.Key.Key_PageUp))
        view.keyPressEvent(make_key_event(Qt.Key.Key_PageDown))
        assert [c.args[0] for c in on_ctl.call_args_list] == ["CTRL_LEFT", "CTRL_RIGHT", "PAGEUP", "PAGEDOWN"]

    def test_the_claude_slash_popup_does_not_appear_in_a_shell(self, qapp, mocker):
        view, _, _ = self._shell(qapp)
        complete = mocker.patch.object(view.completer, "complete")
        view._insert("/")
        complete.assert_not_called()


def _orange_family(color) -> bool:
    """Orange only: red > green > blue, no blue to speak of."""
    return color.red() > color.green() > color.blue() and color.blue() <= 10


class TestColourRendering:
    GREY_SUGGESTION = "$ \x1b[4mcl\x1b[0m\x1b[38;5;244mear\x1b[0m"

    def test_ansi_spans_carry_colour_and_attributes(self, qapp):
        from ui.clClaudeWidget import ansi_spans
        spans = ansi_spans(self.GREY_SUGGESTION)[0]
        assert [t for t, _ in spans] == ["$ ", "cl", "ear"]
        assert spans[1][1].fontUnderline() is True
        suggestion = spans[2][1].foreground().color()
        assert _orange_family(suggestion)
        assert suggestion.green() < 170  # dimmer than the theme's own orange, so it reads as a suggestion

    def test_truecolor_bold_and_reset(self, qapp):
        from ui.clClaudeWidget import ansi_spans
        spans = ansi_spans("\x1b[1;38;2;10;20;30mA\x1b[0mB")[0]
        assert _orange_family(spans[0][1].foreground().color())  # not the blue-ish colour that was sent
        assert spans[0][1].fontWeight() == 700
        assert spans[1][1].fontWeight() != 700

    def test_the_styled_screen_is_rendered_with_the_same_text_as_the_plain_one(self, qapp):
        view, _, _ = _view(qapp)
        plain = "out\n$ clear"
        view.set_server_screen(plain, [4, 1], ansi="out\n" + self.GREY_SUGGESTION)
        assert view.toPlainText() == plain
        cursor = view.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        cursor.movePosition(cursor.MoveOperation.Down)
        cursor.movePosition(cursor.MoveOperation.EndOfLine)
        assert _orange_family(cursor.charFormat().foreground().color())  # the "ear" part
        assert cursor.charFormat().foreground().color().green() < 170

    def test_every_ansi_colour_is_drawn_in_orange(self, qapp):
        from ui.clClaudeWidget import ansi_spans
        for code in ("31", "32", "33", "34", "35", "36", "37", "91", "92", "94", "38;5;196", "38;5;21",
                     "38;5;244", "38;2;0;200;0", "30"):
            fg = ansi_spans(f"\x1b[{code}mx")[0][0][1].foreground().color()
            assert _orange_family(fg), code

    def test_brighter_colours_are_brighter_oranges(self, qapp):
        from ui.clClaudeWidget import orange_shade
        from PyQt6.QtGui import QColor
        black, grey, white = (orange_shade(QColor(*c)) for c in ((0, 0, 0), (128, 128, 128), (255, 255, 255)))
        assert black.green() < grey.green() < white.green()
        assert white.name() == "#ffaa00"  # the theme's primary orange

    def test_backgrounds_become_orange_tints_and_reverse_is_an_orange_block(self, qapp):
        from ui.clClaudeWidget import ansi_spans
        bg = ansi_spans("\x1b[44mx")[0][0][1].background().color()
        assert bg.red() == 255 and bg.blue() == 0 and 0 < bg.alpha() < 255
        rev = ansi_spans("\x1b[7;32mx")[0][0][1]
        assert rev.background().color().name() == "#ffaa00"
        assert rev.foreground().color().red() < 40  # dark text on the orange block

    def test_attributes_other_than_colour_are_kept(self, qapp):
        from ui.clClaudeWidget import ansi_spans
        fmt = ansi_spans("\x1b[1;3;4mx")[0][0][1]
        assert fmt.fontWeight() == 700 and fmt.fontItalic() and fmt.fontUnderline()

    def test_a_snapshot_without_ansi_reverts_to_plain_text(self, qapp):
        view, _, _ = _view(qapp)
        view.set_server_screen("a\nb", ansi="a\n\x1b[31mb")
        view.set_server_screen("c\nd")
        assert view.toPlainText() == "c\nd"


class TestKeyboardControlKeysWithoutPopup:
    def test_tab_changes_focus_is_disabled(self, qapp):
        """Otherwise Qt's own QWidget::event() intercepts Tab/Shift+Tab for
        focus-traversal before keyPressEvent ever runs, so our SHIFT_TAB
        handling below would never fire in the real app -- found live."""
        view, _, _ = _view(qapp)
        assert view.tabChangesFocus() is False

    def test_up_arrow_sends_up_token_not_local_cursor_movement(self, qapp):
        view, _, on_control_key = _view(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Up))
        on_control_key.assert_called_once_with("UP")

    def test_down_arrow_sends_down_token(self, qapp):
        view, _, on_control_key = _view(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Down))
        on_control_key.assert_called_once_with("DOWN")

    def test_escape_sends_escape_token(self, qapp):
        view, _, on_control_key = _view(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Escape))
        on_control_key.assert_called_once_with("ESCAPE")

    def test_backtab_sends_shift_tab_token(self, qapp):
        view, _, on_control_key = _view(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Backtab))
        on_control_key.assert_called_once_with("SHIFT_TAB")

    def test_shift_plus_tab_sends_shift_tab_token(self, qapp):
        view, _, on_control_key = _view(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier))
        on_control_key.assert_called_once_with("SHIFT_TAB")

    def test_plain_tab_is_forwarded_as_a_raw_key(self, qapp):
        view, on_keys, on_control_key = _view(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Tab))
        on_keys.assert_called_once_with("\t")
        on_control_key.assert_not_called()

    def test_enter_is_forwarded_as_the_enter_key(self, qapp):
        view, _, on_control_key = _view(qapp)
        view._insert("hello")
        on_control_key.reset_mock()

        view.keyPressEvent(make_key_event(Qt.Key.Key_Return))

        on_control_key.assert_called_once_with("ENTER")

    def test_plain_typing_is_forwarded_without_a_local_echo(self, qapp):
        view, on_keys, on_control_key = _view(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_A, text="a"))
        on_keys.assert_called_once_with("a")
        on_control_key.assert_not_called()
        assert view.toPlainText() == ""

    def test_backspace_key_is_forwarded_as_del(self, qapp):
        view, on_keys, _ = _view(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Backspace))
        on_keys.assert_called_once_with("\x7f")

    def test_ctrl_v_pastes_clipboard_text(self, qapp, mocker):
        view, on_keys, _ = _view(qapp)
        clipboard = MagicMock()
        clipboard.text.return_value = "sign-in-code"
        mocker.patch("ui.clClaudeWidget.QApplication.clipboard", return_value=clipboard)

        view.keyPressEvent(make_key_event(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier))

        on_keys.assert_called_once_with("sign-in-code")


class TestKeyboardWithPopupOpen:
    """The slash-command popup, once visible, must keep owning Enter/Escape/
    Tab/Backtab/Up/Down/PageUp/PageDown (accept, dismiss, or navigate the
    completion) instead of any of them also being forwarded as control keys
    or line submission. All of these are explicitly ignored in keyPressEvent
    while the popup is visible -- found live that assuming QCompleter's own
    event filter would silently consume Up/Down before keyPressEvent ever
    ran was wrong: they were falling through to the control-key dispatch too,
    so pressing Up while typing a slash command also recalled real CLI
    history at the same time as navigating the popup."""

    def test_escape_is_not_forwarded_while_popup_open(self, qapp, mocker):
        view, on_keys, on_control_key = _view(qapp)
        _with_open_popup(view, mocker)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Escape))
        on_control_key.assert_not_called()

    def test_enter_is_not_forwarded_while_popup_open(self, qapp, mocker):
        view, on_keys, on_control_key = _view(qapp)
        _with_open_popup(view, mocker)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Return))
        on_control_key.assert_not_called()

    def test_shift_tab_is_not_forwarded_while_popup_open(self, qapp, mocker):
        view, on_keys, on_control_key = _view(qapp)
        _with_open_popup(view, mocker)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Backtab))
        on_control_key.assert_not_called()

    def test_up_is_not_forwarded_while_popup_open(self, qapp, mocker):
        view, on_keys, on_control_key = _view(qapp)
        _with_open_popup(view, mocker)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Up))
        on_control_key.assert_not_called()

    def test_down_is_not_forwarded_while_popup_open(self, qapp, mocker):
        view, on_keys, on_control_key = _view(qapp)
        _with_open_popup(view, mocker)
        view.keyPressEvent(make_key_event(Qt.Key.Key_Down))
        on_control_key.assert_not_called()


class TestSlashCommandCompleter:
    def test_typing_slash_triggers_completer_popup(self, qapp, mocker):
        view, _, _ = _local_view(qapp)
        complete = mocker.patch.object(view.completer, "complete")

        view._insert("/")

        complete.assert_called_once()

    def test_plain_text_does_not_trigger_completer_popup(self, qapp, mocker):
        view, _, _ = _local_view(qapp)
        complete = mocker.patch.object(view.completer, "complete")
        hide = mocker.patch.object(view.completer.popup(), "hide")

        view._insert("h")

        complete.assert_not_called()
        hide.assert_called()

    def test_space_after_slash_stops_matching(self, qapp, mocker):
        view, _, _ = _local_view(qapp)
        complete = mocker.patch.object(view.completer, "complete")
        hide = mocker.patch.object(view.completer.popup(), "hide")

        view._lbuf = "/model "
        view._update_completer()

        complete.assert_not_called()
        hide.assert_called()

    def test_accepting_a_completion_fills_the_local_line_and_sends_nothing(self, qapp):
        view, on_keys, _ = _local_view(qapp)
        for ch in "/cl":
            view._insert(ch)

        view._insert_completion("/clear")

        assert view._lbuf == "/clear"
        on_keys.assert_not_called()

    def test_accepting_a_completion_not_matching_the_line_is_ignored(self, qapp):
        view, on_keys, _ = _local_view(qapp)
        for ch in "/x":
            view._insert(ch)

        view._insert_completion("/clear")

        assert view._lbuf == "/x"
        on_keys.assert_not_called()


class TestLocalLineEditing:
    """The Claude input box is edited 100% locally and only written to the CLI on Enter (or when a
    key needs the CLI's own copy of the line), so typing/backspace never wait on a round trip."""

    SCREEN = "─────\n❯{}\n─────"

    def _local(self, qapp, line="", cursor=None):
        from ui.clClaudeWidget import ClaudeTerminalView
        on_keys, on_ctl = MagicMock(), MagicMock()
        view = ClaudeTerminalView(on_keys, on_ctl, MagicMock(), local_line_editing=True)
        view.set_server_screen(self.SCREEN.format(" " + line if line else ""), cursor)
        return view, on_keys, on_ctl

    @staticmethod
    def _row(view):
        return view.toPlainText().split("\n")[1]

    @staticmethod
    def _key(view, key, mods=Qt.KeyboardModifier.NoModifier, text=""):
        view.keyPressEvent(make_key_event(key, mods, text))

    def test_typing_sends_nothing_and_never_starts_inside_the_prompt_padding(self, qapp):
        view, on_keys, on_ctl = self._local(qapp)
        for ch in "hi x":
            self._key(view, Qt.Key.Key_H, text=ch)
        assert self._row(view) == "❯ hi x"
        on_keys.assert_not_called()
        on_ctl.assert_not_called()

    def test_backspace_is_instant_and_stops_at_the_prompt(self, qapp):
        view, on_keys, _ = self._local(qapp, "abc")
        for _ in range(6):
            self._key(view, Qt.Key.Key_Backspace)
        assert self._row(view) == "❯ "
        on_keys.assert_not_called()

    def test_existing_cli_text_is_adopted_and_erasable_locally(self, qapp):
        view, on_keys, _ = self._local(qapp, "leftover")
        assert self._row(view) == "❯ leftover"
        self._key(view, Qt.Key.Key_Backspace)
        assert self._row(view) == "❯ leftove"
        on_keys.assert_not_called()

    def test_cursor_keys_edit_in_the_middle_of_the_line(self, qapp):
        view, on_keys, on_ctl = self._local(qapp, "abcd")
        self._key(view, Qt.Key.Key_Home)
        self._key(view, Qt.Key.Key_Delete)
        self._key(view, Qt.Key.Key_Right)
        self._key(view, Qt.Key.Key_X, text="X")
        assert self._row(view) == "❯ bXcd"
        self._key(view, Qt.Key.Key_End)
        assert view._cursor_cell() == (1, 2 + 4)
        on_keys.assert_not_called()
        on_ctl.assert_not_called()

    def test_enter_overwrites_the_cli_line_then_presses_enter(self, qapp):
        view, on_keys, on_ctl = self._local(qapp, "old")
        for _ in range(3):
            self._key(view, Qt.Key.Key_Backspace)
        for ch in "new":
            self._key(view, Qt.Key.Key_N, text=ch)

        self._key(view, Qt.Key.Key_Return)

        on_keys.assert_called_once_with("\x05\x15new")
        on_ctl.assert_called_once_with("ENTER")
        assert self._row(view) == "❯ "

    def test_enter_on_an_unedited_line_just_presses_enter(self, qapp):
        view, on_keys, on_ctl = self._local(qapp, "already there")
        self._key(view, Qt.Key.Key_Return)
        on_keys.assert_not_called()
        on_ctl.assert_called_once_with("ENTER")

    def test_a_stale_snapshot_right_after_enter_does_not_resurrect_the_line(self, qapp):
        view, _, _ = self._local(qapp)
        self._key(view, Qt.Key.Key_H, text="h")
        self._key(view, Qt.Key.Key_Return)

        view.set_server_screen(self.SCREEN.format(" h"), [3, 1])  # CLI hasn't processed it yet

        assert self._row(view) == "❯ "

    def test_snapshots_never_disturb_a_line_being_edited(self, qapp):
        view, _, _ = self._local(qapp)
        for ch in "abc":
            self._key(view, Qt.Key.Key_A, text=ch)

        view.set_server_screen(self.SCREEN.format(""), [2, 1])

        assert self._row(view) == "❯ abc"

    def test_ctrl_a_selects_and_typing_replaces(self, qapp):
        view, on_keys, _ = self._local(qapp, "hello")
        self._key(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier, "\x01")
        assert view._selected is True
        self._key(view, Qt.Key.Key_X, text="x")
        assert self._row(view) == "❯ x"
        on_keys.assert_not_called()

    def test_ctrl_a_then_backspace_clears_locally(self, qapp):
        view, on_keys, _ = self._local(qapp, "hello")
        self._key(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier, "\x01")
        self._key(view, Qt.Key.Key_Backspace)
        assert self._row(view) == "❯ "
        on_keys.assert_not_called()

    def test_ctrl_c_copies_a_selection_otherwise_interrupts_and_clears(self, qapp):
        from PyQt6.QtWidgets import QApplication
        view, on_keys, _ = self._local(qapp, "hello")
        self._key(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier, "\x01")
        self._key(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, "\x03")
        on_keys.assert_not_called()
        assert QApplication.clipboard().text() == "hello"

        self._key(view, Qt.Key.Key_Left)  # deselect
        self._key(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, "\x03")
        on_keys.assert_called_once_with("\x03")
        assert self._row(view) == "❯ "

    def test_ctrl_u_k_w_edit_the_line_locally(self, qapp):
        view, on_keys, _ = self._local(qapp, "one two three")
        self._key(view, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier, "\x17")
        assert self._row(view) == "❯ one two "
        self._key(view, Qt.Key.Key_Home)
        self._key(view, Qt.Key.Key_Right)
        self._key(view, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier, "\x0b")
        assert self._row(view) == "❯ o"
        self._key(view, Qt.Key.Key_U, Qt.KeyboardModifier.ControlModifier, "\x15")
        assert self._row(view) == "❯ "
        on_keys.assert_not_called()

    def test_tab_syncs_the_line_first_then_adopts_what_the_cli_did(self, qapp):
        view, on_keys, _ = self._local(qapp)
        for ch in "/co":
            self._key(view, Qt.Key.Key_C, text=ch)
        view.completer.popup().hide()

        self._key(view, Qt.Key.Key_Tab)

        assert [c.args[0] for c in on_keys.call_args_list] == ["\x05\x15/co", "\t"]
        view.set_server_screen(self.SCREEN.format(" /compact"), [11, 1])
        assert self._row(view) == "❯ /compact"

    def test_up_syncs_then_adopts_the_recalled_history_line(self, qapp):
        view, on_keys, on_ctl = self._local(qapp)
        self._key(view, Qt.Key.Key_Up)
        on_ctl.assert_called_once_with("UP")
        on_keys.assert_not_called()  # nothing typed, so nothing to sync

        view.set_server_screen(self.SCREEN.format(" previous command"), [18, 1])

        assert self._row(view) == "❯ previous command"

    def test_escape_and_shift_tab_go_straight_to_the_cli(self, qapp):
        view, on_keys, on_ctl = self._local(qapp)
        self._key(view, Qt.Key.Key_Escape)
        self._key(view, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
        assert [c.args[0] for c in on_ctl.call_args_list] == ["ESCAPE", "SHIFT_TAB"]
        on_keys.assert_not_called()

    def test_pasted_newlines_become_spaces_in_the_single_line_buffer(self, qapp):
        from PyQt6.QtWidgets import QApplication
        view, on_keys, _ = self._local(qapp)
        QApplication.clipboard().setText("a\nb")
        view._paste()
        assert self._row(view) == "❯ a b"
        on_keys.assert_not_called()

    def test_without_a_framed_prompt_the_live_path_is_used(self, qapp):
        from ui.clClaudeWidget import ClaudeTerminalView
        on_keys = MagicMock()
        view = ClaudeTerminalView(on_keys, MagicMock(), MagicMock(), local_line_editing=True)
        view.set_server_screen("Choose an option:\n❯ 1. Yes\n  2. No")  # a menu, not the input box

        self._key(view, Qt.Key.Key_2, text="2")

        on_keys.assert_called_once_with("2")


class TestLocalEditingShortcuts:
    SCREEN = "─────\n❯ {}\n─────"
    CTRL = Qt.KeyboardModifier.ControlModifier
    SHIFT = Qt.KeyboardModifier.ShiftModifier

    def _local(self, qapp, line="", on_text=None):
        from ui.clClaudeWidget import ClaudeTerminalView
        on_keys = MagicMock()
        view = ClaudeTerminalView(on_keys, MagicMock(), MagicMock(), local_line_editing=True, on_text=on_text)
        view.set_server_screen(self.SCREEN.format(line))
        return view, on_keys

    @staticmethod
    def _row(view):
        return view.toPlainText().split("\n")[1]

    @staticmethod
    def _key(view, key, mods=Qt.KeyboardModifier.NoModifier, text=""):
        view.keyPressEvent(make_key_event(key, mods, text))

    def test_ctrl_backspace_deletes_the_previous_word(self, qapp):
        view, on_keys = self._local(qapp, "one two three")
        self._key(view, Qt.Key.Key_Backspace, self.CTRL)
        assert self._row(view) == "❯ one two "
        self._key(view, Qt.Key.Key_Backspace, self.CTRL)
        assert self._row(view) == "❯ one "
        on_keys.assert_not_called()

    def test_ctrl_delete_deletes_the_next_word(self, qapp):
        view, _ = self._local(qapp, "one two three")
        self._key(view, Qt.Key.Key_Home)
        self._key(view, Qt.Key.Key_Delete, self.CTRL)
        assert self._row(view) == "❯ two three"

    def test_ctrl_arrows_jump_by_word(self, qapp):
        view, _ = self._local(qapp, "one two three")
        self._key(view, Qt.Key.Key_Left, self.CTRL)
        assert view._lpos == len("one two ")
        self._key(view, Qt.Key.Key_Left, self.CTRL)
        assert view._lpos == len("one ")
        self._key(view, Qt.Key.Key_Right, self.CTRL)
        assert view._lpos == len("one two ")

    def test_shift_arrows_select_and_typing_replaces_the_selection(self, qapp):
        view, _ = self._local(qapp, "hello world")
        self._key(view, Qt.Key.Key_Left, self.SHIFT)
        self._key(view, Qt.Key.Key_Left, self.SHIFT)
        assert view._sel_range() == (9, 11)
        self._key(view, Qt.Key.Key_X, text="X")
        assert self._row(view) == "❯ hello worX"

    def test_ctrl_shift_left_selects_a_word_and_backspace_removes_it(self, qapp):
        view, _ = self._local(qapp, "one two")
        self._key(view, Qt.Key.Key_Left, self.CTRL | self.SHIFT)
        assert view._sel_range() == (4, 7)
        self._key(view, Qt.Key.Key_Backspace)
        assert self._row(view) == "❯ one "

    def test_shift_home_selects_to_the_start(self, qapp):
        view, _ = self._local(qapp, "abc def")
        self._key(view, Qt.Key.Key_Home, self.SHIFT)
        assert view._sel_range() == (0, 7)

    def test_plain_arrow_collapses_a_selection_to_its_edge(self, qapp):
        view, _ = self._local(qapp, "abcdef")
        self._key(view, Qt.Key.Key_Left, self.SHIFT)
        self._key(view, Qt.Key.Key_Left, self.SHIFT)
        self._key(view, Qt.Key.Key_Left)
        assert view._sel_range() is None and view._lpos == 4

    def test_ctrl_x_cuts_the_selection_and_ctrl_v_pastes_at_the_cursor(self, qapp):
        from PyQt6.QtWidgets import QApplication
        view, on_keys = self._local(qapp, "abcdef")
        self._key(view, Qt.Key.Key_Left, self.SHIFT)
        self._key(view, Qt.Key.Key_Left, self.SHIFT)
        self._key(view, Qt.Key.Key_X, self.CTRL, "\x18")
        assert QApplication.clipboard().text() == "ef" and self._row(view) == "❯ abcd"
        self._key(view, Qt.Key.Key_Home)
        view._paste()
        assert self._row(view) == "❯ efabcd"
        on_keys.assert_not_called()

    def test_selection_is_highlighted_on_the_right_columns(self, qapp):
        view, _ = self._local(qapp, "abcdef")
        self._key(view, Qt.Key.Key_Left, self.SHIFT)
        self._key(view, Qt.Key.Key_Left, self.SHIFT)
        sel = view.extraSelections()[0].cursor
        assert sel.selectedText() == "ef"


class TestSlashPopup:
    def test_popup_is_wide_enough_to_show_its_entries(self, qapp):
        from ui.clClaudeWidget import ClaudeTerminalView
        view = ClaudeTerminalView(MagicMock(), MagicMock(), MagicMock(), local_line_editing=True)
        view.resize(700, 400)
        view.show()
        view.set_server_screen("─────\n❯\n─────", [2, 1])

        view._insert("/")

        popup = view.completer.popup()
        assert popup.isVisible()
        assert popup.width() >= 200  # was 1px (the text cursor's width): a "huge line" with no options
        assert view.completer.completionCount() > 5
        view.close()

    def _shown(self):
        from ui.clClaudeWidget import ClaudeTerminalView
        on_keys, on_ctl = MagicMock(), MagicMock()
        view = ClaudeTerminalView(on_keys, on_ctl, MagicMock(), local_line_editing=True)
        view.resize(700, 400)
        view.show()
        view.set_server_screen("─────\n\u276f\n─────", [2, 1])
        return view, on_keys, on_ctl

    def test_popup_goes_away_once_the_text_exactly_matches_a_command(self, qapp):
        view, _, _ = self._shown()
        for ch in "/compac":
            view._insert(ch)
        assert view.completer.popup().isVisible()

        view._insert("t")

        assert not view.completer.popup().isVisible()
        view.close()

    def test_exact_match_is_case_insensitive(self, qapp):
        view, _, _ = self._shown()
        for ch in "/CLEAR":
            view._insert(ch)
        assert not view.completer.popup().isVisible()
        view.close()

    def test_accepting_a_completion_leaves_the_popup_closed_so_enter_submits(self, qapp):
        view, on_keys, on_ctl = self._shown()
        for ch in "/comp":
            view._insert(ch)

        view._insert_completion("/compact")

        assert view._lbuf == "/compact"
        assert not view.completer.popup().isVisible()
        view.keyPressEvent(make_key_event(Qt.Key.Key_Return))
        on_keys.assert_called_once_with("\x05\x15/compact")
        on_ctl.assert_called_once_with("ENTER")
        view.close()

    def test_partial_text_still_shows_the_popup(self, qapp):
        view, _, _ = self._shown()
        view._insert("/")
        assert view.completer.popup().isVisible()
        view.close()

    def test_mode_switch_commands_are_offered(self, qapp):
        from ui.clClaudeWidget import SLASH_COMMANDS
        names = [c for c, _ in SLASH_COMMANDS]
        assert "/terminal" in names and "/claude" in names


class TestModeSwitchCommands:
    """The bridge only recognizes /terminal and /claude as whole-line text messages, so the widget
    (which otherwise only sends keys) must send them that way instead of typing them into the CLI."""

    def test_local_enter_sends_terminal_as_text_not_keys(self, qapp):
        on_text = MagicMock()
        view, on_keys = TestLocalEditingShortcuts()._local(qapp, on_text=on_text)
        for ch in "/terminal":
            view._insert(ch)

        view._submit_line()

        on_text.assert_called_once_with("/terminal")
        on_keys.assert_not_called()
        assert view._lbuf == ""

    def test_case_and_spaces_are_ignored(self, qapp):
        on_text = MagicMock()
        view, _ = TestLocalEditingShortcuts()._local(qapp, on_text=on_text)
        view._insert(" ")
        for ch in "/Claude":
            view._insert(ch)
        view._submit_line()
        on_text.assert_called_once_with("/claude")

    def test_other_lines_are_still_typed_into_the_cli(self, qapp):
        on_text = MagicMock()
        view, on_keys = TestLocalEditingShortcuts()._local(qapp, on_text=on_text)
        for ch in "/terminalx":
            view._insert(ch)
        view._submit_line()
        on_text.assert_not_called()
        on_keys.assert_called_once_with("\x05\x15/terminalx")

    def test_in_a_shell_the_live_typed_command_is_erased_before_switching(self, qapp):
        from ui.clClaudeWidget import ClaudeTerminalView
        on_keys, on_text = MagicMock(), MagicMock()
        view = ClaudeTerminalView(on_keys, MagicMock(), MagicMock(), local_line_editing=True, on_text=on_text)
        view.set_server_screen("out\nuser@host:~$")
        for ch in "/claude":
            view._insert(ch)
        on_keys.reset_mock()

        view._submit_line()

        on_keys.assert_called_once_with("\x7f" * len("/claude"))
        on_text.assert_called_once_with("/claude")


class TestShellLocalEditing:
    """A readline-style shell prompt (mode "terminal", foreground process = the shell) gets the same
    local line editing as Claude's box. Where the editable text begins is an index: the cursor
    column at a fresh prompt, i.e. the prompt's own length -- whatever characters it contains."""

    PROMPT = "user@host:~$ "

    def _shell(self, qapp, typed="", suggestion="", prompt=None, mode="terminal", idle=True, ansi=None):
        from ui.clClaudeWidget import ClaudeTerminalView
        prompt = prompt or self.PROMPT
        on_keys, on_ctl, on_text = MagicMock(), MagicMock(), MagicMock()
        view = ClaudeTerminalView(on_keys, on_ctl, MagicMock(), local_line_editing=True, on_text=on_text)
        view.set_server_screen("out\n" + prompt, [len(prompt), 1], ansi=ansi, mode=mode, idle=idle)
        if typed or suggestion:
            view._shell_touched = True  # the user has already worked on this prompt
            view.set_server_screen("out\n" + prompt + typed + suggestion, [len(prompt + typed), 1],
                                   mode=mode, idle=idle)
        return view, on_keys, on_ctl, on_text

    @staticmethod
    def _key(view, key, mods=Qt.KeyboardModifier.NoModifier, text=""):
        view.keyPressEvent(make_key_event(key, mods, text))

    def test_typing_and_backspace_are_local_and_send_nothing(self, qapp):
        view, on_keys, on_ctl, _ = self._shell(qapp)
        for ch in "lss":
            self._key(view, Qt.Key.Key_L, text=ch)
        self._key(view, Qt.Key.Key_Backspace)
        assert view.toPlainText().split("\n")[1] == self.PROMPT + "ls"
        on_keys.assert_not_called()
        on_ctl.assert_not_called()

    def test_the_prompt_boundary_is_the_cursor_column_at_a_fresh_prompt(self, qapp):
        view, _, _, _ = self._shell(qapp)
        assert view._shell_start == len(self.PROMPT)

    def test_backspace_never_goes_back_past_the_prompt_whatever_it_contains(self, qapp):
        for prompt in ("C: > ", "server ~ ", "PS>", "\u2514\u2500$ ", "weird [x] $#> "):
            view, on_keys, _, _ = self._shell(qapp, prompt=prompt)
            for ch in "ab":
                self._key(view, Qt.Key.Key_A, text=ch)
            for _ in range(10):
                self._key(view, Qt.Key.Key_Backspace)
            assert view.toPlainText().split("\n")[1] == prompt
            on_keys.assert_not_called()

    def test_word_and_line_deletion_also_stop_at_the_prompt(self, qapp):
        view, _, _, _ = self._shell(qapp, prompt="C: > ")
        for ch in "one two":
            self._key(view, Qt.Key.Key_A, text=ch)
        self._key(view, Qt.Key.Key_Backspace, Qt.KeyboardModifier.ControlModifier)
        self._key(view, Qt.Key.Key_Backspace, Qt.KeyboardModifier.ControlModifier)
        self._key(view, Qt.Key.Key_Backspace, Qt.KeyboardModifier.ControlModifier)
        assert view.toPlainText().split("\n")[1] == "C: > "
        for ch in "xyz":
            self._key(view, Qt.Key.Key_A, text=ch)
        self._key(view, Qt.Key.Key_U, Qt.KeyboardModifier.ControlModifier, "\x15")
        assert view.toPlainText().split("\n")[1] == "C: > "

    def test_the_shells_grey_suggestion_is_not_adopted_as_typed_text(self, qapp):
        view, _, _, _ = self._shell(qapp, typed="cl", suggestion="ear")
        assert view._lbuf == "cl"
        self._key(view, Qt.Key.Key_Backspace)
        assert view.toPlainText().split("\n")[1] == self.PROMPT + "c"

    def test_enter_overwrites_the_shell_line_then_presses_enter(self, qapp):
        view, on_keys, on_ctl, _ = self._shell(qapp)
        for ch in "ls -la":
            self._key(view, Qt.Key.Key_L, text=ch)

        self._key(view, Qt.Key.Key_Return)

        on_keys.assert_called_once_with("\x05\x15ls -la")
        on_ctl.assert_called_once_with("ENTER")

    def test_after_enter_the_next_prompt_is_measured_afresh(self, qapp):
        view, _, _, _ = self._shell(qapp, prompt="C: > ")
        self._key(view, Qt.Key.Key_A, text="x")
        self._key(view, Qt.Key.Key_Return)
        assert view._shell_start is None  # unknown until the new prompt has been drawn

        view._hold_until = 0  # the hold is over
        view.set_server_screen("out\nresult\nD:\\other> ", [10, 2], mode="terminal", idle=True)

        assert view._shell_start == 10  # a different prompt length is picked up

    def test_start_is_fixed_once_the_user_has_touched_the_prompt(self, qapp):
        view, _, _, _ = self._shell(qapp)
        self._key(view, Qt.Key.Key_A, text="a")
        view.set_server_screen("out\n" + self.PROMPT + "history command", [len(self.PROMPT) + 15, 1],
                               mode="terminal", idle=True)
        assert view._shell_start == len(self.PROMPT)

    def test_a_prompt_that_is_not_drawn_yet_is_not_editable(self, qapp):
        view, on_keys, _, _ = self._shell(qapp)
        view.set_server_screen("out\n", [0, 1], mode="terminal", idle=True)
        assert view._shell_start is None
        self._key(view, Qt.Key.Key_A, text="a")
        on_keys.assert_called_once_with("a")

    def test_tab_syncs_then_adopts_the_completed_line(self, qapp):
        view, on_keys, _, _ = self._shell(qapp)
        for ch in "ec":
            self._key(view, Qt.Key.Key_E, text=ch)
        self._key(view, Qt.Key.Key_Tab)
        assert [c.args[0] for c in on_keys.call_args_list] == ["\x05\x15ec", "\t"]

        view.set_server_screen("out\n" + self.PROMPT + "echo ", [len(self.PROMPT) + 5, 1],
                               mode="terminal", idle=True)

        assert view._lbuf == "echo "

    def test_up_recalls_history_and_is_adopted(self, qapp):
        view, _, on_ctl, _ = self._shell(qapp)
        self._key(view, Qt.Key.Key_Up)
        on_ctl.assert_called_once_with("UP")
        view.set_server_screen("out\n" + self.PROMPT + "git status", [len(self.PROMPT) + 10, 1],
                               mode="terminal", idle=True)
        assert view._lbuf == "git status"

    def test_ctrl_a_and_ctrl_e_are_beginning_and_end_of_line_not_select_all(self, qapp):
        view, on_keys, _, _ = self._shell(qapp, typed="hello")
        self._key(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier, "\x01")
        assert view._lpos == 0 and view._sel_range() is None
        self._key(view, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier, "\x05")
        assert view._lpos == 5
        on_keys.assert_not_called()

    def test_no_slash_popup_in_a_shell(self, qapp, mocker):
        view, _, _, _ = self._shell(qapp)
        complete = mocker.patch.object(view.completer, "complete")
        self._key(view, Qt.Key.Key_Slash, text="/")
        complete.assert_not_called()

    def test_claude_mode_switch_still_works_from_a_shell(self, qapp):
        view, on_keys, _, on_text = self._shell(qapp)
        for ch in "/claude":
            self._key(view, Qt.Key.Key_C, text=ch)
        self._key(view, Qt.Key.Key_Return)
        on_text.assert_called_once_with("/claude")
        on_keys.assert_not_called()

    def test_while_a_program_runs_keys_go_straight_through(self, qapp):
        """Not idle: e.g. sudo's password prompt or vim -- nothing may be buffered or echoed locally."""
        view, on_keys, _, _ = self._shell(qapp, idle=False)
        self._key(view, Qt.Key.Key_Y, text="y")
        on_keys.assert_called_once_with("y")
        assert view._shell_start is None

    def test_without_an_idle_report_the_terminal_is_a_pure_mirror(self, qapp):
        view, on_keys, _, _ = self._shell(qapp, idle=None)
        self._key(view, Qt.Key.Key_X, text="x")
        on_keys.assert_called_once_with("x")

    def test_a_basic_terminal_is_a_pure_mirror(self, qapp):
        view, on_keys, _, _ = self._shell(qapp, mode="terminal-basic")
        self._key(view, Qt.Key.Key_X, text="x")
        on_keys.assert_called_once_with("x")

    def test_the_prompt_keeps_its_colours_while_the_line_is_edited_locally(self, qapp):
        ansi = "out\n\x1b[32muser@host:~$ \x1b[0m"
        view, _, _, _ = self._shell(qapp, ansi=ansi)

        self._key(view, Qt.Key.Key_H, text="h")

        assert view.toPlainText().split("\n")[1] == self.PROMPT + "h"
        cursor = view.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        cursor.movePosition(cursor.MoveOperation.Down)
        cursor.setPosition(cursor.position() + 1)
        color = cursor.charFormat().foreground().color()
        assert _orange_family(color)  # the prompt keeps its (now orange) styling, not plain default text


class TestSwitchingFromClaudeToTheTerminal:
    """Found live: typing "/terminal" in Claude's box marked the (not yet existing) shell prompt as
    'touched', which froze its start unmeasured -- so after the switch every key went live and
    nothing was predicted."""

    CLAUDE = "─" * 40 + "\n❯\n" + "─" * 40 + "\n  footer"

    def _switch(self, qapp):
        from ui.clClaudeWidget import ClaudeTerminalView
        on_keys, on_text = MagicMock(), MagicMock()
        view = ClaudeTerminalView(on_keys, MagicMock(), MagicMock(), local_line_editing=True, on_text=on_text)
        view.HOLD_S = 0.05
        view.set_server_screen(self.CLAUDE, [2, 1], mode="claude")
        for ch in "/terminal":
            view.keyPressEvent(make_key_event(Qt.Key.Key_A, text=ch))
        view.keyPressEvent(make_key_event(Qt.Key.Key_Return))
        on_text.assert_called_once_with("/terminal")
        return view, on_keys

    @staticmethod
    def _pump(qapp, seconds):
        import time
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.005)

    def test_typing_in_claudes_box_does_not_touch_the_shell_prompt(self, qapp):
        view, _ = self._switch(qapp)
        assert view._shell_touched is False

    def test_the_terminal_becomes_editable_even_if_its_snapshot_arrived_inside_the_hold(self, qapp):
        view, on_keys = self._switch(qapp)
        view.set_server_screen("out\n└─$ ", [4, 1], mode="terminal", idle=True)  # inside the hold
        assert view._local_active() is False

        self._pump(qapp, 0.3)  # no further snapshot arrives; the hold timer re-checks the screen

        assert view._local_active() is True and view._shell_start == 4
        view.keyPressEvent(make_key_event(Qt.Key.Key_C, text="c"))
        on_keys.assert_not_called()

    def test_a_mode_change_starts_prompt_measurement_over(self, qapp):
        from ui.clClaudeWidget import ClaudeTerminalView
        view = ClaudeTerminalView(MagicMock(), MagicMock(), MagicMock(), local_line_editing=True)
        view.set_server_screen("out\n$ ", [2, 1], mode="terminal", idle=True)
        view._shell_touched = True
        view._shell_start = 2

        view.set_server_screen(self.CLAUDE, [2, 1], mode="claude")

        assert view._shell_start is None and view._shell_touched is False


class TestNoShellSuggestions:
    """The terminal never shows a shell's autosuggestion: at an idle prompt anything drawn beyond the
    cursor is dropped, both from the plain text and from the styled copy."""

    ANSI = "out\n$ \x1b[1mcl\x1b[0m\x1b[38;5;244mear\x1b[39m"

    def _view(self, qapp, **screen):
        from ui.clClaudeWidget import ClaudeTerminalView
        view = ClaudeTerminalView(MagicMock(), MagicMock(), MagicMock(), local_line_editing=True)
        view.set_server_screen("out\n$ clear", [4, 1], **screen)
        return view

    def test_plain_screen_drops_the_suggestion(self, qapp):
        view = self._view(qapp, mode="terminal", idle=True)
        assert view.toPlainText().split("\n")[1] == "$ cl"

    def test_styled_screen_drops_the_suggestion_too(self, qapp):
        view = self._view(qapp, ansi=self.ANSI, mode="terminal", idle=True)
        assert view.toPlainText().split("\n")[1] == "$ cl"

    def test_the_pure_mirror_inside_the_post_enter_hold_also_drops_it(self, qapp):
        view = self._view(qapp, mode="terminal", idle=True)
        assert view._local_active() is False or view.toPlainText().split("\n")[1] == "$ cl"

    def test_a_running_program_keeps_all_of_its_output(self, qapp):
        view = self._view(qapp, mode="terminal", idle=False)
        assert view.toPlainText().split("\n")[1] == "$ clear"

    def test_claude_and_basic_terminals_are_untouched(self, qapp):
        for mode in ("claude", "terminal-basic", None):
            view = self._view(qapp, mode=mode, idle=None)
            assert view.toPlainText().split("\n")[1] == "$ clear"
