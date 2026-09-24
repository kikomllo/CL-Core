import pytest
import os
import sys
import json
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from clClaudeBridge import ClaudeBridgeService

_real_path_exists = os.path.exists


def _exists_with_credentials_present(path):
    if "claude_bridge_config" in str(path):
        return True
    return _real_path_exists(path)


@pytest.fixture(autouse=True)
def mock_credentials_present(mocker):
    """run() checks for data/claude_bridge_config/.credentials.json before
    doing anything else, and blocks forever if it's missing (mirrors
    clSpotify.py's precedent) -- tests must never touch the real filesystem
    state or they'd hang. os.path.exists is a shared singleton, so a plain
    return_value=True mock would also affect unrelated os.path.exists calls
    elsewhere (e.g. ClaudePtyBridge's own binary-resolution check)."""
    mocker.patch("clClaudeBridge.os.path.exists", side_effect=_exists_with_credentials_present)


@pytest.fixture
def service():
    return ClaudeBridgeService()


class TestClaudeBridgeRouting:
    """The bridge's job is just relaying jarvis/claude/question into the
    live pty session -- these tests mock the pty layer out entirely so
    they run without a real claude CLI or Windows ConPTY."""

    @pytest.mark.asyncio
    async def test_question_text_gets_injected_into_pty(self, service, mock_mqtt, message_stream, mocker):
        mock_bridge = MagicMock()
        mock_bridge.is_alive.return_value = True
        service.bridge = mock_bridge
        mocker.patch.object(service, "_ensure_pty_started")

        mock_mqtt.messages = message_stream([
            ("jarvis/claude/question", json.dumps({"silent": True, "text": "is this working"})),
        ])

        await service.run()

        mock_bridge.write.assert_called_once_with("is this working")

    @pytest.mark.asyncio
    async def test_raw_keys_are_sent_via_send_keys_not_write(self, service, mock_mqtt, message_stream, mocker):
        """The trust-menu and similar confirm screens need arrow keys, not
        typed text -- 'keys' payloads must route to send_keys(), not write()."""
        mock_bridge = MagicMock()
        mock_bridge.is_alive.return_value = True
        service.bridge = mock_bridge
        mocker.patch.object(service, "_ensure_pty_started")

        mock_mqtt.messages = message_stream([
            ("jarvis/claude/question", json.dumps({"keys": "\x1bOB\r"})),
        ])

        await service.run()

        mock_bridge.send_keys.assert_called_once_with("\x1bOB\r")
        mock_bridge.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_text_is_ignored(self, service, mock_mqtt, message_stream, mocker):
        mock_bridge = MagicMock()
        mock_bridge.is_alive.return_value = True
        service.bridge = mock_bridge
        mocker.patch.object(service, "_ensure_pty_started")

        mock_mqtt.messages = message_stream([
            ("jarvis/claude/question", json.dumps({"text": "   "})),
        ])

        await service.run()

        mock_bridge.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_json_does_not_crash(self, service, mock_mqtt, message_stream, mocker):
        mock_bridge = MagicMock()
        mock_bridge.is_alive.return_value = True
        service.bridge = mock_bridge
        mocker.patch.object(service, "_ensure_pty_started")

        mock_mqtt.messages = message_stream([
            ("jarvis/claude/question", "{ broken json"),
        ])

        await service.run()

        mock_bridge.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_pty_restart_attempted_before_each_question(self, service, mock_mqtt, message_stream, mocker):
        """If the live session died between questions, the next question
        must try to restart it (--continue recovers its history) rather
        than silently failing to deliver."""
        mock_bridge = MagicMock()
        mock_bridge.is_alive.return_value = True
        service.bridge = mock_bridge
        ensure_mock = mocker.patch.object(service, "_ensure_pty_started")

        mock_mqtt.messages = message_stream([
            ("jarvis/claude/question", json.dumps({"text": "first"})),
            ("jarvis/claude/question", json.dumps({"text": "second"})),
        ])

        await service.run()

        assert ensure_mock.call_count >= 2

    @pytest.mark.asyncio
    async def test_write_failure_does_not_crash_the_service(self, service, mock_mqtt, message_stream, mocker):
        mock_bridge = MagicMock()
        mock_bridge.is_alive.return_value = True
        mock_bridge.write.side_effect = RuntimeError("PTY session is not alive")
        service.bridge = mock_bridge
        mocker.patch.object(service, "_ensure_pty_started")

        mock_mqtt.messages = message_stream([
            ("jarvis/claude/question", json.dumps({"text": "hello"})),
        ])

        await service.run()  # should not raise


