import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

openwakeword = pytest.importorskip("openwakeword")
from utils.clVad import SileroVad, WINDOW


@pytest.fixture(scope="module")
def vad():
    return SileroVad()


class TestSileroVad:
    def test_silence_scores_as_non_speech(self, vad):
        vad.reset()
        assert vad.process(np.zeros(1280, dtype=np.int16)) < 0.1

    def test_steady_noise_is_not_speech(self, vad):
        vad.reset()
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(1280) * 300).astype(np.int16)
        assert max(vad.process(noise) for _ in range(10)) < 0.5

    def test_chunks_that_are_not_a_whole_number_of_windows_carry_over(self, vad):
        vad.reset()
        # 1280 samples = 2 full 480-sample windows + 320 left over; the leftover joins the next chunk
        assert vad.process(np.zeros(1280, dtype=np.int16)) >= 0.0
        assert len(vad._tail) == 1280 - 2 * WINDOW
        vad.process(np.zeros(1280, dtype=np.int16))
        assert len(vad._tail) == (320 + 1280) - 3 * WINDOW

    def test_a_chunk_too_short_for_a_window_gives_no_score_yet(self, vad):
        vad.reset()
        assert vad.process(np.zeros(100, dtype=np.int16)) == -1.0

    def test_reset_forgets_the_previous_utterance(self, vad):
        vad.process(np.zeros(1000, dtype=np.int16))
        vad.reset()
        assert len(vad._tail) == 0 and not vad._h.any() and not vad._c.any()
