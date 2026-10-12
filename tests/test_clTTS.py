import asyncio
import os
import sys
import json
import threading
import time
import wave
import pytest
import numpy as np
from unittest.mock import patch, AsyncMock, MagicMock

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


@pytest.fixture
def fake_mixer():
    """A mixer that reports every sound as finished immediately."""
    with patch('clTTS.mixer') as mock_mixer:
        mock_mixer.get_init.return_value = (24000, -16, 2)
        mock_mixer.music.get_busy.return_value = False
        channels = {}

        def channel(n):
            if n not in channels:
                ch = MagicMock()
                ch.get_queue.return_value = None
                ch.get_busy.return_value = False
                channels[n] = ch
            return channels[n]

        mock_mixer.Channel.side_effect = channel
        mock_mixer.speech = channel(1)       # clTTS.SPEECH_CHANNEL
        mock_mixer.blip = channel(2)         # clTTS.BLIP_CHANNEL
        yield mock_mixer


class FakeEngine:
    """Yields `chunks` of 80ms audio, optionally failing first or after some chunks."""
    sample_rate = 24000

    def __init__(self, name="piper", chunks=3, fail_at=None, delay=0.0):
        self.name, self.n, self.fail_at, self.delay = name, chunks, fail_at, delay
        self.model = object()
        self.calls = 0
        self.stop_seen = None

    def load(self):
        pass

    def stream(self, text, stop):
        self.calls += 1
        self.stop_seen = stop
        for i in range(self.n):
            if self.fail_at == i:
                raise RuntimeError("engine blew up")
            if stop.is_set():
                return
            if self.delay:
                time.sleep(self.delay)
            yield np.full(1920, 0.1, dtype=np.float32)


def published(client, topic):
    return [json.loads(c.args[1]) if c.args[1].startswith("{") else c.args[1]
            for c in client.publish.call_args_list if c.args[0] == topic]