class TestMissingCredentials:
    """Mirrors clSpotify.py's precedent: an unconfigured integration parks
    itself instead of letting the supervisor resurrect a process that can't
    do its one job, over and over."""

    @pytest.mark.asyncio
    async def test_missing_credentials_file_blocks_instead_of_connecting(self, service, mock_mqtt, mocker):
        mocker.patch("clClaudeBridge.os.path.exists", return_value=False)

        import asyncio
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(service.run(), timeout=0.2)

        mock_mqtt.subscribe.assert_not_called()


class TestScreenUpdatePublishing:
    """Debounced screen snapshots should only actually publish when the
    screen changed, and only once a real MQTT client/loop are wired up."""

    def test_duplicate_screen_is_not_republished(self, service):
        service.loop = None
        service._on_screen_update("frame A")
        assert service._last_screen == "frame A"
        service._on_screen_update("frame A")
        assert service._last_screen == "frame A"

    def test_changed_screen_schedules_a_publish(self, service, mocker):
        service.mqtt_client = MagicMock()
        service.loop = MagicMock()
        mocker.patch.object(service, "_publish_screen")
        scheduled = mocker.patch("asyncio.run_coroutine_threadsafe")

        service._on_screen_update("frame A")

        scheduled.assert_called_once()

    def test_grid_padding_is_stripped_before_publishing(self, service, mocker):
        """The screen is a fixed 120x40 grid -- almost all of it trailing
        spaces and empty bottom rows, which is pure noise on the wire/logs."""
        service.mqtt_client = MagicMock()
        service.loop = MagicMock()
        publish = mocker.patch.object(service, "_publish_screen")
        mocker.patch("asyncio.run_coroutine_threadsafe")
        padded = "\n".join(["hello" + " " * 115, " " * 120, "world  " + " " * 113] + [" " * 120] * 37)

        service._on_screen_update(padded)

        publish.assert_called_once_with("hello\n\nworld")

    def test_padding_only_changes_do_not_republish(self, service, mocker):
        service.mqtt_client = MagicMock()
        service.loop = MagicMock()
        mocker.patch.object(service, "_publish_screen")
        scheduled = mocker.patch("asyncio.run_coroutine_threadsafe")

        service._on_screen_update("frame A" + " " * 50)
        service._on_screen_update("frame A" + " " * 90 + "\n" + " " * 120)

        scheduled.assert_called_once()

    def test_no_publish_attempted_without_a_connected_client(self, service, mocker):
        service.mqtt_client = None
        service.loop = None
        scheduled = mocker.patch("asyncio.run_coroutine_threadsafe")

        service._on_screen_update("frame A")

        scheduled.assert_not_called()


class TestClaudeBinaryResolution:
    """The npm shim (claude.cmd) can't be spawned directly under ConPTY --
    its arguments get silently dropped -- so the bridge must resolve and
    spawn the native binary it wraps instead."""

    def test_missing_claude_cli_raises_clear_error(self, mocker):
        from utils.clPtyBridge import ClaudePtyBridge
        mocker.patch("shutil.which", return_value=None)
        with pytest.raises(FileNotFoundError, match="claude CLI not found"):
            ClaudePtyBridge._resolve_claude_binary()

    def test_shim_without_native_binary_raises_clear_error(self, mocker, tmp_path):
        from utils.clPtyBridge import ClaudePtyBridge
        shim = tmp_path / "claude.cmd"
        shim.write_text("@echo off")
        mocker.patch("shutil.which", return_value=str(shim))
        with pytest.raises(FileNotFoundError, match="native binary"):
            ClaudePtyBridge._resolve_claude_binary()

    def test_resolves_real_binary_next_to_shim(self, mocker, tmp_path):
        from utils.clPtyBridge import ClaudePtyBridge
        shim = tmp_path / "claude.cmd"
        shim.write_text("@echo off")
        binary_dir = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
        binary_dir.mkdir(parents=True)
        binary = binary_dir / "claude.exe"
        binary.write_text("")
        mocker.patch("shutil.which", return_value=str(shim))

        resolved = ClaudePtyBridge._resolve_claude_binary()

        assert resolved == str(binary)


