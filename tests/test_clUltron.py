import os
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils.clTTSEngines import UltronEngine, PIPER_VOICE, PIPER_SPEAKER, _overlay
from utils.clVoice import level_settings, merge_settings, match_loudness

TWO_SENTENCES = "The first sentence is quite long enough. The second sentence is also long enough."


class FakeVoice:
    """Stands in for piper.PiperVoice: a voiced-sounding tone per sentence, and a record of how it was asked to speak."""
    class config:
        sample_rate = 22050

    def __init__(self):
        self.texts, self.configs = [], []

    def synthesize(self, text, syn_config):
        self.texts.append(text)
        self.configs.append(syn_config)
        t = np.arange(11025) / 22050.0
        tone = 0.25 * np.sin(2 * np.pi * 140 * t) + 0.1 * np.sin(2 * np.pi * 900 * t)
        yield SimpleNamespace(audio_float_array=tone.astype(np.float32), sample_rate=22050)


_patchers = []


@pytest.fixture(autouse=True)
def _stop_voice_patches():
    yield
    while _patchers:
        _patchers.pop().stop()


def make_engine(tmp_path, settings=None, trained=True):
    """An engine whose trained model file exists (or not, to exercise the stock fallback); piper.PiperVoice.load
    stays patched until the test ends."""
    model = tmp_path / "actor_voice.onnx"
    if trained:
        model.write_bytes(b"x")
    (tmp_path / f"{PIPER_VOICE}.onnx").write_bytes(b"x")
    voice = FakeVoice()
    patcher = patch("piper.PiperVoice.load", return_value=voice)
    patcher.start()
    _patchers.append(patcher)
    engine = UltronEngine({"piper_model": str(model), **(settings or {})}, model_dir=str(tmp_path))
    engine.fake = voice
    return engine


@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path)


class TestLevels:
    def test_light_drops_the_expensive_stages_and_keeps_the_cheap_ones(self):
        cfg = merge_settings({"fx_level": "light"})
        strength, fx = level_settings(cfg)
        assert strength == 0.0
        assert fx["vocoder_mix"] == fx["comb_mix"] == fx["ring_mod_mix"] == 0.0
        assert fx["reverb_mix"] == cfg["fx"]["reverb_mix"] and fx["pitch_semitones"] == cfg["fx"]["pitch_semitones"]

    def test_full_keeps_everything_configured(self):
        cfg = merge_settings({"fx_level": "full"})
        strength, fx = level_settings(cfg)
        assert strength == cfg["denoise"] > 0 and fx == cfg["fx"]

    def test_off_disables_the_chain(self):
        strength, fx = level_settings(merge_settings({"fx_level": "off"}))
        assert strength == 0.0 and fx["enabled"] is False

    def test_the_level_does_not_modify_the_settings(self):
        cfg = merge_settings({"fx_level": "light"})
        level_settings(cfg)
        assert cfg["fx"]["vocoder_mix"] > 0

    def test_the_default_level_is_full(self):
        assert merge_settings(None)["fx_level"] == "full"


class TestOverlay:
    def test_adds_at_the_offset_and_extends_to_the_later_end(self):
        out = _overlay(np.ones(4, np.float32), np.ones(3, np.float32), 3)
        assert out.tolist() == [1, 1, 1, 2, 1, 1]

    def test_an_empty_extra_changes_nothing(self):
        assert _overlay(np.ones(3, np.float32), np.zeros(0, np.float32)).tolist() == [1, 1, 1]


