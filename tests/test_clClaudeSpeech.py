import json
import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from clClaudeBridge import ClaudeBridgeService, speakable_text, MAX_SPOKEN_CHARS
from utils.clClaudeStopHook import extract_reply

_real_path_exists = os.path.exists


@pytest.fixture(autouse=True)
def mock_credentials_present(mocker):
    mocker.patch(
        "clClaudeBridge.os.path.exists",
        side_effect=lambda p: True if "claude_bridge_config" in str(p) else _real_path_exists(p),
    )


@pytest.fixture
def service():
    return ClaudeBridgeService()


class TestSpeakableText:
    def test_plain_text_passes_through(self):
        assert speakable_text("The sky is blue.") == "The sky is blue."

    def test_code_blocks_are_dropped(self):
        raw = "Here is the fix.\n```python\nprint('hi')\n```\nRun it again."
        assert speakable_text(raw) == "Here is the fix. Run it again."

    def test_code_only_answer_points_to_the_screen(self):
        assert speakable_text("```\nls -la\n```") == "The details are on screen."

    def test_markdown_marks_are_stripped(self):
        raw = "## Result\n- **bold** item\n- `code` item\n1. numbered"
        assert speakable_text(raw) == "Result bold item code item numbered"

    def test_links_keep_their_text_and_bare_urls_go(self):
        raw = "See [the docs](https://example.com/x) or https://example.com/y now."
        assert speakable_text(raw) == "See the docs or now."

    def test_tables_are_dropped(self):
        assert speakable_text("Summary:\n| a | b |\n|---|---|\n| 1 | 2 |") == "Summary:"

    def test_empty_stays_empty(self):
        assert speakable_text("   \n ") == ""

    def test_long_answer_is_cut_at_a_sentence_boundary(self):
        sentence = "This is a fairly ordinary sentence. "
        spoken = speakable_text(sentence * 100)
        assert len(spoken) <= MAX_SPOKEN_CHARS
        assert spoken.endswith("sentence.")


class TestSpeakReply:
    @pytest.mark.asyncio
    async def test_reply_is_published_to_tts_as_a_claude_session(self, service):
        service.mqtt_client = MagicMock(publish=AsyncMock())

        await service._speak_reply("It is four.")

        topic, payload = service.mqtt_client.publish.call_args[0]
        assert topic == "jarvis/sys/speak"
        assert json.loads(payload) == {"text": "It is four.", "claude_session": True, "request_reply": False}

    @pytest.mark.asyncio
    async def test_speech_respects_silent_mode(self, service):
        service.mqtt_client = MagicMock(publish=AsyncMock())

        await service._speak_reply("Hello.")

        assert "ignore_silent" not in json.loads(service.mqtt_client.publish.call_args[0][1])

    @pytest.mark.asyncio
    async def test_a_trailing_question_requests_a_spoken_reply(self, service):
        service.mqtt_client = MagicMock(publish=AsyncMock())

        await service._speak_reply("Which file do you mean?")

        assert json.loads(service.mqtt_client.publish.call_args[0][1])["request_reply"] is True

    @pytest.mark.asyncio
    async def test_nothing_speakable_publishes_nothing(self, service):
        service.mqtt_client = MagicMock(publish=AsyncMock())

        await service._speak_reply("   ")

        service.mqtt_client.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_failure_does_not_crash(self, service):
        service.mqtt_client = MagicMock(publish=AsyncMock(side_effect=RuntimeError("broker gone")))

        await service._speak_reply("Hello.")  # should not raise

    @pytest.mark.asyncio
    async def test_reply_topic_routes_to_speech_not_the_pty(self, service, mock_mqtt, message_stream, mocker):
        service.bridge = MagicMock()
        speak = mocker.patch.object(service, "_speak_reply", new=AsyncMock())
        mock_mqtt.messages = message_stream([
            ("jarvis/claude/reply", json.dumps({"text": "All done."})),
        ])

        await service.run()

        speak.assert_awaited_once_with("All done.")
        service.bridge.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_reply_json_does_not_crash(self, service, mock_mqtt, message_stream, mocker):
        service.bridge = MagicMock()
        speak = mocker.patch.object(service, "_speak_reply", new=AsyncMock())
        mock_mqtt.messages = message_stream([("jarvis/claude/reply", "{ broken")])

        await service.run()

        speak.assert_not_awaited()


