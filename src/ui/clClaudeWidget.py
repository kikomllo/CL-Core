import re
import time
from clTheme import Theme
from utils.clActionRouter import ActionRouter
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (
    QPainter, QColor, QStandardItemModel, QStandardItem, QTextCursor, QKeySequence, QTextCharFormat
)
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
    Qt.Key.Key_PageUp: "PAGEUP",
    Qt.Key.Key_PageDown: "PAGEDOWN",
}

_ANSI_SGR = re.compile(r"\x1b\[([0-9;:]*)m")
_BASE16 = [
    (0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16), (36, 114, 200), (188, 63, 188),
    (17, 168, 205), (204, 204, 204), (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67),
    (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255),
]


# The terminal keeps the dashboard's orange-only scheme: every ANSI colour is reduced to how bright
# it is and drawn on this ramp instead (dim orange -> the theme's primary orange).
_ORANGE_DARK = (130, 65, 0)
_ORANGE_BRIGHT = (255, 170, 0)  # Theme.C_PRIMARY
_ORANGE_BG = (255, 140, 0)


def _luminance(color: QColor) -> float:
    return (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255


def _brightness(color: QColor) -> float:
    # Greys go by luminance (so a shell's grey suggestion stays dim), saturated colours by their
    # strongest channel (ANSI blue is perceptually dark but must stay readable, e.g. directories).
    return max(_luminance(color), color.hsvSaturationF() * color.valueF())


def orange_shade(color: QColor) -> QColor:
    v = _brightness(color)
    return QColor(*(round(d + (b - d) * v) for d, b in zip(_ORANGE_DARK, _ORANGE_BRIGHT)))


def orange_background(color: QColor) -> QColor:
    return QColor(*_ORANGE_BG, round(30 + 90 * _luminance(color)))


def _xterm_color(n: int) -> QColor:
    if n < 16:
        return QColor(*_BASE16[n])
    if n < 232:
        n -= 16
        level = lambda v: 0 if v == 0 else 55 + 40 * v
        return QColor(level(n // 36), level((n // 6) % 6), level(n % 6))
    g = 8 + 10 * (n - 232)
    return QColor(g, g, g)


class _AnsiStyle:
    def __init__(self):
        self.reset()

    def reset(self):
        self.fg = self.bg = None
        self.bold = self.dim = self.italic = self.underline = self.reverse = False

    def apply(self, params: str):
        codes = [int(p) if p.isdigit() else 0 for p in re.split(r"[;:]", params)] if params else [0]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                self.reset()
            elif c in (1, 2, 3, 4, 7):
                setattr(self, {1: "bold", 2: "dim", 3: "italic", 4: "underline", 7: "reverse"}[c], True)
            elif c in (22, 23, 24, 27):
                if c == 22:
                    self.bold = self.dim = False
                else:
                    setattr(self, {23: "italic", 24: "underline", 27: "reverse"}[c], False)
            elif 30 <= c <= 37:
                self.fg = _xterm_color(c - 30)
            elif 90 <= c <= 97:
                self.fg = _xterm_color(c - 90 + 8)
            elif 40 <= c <= 47:
                self.bg = _xterm_color(c - 40)
            elif 100 <= c <= 107:
                self.bg = _xterm_color(c - 100 + 8)
            elif c == 39:
                self.fg = None
            elif c == 49:
                self.bg = None
            elif c in (38, 48) and i + 1 < len(codes):
                target = "fg" if c == 38 else "bg"
                if codes[i + 1] == 5 and i + 2 < len(codes):
                    setattr(self, target, _xterm_color(codes[i + 2] & 255))
                    i += 2
                elif codes[i + 1] == 2 and i + 4 < len(codes):
                    setattr(self, target, QColor(*(v & 255 for v in codes[i + 2:i + 5])))
                    i += 4
            i += 1

    def char_format(self) -> QTextCharFormat:
        primary, ink = QColor(*_ORANGE_BRIGHT), QColor(20, 10, 0)
        fmt = QTextCharFormat()
        if self.reverse:
            # A solid orange block with dark text, whatever the original colours were.
            fmt.setForeground(ink)
            fmt.setBackground(primary)
        else:
            if self.fg is not None:
                fmt.setForeground(orange_shade(self.fg))
            elif self.dim:
                fmt.setForeground(orange_shade(QColor(110, 110, 110)))
            if self.dim and self.fg is not None:
                fmt.setForeground(orange_shade(QColor(self.fg).darker(180)))
            if self.bg is not None:
                fmt.setBackground(orange_background(self.bg))
        if self.bold:
            fmt.setFontWeight(700)
        fmt.setFontItalic(self.italic)
        fmt.setFontUnderline(self.underline)
        return fmt


def ansi_spans(ansi_text: str):
    """[[(text, QTextCharFormat), ...] per row] for text carrying SGR escapes (colour/attributes)."""
    style, rows = _AnsiStyle(), []
    for line in ansi_text.split("\n"):
        spans, pos = [], 0
        for m in _ANSI_SGR.finditer(line):
            if m.start() > pos:
                spans.append((line[pos:m.start()], style.char_format()))
            style.apply(m.group(1))
            pos = m.end()
        if pos < len(line):
            spans.append((line[pos:], style.char_format()))
        rows.append(spans)
    return rows

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
    ("/terminal", "Switch this widget to a plain shell"),
    ("/claude", "Switch back to the Claude session"),
]

MODE_SWITCH_COMMANDS = ("/terminal", "/claude")


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

    # After the line is synced/submitted, snapshots may still show the old CLI line for a moment;
    # don't re-adopt it in that window. Adopting after Tab/Up/Down waits at most this long.
    HOLD_S = 0.8
    ADOPT_S = 1.5

    def __init__(self, on_keys, on_control_key, on_resize, parent=None, local_line_editing=False, on_text=None):
        super().__init__(parent)
        self._on_text = on_text  # whole-line messages, e.g. the /terminal and /claude mode switches
        self._sel_anchor = None  # local editing: other end of the selection (the cursor is _lpos)
        # Claude's framed input box is edited entirely locally (text + cursor) and only written to
        # the CLI on Enter or when a key needs the CLI's own copy of the line; shells and menus
        # keep the live-forwarding path below.
        self._local_enabled = local_line_editing
        self._lbuf = ""
        self._lpos = 0
        self._lsynced = ""  # what the CLI's own input line is known to contain
        self._hold_until = 0.0
        self._adopt = False
        self._adopt_from = ""
        self._adopt_deadline = 0.0
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
        self._server_ansi = None  # the same screen with colour/attribute escapes, when the backend has them
        self._server_mode = None  # "claude", "terminal" (readline-style shell) or "terminal-basic"
        self._server_idle = None  # terminal mode: the foreground process is the shell (at its prompt)
        # Column where the shell's editable text begins: the cursor column at a fresh, idle prompt
        # (i.e. the prompt's own length). Everything before that index is never editable locally.
        self._shell_start = None
        self._shell_touched = False  # the user has edited/moved on this prompt, so the start is fixed
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._refresh_prompt)
        # (kind, row, start_col, line) of the input line being edited locally, or None.
        self._prompt = None
        self._server_cursor = None  # (col, row) reported by the backend, when it can
        # Only to recognise "/claude" / "/terminal" typed live into a shell on Enter; never displayed.
        self._shadow = ""
        self._selected = False
        self._blink_on = True
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

    def set_server_screen(self, text: str, cursor=None, ansi=None, mode=None, idle=None):
        self._server_screen = text
        self._server_ansi = ansi
        if mode != self._server_mode:
            self._shell_start, self._shell_touched = None, False
        self._server_mode = mode
        self._server_idle = idle
        self._server_cursor = tuple(cursor) if cursor else None
        self._refresh_prompt()

    def _refresh_prompt(self):
        self._update_shell_start()
        self._prompt = self._compute_prompt()
        if self._prompt:
            self._adopt_snapshot()
        self._render()

    def _touch(self):
        # Fixes the measured prompt start -- but only for a shell prompt; typing into Claude's box
        # (or anywhere else) must not leak into how the next shell prompt is measured.
        if self._local_kind() == "shell":
            self._shell_touched = True

    def _hold(self):
        self._hold_until = time.monotonic() + self.HOLD_S
        # Nothing else may arrive once the hold is over (a fast command already redrew the prompt),
        # so look at the latest screen again then.
        self._hold_timer.start(int(self.HOLD_S * 1000) + 50)

    def _shell_ready(self) -> bool:
        return (self._local_enabled and self._server_mode == "terminal"
                and self._server_idle is True and self._server_cursor is not None)

    def _update_shell_start(self):
        if not self._shell_ready():
            self._shell_start, self._shell_touched = None, False
            return
        if self._shell_touched or time.monotonic() < self._hold_until:
            return
        col = self._server_cursor[0]
        self._shell_start = col if col > 0 else None

    def _compute_prompt(self):
        if not self._local_enabled:
            return None
        rows = self._server_screen.split("\n")
        framed = self._framed_prompt_row(rows)
        if framed is not None:
            row = self._padded_row(rows[framed], framed)
            start = self._local_start(row)
            return ("claude", framed, start, row[start:].rstrip())
        if self._shell_ready() and self._shell_start is not None:
            col, r = self._server_cursor
            start = self._shell_start
            if 0 <= r < len(rows) and col >= start:
                # Anything after the cursor is the shell's own suggestion, not typed text.
                return ("shell", r, start, rows[r].ljust(col)[start:col])
        return None

    def _snapshot_line(self):
        _kind, row, start, line = self._prompt
        return line, row, start

    def _set_line(self, line: str, pos=None):
        self._lbuf = self._lsynced = line
        self._lpos = len(line) if pos is None else max(0, min(pos, len(line)))
        self._selected, self._sel_anchor = False, None

    def _adopt_snapshot(self):
        now = time.monotonic()
        line, target, start = self._snapshot_line()
        if self._adopt:
            if now > self._adopt_deadline:
                self._adopt = False
            elif line != self._adopt_from.rstrip():
                self._adopt = False
                self._set_line(line)
                return
        if self._lbuf == self._lsynced and now >= self._hold_until and line != self._lbuf.rstrip():
            pos = None
            if self._server_cursor and self._server_cursor[1] == target:
                pos = self._server_cursor[0] - start
            self._set_line(line, pos)

    def _toggle_blink(self):
        self._blink_on = not self._blink_on
        self.viewport().update()

    @staticmethod
    def _framed_prompt_row(rows):
        for i in range(1, len(rows) - 1):
            if (rows[i].lstrip().startswith("\u276f")
                    and rows[i - 1].startswith("\u2500") and rows[i + 1].startswith("\u2500")):
                return i
        return None

    def _local_active(self) -> bool:
        return self._prompt is not None

    def _local_kind(self):
        return self._prompt[0] if self._prompt else None

    @staticmethod
    def _local_start(row: str) -> int:
        i = row.find("\u276f")
        return i + 2 if i >= 0 else len(row)

    def _input_start(self, row: str) -> int:
        return self._prompt[2] if self._local_active() else self._editable_start(row)

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
        start = self._prompt[2]
        return self._padded_row(row, row_index).ljust(start)[:start] + self._lbuf

    def _cursor_cell(self):
        """(row, col) to draw the cursor at: the local line's cursor inside Claude's input box,
        otherwise wherever the backend says the real cursor is."""
        rows = self._server_screen.split("\n")
        target = self._find_prompt_row(rows)
        if self._local_active():
            return self._prompt[1], self._prompt[2] + self._lpos
        if self._server_cursor is None:
            return target, len(rows[target])
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

    def _sel_range(self):
        if self._sel_anchor is None or self._sel_anchor == self._lpos:
            return None
        return min(self._sel_anchor, self._lpos), max(self._sel_anchor, self._lpos)

    def _apply_selection_highlight(self, row: str, row_index: int):
        local = self._local_active()
        rng = self._sel_range() if local else ((0, len(row) - self._input_start(row)) if self._selected else None)
        if rng is None:
            self.setExtraSelections([])
            return
        block = self.document().findBlockByNumber(row_index)
        if not block.isValid():
            return
        start = min(self._input_start(row), len(row))
        tc = QTextCursor(block)
        tc.setPosition(block.position() + min(start + rng[0], len(row)))
        tc.setPosition(block.position() + min(start + rng[1], len(row)), QTextCursor.MoveMode.KeepAnchor)
        sel = QTextEdit.ExtraSelection()
        sel.cursor = tc
        sel.format.setBackground(QColor(255, 150, 0, 110))
        self.setExtraSelections([sel])

    def _suggestion_cut(self):
        """(row, col) after which nothing is drawn: at an idle shell prompt everything beyond the
        cursor is the shell's own autosuggestion, which the terminal deliberately never shows."""
        if self._server_mode == "terminal" and self._server_idle is True and self._server_cursor:
            return self._server_cursor[1], self._server_cursor[0]
        return None

    def _set_styled_document(self, ansi_text: str, override=None):
        """override=(row, start_col, text): that row keeps its styled prompt (first start_col
        columns) and shows `text` after it in the default style -- the locally edited line."""
        self.clear()
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        for i, spans in enumerate(ansi_spans(ansi_text)):
            if override and i == override[0]:
                kept, used = [], 0
                for text, fmt in spans:
                    if used >= override[1]:
                        break
                    piece = text[:override[1] - used]
                    kept.append((piece, fmt))
                    used += len(piece)
                if used < override[1]:
                    kept.append((" " * (override[1] - used), QTextCharFormat()))
                spans = kept + [(override[2], QTextCharFormat())]
            if i:
                cursor.insertBlock()
            for text, fmt in spans:
                cursor.insertText(text, fmt)
        cursor.endEditBlock()

    def _render(self):
        scrollbar = self.verticalScrollBar()
        at_bottom = scrollbar.value() == scrollbar.maximum()
        preserved_value = scrollbar.value()
        rows = self._server_screen.split("\n")
        target_row = self._prompt[1] if self._local_active() else self._find_prompt_row(rows)
        if self._local_active():
            rows[target_row] = self._visible_row(rows[target_row], target_row)
            if self._server_ansi and self._local_kind() == "shell":
                self._set_styled_document(self._server_ansi, (target_row, self._prompt[2], self._lbuf))
            else:
                self.setPlainText("\n".join(rows))
        else:
            cut = self._suggestion_cut()
            if self._server_ansi:
                self._set_styled_document(self._server_ansi, (cut[0], cut[1], "") if cut else None)
            else:
                if cut and cut[0] < len(rows):
                    rows[cut[0]] = rows[cut[0]][:cut[1]]
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
        if self._local_kind() != "claude":
            self.completer.popup().hide()
            return
        line = self._lbuf
        # An exact match has nothing left to complete; keeping the popup up would swallow Enter
        # (it "accepts" the identical text) so the line could never be submitted.
        exact = any(line.lower() == command.lower() for command, _ in SLASH_COMMANDS)
        if line.startswith("/") and " " not in line and not exact:
            self.completer.setCompletionPrefix(line)
            if self.completer.completionCount() > 0:
                popup = self.completer.popup()
                popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
                rect = self.cursorRect()
                rect.setWidth(max(220, popup.sizeHintForColumn(0) + 2 * popup.frameWidth() + 24))
                self.completer.complete(rect)
                return
        self.completer.popup().hide()

    def _line_edit(self, start: int, end: int, text: str):
        self._touch()
        self._lbuf = self._lbuf[:start] + text + self._lbuf[end:]
        self._lpos = start + len(text)
        self._selected, self._sel_anchor = False, None
        self._blink_on = True
        self._render()

    def _local_span(self):
        return self._sel_range() or (self._lpos, self._lpos)

    def _local_insert(self, text: str):
        start, end = self._local_span()
        self._line_edit(start, end, text)

    def _local_backspace(self):
        rng = self._sel_range()
        if rng:
            self._line_edit(*rng, "")
        elif self._lpos > 0:
            self._line_edit(self._lpos - 1, self._lpos, "")

    def _word_left(self, pos: int) -> int:
        while pos > 0 and self._lbuf[pos - 1].isspace():
            pos -= 1
        while pos > 0 and not self._lbuf[pos - 1].isspace():
            pos -= 1
        return pos

    def _word_right(self, pos: int) -> int:
        n = len(self._lbuf)
        while pos < n and not self._lbuf[pos].isspace():
            pos += 1
        while pos < n and self._lbuf[pos].isspace():
            pos += 1
        return pos

    def _move_cursor(self, pos: int, extend: bool):
        self._touch()
        pos = max(0, min(pos, len(self._lbuf)))
        if extend:
            if self._sel_anchor is None:
                self._sel_anchor = self._lpos
        else:
            self._sel_anchor = None
        self._lpos = pos
        self._selected = self._sel_range() is not None

    def _sync_line(self):
        """Overwrite the CLI's line with the local one (end-of-line, kill-line, then the text) so
        the CLI can never disagree with what's on screen."""
        if self._lbuf != self._lsynced:
            self._on_keys("\x05\x15" + self._lbuf)
            self._lsynced = self._lbuf
        self._hold()

    def _forward_after_sync(self, send):
        # For keys whose meaning depends on the CLI's own copy of the line (Tab completion,
        # history, most Ctrl shortcuts): sync first, then re-adopt whatever the CLI does with it.
        self._touch()
        self._sync_line()
        send()
        self._adopt, self._adopt_from = True, self._lsynced
        self._adopt_deadline = time.monotonic() + self.ADOPT_S
        self._selected, self._sel_anchor = False, None
        self._render()

    def _local_enter(self):
        cmd = self._lbuf.strip().lower()
        if cmd in MODE_SWITCH_COMMANDS and self._on_text:
            # A widget-level mode switch, not something to type into the CLI.
            self._on_text(cmd)
            self._lbuf, self._lpos = "", 0
            self._selected, self._sel_anchor = False, None
            self._hold()
            self._render()
            return
        self._sync_line()
        self._on_control_key("ENTER")
        self._lbuf = self._lsynced = ""
        self._lpos = 0
        self._selected, self._sel_anchor = False, None
        # The next prompt gets measured afresh once the hold is over.
        self._shell_start, self._shell_touched = None, False
        self._refresh_prompt()

    def _local_key(self, event) -> bool:
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        n = len(self._lbuf)
        rng = self._sel_range()
        word = ctrl or alt
        K = Qt.Key
        if key == K.Key_Backspace and word:
            self._line_edit(*(rng or (self._word_left(self._lpos), self._lpos)), "")
        elif key == K.Key_Delete and word:
            self._line_edit(*(rng or (self._lpos, self._word_right(self._lpos))), "")
        elif key == K.Key_Left:
            if rng and not shift and not word:
                self._move_cursor(rng[0], False)
            else:
                self._move_cursor(self._word_left(self._lpos) if word else self._lpos - 1, shift)
        elif key == K.Key_Right:
            if rng and not shift and not word:
                self._move_cursor(rng[1], False)
            else:
                self._move_cursor(self._word_right(self._lpos) if word else self._lpos + 1, shift)
        elif key == K.Key_Home:
            self._move_cursor(0, shift)
        elif key == K.Key_End:
            self._move_cursor(n, shift)
        elif ctrl and key == K.Key_A:
            if self._local_kind() == "shell":
                self._move_cursor(0, False)  # readline: beginning of line
            else:
                self._sel_anchor, self._lpos = (0 if n else None), n
                self._selected = self._sel_range() is not None
        elif ctrl and key == K.Key_C:
            if rng:
                QApplication.clipboard().setText(self._lbuf[rng[0]:rng[1]])
            else:
                self._on_keys("\x03")
                self._lbuf = self._lsynced = ""
                self._lpos = 0
                self._hold()
        elif ctrl and key == K.Key_X:
            if rng:
                QApplication.clipboard().setText(self._lbuf[rng[0]:rng[1]])
                self._line_edit(*rng, "")
        elif ctrl and event.matches(QKeySequence.StandardKey.Paste):
            self._paste()
        elif ctrl and key == K.Key_U:
            self._line_edit(0, self._lpos, "")
        elif ctrl and key == K.Key_K:
            self._line_edit(self._lpos, n, "")
        elif ctrl and key == K.Key_W:
            self._line_edit(self._word_left(self._lpos), self._lpos, "")
        elif ctrl and key == K.Key_E:
            self._move_cursor(n, False)
        elif ctrl and self._is_ctrl_letter(event):
            byte = chr(key - K.Key_A.value + 1)
            self._forward_after_sync(lambda: self._on_keys(byte))
        elif ctrl:
            return False
        elif key == K.Key_Delete:
            if rng:
                self._line_edit(*rng, "")
            elif self._lpos < n:
                self._line_edit(self._lpos, self._lpos + 1, "")
        elif key == K.Key_Backspace:
            self._local_backspace()
        elif key in (K.Key_Return, K.Key_Enter):
            self._local_enter()
        elif key == K.Key_Escape:
            self._on_control_key("ESCAPE")
        elif key == K.Key_Backtab or (key == K.Key_Tab and shift):
            self._on_control_key("SHIFT_TAB")
        elif key == K.Key_Tab:
            self._forward_after_sync(lambda: self._on_keys("\t"))
        elif key in (K.Key_Up, K.Key_Down):
            token = "UP" if key == K.Key_Up else "DOWN"
            self._forward_after_sync(lambda: self._on_control_key(token))
        elif event.matches(QKeySequence.StandardKey.Paste):
            self._paste()
        elif event.text() and event.text().isprintable():
            self._insert(event.text())
        else:
            return False
        self._blink_on = True
        self._render()
        return True

    def _insert(self, char: str):
        if self._local_active():
            self._local_insert(char)
            return
        self._shadow += char
        self._on_keys(char)

    def _backspace(self):
        if self._local_active():
            self._local_backspace()
            return
        self._shadow = self._shadow[:-1]
        self._on_keys("\x7f")

    def _paste(self):
        text = QApplication.clipboard().text()
        if not text:
            return
        if self._local_active():
            self._local_insert(text.replace("\r\n", " ").replace("\n", " ").replace("\r", " "))
            return
        self._shadow += text
        self._on_keys(text)

    def _submit_line(self):
        if self._local_active():
            self._local_enter()
            return
        cmd = self._shadow.strip().lower()
        if self._on_text and cmd in MODE_SWITCH_COMMANDS:
            # Already typed into the shell live -- erase it, then switch instead of running it.
            self._on_keys("\x7f" * len(self._shadow))
            self._on_text(cmd)
        else:
            self._on_control_key("ENTER")
        self._shadow = ""

    def _insert_completion(self, text: str):
        if self._local_active() and text.startswith(self._lbuf):
            self._lbuf, self._lpos, self._selected, self._sel_anchor = text, len(text), False, None
            self.completer.popup().hide()
            self._render()

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

        if self._local_active() and self._local_key(event):
            event.accept()
            return

        # Everything else is a pure mirror: each key goes to the real shell/CLI exactly as a
        # terminal would send it, and the screen shows only what the backend reports.
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        token = _KEYBOARD_CONTROL_KEYS.get(key)
        if token is not None:
            if ctrl and token in ("LEFT", "RIGHT"):
                token = "CTRL_" + token
            self._shadow = ""
            self._on_control_key(token)
        elif key == Qt.Key.Key_Backtab or (key == Qt.Key.Key_Tab and shift):
            self._on_control_key("SHIFT_TAB")
        elif key == Qt.Key.Key_Tab:
            self._shadow = ""
            self._on_keys("\t")
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._submit_line()
        elif key == Qt.Key.Key_Backspace:
            if ctrl:
                self._shadow = ""
                self._on_keys("\x17")  # readline's delete-previous-word
            else:
                self._backspace()
        elif event.matches(QKeySequence.StandardKey.Paste):
            self._paste()
        elif self._is_ctrl_letter(event):
            if key == Qt.Key.Key_C and self.textCursor().hasSelection():
                self.copy()  # like any terminal: copy a mouse selection, otherwise interrupt
            else:
                self._shadow = ""
                self._on_keys(chr(key - Qt.Key.Key_A.value + 1))
        elif event.text() and event.text().isprintable():
            self._insert(event.text())
        else:
            event.ignore()
            return
        event.accept()


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

        self.screen_view = ClaudeTerminalView(
            self._send_keys, self._send_control_key, self._send_resize,
            local_line_editing=True, on_text=self._send_text)
        self.screen_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.screen_view.setStyleSheet(Theme.get_style("LogViewer"))
        layout.addWidget(self.screen_view, stretch=1)

    def _send_control_key(self, token: str):
        try:
            self.router.dispatch("claude.control_key", control_key=token)
        except Exception:
            pass

    def _send_text(self, text: str):
        try:
            self.router.dispatch("claude.ask", text=text)
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

    def update_from_payload(self, payload: dict):
        self.update_screen(payload.get("text", ""), payload.get("cursor"), payload.get("ansi"),
                           payload.get("mode"), payload.get("idle"))

    def update_screen(self, text: str, cursor=None, ansi=None, mode=None, idle=None):
        # jarvis/claude/screen is a full-snapshot payload, not an incremental
        # tail, so each update replaces the whole view rather than appending.
        self.screen_view.set_server_screen(text, cursor, ansi, mode, idle)

    def get_standalone_min_size(self):
        return 480, 420
