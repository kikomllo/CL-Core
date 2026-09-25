from clTheme import Theme
from utils.clActionRouter import ActionRouter
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QTextCursor, QKeySequence
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QSizePolicy, QCompleter, QApplication
)
from ui.clZoomTextEdit import ZoomTextEdit

# Arrow/Escape have no useful local meaning in a read-only mirror and their
# real effect (CLI history recall, "esc to interrupt") lives server-side, so
# they're always forwarded as control keys rather than predicted locally --
# same backend-neutral tokens as ClaudeSessionBase.send_control_key().
_KEYBOARD_CONTROL_KEYS = {
    Qt.Key.Key_Up: "UP",
    Qt.Key.Key_Down: "DOWN",
    Qt.Key.Key_Escape: "ESCAPE",
}

# Common Claude Code slash commands -- a starting set, not exhaustive; extend as needed.
SLASH_COMMANDS = [
    ("/clear", "Clear conversation history"),
    ("/compact", "Compact the conversation to save context"),
    ("/help", "Show help"),
    ("/cost", "Show token usage for this session"),
    ("/status", "Show session status"),
    ("/model", "Change the active model"),
    ("/permissions", "Manage tool permissions"),
    ("/agents", "Manage subagents"),
    ("/mcp", "Manage MCP servers"),
    ("/init", "Create a CLAUDE.md for this project"),
    ("/review", "Review code changes"),
    ("/resume", "Resume a previous session"),
    ("/login", "Sign in to your account"),
    ("/logout", "Sign out"),
    ("/bug", "Report a bug to Anthropic"),
    ("/doctor", "Check Claude Code's installation health"),
    ("/memory", "Edit CLAUDE.md memory files"),
    ("/vim", "Toggle vim keybindings"),
    ("/config", "Open settings"),
]


def _build_slash_completer(parent) -> QCompleter:
    """A QCompleter's popup only ever shows entries matching the current text as
    a prefix, so it naturally stays hidden for ordinary questions (nothing here
    starts with anything but '/') and appears the moment '/' is typed -- no
    extra trigger logic needed."""
    model = QStandardItemModel(parent)
    for command, description in SLASH_COMMANDS:
        item = QStandardItem(command)
        item.setToolTip(description)
        model.appendRow(item)

    completer = QCompleter(model, parent)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
    completer.popup().setStyleSheet(f"""
        QListView {{
            background-color: rgba(25, 12, 3, 250);
            color: {Theme.C_TEXT};
            border: 1px solid rgba(255, 180, 0, 150);
            font-family: {Theme.FONT_FAMILY};
            font-size: {Theme.F_NORMAL};
            outline: none;
        }}
        QListView::item {{
            padding: 4px 8px;
        }}
        QListView::item:selected {{
            background-color: rgba(255, 150, 0, 100);
            color: #ffffff;
        }}
    """)
    return completer


