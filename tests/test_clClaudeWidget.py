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
        view, _, _ = _view(qapp)
        view.set_server_screen(self.SCREEN_WITH_FOOTER)

        view._insert("h")
        view._insert("i")

        lines = view.toPlainText().split("\n")
        assert lines[3] == "\u276f hi"
        assert lines[5] == "  \u23ed\u23ed accept edits on (shift+tab to cycle)"

    def test_cursor_ends_up_on_the_prompt_row(self, qapp):
        view, _, _ = _view(qapp)
        view.set_server_screen(self.SCREEN_WITH_FOOTER)

        view._insert("h")

        assert view.textCursor().blockNumber() == 3

    def test_bare_shell_prompt_with_no_bracketed_box_still_appends_at_the_end(self, qapp):
        """/terminal mode's plain shell has no ❯-between-rules framing at
        all -- falls back to the old "append at the very end" behavior,
        which is correct there since the shell's prompt IS the last line."""
        view, _, _ = _view(qapp)
        view.set_server_screen("C:\\repo>")

        view._insert("d")
        view._insert("i")
        view._insert("r")

        assert view.toPlainText() == "C:\\repo>dir"


class TestLocalEcho:
    def test_insert_appends_to_pending_and_forwards_the_char(self, qapp):
        view, on_keys, _ = _view(qapp)
        view._insert("a")
        assert view._pending == "a"
        assert view.toPlainText() == "a"
        on_keys.assert_called_once_with("a")

    def test_backspace_removes_last_pending_char_and_sends_del_byte(self, qapp):
        view, on_keys, _ = _view(qapp)
        view._insert("a")
        view._insert("b")
        on_keys.reset_mock()

        view._backspace()

        assert view._pending == "a"
        on_keys.assert_called_once_with("\x7f")

    def test_backspace_on_empty_pending_is_a_no_op(self, qapp):
        view, on_keys, _ = _view(qapp)
        view._backspace()
        on_keys.assert_not_called()

    def test_new_server_screen_drops_the_local_overlay(self, qapp):
        view, on_keys, _ = _view(qapp)
        view._insert("h")
        view._insert("i")

        view.set_server_screen("claude> ")

        assert view._pending == ""
        assert view.toPlainText() == "claude> "

    def test_paste_inserts_clipboard_text_as_one_forward(self, qapp, mocker):
        view, on_keys, _ = _view(qapp)
        clipboard = MagicMock()
        clipboard.text.return_value = "pasted code"
        mocker.patch("ui.clClaudeWidget.QApplication.clipboard", return_value=clipboard)

        view._paste()

        assert view._pending == "pasted code"
        on_keys.assert_called_once_with("pasted code")

    def test_paste_with_empty_clipboard_does_nothing(self, qapp, mocker):
        view, on_keys, _ = _view(qapp)
        clipboard = MagicMock()
        clipboard.text.return_value = ""
        mocker.patch("ui.clClaudeWidget.QApplication.clipboard", return_value=clipboard)

        view._paste()

        on_keys.assert_not_called()


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

    def test_enter_submits_the_line_and_clears_pending(self, qapp):
        view, _, on_control_key = _view(qapp)
        view._insert("hello")
        on_control_key.reset_mock()

        view.keyPressEvent(make_key_event(Qt.Key.Key_Return))

        on_control_key.assert_called_once_with("ENTER")
        assert view._pending == ""

    def test_plain_typing_is_echoed_locally_and_forwarded(self, qapp):
        view, on_keys, on_control_key = _view(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_A, text="a"))
        on_keys.assert_called_once_with("a")
        on_control_key.assert_not_called()
        assert view.toPlainText() == "a"

    def test_backspace_key_removes_locally_echoed_char(self, qapp):
        view, on_keys, _ = _view(qapp)
        view.keyPressEvent(make_key_event(Qt.Key.Key_A, text="a"))
        on_keys.reset_mock()

        view.keyPressEvent(make_key_event(Qt.Key.Key_Backspace))

        assert view._pending == ""
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
        assert view._pending == ""

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
        view, _, _ = _view(qapp)
        complete = mocker.patch.object(view.completer, "complete")

        view._insert("/")

        complete.assert_called_once()

    def test_plain_text_does_not_trigger_completer_popup(self, qapp, mocker):
        view, _, _ = _view(qapp)
        complete = mocker.patch.object(view.completer, "complete")
        hide = mocker.patch.object(view.completer.popup(), "hide")

        view._insert("h")

        complete.assert_not_called()
        hide.assert_called()

    def test_space_after_slash_stops_matching(self, qapp, mocker):
        view, _, _ = _view(qapp)
        complete = mocker.patch.object(view.completer, "complete")
        hide = mocker.patch.object(view.completer.popup(), "hide")

        view._pending = "/model "
        view._update_completer()

        complete.assert_not_called()
        hide.assert_called()

    def test_accepting_a_completion_sends_only_the_missing_suffix(self, qapp):
        view, on_keys, _ = _view(qapp)
        view._insert("/")
        view._insert("c")
        view._insert("l")
        on_keys.reset_mock()

        view._insert_completion("/clear")

        assert view._pending == "/clear"
        on_keys.assert_called_once_with("ear")

    def test_accepting_a_completion_not_matching_pending_is_ignored(self, qapp):
        view, on_keys, _ = _view(qapp)
        view._insert("/")
        view._insert("x")
        on_keys.reset_mock()

        view._insert_completion("/clear")

        assert view._pending == "/x"
        on_keys.assert_not_called()
