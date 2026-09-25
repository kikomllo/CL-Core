from clTheme import Theme
from utils.clActionRouter import ActionRouter
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItemModel, QStandardItem
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QTextEdit, QSizePolicy, QCompleter
)
from ui.clZoomTextEdit import ZoomTextEdit

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


class ClaudeWidget(QWidget):
    """Mirrors the Claude terminal bridge's screen (jarvis/claude/screen) and
    lets you type into the live session (jarvis/claude/question, via the
    claude.ask action) -- the dashboard's window onto src/clClaudeBridge.py."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        self.screen_view = ZoomTextEdit()
        self.screen_view.setReadOnly(True)
        self.screen_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.screen_view.setStyleSheet(Theme.get_style("LogViewer"))
        layout.addWidget(self.screen_view, stretch=1)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Claude, or paste a sign-in code…")
        self.input.setFixedHeight(32)
        self.input.setStyleSheet(Theme.get_style("SettingsLineEdit"))
        self.input.setCompleter(_build_slash_completer(self.input))
        self.input.returnPressed.connect(self.submit_message)
        input_row.addWidget(self.input, stretch=1)

        self.send_btn = QPushButton("Send")
        self.send_btn.setFixedHeight(32)
        self.send_btn.setStyleSheet(Theme.get_style("AddButton"))
        self.send_btn.clicked.connect(self.submit_message)
        input_row.addWidget(self.send_btn)

        layout.addLayout(input_row)

    def submit_message(self):
        text = self.input.text().strip()
        if not text:
            return
        try:
            self.router.dispatch("claude.ask", text=text)
        except Exception:
            pass
        self.input.clear()

    def update_screen(self, text: str):
        # jarvis/claude/screen is a full-snapshot payload, not an incremental
        # tail, so each update replaces the whole view rather than appending.
        scrollbar = self.screen_view.verticalScrollBar()
        at_bottom = scrollbar.value() == scrollbar.maximum()
        self.screen_view.setPlainText(text)
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def get_standalone_min_size(self):
        return 480, 420
