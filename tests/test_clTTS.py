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


class TestGenerateAndPlayNetworkFailure:
    """A dropped edge-tts connection must not strand the daemon waiting on a
    tts_state:idle signal that would otherwise never arrive (see clDaemon.py's
    pending_mic_request handling)."""

    @pytest.mark.asyncio
    async def test_retries_once_then_emits_idle_without_raising(self, manager):
        client = AsyncMock()
        with patch('clTTS.edge_tts.Communicate') as MockCommunicate:
            MockCommunicate.return_value.save = AsyncMock(side_effect=Exception("DNS failure"))
            await manager.generate_and_play(client, "unique network failure test phrase", abort_count=0)

        assert MockCommunicate.return_value.save.call_count == 2

        idle_calls = [
            c for c in client.publish.call_args_list
            if c.args[0] == "jarvis/sys/tts_state" and json.loads(c.args[1])["state"] == "idle"
        ]
        assert idle_calls, "generate_and_play must publish tts_state:idle even when edge-tts fails entirely"

    @pytest.mark.asyncio
    async def test_succeeds_after_one_retry(self, manager, tmp_path):
        client = AsyncMock()
        calls = {"count": 0}

        async def fake_save(path):
            calls["count"] += 1
            if calls["count"] == 1:
                raise Exception("transient DNS failure")
            with open(path, "wb") as f:
                f.write(b"\x00")

        # Mock the playback pipeline entirely -- it's unrelated to this fix,
        # and this just needs to prove the retry lets generation proceed.
        with patch('clTTS.edge_tts.Communicate') as MockCommunicate, \
             patch('clTTS.mixer') as mock_mixer, \
             patch.object(manager, 'get_audio_rms', return_value=[]):
            MockCommunicate.return_value.save = fake_save
            mock_mixer.music.get_busy.return_value = False
            await manager.generate_and_play(client, "unique retry success test phrase", abort_count=0)

        assert calls["count"] == 2, "a transient failure must be retried, not just once, before giving up"
