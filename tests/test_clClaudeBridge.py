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
        bridge._maybe_answer_trust_prompt("Is this a project you created or one you trust?")
        bridge.send_keys.assert_called_once_with("\x1bOB\r")

    def test_only_answers_once_per_session(self):
        bridge = self._bridge()
        bridge._maybe_answer_trust_prompt("Is this a project you created or one you trust?")
        bridge._maybe_answer_trust_prompt("Is this a project you created or one you trust?")
        bridge.send_keys.assert_called_once()

    def test_ignores_unrelated_screens(self):
        bridge = self._bridge()
        bridge._maybe_answer_trust_prompt("some other screen entirely")
        bridge.send_keys.assert_not_called()
