import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils.clEcho import ReferenceBuffer, EchoCanceller, StreamResampler24to16, RATE
from utils.clEchoGuard import EchoGuard


class TestReferenceBuffer:
    def test_returns_what_was_played_at_the_requested_time(self):
        ref = ReferenceBuffer()
        pcm = np.arange(1, 1601, dtype=np.int16)          # 0.1s
        ref.add(100.0, 0, pcm)
        np.testing.assert_array_equal(ref.get(100.0, 1600), pcm)
        np.testing.assert_array_equal(ref.get(100.0 + 800 / RATE, 800), pcm[800:])

    def test_offsets_continue_an_utterance_gaplessly(self):
        ref = ReferenceBuffer()
        ref.add(50.0, 0, np.full(1000, 7, dtype=np.int16))
        ref.add(50.0, 1000, np.full(1000, 9, dtype=np.int16))
        got = ref.get(50.0, 2000)
        assert (got[:1000] == 7).all() and (got[1000:] == 9).all()

    def test_silence_where_nothing_was_played(self):
        ref = ReferenceBuffer()
        ref.add(10.0, 0, np.full(800, 5, dtype=np.int16))
        assert not ref.get(10.0 + 1.0, 800).any()          # a second later
        assert not ref.get(9.0, 800).any()                 # before it started

    def test_an_old_lap_of_the_ring_is_never_returned(self):
        ref = ReferenceBuffer(seconds=2)
        ref.add(0.0, 0, np.full(RATE, 3, dtype=np.int16))                  # t = 0..1s
        ref.add(2.0, 0, np.full(RATE, 4, dtype=np.int16))                  # t = 2..3s reuses the same slots
        assert not ref.get(0.0, RATE).any()                                # the overwritten second is gone, not stale
        assert (ref.get(2.0, RATE) == 4).all()

    def test_active_tracks_playing_and_a_short_echo_tail(self):
        ref = ReferenceBuffer()
        assert ref.active() is False
        now = time.time()
        ref.add(now, 0, np.zeros(RATE, dtype=np.int16))                    # plays for the next second
        assert ref.active(now + 0.5) is True
        assert ref.active(now + 1.4) is True                               # inside the 0.6s tail
        assert ref.active(now + 2.0) is False


class TestEchoCanceller:
    def test_passes_the_microphone_through_without_pyaec(self):
        ec = EchoCanceller()
        ec.available = False
        mic = np.arange(320, dtype=np.int16)
        np.testing.assert_array_equal(ec.process(mic, np.zeros(320, dtype=np.int16)), mic)

    def test_removes_a_delayed_echo_of_the_reference(self):
        pytest.importorskip("pyaec")
        rng = np.random.default_rng(3)
        n = RATE * 6
        far = (rng.standard_normal(n) * 3000).astype(np.int16)
        delay = int(0.08 * RATE)
        echo = np.zeros(n); echo[delay:] = far[:-delay] * 0.5
        mic = np.clip(echo, -32768, 32767).astype(np.int16)

        ec = EchoCanceller()
        out = np.concatenate([ec.process(mic[i:i + 1280], far[i:i + 1280]) for i in range(0, n - 1280, 1280)])

        tail = slice(RATE * 3, RATE * 5)                     # after it has converged
        rms = lambda x: float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
        assert 20 * np.log10(rms(mic[tail]) / max(rms(out[tail]), 1e-9)) > 12


class TestStreamResampler:
    def test_chunked_output_matches_resampling_the_whole_signal(self):
        from scipy.signal import resample_poly
        t = np.arange(24000) / 24000
        x = (0.5 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 3100 * t)).astype(np.float32)
        whole = resample_poly(x, 2, 3)
        rs = StreamResampler24to16()
        out = np.concatenate([rs.process(x[i:i + 1920]) for i in range(0, len(x), 1920)])
        n = len(out)
        assert len(whole) - n <= StreamResampler24to16.LOOK        # only the held-back tail is missing
        np.testing.assert_allclose(out, whole[:n], atol=1e-5)      # identical, including at every chunk seam

    def test_uneven_chunk_sizes_make_no_difference(self):
        from scipy.signal import resample_poly
        x = np.random.default_rng(1).standard_normal(20000).astype(np.float32) * 0.3
        whole = resample_poly(x, 2, 3)
        rs = StreamResampler24to16()
        parts, i = [], 0
        for size in (1, 7, 480, 1920, 333, 2500, 4001, 100):
            parts.append(rs.process(x[i:i + size])); i += size
        parts.append(rs.process(x[i:]))
        out = np.concatenate(parts)
        np.testing.assert_allclose(out, whole[:len(out)], atol=1e-5)


class TestEchoGuard:
    def make(self, tmp_path=None, **kw):
        return EchoGuard(target_esr_db=6.0, state_path=str(tmp_path / "state.json") if tmp_path else None, **kw)

    def test_leaves_a_quiet_speaker_alone(self):
        g = self.make()
        for _ in range(4):
            assert g.note_echo(0.0057, 1.0) is None            # echo 15 dB under a 0.03 voice
        assert g.gain == 1.0

    def test_lowers_a_speaker_too_loud_to_interrupt(self):
        g = self.make()
        g.note_echo(0.30, 1.0)                                 # echo 10x the voice
        new = g.note_echo(0.30, 1.0)
        assert new is not None and new < 1.0
        assert new >= 0.5 * 1.0 - 1e-9                         # never more than 6 dB per step

    def test_converges_to_the_target_ratio_over_a_few_replies(self):
        g = self.make()
        gain = 1.0
        for _ in range(12):
            new = g.note_echo(0.30 * gain, gain)               # echo scales with the gain we play at
            gain = new if new is not None else gain
        assert 0.8 * g.target * g.voice <= 0.30 * gain <= 1.25 * g.target * g.voice

    def test_never_below_the_floor(self):
        g = self.make(voice_default=0.001)
        gain = 1.0
        for _ in range(30):
            new = g.note_echo(1.0 * gain, gain)
            gain = new if new is not None else gain
        assert gain >= g.min_gain - 1e-9

    def test_comes_back_up_when_there_is_room(self):
        g = self.make()
        g.gain = 0.3
        for _ in range(3):
            g.note_echo(0.01 * 0.3, 0.3)                       # echo now far under the voice
        assert g.gain > 0.3

    def test_a_single_loud_reading_is_not_acted_on(self):
        g = self.make()
        assert g.note_echo(0.5, 1.0) is None                   # could be the user talking over it

    def test_learns_the_users_voice_level(self):
        g = self.make()
        for v in (0.1, 0.1, 0.1):
            g.note_voice(v)
        assert g.voice == pytest.approx(0.1)

    def test_state_survives_a_restart(self, tmp_path):
        g = self.make(tmp_path)
        g.note_echo(0.3, 1.0); g.note_echo(0.3, 1.0)
        for v in (0.02, 0.02, 0.02):
            g.note_voice(v)
        again = self.make(tmp_path)
        assert again.gain == pytest.approx(g.gain) and again.voice == pytest.approx(0.02)