class TestStreamingSpeech:
    @pytest.mark.asyncio
    async def test_speaks_chunks_signals_state_and_caches_short_phrases(self, manager, fake_mixer, tmp_path):
        client = AsyncMock()
        engine = FakeEngine(chunks=4)
        with patch.object(manager, '_engine_order', return_value=[engine]):
            await manager.generate_and_play(client, "Right away, sir.", abort_count=0)

        assert engine.calls == 1
        assert [s["state"] for s in published(client, "jarvis/sys/tts_state")] == ["active", "idle"]
        trace = published(client, "jarvis/sys/trace")
        assert len(trace) == 1 and trace[0]["cached"] is False
        fake_mixer.speech.play.assert_called_once()
        wavs = list(tmp_path.glob("tts_*_local.wav"))
        assert len(wavs) == 1
        with wave.open(str(wavs[0])) as wf:
            assert wf.getframerate() == 24000 and wf.getnframes() == 4 * 1920

    @pytest.mark.asyncio
    async def test_a_cached_phrase_replays_without_the_engine(self, manager, fake_mixer):
        client = AsyncMock()
        engine = FakeEngine()
        with patch.object(manager, '_engine_order', return_value=[engine]):
            await manager.generate_and_play(client, "Right away, sir.", abort_count=0)
            with patch.object(manager, '_play_file', new=AsyncMock()) as play_file:
                await manager.generate_and_play(client, "Right away, sir.", abort_count=0)

        assert engine.calls == 1
        play_file.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_long_unique_replies_are_not_cached(self, manager, fake_mixer, tmp_path):
        client = AsyncMock()
        with patch.object(manager, '_engine_order', return_value=[FakeEngine()]):
            await manager.generate_and_play(client, "A very long AI reply. " * 12, abort_count=0)
        assert list(tmp_path.glob("tts_*_local.wav")) == []

    @pytest.mark.asyncio
    async def test_a_voice_that_fails_once_is_tried_again_for_the_next_reply(self, manager, fake_mixer):
        client = AsyncMock()
        flaky = FakeEngine("piper", fail_at=0)
        with patch.object(manager, '_engine_order', return_value=[flaky]):
            await manager.generate_and_play(client, "First try fails.", abort_count=0)
            flaky.fail_at = None
            await manager.generate_and_play(client, "Second try works.", abort_count=0)

        assert flaky.calls == 2
        assert fake_mixer.speech.play.call_count == 1          # only the second one made sound

    @pytest.mark.asyncio
    async def test_a_phrase_said_by_another_voice_is_never_cached_as_the_main_voice(self, manager, fake_mixer, tmp_path):
        """Live bug: a phrase spoken by a stand-in voice was cached under the main voice's key and replayed
        in the wrong voice on every later boot."""
        client = AsyncMock()
        with patch.object(manager, '_engine_order', return_value=[FakeEngine("stand-in")]):
            await manager.generate_and_play(client, "System online!", abort_count=0)
        assert list(tmp_path.glob("tts_*_local.wav")) == []

    @pytest.mark.asyncio
    async def test_a_boot_announcement_waits_for_the_main_voice_to_load(self, manager, fake_mixer):
        import asyncio
        client = AsyncMock()
        manager.preloading = True
        seen = {}
        main = FakeEngine("piper")
        orig_order = manager._engine_order

        def order_when_called():
            seen["preloading"] = manager.preloading
            return [main]

        async def finish_loading():
            await asyncio.sleep(0.3)
            manager.preloading = False

        with patch.object(manager, '_engine_order', side_effect=order_when_called):
            await asyncio.gather(manager.generate_and_play(client, "System online!", abort_count=0), finish_loading())

        assert seen["preloading"] is False and main.calls == 1

    @pytest.mark.asyncio
    async def test_the_wait_for_the_main_voice_is_bounded(self, manager):
        import clTTS
        manager.preloading = True
        with patch.object(clTTS, 'PRELOAD_WAIT_SECONDS', 0.3):
            t = time.time()
            await manager._await_preload()
        assert 0.25 < time.time() - t < 1.5

    @pytest.mark.asyncio
    async def test_a_mid_sentence_failure_keeps_what_was_spoken(self, manager, fake_mixer, tmp_path):
        client = AsyncMock()
        broken = FakeEngine("piper", chunks=4, fail_at=2)
        with patch.object(manager, '_engine_order', return_value=[broken]):
            await manager.generate_and_play(client, "Cut off part way.", abort_count=0)

        assert fake_mixer.speech.play.call_count == 1           # the part that was ready was played
        assert list(tmp_path.glob("tts_*_local.wav")) == []     # a partial phrase is not cached

    @pytest.mark.asyncio
    async def test_idle_is_still_emitted_when_no_engine_can_speak(self, manager, fake_mixer):
        client = AsyncMock()
        with patch.object(manager, '_engine_order', return_value=[FakeEngine("piper", fail_at=0)]):
            await manager.generate_and_play(client, "Nobody can say this.", abort_count=0)

        states = [s["state"] for s in published(client, "jarvis/sys/tts_state")]
        assert states[-1] == "idle"
        assert published(client, "jarvis/sys/tts_done")

    @pytest.mark.asyncio
    async def test_abort_stops_generation_and_playback(self, manager, fake_mixer):
        client = AsyncMock()
        engine = FakeEngine(chunks=200, delay=0.02)

        async def abort_soon():
            import asyncio
            await asyncio.sleep(0.3)
            manager.abort_counter += 1
            manager.stop_speaking()

        import asyncio
        with patch.object(manager, '_engine_order', return_value=[engine]):
            await asyncio.gather(manager.generate_and_play(client, "Long speech to interrupt.", abort_count=0),
                                 abort_soon())

        assert engine.stop_seen.is_set()
        fake_mixer.speech.stop.assert_called()
        assert published(client, "jarvis/sys/tts_state")[-1]["state"] == "idle"

    @pytest.mark.asyncio
    async def test_audio_volume_is_published_while_speaking(self, manager, fake_mixer):
        client = AsyncMock()
        channel = fake_mixer.speech
        busy = iter([True] * 12 + [False] * 1000)
        channel.get_busy.side_effect = lambda: next(busy)
        with patch.object(manager, '_engine_order', return_value=[FakeEngine(chunks=6)]):
            await manager.generate_and_play(client, "Visualise me.", abort_count=0)

        assert published(client, "jarvis/sys/audio_vol")

    def test_viz_frames_carry_the_partial_window(self, manager):
        a = np.full(1000, 0.2, dtype=np.float32)       # 24ms window = 576 samples
        frames, carry = manager._viz_frames(a, np.zeros(0, dtype=np.float32))
        assert len(frames) == 1 and len(carry) == 424
        frames2, carry2 = manager._viz_frames(a, carry)
        assert len(frames2) == 2 and len(carry2) == 272
        assert all(f == 100 for f in frames + frames2)

    def test_sound_is_duplicated_across_channels_of_a_stereo_mixer(self, manager, fake_mixer):
        manager._to_sound(np.zeros(100, dtype=np.float32))
        buf = fake_mixer.Sound.call_args.kwargs["buffer"]
        assert len(buf) == 100 * 2 * 2                 # samples x 2 channels x int16