class TestStreaming:
    def test_one_chunk_per_sentence_and_the_last_rings_out(self, engine):
        chunks = list(engine.stream(TWO_SENTENCES, threading.Event()))
        assert len(chunks) == 2
        assert [t for t in engine.fake.texts] == ["The first sentence is quite long enough.", "The second sentence is also long enough."]
        pause = int(engine.sample_rate * 0.18)
        assert len(chunks[0]) > pause                            # sentence plus its trailing silence
        assert len(chunks[1]) > len(chunks[0]) - pause           # the final chunk carries the reverb tail

    def test_audio_is_finite_and_within_range(self, engine):
        for chunk in engine.stream(TWO_SENTENCES, threading.Event()):
            assert chunk.dtype == np.float32 and np.isfinite(chunk).all() and np.abs(chunk).max() <= 1.0

    def test_it_stops_between_sentences(self, engine):
        stop = threading.Event()
        stream = engine.stream(TWO_SENTENCES, stop)
        next(stream)
        stop.set()
        assert list(stream) == [] and len(engine.fake.texts) == 1

    def test_a_sentence_reverb_tail_is_mixed_into_the_next_not_played_after_it(self, engine):
        chunks = list(engine.stream(TWO_SENTENCES, threading.Event()))
        dry = list(make_engine_stream(engine, "off"))
        wet_total = sum(len(c) for c in chunks)
        # with the tail overlapped, the whole reply is barely longer than the dry one (just the final ring-out)
        assert wet_total < sum(len(c) for c in dry) + engine.sample_rate * 1.0

    def test_off_returns_the_dry_voice(self, tmp_path):
        eng = make_engine(tmp_path, {"fx_level": "off", "loudness_dbfs": 0})    # no effects and no level stage: the raw render
        (chunk,) = list(eng.stream("The only sentence here is long enough.", threading.Event()))
        assert len(chunk) == 11025 and np.allclose(chunk, 0.25 * np.sin(2 * np.pi * 140 * np.arange(11025) / 22050) +
                                                   0.1 * np.sin(2 * np.pi * 900 * np.arange(11025) / 22050), atol=1e-5)

    def test_light_never_runs_the_vocoder_or_the_denoiser_and_full_does(self, tmp_path):
        for level, expected in (("light", False), ("full", True)):
            eng = make_engine(tmp_path, {"fx_level": level})
            with patch("utils.clVoice.vocode", side_effect=lambda x, *a, **k: x) as voc, \
                 patch("utils.clVoice.denoise", side_effect=lambda x, *a, **k: x) as den:
                list(eng.stream("The only sentence here is long enough.", threading.Event()))
            assert voc.called is expected
            assert (den.call_args[0][2] > 0) is expected          # `light` calls it with strength 0, which is a no-op

    def test_piper_speaks_faster_to_pay_for_the_pitch_drop(self, engine):
        list(engine.stream("The only sentence here is long enough.", threading.Event()))
        cfg = engine.cfg
        expected = cfg["length_scale"] * 2.0 ** (cfg["fx"]["pitch_semitones"] / 12.0)
        assert engine.fake.configs[0].length_scale == pytest.approx(expected)

    def test_with_the_chain_off_the_pace_is_unchanged(self, tmp_path):
        eng = make_engine(tmp_path, {"fx_level": "off"})
        list(eng.stream("The only sentence here is long enough.", threading.Event()))
        assert eng.fake.configs[0].length_scale == pytest.approx(eng.cfg["length_scale"])

    def test_nothing_is_dropped_when_the_last_sentence_makes_no_audio(self, engine):
        real = engine.fake.synthesize

        def silent_last(text, syn_config):
            if text.startswith("The second"):
                return iter(())
            return real(text, syn_config)

        engine.fake.synthesize = silent_last
        chunks = list(engine.stream(TWO_SENTENCES, threading.Event()))
        assert len(chunks) == 2 and len(chunks[1]) > 0           # the first sentence's tail still rings out


def make_engine_stream(engine, level):
    engine.cfg["fx_level"] = level
    return engine.stream(TWO_SENTENCES, threading.Event())


class TestVoiceModel:
    def test_the_trained_voice_is_used_when_the_file_exists(self, engine):
        engine.load()
        assert engine.using_fallback is False and engine.sample_rate == 22050
        list(engine.stream("The only sentence here is long enough.", threading.Event()))
        assert engine.fake.configs[0].speaker_id is None

    def test_the_stock_voice_speaks_when_the_trained_one_is_missing(self, tmp_path):
        eng = make_engine(tmp_path, trained=False)
        list(eng.stream("The only sentence here is long enough.", threading.Event()))
        assert eng.using_fallback is True and eng.fake.configs[0].speaker_id == PIPER_SPEAKER

    def test_a_relative_model_path_is_resolved_from_the_repo_root(self):
        eng = UltronEngine({"piper_model": "models/piper/actor_voice.onnx"})
        assert eng.model_path.endswith(os.path.join("models", "piper", "actor_voice.onnx"))
        assert os.path.isabs(eng.model_path)

    def test_the_cache_tag_changes_with_the_level_and_the_model(self, tmp_path):
        a = UltronEngine({"fx_level": "light"}).voice_tag
        b = UltronEngine({"fx_level": "full"}).voice_tag
        c = UltronEngine({"fx_level": "light", "piper_model": "models/piper/other.onnx"}).voice_tag
        assert len({a, b, c}) == 3 and a == UltronEngine({"fx_level": "light"}).voice_tag

    def test_loads_on_first_use_and_unloads_when_idle(self, engine):
        assert engine.ready() is False
        list(engine.stream("The only sentence here is long enough.", threading.Event()))
        assert engine.ready() is True
        engine.last_used = time.time() - 120
        assert engine.unload_if_idle(30) is True and engine.ready() is False


