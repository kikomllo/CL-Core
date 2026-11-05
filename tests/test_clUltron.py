import os
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils.clTTSEngines import UltronEngine, PIPER_VOICE, PIPER_SPEAKER, _overlay
from utils.clVoice import level_settings, merge_settings

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
        eng = make_engine(tmp_path, {"fx_level": "off"})
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
    def ultron_manager(self, tmp_path):
        with patch('clTTS.ConfigLoader') as MockLoader:
            MockLoader.return_value.load_json.side_effect = lambda name: (
                {"settings": {"silent_mode": False, "tts_engine": "ultron", "tts_settings": {"fx_level": "light"}}}
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