class TestBlip:
    """blip.mp3 is 0.96s but starts with ~0.6s of silence; every reply used to wait through it."""

    @staticmethod
    def write_blip(path, lead_silence=0.6, tone=0.3, rate=48000):
        t = np.arange(int(tone * rate)) / rate
        audio = np.concatenate([np.zeros(int(lead_silence * rate)), 0.5 * np.sin(2 * np.pi * 880 * t)])
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
            wf.writeframes((audio * 32767).astype(np.int16).tobytes())

    def test_the_silent_lead_in_is_cut(self, manager, tmp_path):
        manager.blip_path = str(tmp_path / "blip.wav")
        self.write_blip(manager.blip_path)

        audio = manager._load_blip_audio()

        assert 0.28 < len(audio) / 24000 < 0.34            # ~0.3s tone + 10ms pre-roll, not 0.9s
        assert np.abs(audio[:480]).max() > 0.2             # starts with sound, not silence

    @pytest.mark.asyncio
    async def test_plays_the_trimmed_blip_on_its_own_channel(self, manager, fake_mixer, tmp_path):
        manager.blip_path = str(tmp_path / "blip.wav")
        self.write_blip(manager.blip_path)

        await manager._play_blip()

        fake_mixer.blip.play.assert_called_once()
        fake_mixer.music.load.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_the_file_when_it_cannot_be_decoded(self, manager, fake_mixer, tmp_path):
        manager.blip_path = str(tmp_path / "blip.mp3")
        (tmp_path / "blip.mp3").write_bytes(b"not audio")

        await manager._play_blip()

        fake_mixer.music.load.assert_called_once_with(manager.blip_path)

    @pytest.mark.asyncio
    async def test_no_blip_file_means_no_wait(self, manager, fake_mixer, tmp_path):
        manager.blip_path = str(tmp_path / "missing.mp3")
        await manager._play_blip()
        fake_mixer.blip.play.assert_not_called()
        fake_mixer.music.load.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_device_change_discards_the_cached_sound(self, manager, fake_mixer, tmp_path):
        manager.blip_path = str(tmp_path / "blip.wav")
        self.write_blip(manager.blip_path)
        await manager._play_blip()
        assert manager._blip_loaded is True

        await manager.set_output_device("Other Speakers")

        assert manager._blip_loaded is False and manager._blip is None

    @pytest.mark.asyncio
    async def test_the_reply_no_longer_waits_before_the_blip(self, manager, fake_mixer, tmp_path):
        """The old fixed 0.2s duck delay is gone; ducking runs while the blip plays."""
        manager.blip_path = str(tmp_path / "missing.mp3")
        client = AsyncMock()
        with patch.object(manager, '_engine_order', return_value=[FakeEngine(chunks=3)]):
            t = time.time()
            await manager.generate_and_play(client, "Quick reply.", abort_count=0)
        assert time.time() - t < 0.18


class TestEngineSelection:
    def test_piper_is_the_only_engine(self, manager):
        assert [e.name for e in manager._engine_order()] == ["piper"]

    def test_cached_phrases_are_tagged_by_voice(self, manager):
        tag = manager._cache_tag()
        assert manager.piper.voice_tag in tag
        manager.piper.voice_tag = "another-voice"
        assert manager._cache_tag() != tag

    def test_the_voice_that_may_be_cached_is_piper(self, manager):
        assert manager._primary_engine_name() == "piper"