class TestClaudePtySpawnArgs:
    """`claude --continue` hard-errors and exits immediately if there's no
    prior conversation recorded yet under the config dir -- it must only be
    requested once one genuinely exists. Env inherited from whatever shell
    launched the ecosystem must also be scrubbed of CLAUDE_CODE_* markers,
    or the spawned session sees itself as a nested child and silently
    disables transcript saving (which --continue itself depends on)."""

    @pytest.fixture(autouse=True)
    def _stub_binary(self, mocker, tmp_path):
        binary_dir = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
        binary_dir.mkdir(parents=True)
        (binary_dir / "claude.exe").write_text("")
        shim = tmp_path / "claude.cmd"
        shim.write_text("@echo off")
        mocker.patch("shutil.which", return_value=str(shim))

    def _spawn(self, mocker, tmp_path):
        from utils.clPtyBridge import ClaudePtyBridge
        mocker.patch.object(ClaudePtyBridge, "_read_loop")
        spawn = mocker.patch("winpty.PtyProcess.spawn")
        spawn.return_value = MagicMock()
        bridge = ClaudePtyBridge(cwd=str(tmp_path), on_screen_update=MagicMock())
        bridge.start()
        return spawn

    def test_continue_omitted_with_no_prior_session(self, mocker, tmp_path):
        spawn = self._spawn(mocker, tmp_path)
        args = spawn.call_args[0][0]
        assert "--continue" not in args

    def test_continue_included_once_a_session_exists(self, mocker, tmp_path):
        (tmp_path / "data" / "claude_bridge_config" / "projects").mkdir(parents=True)
        spawn = self._spawn(mocker, tmp_path)
        args = spawn.call_args[0][0]
        assert "--continue" in args

    def test_claude_code_env_vars_are_stripped(self, mocker, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
        spawn = self._spawn(mocker, tmp_path)
        child_env = spawn.call_args[1]["env"]
        assert "CLAUDE_CODE_CHILD_SESSION" not in child_env


class TestSendKeys:
    """send_keys() writes raw control sequences (arrow keys, etc.) with no
    appended CR -- unlike write(), which always types a line and submits it."""

    def _bridge(self):
        from utils.clPtyBridge import ClaudePtyBridge
        bridge = ClaudePtyBridge(cwd=".", on_screen_update=MagicMock())
        bridge._pty = MagicMock()
        return bridge

    def test_raises_if_session_not_alive(self):
        bridge = self._bridge()
        bridge._pty.isalive.return_value = False
        with pytest.raises(RuntimeError, match="not alive"):
            bridge.send_keys("\x1bOB\r")

    def test_writes_raw_bytes_with_no_appended_cr(self):
        bridge = self._bridge()
        bridge._pty.isalive.return_value = True
        bridge.send_keys("\x1bOB\r")
        bridge._pty.write.assert_called_once_with("\x1bOB\r")


class TestTrustPromptAutoAnswer:
    """First launch against a fresh config dir asks to trust the workspace
    via an arrow-key menu defaulting to 'No, exit' -- confirming it needs a
    down arrow + enter, not typed text, and must only fire once per session."""

    def _bridge(self):
        from utils.clPtyBridge import ClaudePtyBridge
        bridge = ClaudePtyBridge(cwd=".", on_screen_update=MagicMock())
        bridge.send_keys = MagicMock()
        return bridge

    def test_sends_down_arrow_and_enter_on_trust_screen(self):
        bridge = self._bridge()
        bridge._maybe_auto_answer("Is this a project you created or one you trust?")
        bridge.send_keys.assert_called_once_with("\x1bOB\r")

    def test_same_screen_is_only_answered_once(self):
        bridge = self._bridge()
        bridge._maybe_auto_answer("Is this a project you created or one you trust?")
        bridge._maybe_auto_answer("Is this a project you created or one you trust?")
        bridge.send_keys.assert_called_once()

    def test_ignores_unrelated_screens(self):
        bridge = self._bridge()
        bridge._maybe_auto_answer("some other screen entirely")
        bridge.send_keys.assert_not_called()

    @pytest.mark.parametrize("screen", [
        "Choose the text style that looks best with your terminal",
        "Select login method:\n 1. Claude account with subscription",
        "Login successful. Press Enter to continue…",
    ])
    def test_plain_first_run_screens_get_enter(self, screen):
        bridge = self._bridge()
        bridge._maybe_auto_answer(screen)
        bridge.send_keys.assert_called_once_with("\r")

    def test_back_to_back_enter_screens_are_each_answered(self):
        """Login-success and security-notes both say 'Press Enter to continue' --
        the second is a different screen and must not be mistaken for the first."""
        bridge = self._bridge()
        bridge._maybe_auto_answer("Login successful. Press Enter to continue")
        bridge._maybe_auto_answer("Security notes: ...\nPress Enter to continue")
        assert bridge.send_keys.call_count == 2

    def test_a_dropped_enter_is_retried_then_gives_up(self):
        import time
        bridge = self._bridge()
        bridge._maybe_auto_answer("Press Enter to continue")
        for _ in range(5):
            bridge._retry_dropped_answer(time.time() + 100)
            bridge._answered_at -= 100
        assert bridge.send_keys.call_count == bridge.ANSWER_MAX_TRIES

    def test_no_retry_once_the_screen_moved_on(self):
        import time
        bridge = self._bridge()
        bridge._maybe_auto_answer("Press Enter to continue")
        bridge._last_data_at = time.time() + 1  # new output arrived after the answer
        bridge._retry_dropped_answer(time.time() + 100)
        bridge.send_keys.assert_called_once()

    def test_answer_state_clears_when_no_rule_matches(self):
        bridge = self._bridge()
        bridge._maybe_auto_answer("Press Enter to continue")
        bridge._maybe_auto_answer("chat prompt")
        assert bridge._answered_screen is None


class TestSetupMode:
    """`--setup` drives first-run sign-in to the idle chat prompt; the only human steps
    are the browser Authorize click and (if the page shows one) pasting a code."""

    def _patch_bridge(self, mocker, ready_after=1, alive=True, screen=""):
        import clClaudeBridge
        fake = MagicMock()
        fake.is_alive.return_value = alive
        fake.is_ready.side_effect = [False] * ready_after + [True] * 50

        def factory(cwd, on_screen_update):
            on_screen_update(screen)
            return fake
        mocker.patch("utils.clPtyBridge.ClaudePtyBridge", side_effect=factory)
        mocker.patch("clClaudeBridge.CURRENT_OS", "Windows")
        mocker.patch("clClaudeBridge.time.sleep")
        return fake

    def test_returns_true_once_the_chat_prompt_is_reached(self, mocker):
        from clClaudeBridge import run_setup
        fake = self._patch_bridge(mocker)
        assert run_setup() is True
        fake.start.assert_called_once()
        fake.stop.assert_called_once()

    def test_fails_if_claude_exits_early(self, mocker):
        from clClaudeBridge import run_setup
        fake = self._patch_bridge(mocker, ready_after=99, alive=False)
        assert run_setup() is False
        fake.stop.assert_called_once()

    def test_pasted_code_is_typed_into_the_session(self, mocker):
        from clClaudeBridge import run_setup
        fake = self._patch_bridge(mocker, ready_after=2, screen="Paste code here if prompted >")
        mocker.patch("builtins.input", return_value="  abc123  ")
        assert run_setup() is True
        fake.write.assert_called_once_with("abc123")

    def test_code_is_only_asked_for_once(self, mocker):
        from clClaudeBridge import run_setup
        self._patch_bridge(mocker, ready_after=5, screen="Paste code here if prompted >")
        ask = mocker.patch("builtins.input", return_value="abc")
        run_setup()
        ask.assert_called_once()

    def test_refuses_on_non_windows(self, mocker):
        from clClaudeBridge import run_setup
        mocker.patch("clClaudeBridge.CURRENT_OS", "Linux")
        assert run_setup() is False


class TestPromptAndBusyDetection:
    """The chat input box is a ❯ row between horizontal rules; menus reuse
    ❯ without the rules and must not read as ready for typed input."""

    RULE = "─" * 20

    def _bridge(self):
        from utils.clPtyBridge import ClaudePtyBridge
        return ClaudePtyBridge(cwd=".", on_screen_update=MagicMock())

    def test_input_box_is_detected(self):
        rows = ["banner", self.RULE, "❯ ", self.RULE, "footer"]
        assert self._bridge()._prompt_row_visible(rows) is True

    def test_menu_selection_row_is_not_a_prompt(self):
        rows = ["Is this a project you trust?", "❯ No, exit", "  Yes, I trust this folder"]
        assert self._bridge()._prompt_row_visible(rows) is False

    def test_busy_flag_follows_the_interrupt_hint(self):
        bridge = self._bridge()
        bridge._stream.feed("working... esc to interrupt")
        bridge._emit_screen()
        assert bridge._busy is True

    def test_not_ready_while_busy_or_recently_active(self, mocker):
        import time
        bridge = self._bridge()
        bridge._prompt_visible = True
        bridge._last_data_at = time.time() - 10
        assert bridge.is_ready() is True
        bridge._busy = True
        assert bridge.is_ready() is False
        bridge._busy = False
        bridge._last_data_at = time.time()
        assert bridge.is_ready() is False


class TestStallDetection:
    def _bridge(self, on_stall):
        from utils.clPtyBridge import ClaudePtyBridge
        bridge = ClaudePtyBridge(cwd=".", on_screen_update=MagicMock(), on_stall=on_stall)
        bridge._pty = MagicMock()
        bridge._running = True
        return bridge

    def test_silent_busy_session_reports_a_stall_once(self, mocker):
        import time
        on_stall = MagicMock()
        bridge = self._bridge(on_stall)
        bridge._busy = True
        bridge._last_data_at = time.time() - 1000
        bridge._pty.isalive.side_effect = [True, True, False]
        mocker.patch("select.select", return_value=([], [], []))

        bridge._read_loop()

        on_stall.assert_called_once()

    def test_silent_idle_session_is_not_a_stall(self, mocker):
        import time
        on_stall = MagicMock()
        bridge = self._bridge(on_stall)
        bridge._busy = False
        bridge._last_data_at = time.time() - 1000
        bridge._pty.isalive.side_effect = [True, False]
        mocker.patch("select.select", return_value=([], [], []))

        bridge._read_loop()

        on_stall.assert_not_called()


class TestStallRecovery:
    @pytest.fixture
    def recovering_service(self, service, mocker, tmp_path):
        mocker.patch("clClaudeBridge.REPO_ROOT", str(tmp_path))
        service.bridge = MagicMock()
        service._ensure_pty_started = MagicMock(return_value=True)
        service._wait_until_ready = mocker.AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_restarts_the_session_and_resends_the_last_question(self, recovering_service):
        svc = recovering_service
        old_bridge = svc.bridge
        svc._last_question = "what time is it"

        await svc._recover_from_stall("screen tail")

        old_bridge.stop.assert_called_once()
        svc._ensure_pty_started.assert_called_once()
        svc.bridge.write.assert_called_once_with("what time is it")

    @pytest.mark.asyncio
    async def test_already_answered_question_is_not_resent(self, recovering_service):
        svc = recovering_service
        svc._last_question = "what time is it"
        svc._answered = True

        await svc._recover_from_stall("tail")

        svc._ensure_pty_started.assert_called_once()
        svc.bridge.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_question_is_retried_at_most_once(self, recovering_service):
        svc = recovering_service
        svc._last_question = "what time is it"

        await svc._recover_from_stall("tail")
        svc.bridge.write.reset_mock()
        await svc._recover_from_stall("tail")

        svc.bridge.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_recovery_failure_does_not_crash_the_service(self, recovering_service):
        svc = recovering_service
        svc._ensure_pty_started.side_effect = RuntimeError("spawn failed")

        await svc._recover_from_stall("tail")  # should not raise

        assert svc._recovering is False

    @pytest.mark.asyncio
    async def test_new_session_waits_for_ready_before_injecting(self, service, mock_mqtt, message_stream, mocker):
        service.bridge = MagicMock()
        mocker.patch.object(service, "_ensure_pty_started", return_value=True)
        wait = mocker.patch.object(service, "_wait_until_ready", new=mocker.AsyncMock())

        mock_mqtt.messages = message_stream([("jarvis/claude/question", json.dumps({"text": "hi"}))])
        await service.run()

        wait.assert_awaited_once()
        service.bridge.write.assert_called_once_with("hi")


class TestStallDump:
    @pytest.mark.asyncio
    async def test_screen_goes_to_a_file_not_the_log(self, service, mocker, tmp_path, caplog):
        mocker.patch("clClaudeBridge.REPO_ROOT", str(tmp_path))
        service.bridge = MagicMock()
        service._ensure_pty_started = MagicMock(return_value=False)
        service._last_question = "what time is it"

        with caplog.at_level("ERROR"):
            await service._recover_from_stall("line one\nline two")

        dump = (tmp_path / "logs" / "claude_stall.txt").read_text(encoding="utf-8")
        assert "line one\nline two" in dump
        assert "line two" not in caplog.text
        assert "what time is it" in caplog.text