class TestStopHookPayload:
    def test_extracts_the_final_assistant_message(self):
        stdin = json.dumps({"hook_event_name": "Stop", "last_assistant_message": "  Four.  "})
        assert extract_reply(stdin) == "Four."

    def test_missing_message_is_empty(self):
        assert extract_reply(json.dumps({"hook_event_name": "Stop"})) == ""

    def test_garbage_input_is_empty_not_an_exception(self):
        assert extract_reply("not json") == ""
        assert extract_reply("[1, 2]") == ""


class TestSessionFiles:
    """The Stop hook and the no-double-speech prompt are passed per launch via CLI
    flags so they never touch a developer's own claude sessions."""

    def _bridge(self):
        from utils.clPtyBridge import ClaudePtyBridge
        return ClaudePtyBridge(cwd=".", on_screen_update=MagicMock())

    def test_settings_declare_a_stop_command_hook_for_the_hook_script(self, tmp_path):
        settings_path, _ = self._bridge()._write_session_files(str(tmp_path))
        settings = json.loads(open(settings_path, encoding="utf-8").read())
        hook = settings["hooks"]["Stop"][0]["hooks"][0]
        assert hook["type"] == "command"
        assert hook["args"][0].endswith("clClaudeStopHook.py")
        assert "\\" not in hook["command"] and "\\" not in hook["args"][0]

    def test_prompt_tells_the_session_not_to_speak_itself(self, tmp_path):
        _, prompt_path = self._bridge()._write_session_files(str(tmp_path))
        assert "Do NOT run the speak" in open(prompt_path, encoding="utf-8").read()

    def test_spawn_passes_both_files_as_cli_flags(self, mocker, tmp_path):
        from utils.clPtyBridge import ClaudePtyBridge
        binary_dir = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
        binary_dir.mkdir(parents=True)
        (binary_dir / "claude.exe").write_text("")
        shim = tmp_path / "claude.cmd"
        shim.write_text("@echo off")
        mocker.patch("shutil.which", return_value=str(shim))
        mocker.patch.object(ClaudePtyBridge, "_read_loop")
        spawn = mocker.patch("winpty.PtyProcess.spawn", return_value=MagicMock())

        ClaudePtyBridge(cwd=str(tmp_path), on_screen_update=MagicMock()).start()

        args = spawn.call_args[0][0]
        assert args[args.index("--settings") + 1].endswith("bridge_settings.json")
        assert args[args.index("--append-system-prompt-file") + 1].endswith("bridge_prompt.txt")


class TestBlockSpeakHook:
    def _call(self, command):
        return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}})

    def test_blocks_the_standing_speak_command(self):
        from utils.clClaudeStopHook import is_speak_command
        cmd = "python -c \"publish.single('jarvis/sys/speak', json.dumps({'text': 'hi'}))\""
        assert is_speak_command(self._call(cmd)) is True

    def test_allows_ordinary_commands(self):
        from utils.clClaudeStopHook import is_speak_command
        assert is_speak_command(self._call("git status")) is False

    def test_garbage_input_is_not_a_block(self):
        from utils.clClaudeStopHook import is_speak_command
        assert is_speak_command("not json") is False

    def test_settings_register_the_block_hook_on_bash(self, tmp_path):
        from utils.clPtyBridge import ClaudePtyBridge
        bridge = ClaudePtyBridge(cwd=".", on_screen_update=MagicMock())
        settings_path, _ = bridge._write_session_files(str(tmp_path))
        pre = json.loads(open(settings_path, encoding="utf-8").read())["hooks"]["PreToolUse"][0]
        assert pre["matcher"] == "Bash"
        assert pre["hooks"][0]["args"][-1] == "--block-speak"

    def test_script_exits_2_and_explains_when_blocking(self):
        import subprocess
        script = os.path.join(os.path.dirname(__file__), "..", "src", "utils", "clClaudeStopHook.py")
        result = subprocess.run(
            [sys.executable, script, "--block-speak"],
            input=self._call("publish.single('jarvis/sys/speak', x)"),
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "already reads your final answer" in result.stderr

    def test_script_exits_0_for_other_commands(self):
        import subprocess
        script = os.path.join(os.path.dirname(__file__), "..", "src", "utils", "clClaudeStopHook.py")
        result = subprocess.run(
            [sys.executable, script, "--block-speak"], input=self._call("ls"), capture_output=True, text=True
        )
        assert result.returncode == 0
