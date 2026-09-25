"""Tests for the backend-neutral shared logic in ClaudeSessionBase: prompt/busy
detection, first-run auto-answers and their retry, stall detection, and sign-in
URL opening. Both ClaudePtyBridge (Windows) and ClaudeTmuxBridge (Linux) inherit
this behavior unchanged; backend-specific tests (spawn args, key encoding, I/O)
live in test_clClaudeBridge.py and test_clClaudeTmuxBridge.py."""
import os
import sys
import time
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clClaudeSession import ClaudeSessionBase


class _FakeSession(ClaudeSessionBase):
    """Minimal concrete backend: records auto-answer tokens instead of
    translating them into a real key encoding, which is backend-specific."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent_tokens = []
        self.resize_calls = []

    def _send_control_keys(self, token: str):
        self.sent_tokens.append(token)

    def _apply_resize(self, cols: int, rows: int):
        self.resize_calls.append((cols, rows))

    def send_keys(self, raw: str):
        pass

    def write(self, text: str):
        pass

    def is_alive(self) -> bool:
        return True

    def start(self):
        pass

    def stop(self):
        pass


def _session(on_stall=None):
    return _FakeSession(cwd=".", on_screen_update=MagicMock(), on_stall=on_stall)


class TestPromptAndBusyDetection:
    """The chat input box is a ❯ row between horizontal rules; menus reuse
    ❯ without the rules and must not read as ready for typed input."""

    RULE = "─" * 20

    def test_input_box_is_detected(self):
        rows = ["banner", self.RULE, "❯ ", self.RULE, "footer"]
        assert ClaudeSessionBase._prompt_row_visible(rows) is True

    def test_menu_selection_row_is_not_a_prompt(self):
        rows = ["Is this a project you trust?", "❯ No, exit", "  Yes, I trust this folder"]
        assert ClaudeSessionBase._prompt_row_visible(rows) is False

    def test_busy_flag_follows_the_interrupt_hint(self):
        session = _session()
        session.process_screen("working... esc to interrupt")
        assert session._busy is True

    def test_not_ready_while_busy_or_recently_active(self):
        session = _session()
        session._prompt_visible = True
        session._last_data_at = time.time() - 10
        assert session.is_ready() is True
        session._busy = True
        assert session.is_ready() is False
        session._busy = False
        session._last_data_at = time.time()
        assert session.is_ready() is False


class TestTrustPromptAutoAnswer:
    """First launch against a fresh config dir asks to trust the workspace
    via an arrow-key menu defaulting to 'No, exit' -- confirming it needs a
    down-arrow-then-enter token, not typed text, and must only fire once."""

    def test_sends_down_then_enter_token_on_trust_screen(self):
        session = _session()
        session._maybe_auto_answer("Is this a project you created or one you trust?")
        assert session.sent_tokens == ["DOWN ENTER"]

    def test_same_screen_is_only_answered_once(self):
        session = _session()
        session._maybe_auto_answer("Is this a project you created or one you trust?")
        session._maybe_auto_answer("Is this a project you created or one you trust?")
        assert session.sent_tokens == ["DOWN ENTER"]

    def test_ignores_unrelated_screens(self):
        session = _session()
        session._maybe_auto_answer("some other screen entirely")
        assert session.sent_tokens == []

    def test_plain_first_run_screens_get_enter_token(self):
        for screen in [
            "Choose the text style that looks best with your terminal",
            "Select login method:\n 1. Claude account with subscription",
            "Login successful. Press Enter to continue…",
        ]:
            session = _session()
            session._maybe_auto_answer(screen)
            assert session.sent_tokens == ["ENTER"]

    def test_back_to_back_enter_screens_are_each_answered(self):
        """Login-success and security-notes both say 'Press Enter to continue' --
        the second is a different screen and must not be mistaken for the first."""
        session = _session()
        session._maybe_auto_answer("Login successful. Press Enter to continue")
        session._maybe_auto_answer("Security notes: ...\nPress Enter to continue")
        assert session.sent_tokens == ["ENTER", "ENTER"]

    def test_a_dropped_enter_is_retried_then_gives_up(self):
        session = _session()
        session._maybe_auto_answer("Press Enter to continue")
        for _ in range(5):
            session._retry_dropped_answer(time.time() + 100)
            session._answered_at -= 100
        assert len(session.sent_tokens) == session.ANSWER_MAX_TRIES

    def test_no_retry_once_the_screen_moved_on(self):
        session = _session()
        session._maybe_auto_answer("Press Enter to continue")
        session._last_data_at = time.time() + 1  # new output arrived after the answer
        session._retry_dropped_answer(time.time() + 100)
        assert session.sent_tokens == ["ENTER"]

    def test_answer_state_clears_when_no_rule_matches(self):
        session = _session()
        session._maybe_auto_answer("Press Enter to continue")
        session._maybe_auto_answer("chat prompt")
        assert session._answered_screen is None


class TestSendControlKey:
    """send_control_key() is the public entry point a widget uses to press
    Esc/Shift+Tab/arrows -- it must go through the same backend-specific
    _send_control_keys() translation as the internal auto-answers."""

    def test_delegates_to_backend_specific_translation(self):
        session = _session()
        session.send_control_key("ESCAPE")
        assert session.sent_tokens == ["ESCAPE"]


class TestStallDetection:
    def test_silent_busy_session_reports_a_stall_once(self):
        on_stall = MagicMock()
        session = _session(on_stall)
        session._busy = True
        session._last_data_at = time.time() - 1000

        session._check_stall(time.time())
        session._check_stall(time.time())

        on_stall.assert_called_once()

    def test_silent_idle_session_is_not_a_stall(self):
        on_stall = MagicMock()
        session = _session(on_stall)
        session._busy = False
        session._last_data_at = time.time() - 1000

        session._check_stall(time.time())

        on_stall.assert_not_called()


class TestConfigDirAndContinue:
    def test_continue_false_with_no_prior_session(self, tmp_path):
        assert ClaudeSessionBase._should_continue(str(tmp_path)) is False

    def test_continue_true_once_projects_dir_exists(self, tmp_path):
        (tmp_path / "projects").mkdir()
        assert ClaudeSessionBase._should_continue(str(tmp_path)) is True

    def test_resolve_config_dir_creates_it_under_cwd(self, tmp_path):
        session = _session()
        session._cwd = str(tmp_path)
        config_dir = session._resolve_config_dir()
        assert config_dir == str(tmp_path / "data" / "claude_bridge_config")
        assert os.path.isdir(config_dir)

    def test_strip_claude_code_env_removes_only_matching_keys(self):
        env = {"CLAUDE_CODE_CHILD_SESSION": "1", "PATH": "/usr/bin"}
        stripped = ClaudeSessionBase._strip_claude_code_env(env)
        assert stripped == {"PATH": "/usr/bin"}


class TestResize:
    """resize() is the shared entry point the widget's own resize dispatches
    into (via clClaudeBridge.py); each backend implements _apply_resize()
    for its own child process/screen-emulator."""

    def test_constructor_cols_rows_override_the_class_defaults(self):
        session = _FakeSession(cwd=".", on_screen_update=MagicMock(), cols=80, rows=24)
        assert session.COLS == 80
        assert session.ROWS == 24

    def test_constructor_without_cols_rows_keeps_class_defaults(self):
        session = _session()
        assert session.COLS == ClaudeSessionBase.COLS
        assert session.ROWS == ClaudeSessionBase.ROWS

    def test_resize_updates_cols_rows_and_calls_apply_resize(self):
        session = _session()
        session.resize(100, 30)
        assert session.COLS == 100
        assert session.ROWS == 30
        assert session.resize_calls == [(100, 30)]