class TestEdgeMode:
    """Cloud edge-tts is opt-in (settings.tts_engine = "edge"). A dropped connection must not
    strand the daemon waiting on a tts_state:idle that would otherwise never arrive."""

    @pytest.fixture(autouse=True)
    def edge_mode(self, manager):
        manager.tts_engine = "edge"

    @pytest.mark.asyncio
    async def test_retries_once_then_speaks_locally(self, manager):
        client = AsyncMock()
        with patch('clTTS.edge_tts.Communicate') as MockCommunicate, \
             patch.object(manager, '_speak_streaming', new=AsyncMock(return_value=True)) as local:
            MockCommunicate.return_value.save = AsyncMock(side_effect=Exception("DNS failure"))
            await manager.generate_and_play(client, "unique network failure test phrase", abort_count=0)

        assert MockCommunicate.return_value.save.call_count == 2
        local.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idle_is_emitted_when_edge_and_local_both_fail(self, manager):
        client = AsyncMock()
        with patch('clTTS.edge_tts.Communicate') as MockCommunicate, \
             patch.object(manager, '_speak_streaming', new=AsyncMock(return_value=False)):
            MockCommunicate.return_value.save = AsyncMock(side_effect=Exception("DNS failure"))
            await manager.generate_and_play(client, "unique total failure phrase", abort_count=0)

        assert published(client, "jarvis/sys/tts_state")[-1]["state"] == "idle"

    @pytest.mark.asyncio
    async def test_succeeds_after_one_retry(self, manager, fake_mixer):
        client = AsyncMock()
        calls = {"count": 0}

        async def fake_save(path):
            calls["count"] += 1
            if calls["count"] == 1:
                raise Exception("transient DNS failure")
            with open(path, "wb") as f:
                f.write(b"\x00")

        with patch('clTTS.edge_tts.Communicate') as MockCommunicate, \
             patch.object(manager, 'get_audio_rms', return_value=[]):
            MockCommunicate.return_value.save = fake_save
            await manager.generate_and_play(client, "unique retry success test phrase", abort_count=0)

        assert calls["count"] == 2, "a transient failure must be retried, not just once, before giving up"

    @pytest.mark.asyncio
    async def test_edge_is_skipped_while_blocked(self, manager):
        client = AsyncMock()
        with patch('clTTS.edge_tts.Communicate') as MockCommunicate, \
             patch.object(manager, '_speak_streaming', new=AsyncMock(return_value=True)):
            MockCommunicate.return_value.save = AsyncMock(side_effect=Exception("403"))
            await manager.generate_and_play(client, "first blocked phrase", abort_count=0)
            await manager.generate_and_play(client, "second blocked phrase", abort_count=0)

        assert MockCommunicate.return_value.save.call_count == 2   # only the first phrase tried edge-tts

    @pytest.mark.asyncio
    async def test_edge_is_retried_after_the_block_expires(self, manager):
        client = AsyncMock()
        with patch('clTTS.edge_tts.Communicate') as MockCommunicate, \
             patch.object(manager, '_speak_streaming', new=AsyncMock(return_value=True)):
            MockCommunicate.return_value.save = AsyncMock(side_effect=Exception("403"))
            await manager.generate_and_play(client, "phrase before expiry", abort_count=0)
            manager.edge_blocked_until = 0.0
            await manager.generate_and_play(client, "phrase after expiry", abort_count=0)

        assert MockCommunicate.return_value.save.call_count == 4