class TestInTheManager:
    @pytest.fixture
    def ultron_manager(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_THEME", "ultron")           # conftest pins the tests to the jarvis skin; the voice follows the skin
        with patch('clTTS.ConfigLoader') as MockLoader:
            MockLoader.return_value.load_json.side_effect = lambda name: (
                {"settings": {"silent_mode": False, "theme": "ultron", "tts_settings": {"fx_level": "light"}}}
                if name == "core.json" else {"mqtt": {}})
            from clTTS import TTSManager
            m = TTSManager()
        m.assets_dir = str(tmp_path)
        return m

    def test_ultron_speaks_first_and_stock_piper_is_the_fallback(self, ultron_manager):
        assert [e.name for e in ultron_manager._engine_order()] == ["ultron", "piper"]
        assert ultron_manager._primary_engine_name() == "ultron"

    def test_settings_reach_the_engine(self, ultron_manager):
        assert ultron_manager.ultron.cfg["fx_level"] == "light"

    def test_cached_phrases_are_tagged_with_the_ultron_voice(self, ultron_manager):
        assert ultron_manager._cache_tag().startswith("ultron:") and "light" in ultron_manager._cache_tag()

    def test_it_is_released_when_idle(self, ultron_manager, tmp_path):
        eng = make_engine(tmp_path)
        ultron_manager.ultron = eng
        list(eng.stream("The only sentence here is long enough.", threading.Event()))
        eng.last_used = time.time() - 120
        assert ultron_manager.reap_idle() is True and eng.ready() is False

    def test_piper_stays_the_default(self, tmp_path):
        with patch('clTTS.ConfigLoader') as MockLoader:
            MockLoader.return_value.load_json.side_effect = lambda name: (
                {"settings": {}} if name == "core.json" else {"mqtt": {}})
            from clTTS import TTSManager
            m = TTSManager()
        assert m._primary_engine_name() == "piper" and [e.name for e in m._engine_order()] == ["piper"]


class TestTheVoiceFollowsTheTheme:
    """Switching the skin switches the voice both ways; settings.tts_engine is the JARVIS voice and is never what keeps Ultron talking."""

    @pytest.fixture(autouse=True)
    def _no_env_override(self, monkeypatch):
        monkeypatch.delenv("JARVIS_THEME", raising=False)

    def _engine(self, **settings):
        from clTTS import resolve_tts_engine
        return resolve_tts_engine(settings)

    def test_the_ultron_skin_speaks_with_ultron_whatever_tts_engine_says(self):
        assert self._engine(theme="ultron", tts_engine="kokoro") == "ultron"
        assert self._engine(theme="ultron") == "ultron"

    def test_switching_back_to_jarvis_returns_the_jarvis_voice(self):
        assert self._engine(theme="jarvis", tts_engine="kokoro") == "kokoro"
        assert self._engine(theme="jarvis", tts_engine="piper") == "piper"
        assert self._engine(tts_engine="kokoro") == "kokoro"           # no theme setting is the JARVIS skin

    def test_a_stale_ultron_in_tts_engine_does_not_keep_the_voice_under_the_jarvis_skin(self):
        assert self._engine(theme="jarvis", tts_engine="ultron", hardware="cuda") == "kokoro"
        assert self._engine(theme="jarvis", tts_engine="ultron", hardware="cpu") == "piper"
        assert self._engine(tts_engine="ultron") == "piper"

    def test_the_jarvis_theme_through_the_manager_gives_the_jarvis_voice(self, tmp_path):
        with patch('clTTS.ConfigLoader') as MockLoader:
            MockLoader.return_value.load_json.side_effect = lambda name: (
                {"settings": {"theme": "jarvis", "tts_engine": "ultron", "hardware": "cpu"}} if name == "core.json" else {"mqtt": {}})
            from clTTS import TTSManager
            m = TTSManager()
        assert m._primary_engine_name() == "piper" and [e.name for e in m._engine_order()] == ["piper"]

    def test_the_env_override_moves_the_voice_with_the_skin(self, monkeypatch):
        monkeypatch.setenv("JARVIS_THEME", "ultron")
        assert self._engine(theme="jarvis", tts_engine="kokoro") == "ultron"
        monkeypatch.setenv("JARVIS_THEME", "jarvis")
        assert self._engine(theme="ultron", tts_engine="kokoro") == "kokoro"

    def test_an_unknown_theme_is_the_jarvis_skin(self):
        assert self._engine(theme="nonsense", tts_engine="kokoro") == "kokoro"


class CountingEngine:
    """A minimal primary engine: counts loads and renders a short tone per phrase (or fails for chosen phrases)."""
    name = "ultron"
    voice_tag = "test"
    sample_rate = 24000

    def __init__(self, fail=()):
        self.loads, self.spoken, self.fail = 0, [], set(fail)

    def load(self):
        self.loads += 1

    def ready(self):
        return True

    def unload_if_idle(self, idle_seconds):
        return False

    def unload(self):
        return True

    def shutdown(self):
        pass

    def stream(self, text, stop):
        self.spoken.append(text)
        if text in self.fail:
            raise RuntimeError("boom")
        yield np.full(2400, 0.1, dtype=np.float32)


@pytest.fixture
def prerender_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_THEME", "ultron")
    with patch('clTTS.ConfigLoader') as MockLoader:
        MockLoader.return_value.load_json.side_effect = lambda name: (
            {"settings": {"silent_mode": False, "theme": "ultron"}} if name == "core.json" else {"mqtt": {}})
        from clTTS import TTSManager
        m = TTSManager()
    m.assets_dir = str(tmp_path)
    m.ultron = CountingEngine()
    return m


PHRASES = ["Going dark.", "Halting the media streams.", "Powering them up."]


class TestPlainPhrases:
    def test_only_fixed_short_phrases_are_taken_from_every_section(self):
        from clTTS import TTSManager
        data = {"mqtt": {"a": {"on": ["Going dark.", "Going dark.", "Lights at {lum} percent."]}},
                "conversational": {"x": ["  Anything else?  "]}, "followup_suggestions": ["y" * 200, "Shall I?"]}
        assert TTSManager.plain_phrases(data) == ["Anything else?", "Going dark.", "Shall I?"]

    def test_the_shipped_responses_have_phrases(self):
        import json
        from clTTS import TTSManager
        path = os.path.join(os.path.dirname(__file__), "..", "config", "responses.json")
        phrases = TTSManager.plain_phrases(json.load(open(path, encoding="utf-8")))
        assert len(phrases) > 20 and not any("{" in p for p in phrases)


class TestPrerender:
    def test_renders_the_missing_phrases_into_the_cache_the_player_looks_in(self, prerender_manager):
        m = prerender_manager
        stats = m.prerender(PHRASES)
        assert stats == {"rendered": 3, "failed": 0, "already_cached": 0}
        for text in PHRASES:
            path = m._cache_file(text)
            assert os.path.dirname(path) == m.cache_dir and os.path.getsize(path) > 0

    def test_a_second_run_does_nothing_and_loads_nothing(self, prerender_manager):
        m = prerender_manager
        m.prerender(PHRASES)
        loads = m.ultron.loads
        assert m.prerender(PHRASES) == {"rendered": 0, "failed": 0, "already_cached": 3}
        assert m.ultron.loads == loads and len(m.ultron.spoken) == 3

    def test_a_phrase_that_fails_does_not_stop_the_others(self, prerender_manager):
        m = prerender_manager
        m.ultron.fail = {PHRASES[1]}
        assert m.prerender(PHRASES) == {"rendered": 2, "failed": 1, "already_cached": 0}
        assert not os.path.exists(m._cache_file(PHRASES[1]))

    def test_a_changed_voice_is_rendered_again(self, prerender_manager):
        m = prerender_manager
        m.prerender(PHRASES)
        m.ultron.voice_tag = "other voice"
        assert m.prerender(PHRASES)["rendered"] == 3

    def test_it_waits_while_the_user_is_speaking(self, prerender_manager):
        m = prerender_manager
        m.mic_active = True
        worker = threading.Thread(target=m.prerender, args=(PHRASES,))
        worker.start()
        time.sleep(0.6)
        assert m.ultron.spoken == []                       # nothing rendered while the mic is busy
        m.mic_active = False
        worker.join(timeout=5)
        assert len(m.ultron.spoken) == 3

    def test_it_can_be_stopped(self, prerender_manager):
        m = prerender_manager
        m.mic_active = True
        worker = threading.Thread(target=m.prerender, args=(PHRASES,))
        worker.start()
        time.sleep(0.3)
        m.stop_prerender()
        worker.join(timeout=5)
        assert not worker.is_alive() and m.ultron.spoken == []

    @pytest.mark.asyncio
    async def test_a_cached_phrase_is_played_from_the_file_without_synthesis(self, prerender_manager):
        m = prerender_manager
        m.prerender(PHRASES)
        m.ultron.spoken.clear()
        with patch.object(m, "_play_file", new=AsyncMock()) as play:
            await m.generate_and_play(None, PHRASES[0], ignore_silent=True)
        assert play.call_args[0][1] == m._cache_file(PHRASES[0]) and m.ultron.spoken == []

    @pytest.mark.asyncio
    async def test_mic_state_tracks_whether_the_user_is_speaking(self, prerender_manager):
        m = prerender_manager
        with patch.object(m, "warm", new=AsyncMock()):
            m.handle_mic_state({"state": "recording"})
            assert m.mic_active is True
            m.handle_mic_state({"state": "idle"})
            assert m.mic_active is False


class TestLoudness:
    """The voice is brought to a fixed speech level whatever the model renders at, so a quiet trained model
    (or the effect chain, which only matches its own input) cannot make JARVIS faint."""

    @staticmethod
    def speech(level_db, seconds=1.0, rate=24000):
        t = np.arange(int(rate * seconds)) / rate
        return (10 ** (level_db / 20) * np.sqrt(2) * np.sin(2 * np.pi * 180 * t)).astype(np.float32)

    @staticmethod
    def level_db(x):
        active = x[np.abs(x) > 0.01]
        return 20 * np.log10(np.sqrt(np.mean(active ** 2)))

    def test_a_quiet_clip_is_raised_to_the_target(self):
        assert self.level_db(match_loudness(self.speech(-20), -14.5)) == pytest.approx(-14.5, abs=0.3)

    def test_a_loud_clip_is_lowered_to_the_target(self):
        assert self.level_db(match_loudness(self.speech(-8), -14.5)) == pytest.approx(-14.5, abs=0.5)

    def test_the_boost_is_capped_so_near_silence_is_not_blown_up(self):
        quiet = self.speech(-32)
        rms = lambda x: 20 * np.log10(np.sqrt(np.mean(x ** 2)))
        assert rms(match_loudness(quiet, -14.5)) - rms(quiet) == pytest.approx(9.0, abs=0.2)   # wanted 17.5 dB, got the 9 dB cap

    def test_fx_off_is_still_leveled_by_default(self, tmp_path):
        eng = make_engine(tmp_path, {"fx_level": "off"})
        (chunk,) = list(eng.stream("The only sentence here is long enough.", threading.Event()))
        assert self.level_db(chunk) == pytest.approx(-14.5, abs=0.6)

    def test_peaks_never_exceed_full_scale(self):
        spiky = self.speech(-20)
        spiky[::2000] = 0.99
        assert np.abs(match_loudness(spiky, -8.0)).max() <= 1.0

    def test_off_returns_the_clip_untouched(self):
        x = self.speech(-20)
        assert match_loudness(x, None) is x and match_loudness(x, 0) is x

    def test_silence_and_empty_clips_are_left_alone(self):
        assert len(match_loudness(np.zeros(0, dtype=np.float32), -14.5)) == 0
        silent = np.zeros(2400, dtype=np.float32)
        assert np.array_equal(match_loudness(silent, -14.5), silent)

    def test_the_level_is_part_of_the_voice_tag_so_the_phrase_cache_re_renders(self):
        a = UltronEngine({"loudness_dbfs": -14.5}).voice_tag
        b = UltronEngine({"loudness_dbfs": -20}).voice_tag
        assert a != b