class ClaudeTerminalView(ZoomTextEdit):
    """The screen mirror doubles as the input surface -- there's no separate
    input box; typed keys go straight to the live session, like a real
    terminal. Plain characters, backspace, and paste are echoed locally the
    instant they're pressed (appended after the last known server screen) so
    typing feels instant despite the MQTT round trip to clClaudeBridge.py and
    back; the overlay is simply dropped the moment a new authoritative screen
    snapshot arrives (jarvis/claude/screen is a full-snapshot payload, and
    everything typed so far has always already been sent by then, since each
    keystroke is dispatched synchronously on press). This is a heuristic, not
    a real terminal emulator: it assumes the cursor is at the end of the
    current line, which holds for ordinary typing but not for arrow-key
    editing mid-line -- arrows are forwarded to the real session instead of
    predicted locally, exactly because this view can't know what they'd do
    there (CLI history, its own completion).

    Read-only at the Qt level (blocks QTextEdit's own built-in typing/paste)
    so every key is funneled through keyPressEvent below instead of mutating
    the displayed text directly.
    """

    # Debounced, not sent on every pixel of a drag -- a live resize (ConPTY
    # setwinsize / tmux resize-window) is real backend work, not free.
    RESIZE_DEBOUNCE_MS = 300
    MIN_COLS = 20
    MIN_ROWS = 5

    def __init__(self, on_keys, on_control_key, on_resize, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Without this, Qt's own QWidget::event() intercepts Tab/Shift+Tab for
        # focus-traversal *before* keyPressEvent ever runs, silently shifting
        # keyboard focus off this widget instead of reaching our handling
        # below -- found live ("shift+tab does nothing").
        self.setTabChangesFocus(False)
        self._on_keys = on_keys
        self._on_control_key = on_control_key
        self._on_resize = on_resize
        self._server_screen = ""
        self._pending = ""  # typed locally since the last authoritative screen
        self._last_dispatched_grid = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._dispatch_resize)
        self.completer = _build_slash_completer(self)
        self.completer.setWidget(self)
        self.completer.activated.connect(self._insert_completion)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start(self.RESIZE_DEBOUNCE_MS)

    def zoomIn(self, range=1):
        # Changing font size changes how many characters fit too -- a real
        # terminal reflows the same way when you change its font size.
        super().zoomIn(range)
        self._resize_timer.start(self.RESIZE_DEBOUNCE_MS)

    def zoomOut(self, range=1):
        super().zoomOut(range)
        self._resize_timer.start(self.RESIZE_DEBOUNCE_MS)

    def _grid_size(self):
        metrics = self.fontMetrics()
        char_w = metrics.horizontalAdvance("0") or 1
        char_h = metrics.height() or 1
        cols = max(self.MIN_COLS, self.viewport().width() // char_w)
        rows = max(self.MIN_ROWS, self.viewport().height() // char_h)
        return cols, rows

    def _dispatch_resize(self):
        grid = self._grid_size()
        if grid == self._last_dispatched_grid:
            return
        self._last_dispatched_grid = grid
        self._on_resize(*grid)

    def set_server_screen(self, text: str):
        self._server_screen = text
        self._pending = ""
        self._render()

    @staticmethod
    def _find_prompt_row(rows) -> int:
        # Same heuristic as ClaudeSessionBase._prompt_row_visible(): the chat
        # input box is a "❯" row bracketed by horizontal-rule rows -- not
        # necessarily the last row on screen, since a footer/status line (e.g.
        # "accept edits on (shift+tab to cycle)") can follow it. Falls back to
        # the last row (a bare shell prompt has no such framing at all).
        for i in range(1, len(rows) - 1):
            if (rows[i].lstrip().startswith("❯")
                    and rows[i - 1].startswith("─") and rows[i + 1].startswith("─")):
                return i
        return len(rows) - 1

    def _render(self):
        scrollbar = self.verticalScrollBar()
        at_bottom = scrollbar.value() == scrollbar.maximum()
        preserved_value = scrollbar.value()
        rows = self._server_screen.split("\n")
        target_row = self._find_prompt_row(rows)
        rows[target_row] += self._pending
        self.setPlainText("\n".join(rows))
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.MoveAnchor, target_row)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfLine)
        self.setTextCursor(cursor)
        # setTextCursor() can itself scroll to keep the cursor in view (the
        # prompt row), which -- whenever a footer row follows it -- fought
        # both a manual scroll-down and this method's own "stay at bottom"
        # intent, permanently hiding anything below the prompt row no matter
        # how far down you scrolled. Always override with our own explicit
        # target afterward instead of trusting wherever that left it -- found
        # live ("have to zoom out for the bottom bar to appear").
        scrollbar.setValue(scrollbar.maximum() if at_bottom else preserved_value)
        self._update_completer()

    def _update_completer(self):
        # Only while typing the command token itself -- a trailing space means
        # the user's on to arguments, which aren't slash commands to match.
        if self._pending.startswith("/") and " " not in self._pending:
            self.completer.setCompletionPrefix(self._pending)
            if self.completer.completionCount() > 0:
                self.completer.popup().setCurrentIndex(self.completer.completionModel().index(0, 0))
                self.completer.complete(self.cursorRect())
                return
        self.completer.popup().hide()

    def _insert(self, char: str):
        self._pending += char
        self._render()
        self._on_keys(char)

    def _backspace(self):
        if not self._pending:
            return
        self._pending = self._pending[:-1]
        self._render()
        self._on_keys("\x7f")

    def _paste(self):
        text = QApplication.clipboard().text()
        if not text:
            return
        self._pending += text
        self._render()
        self._on_keys(text)

    def _submit_line(self):
        # Optimistic reset: don't wait for the round trip to clear the typed
        # line -- the next real screen snapshot will overwrite this anyway.
        self._on_control_key("ENTER")
        self._pending = ""
        self._render()

    def _insert_completion(self, text: str):
        if not text.startswith(self._pending):
            return
        suffix = text[len(self._pending):]
        self._pending = text
        self._render()
        if suffix:
            self._on_keys(suffix)
        self.completer.popup().hide()

    # Every key the completer's popup cares about (navigation, accept,
    # dismiss) while it's visible -- explicitly deferred to Qt's own popup
    # handling rather than assumed to be consumed by QCompleter's internal
    # event filter before keyPressEvent runs. That assumption was wrong for
    # Up/Down specifically: they were falling through to the control-key
    # dispatch below *in addition to* navigating the popup, so pressing Up
    # while typing a slash command also recalled real CLI history at the
    # same time -- found live.
    _POPUP_OWNED_KEYS = (
        Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape,
        Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
        Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
    )

    def keyPressEvent(self, event):
        if self.completer.popup().isVisible() and event.key() in self._POPUP_OWNED_KEYS:
            event.ignore()
            return

        key = event.key()
        token = _KEYBOARD_CONTROL_KEYS.get(key)
        if token is not None:
            self._on_control_key(token)
            event.accept()
            return
        if key == Qt.Key.Key_Backtab or (key == Qt.Key.Key_Tab and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._on_control_key("SHIFT_TAB")
            event.accept()
            return
        if key == Qt.Key.Key_Tab:
            self._on_keys("\t")
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._submit_line()
            event.accept()
            return
        if key == Qt.Key.Key_Backspace:
            self._backspace()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self._paste()
            event.accept()
            return
        text = event.text()
        if text and text.isprintable():
            self._insert(text)
            event.accept()
            return
        event.ignore()


class ClaudeWidget(QWidget):
    """Mirrors the Claude terminal bridge's screen (jarvis/claude/screen) and
    lets you type directly into the live session, like a real terminal --
    the dashboard's window onto src/clClaudeBridge.py. No separate input box:
    ClaudeTerminalView (above) captures keys straight off the screen mirror
    and forwards them via jarvis/claude/question (claude.keys/control_key)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        self.screen_view = ClaudeTerminalView(self._send_keys, self._send_control_key, self._send_resize)
        self.screen_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.screen_view.setStyleSheet(Theme.get_style("LogViewer"))
        layout.addWidget(self.screen_view, stretch=1)

    def _send_control_key(self, token: str):
        try:
            self.router.dispatch("claude.control_key", control_key=token)
        except Exception:
            pass

    def _send_keys(self, keys: str):
        try:
            self.router.dispatch("claude.keys", keys=keys)
        except Exception:
            pass

    def _send_resize(self, cols: int, rows: int):
        try:
            self.router.dispatch("claude.resize", cols=cols, rows=rows)
        except Exception:
            pass

    def update_screen(self, text: str):
        # jarvis/claude/screen is a full-snapshot payload, not an incremental
        # tail, so each update replaces the whole view rather than appending.
        self.screen_view.set_server_screen(text)

    def get_standalone_min_size(self):
        return 480, 420
