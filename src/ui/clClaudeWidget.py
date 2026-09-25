import re
from clTheme import Theme
from utils.clActionRouter import ActionRouter
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QColor, QStandardItemModel, QStandardItem, QTextCursor, QKeySequence
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
    Qt.Key.Key_Left: "LEFT",
    Qt.Key.Key_Right: "RIGHT",
    Qt.Key.Key_Home: "HOME",
    Qt.Key.Key_End: "END",
    Qt.Key.Key_Delete: "DELETE",
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
        self._erased = 0  # chars backspaced off the server's own prompt-row text since then
        self._server_cursor = None  # (col, row) reported by the backend, when it can
        self._uncertain = False  # a cursor-moving key was sent; the real cursor is unknown until the next snapshot
        self._ops = []  # keystrokes predicted locally and not yet confirmed by a snapshot
        self._base_row = ""  # the prompt row of the last snapshot, which _ops are applied on top of
        self._selected = False  # Ctrl+A: the whole editable line is highlighted
        self._blink_on = True
        # If a local edit never produces a new server snapshot (the CLI ignored it, or the
        # screen ended up identical), the guess would otherwise sit on screen forever.
        self._reconcile_timer = QTimer(self)
        self._reconcile_timer.setSingleShot(True)
        self._reconcile_timer.timeout.connect(self._drop_local_edits)
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._blink_timer.start(530)
        self._last_dispatched_grid = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._dispatch_resize)
        self.completer = _build_slash_completer(self)
        self.completer.setWidget(self)
        self.completer.activated.connect(self._insert_completion)

    def focusNextPrevChild(self, next):
        # setTabChangesFocus(False) is ignored by QTextEdit while it's read-only, so Tab/Shift+Tab
        # would still hop between the surrounding buttons; refusing traversal delivers them
        # to keyPressEvent instead.
        return False

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

    RECONCILE_MS = 1500

    def set_server_screen(self, text: str, cursor=None):
        old_ops, old_base = self._ops, self._base_row
        self._server_screen = text
        self._server_cursor = tuple(cursor) if cursor else None
        self._uncertain = False
        rows = text.split("\n")
        target = self._find_prompt_row(rows)
        self._base_row = self._padded_row(rows[target], target)
        self._ops = []
        self._pending, self._erased = "", 0
        self._reconcile_timer.stop()
        if old_ops:
            # A snapshot can lag a few keystrokes behind what was typed. Keep the prediction while
            # the snapshot is just an earlier state of the same edits; a different one is the truth.
            done = None
            for k in range(len(old_ops), -1, -1):
                if self._simulate(old_base, old_ops[:k]).rstrip() == self._base_row.rstrip():
                    done = k
                    break
            if done is not None and done < len(old_ops):
                self._ops = old_ops[done:]
                self._recompute()
                self._reconcile_timer.start(self.RECONCILE_MS)
        self._render()

    def _simulate(self, base_row: str, ops) -> str:
        erased, pending = self._apply_ops(base_row, ops)
        return base_row[:len(base_row) - erased] + pending

    def _apply_ops(self, base_row: str, ops):
        avail = max(0, len(base_row) - self._editable_start(base_row))
        erased, pending = 0, ""
        for op in ops:
            if op == "\x7f":
                if pending:
                    pending = pending[:-1]
                elif erased < avail:
                    erased += 1
            elif op == "\x15":
                pending, erased = "", avail
            else:
                pending += op
        return erased, pending

    def _recompute(self):
        self._erased, self._pending = self._apply_ops(self._base_row, self._ops)

    def _record(self, *ops):
        self._ops.extend(ops)
        self._recompute()
        self._local_edit()

    def _predict_ok(self) -> bool:
        """Local echo/erase assumes the cursor is at the end of the line. Once it isn't (Ctrl+A,
        Left, Home...), the CLI edits mid-line and only its own next snapshot can be trusted."""
        if self._uncertain:
            return False
        if self._server_cursor:
            rows = self._server_screen.split("\n")
            target = self._find_prompt_row(rows)
            col, row = self._server_cursor
            if row == target and col < len(rows[target].rstrip()):
                return False
        return True

    def _drop_local_edits(self):
        self._ops = []
        self._pending = ""
        self._erased = 0
        self._render()

    def _local_edit(self):
        self._blink_on = True
        self._reconcile_timer.start(self.RECONCILE_MS)

    def _toggle_blink(self):
        self._blink_on = not self._blink_on
        self.viewport().update()

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

    _PROMPT_END = re.compile(r"[$#>%\u276f] ")

    @classmethod
    def _editable_start(cls, row: str) -> int:
        # Index where user-editable text begins, i.e. just past the last prompt marker. Falls back
        # to the whole row (nothing erasable locally) when no marker is recognizable.
        matches = list(cls._PROMPT_END.finditer(row))
        return matches[-1].end() if matches else len(row)

    @staticmethod
    def _with_prompt_padding(row: str) -> str:
        # The backend trims trailing spaces, so an empty prompt arrives as "❯" while the CLI's
        # real input starts one column later -- local text must never start inside that gap.
        return row + " " if row and row[-1] in "\u276f$#%" else row

    def _padded_row(self, row: str, row_index: int) -> str:
        # Trailing spaces are trimmed from every published row, but the backend's cursor still
        # sits after them -- restore them so local text lands where the real cursor is.
        if self._server_cursor and self._server_cursor[1] == row_index and self._server_cursor[0] > len(row):
            return row.ljust(self._server_cursor[0])
        return self._with_prompt_padding(row)

    def _visible_row(self, row: str, row_index: int) -> str:
        row = self._padded_row(row, row_index)
        start = self._editable_start(row)
        self._erased = min(self._erased, max(0, len(row) - start))
        return row[:len(row) - self._erased] + self._pending

    def _cursor_cell(self):
        """(row, col) to draw the cursor at: the end of the predicted line while local edits are
        pending, otherwise where the backend says the real cursor is."""
        rows = self._server_screen.split("\n")
        target = self._find_prompt_row(rows)
        if self._pending or self._erased or self._server_cursor is None:
            return target, len(self._visible_row(rows[target], target))
        col, row = self._server_cursor
        return min(row, len(rows) - 1), col

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._blink_on and self.hasFocus():
            return
        row, col = self._cursor_cell()
        block = self.document().findBlockByNumber(row)
        if not block.isValid():
            return
        shown = min(col, max(0, block.length() - 1))
        tc = QTextCursor(block)
        tc.setPosition(block.position() + shown)
        rect = self.cursorRect(tc)
        cell_w = self.fontMetrics().horizontalAdvance(" ")
        x = rect.left() + (col - shown) * cell_w
        painter = QPainter(self.viewport())
        color = QColor(255, 180, 0, 190)
        if self.hasFocus():
            painter.fillRect(x, rect.top(), cell_w, rect.height(), color)
        else:
            painter.setPen(color)
            painter.drawRect(x, rect.top(), cell_w - 1, rect.height() - 1)
        painter.end()

    def _apply_selection_highlight(self, row: str, row_index: int):
        if not self._selected:
            self.setExtraSelections([])
            return
        block = self.document().findBlockByNumber(row_index)
        if not block.isValid():
            return
        tc = QTextCursor(block)
        tc.setPosition(block.position() + min(self._editable_start(row), len(row)))
        tc.setPosition(block.position() + len(row), QTextCursor.MoveMode.KeepAnchor)
        sel = QTextEdit.ExtraSelection()
        sel.cursor = tc
        sel.format.setBackground(QColor(255, 150, 0, 110))
        self.setExtraSelections([sel])

    def _render(self):
        scrollbar = self.verticalScrollBar()
        at_bottom = scrollbar.value() == scrollbar.maximum()
        preserved_value = scrollbar.value()
        rows = self._server_screen.split("\n")
        target_row = self._find_prompt_row(rows)
        rows[target_row] = self._visible_row(rows[target_row], target_row)
        self.setPlainText("\n".join(rows))
        self._apply_selection_highlight(rows[target_row], target_row)
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

    def _consume_selection(self) -> bool:
        """Ctrl+A highlighted the line: the next edit replaces it. End-then-kill-line clears the
        CLI's line wherever its cursor is. Returns True if there was a selection to clear."""
        if not self._selected:
            return False
        self._selected = False
        self._record("\x15")
        self._render()
        self._on_keys("\x05\x15")
        return True

    def _insert(self, char: str):
        self._consume_selection()
        if not self._predict_ok():
            self._on_keys(char)
            return
        self._record(char)
        self._render()
        self._on_keys(char)

    def _backspace(self):
        # With nothing pending the text being erased is already in the CLI's own input
        # (typed earlier, or left there), so there's nothing to predict locally -- just forward it.
        if self._consume_selection():
            return
        if not self._predict_ok():
            self._on_keys("\x7f")
            return
        self._record("\x7f")
        self._render()
        self._on_keys("\x7f")

    def _paste(self):
        text = QApplication.clipboard().text()
        if not text:
            return
        self._consume_selection()
        if not self._predict_ok():
            self._on_keys(text)
            return
        self._record(*text)
        self._render()
        self._on_keys(text)

    def _submit_line(self):
        # Optimistic reset: don't wait for the round trip to clear the typed
        # line -- the next real screen snapshot will overwrite this anyway.
        self._on_control_key("ENTER")
        self._pending = ""
        self._erased = 0
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

    @staticmethod
    def _is_ctrl_letter(event) -> bool:
        # Ctrl+Z is left out on purpose: the CLI treats it as "suspend", which strands the session.
        key = event.key()
        return (bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value and key != Qt.Key.Key_Z.value)

    def keyPressEvent(self, event):
        if self.completer.popup().isVisible() and event.key() in self._POPUP_OWNED_KEYS:
            event.ignore()
            return

        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key == Qt.Key.Key_A:
            self._selected = True
            self._render()
            event.accept()
            return
        if self._selected and ctrl and key == Qt.Key.Key_C:
            rows = self._server_screen.split("\n")
            target = self._find_prompt_row(rows)
            row = self._visible_row(rows[target], target)
            QApplication.clipboard().setText(row[self._editable_start(row):])
            event.accept()
            return
        if self._selected and key == Qt.Key.Key_Delete:
            self._consume_selection()
            event.accept()
            return
        keeps_selection = key == Qt.Key.Key_Backspace or event.matches(QKeySequence.StandardKey.Paste) or (
            not ctrl and bool(event.text()) and event.text().isprintable())
        if self._selected and not keeps_selection:
            self._selected = False
            self._render()
        token = _KEYBOARD_CONTROL_KEYS.get(key)
        if token is not None:
            if token in ("LEFT", "RIGHT", "HOME", "END", "DELETE"):
                self._uncertain = True
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
        if self._is_ctrl_letter(event):
            # Ctrl+C copies a selection like any terminal; otherwise it's the interrupt byte.
            if key == Qt.Key.Key_C and self.textCursor().hasSelection():
                self.copy()
            else:
                if key not in (Qt.Key.Key_C, Qt.Key.Key_U):
                    self._uncertain = True
                if key in (Qt.Key.Key_C, Qt.Key.Key_U):
                    self._record("\x15")
                    self._render()
                self._on_keys(chr(key - Qt.Key.Key_A.value + 1))
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

    def update_screen(self, text: str, cursor=None):
        # jarvis/claude/screen is a full-snapshot payload, not an incremental
        # tail, so each update replaces the whole view rather than appending.
        self.screen_view.set_server_screen(text, cursor)

    def get_standalone_min_size(self):
        return 480, 420
