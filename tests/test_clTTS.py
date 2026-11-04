import os
import sys
import json
import pytest
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def manager(tmp_path):
    with patch('clTTS.ConfigLoader') as MockLoader:
        MockLoader.return_value.load_json.side_effect = lambda name: (
            {"settings": {"silent_mode": False}} if name == "core.json" else {"mqtt": {}}
        )
        from clTTS import TTSManager
        m = TTSManager()
    m.assets_dir = str(tmp_path)
    return m


class TestGenerateAndPlaySynthesisFailure:
    """A failed render must not strand the daemon waiting on a tts_state:idle
    signal that would otherwise never arrive (see clDaemon.py's
    pending_mic_request handling)."""

    @pytest.mark.asyncio
    async def test_emits_idle_without_raising(self, manager):
        client = AsyncMock()
        with patch.object(manager.synth, 'synthesize_to_file', side_effect=RuntimeError("model missing")):
            await manager.generate_and_play(client, "unique synthesis failure test phrase", abort_count=0)

        idle_calls = [
            c for c in client.publish.call_args_list
            if c.args[0] == "jarvis/sys/tts_state" and json.loads(c.args[1])["state"] == "idle"
        ]
        assert idle_calls, "generate_and_play must publish tts_state:idle even when synthesis fails"

    @pytest.mark.asyncio
    async def test_renders_once_and_reuses_the_cache(self, manager):
        client = AsyncMock()
        calls = {"count": 0}

        def fake_synth(text, path):
            calls["count"] += 1
            with open(path, "wb") as f:
                f.write(b"\x00")

        # Mock the playback pipeline entirely -- this only needs to prove the render
        # happens once per phrase and the cached file is reused afterwards.
        with patch.object(manager.synth, 'synthesize_to_file', side_effect=fake_synth), \
             patch('clTTS.mixer') as mock_mixer, \
             patch.object(manager, 'get_audio_rms', return_value=[0.1]):
            mock_mixer.music.get_busy.return_value = False
            await manager.generate_and_play(client, "unique cache reuse test phrase", abort_count=0)
            await manager.generate_and_play(client, "unique cache reuse test phrase", abort_count=0)

        assert calls["count"] == 1


class TestSetOutputDevice:
    """A device change picked in Settings must swap the mixer's active
    device live without restarting clTTS.py, and must never leave the
    mixer uninitialized if the requested device fails to open."""

    @pytest.mark.asyncio
    async def test_switches_to_named_device(self, manager):
        with patch('clTTS.mixer') as mock_mixer:
            await manager.set_output_device("Speakers (Realtek Audio)")

        mock_mixer.quit.assert_called_once()
        mock_mixer.init.assert_called_once_with(devicename="Speakers (Realtek Audio)")

    @pytest.mark.asyncio
    async def test_system_default_reinitializes_with_no_devicename(self, manager):
        with patch('clTTS.mixer') as mock_mixer:
            await manager.set_output_device("System Default")

        mock_mixer.init.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_falls_back_to_default_when_device_open_fails(self, manager):
        with patch('clTTS.mixer') as mock_mixer:
            mock_mixer.init.side_effect = [Exception("device not found"), None]
            await manager.set_output_device("Unplugged Speakers")

        assert mock_mixer.init.call_count == 2
        mock_mixer.init.assert_called_with()