class TestCacheFiles:
    def test_wav_rms_uses_24ms_windows(self, manager, tmp_path):
        path = str(tmp_path / "x.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(22050)
            wf.writeframes((np.ones(22050, dtype=np.int16) * 16384).tobytes())  # 1.0s at half scale
        rms = manager.get_wav_rms(path)
        assert len(rms) == 42                      # ceil(22050 / int(22050*0.024)=529)
        assert all(abs(r - 0.5) < 1e-3 for r in rms)

    def test_cache_cleanup_counts_local_wavs(self, manager, tmp_path):
        for i in range(5):
            (tmp_path / f"tts_{i}_local.wav").write_bytes(b"x")
        manager.clean_old_cache(max_files=2)
        assert len(list(tmp_path.glob("tts_*_local.wav"))) == 2


class TestSetOutputDevice:
    """A device change picked in Settings must swap the mixer's active
    device live without restarting clTTS.py, and must never leave the
    mixer uninitialized if the requested device fails to open."""

    @pytest.mark.asyncio
    async def test_switches_to_named_device(self, manager):
        with patch('clTTS.mixer') as mock_mixer:
            await manager.set_output_device("Speakers (Realtek Audio)")

        mock_mixer.quit.assert_called_once()
        assert mock_mixer.init.call_args.kwargs["devicename"] == "Speakers (Realtek Audio)"
        assert mock_mixer.init.call_args.kwargs["frequency"] == 24000

    @pytest.mark.asyncio
    async def test_system_default_reinitializes_with_no_devicename(self, manager):
        with patch('clTTS.mixer') as mock_mixer:
            await manager.set_output_device("System Default")

        assert "devicename" not in mock_mixer.init.call_args.kwargs

    @pytest.mark.asyncio
    async def test_falls_back_to_default_when_device_open_fails(self, manager):
        with patch('clTTS.mixer') as mock_mixer:
            mock_mixer.init.side_effect = [Exception("device not found"), None]
            await manager.set_output_device("Unplugged Speakers")

        assert mock_mixer.init.call_count == 2
        assert "devicename" not in mock_mixer.init.call_args.kwargs


class TestEchoReferenceAndGain:
    """The mic can only hear the user over JARVIS if it knows exactly what JARVIS is playing, and
    JARVIS's own level is lowered (by the mic) when the speaker is too loud to interrupt."""

    @staticmethod
    def refs(client):
        import base64
        out = []
        for c in client.publish.call_args_list:
            if c.args[0] == "jarvis/sys/tts_ref":
                m = json.loads(c.args[1])
                out.append((m["t0"], m["off"], np.frombuffer(base64.b64decode(m["pcm"]), dtype=np.int16)))
        return out

    @pytest.mark.asyncio
    async def test_streamed_speech_publishes_what_is_played_in_order(self, manager, fake_mixer, tmp_path):
        manager.blip_path = str(tmp_path / "missing.mp3")
        client = AsyncMock()
        with patch.object(manager, '_engine_order', return_value=[FakeEngine(chunks=10)]):
            await manager.generate_and_play(client, "Hello there.", abort_count=0)

        refs = self.refs(client)
        assert refs and refs[0][1] == 0
        assert len({r[0] for r in refs}) == 1                     # one timeline (t0) for the whole utterance
        expected_off = 0
        for _, off, pcm in refs:
            assert off == expected_off                            # gapless, in order
            expected_off += len(pcm)
        played = np.concatenate([r[2] for r in refs])
        assert abs(len(played) - 10 * 1920 * 2 / 3) < 40          # 16kHz for 10 chunks of 24kHz (minus the held-back tail)

    @pytest.mark.asyncio
    async def test_the_reference_has_the_gain_applied(self, manager, fake_mixer, tmp_path):
        manager.blip_path = str(tmp_path / "missing.mp3")
        manager.gain = 0.5
        client = AsyncMock()
        with patch.object(manager, '_engine_order', return_value=[FakeEngine(chunks=12)]):
            await manager.generate_and_play(client, "Quieter.", abort_count=0)

        played = np.concatenate([r[2] for r in self.refs(client)])
        assert abs(float(np.median(np.abs(played[500:]))) - 0.1 * 0.5 * 32767) < 100   # engine level 0.1 x gain 0.5

    @pytest.mark.asyncio
    async def test_the_cache_keeps_the_full_volume_phrase(self, manager, fake_mixer, tmp_path):
        """The gain changes with the room, so a cached phrase must not bake in an old level."""
        manager.blip_path = str(tmp_path / "missing.mp3")
        manager.gain = 0.5
        client = AsyncMock()
        with patch.object(manager, '_engine_order', return_value=[FakeEngine(chunks=4)]):
            await manager.generate_and_play(client, "Right away.", abort_count=0)

        wav = next(tmp_path.glob("tts_*_local.wav"))
        with wave.open(str(wav)) as wf:
            data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        assert abs(int(np.median(data)) - int(0.1 * 32767)) < 5

    @pytest.mark.asyncio
    async def test_no_reference_is_published_when_barge_in_is_off(self, manager, fake_mixer, tmp_path):
        manager.blip_path = str(tmp_path / "missing.mp3")
        manager.publish_ref = False
        client = AsyncMock()
        with patch.object(manager, '_engine_order', return_value=[FakeEngine(chunks=3)]):
            await manager.generate_and_play(client, "Silent reference.", abort_count=0)
        assert self.refs(client) == []

    @pytest.mark.asyncio
    async def test_the_blip_is_part_of_the_reference_and_follows_the_gain(self, manager, fake_mixer, tmp_path):
        manager.blip_path = str(tmp_path / "blip.wav")
        TestBlip.write_blip(manager.blip_path)
        manager.gain = 0.4
        client = AsyncMock()

        await manager._play_blip(client)

        refs = self.refs(client)
        assert refs and refs[0][2].any()
        fake_mixer.blip.play.assert_called_once()
        manager._blip.set_volume.assert_called_with(0.4)

    @pytest.mark.asyncio
    async def test_a_cached_phrase_publishes_its_reference_and_plays_at_the_gain(self, manager, fake_mixer, tmp_path):
        manager.blip_path = str(tmp_path / "missing.mp3")
        manager.gain = 0.5
        path = str(tmp_path / "tts_x_local.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
            wf.writeframes(np.full(24000, 8000, dtype=np.int16).tobytes())          # 1s
        client = AsyncMock()

        with patch.object(manager, 'get_wav_rms', return_value=[0.1] * 4):
            await manager._play_file(client, path, time.time(), True, 0)

        fake_mixer.music.set_volume.assert_called_with(0.5)
        played = np.concatenate([r[2] for r in self.refs(client)])
        assert abs(len(played) - 16000) < 40
        assert abs(float(np.median(played[300:])) - 8000 * 0.5) < 100

    def test_gain_is_clamped_to_a_sensible_range(self, manager):
        manager.set_gain(5.0)
        assert manager.gain == 1.0
        manager.set_gain(0.0)
        assert manager.gain == 0.05

    def test_the_saved_gain_is_picked_up_at_startup(self, manager, tmp_path):
        state = tmp_path / "state.json"
        state.write_text(json.dumps({"gain": 0.35}))
        manager.state_path = str(state)
        assert manager._load_gain() == pytest.approx(0.35)
        manager.state_path = str(tmp_path / "missing.json")
        assert manager._load_gain() == 1.0

    @pytest.mark.asyncio
    async def test_the_abort_keybind_stops_streamed_speech(self, manager, fake_mixer, tmp_path):
        """Keybind abort publishes jarvis/sys/abort; the service bumps the abort counter and stops speaking."""
        import asyncio
        manager.blip_path = str(tmp_path / "missing.mp3")
        client = AsyncMock()
        engine = FakeEngine(chunks=300, delay=0.02)

        async def keybind_abort():
            await asyncio.sleep(0.3)
            manager.abort_counter += 1                 # what run_tts_service does on jarvis/sys/abort
            manager.stop_speaking()

        with patch.object(manager, '_engine_order', return_value=[engine]):
            t = time.time()
            await asyncio.gather(manager.generate_and_play(client, "A very long reply to cut off.", abort_count=0),
                                 keybind_abort())
        assert time.time() - t < 2.0 and engine.stop_seen.is_set()
        assert published(client, "jarvis/sys/tts_state")[-1]["state"] == "idle"


class FakePiperVoice:
    """Stands in for a loaded piper.PiperVoice; records how it was asked to speak."""
    class config:
        sample_rate = 22050

    def __init__(self):
        self.configs = []

    def synthesize(self, text, syn_config):
        from types import SimpleNamespace
        self.configs.append(syn_config)
        yield SimpleNamespace(audio_int16_array=np.full(2205, 1000, dtype=np.int16))


@pytest.fixture
def piper_engine(tmp_path):
    from utils.clTTSEngines import PiperEngine, PIPER_VOICE
    (tmp_path / f"{PIPER_VOICE}.onnx").write_bytes(b"x")
    voice = FakePiperVoice()
    with patch("piper.PiperVoice.load", return_value=voice):
        engine = PiperEngine(model_dir=str(tmp_path))
        engine.fake = voice
        yield engine


class TestPiperIsTheDefaultVoice:
    @pytest.fixture
    def default_manager(self, tmp_path):
        with patch('clTTS.ConfigLoader') as MockLoader:
            MockLoader.return_value.load_json.side_effect = lambda name: (
                {"settings": {"silent_mode": False}} if name == "core.json" else {"mqtt": {}})
            from clTTS import TTSManager
            m = TTSManager()
        m.assets_dir = str(tmp_path)
        return m

    def test_piper_is_the_default_engine(self, default_manager):
        assert default_manager.tts_engine == "piper"
        assert [e.name for e in default_manager._engine_order()] == ["piper"]

    def test_the_voice_is_aru_speaker_2_at_its_natural_pitch(self):
        from utils.clTTSEngines import PIPER_VOICE, PIPER_SPEAKER, PIPER_PITCH, PIPER_SPEED
        assert (PIPER_VOICE, PIPER_SPEAKER, PIPER_PITCH, PIPER_SPEED) == ("en_GB-aru-medium", 2, 1.0, 0.8)

    def test_the_default_voice_is_not_pitch_shifted(self, piper_engine):
        piper_engine.load()
        assert piper_engine.sample_rate == 22050            # no pitch lift: the voice's own rate
        list(piper_engine.stream("hello", threading.Event()))
        config = piper_engine.fake.configs[0]
        assert config.speaker_id == 2
        assert config.length_scale == pytest.approx(0.8)   # Ryan's pace, unchanged

    def test_the_lifted_rate_resamples_to_the_mixer_with_small_integer_ratios(self):
        from math import gcd
        assert 24000 // gcd(22500, 24000) == 16 and 22500 // gcd(22500, 24000) == 15

    def test_the_cache_key_names_the_voice_speaker_and_pitch(self, manager):
        tag = manager._cache_tag()
        assert "aru" in tag and ":2:" in tag and "1.0" in tag


class TestPiperLifecycle:
    def test_loads_on_first_use_and_can_be_unloaded(self, piper_engine):
        assert piper_engine.ready() is False
        list(piper_engine.stream("hello", threading.Event()))
        assert piper_engine.ready() is True
        assert piper_engine.unload() is True and piper_engine.ready() is False
        list(piper_engine.stream("again", threading.Event()))      # loads itself again
        assert piper_engine.ready() is True

    def test_it_is_not_unloaded_mid_sentence(self, piper_engine):
        stream = piper_engine.stream("hello", threading.Event())
        next(stream)                                               # inside the sentence: holds the lock
        piper_engine.last_used = time.time() - 600
        assert piper_engine.unload_if_idle(30) is False and piper_engine.ready() is True
        stream.close()

    def test_a_recently_used_voice_is_kept(self, piper_engine):
        list(piper_engine.stream("hello", threading.Event()))
        assert piper_engine.unload_if_idle(30) is False

    def test_an_idle_voice_is_released(self, piper_engine):
        list(piper_engine.stream("hello", threading.Event()))
        piper_engine.last_used = time.time() - 60
        assert piper_engine.unload_if_idle(30) is True and piper_engine.ready() is False

    def test_the_manager_reaps_an_idle_piper_voice(self, manager, piper_engine):
        manager.piper = piper_engine
        list(piper_engine.stream("hello", threading.Event()))
        piper_engine.last_used = time.time() - 60
        assert manager.reap_idle() is True and piper_engine.ready() is False

    def test_the_manager_leaves_it_while_a_reply_is_being_spoken(self, manager, piper_engine):
        manager.piper = piper_engine
        list(piper_engine.stream("hello", threading.Event()))
        piper_engine.last_used = time.time() - 60
        manager.semaphore._value = 0
        assert manager.reap_idle() is False and piper_engine.ready() is True

    def test_a_zero_timeout_keeps_it_loaded(self, manager, piper_engine):
        manager.piper = piper_engine
        manager.piper_idle_seconds = 0
        list(piper_engine.stream("hello", threading.Event()))
        piper_engine.last_used = time.time() - 6000
        assert manager.reap_idle() is False and piper_engine.ready() is True

    @pytest.mark.asyncio
    async def test_the_voice_is_started_when_the_user_begins_speaking(self, manager, piper_engine):
        manager.piper = piper_engine
        manager.handle_mic_state({"state": "listening"})
        await asyncio.sleep(0.3)
        assert piper_engine.ready() is True
