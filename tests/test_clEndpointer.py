import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils.clEndpointer import Endpointer

CHUNK = 0.08


def feed(ep, probs):
    """Feeds probabilities one by one; returns (state at the end, index it fired at or None)."""
    state = None
    for i, p in enumerate(probs):
        state = ep.update(p)
        if state in (Endpointer.END, Endpointer.TIMEOUT):
            return state, i
    return state, None


class TestEndpointer:
    def test_command_ends_after_the_configured_silence(self):
        ep = Endpointer(CHUNK, end_silence_s=0.7)
        state, at = feed(ep, [0.0] * 3 + [0.9] * 6 + [0.0] * 20)
        assert state == Endpointer.END
        assert at == 3 + 6 + 8          # 9 silent chunks (0.72s) >= 0.7s, the 9th fires

    def test_a_pause_shorter_than_the_limit_keeps_the_command_going(self):
        ep = Endpointer(CHUNK, end_silence_s=0.7)
        state, at = feed(ep, [0.9] * 5 + [0.0] * 6 + [0.9] * 5 + [0.0] * 20)   # 0.48s pause
        assert at == 5 + 6 + 5 + 8

    def test_a_pause_longer_than_the_limit_ends_the_command(self):
        ep = Endpointer(CHUNK, end_silence_s=0.7)
        _, at = feed(ep, [0.9] * 5 + [0.0] * 12 + [0.9] * 5)
        assert at == 5 + 8

    def test_nothing_said_times_out(self):
        ep = Endpointer(CHUNK, wait_s=3.0)
        state, at = feed(ep, [0.02] * 100)
        assert state == Endpointer.TIMEOUT and at == 37      # 38 chunks = 3.04s

    def test_a_click_shorter_than_min_speech_does_not_start_a_command(self):
        ep = Endpointer(CHUNK, wait_s=3.0, min_speech_s=0.16)
        feed(ep, [0.0] * 5 + [0.95] + [0.0] * 5)
        assert ep.started is False

    def test_sustained_speech_starts_a_command(self):
        ep = Endpointer(CHUNK, min_speech_s=0.16)
        assert ep.update(0.9) == Endpointer.WAITING
        assert ep.update(0.9) == Endpointer.SPEAKING and ep.started

    def test_hysteresis_a_dip_between_the_thresholds_does_not_count_as_silence(self):
        ep = Endpointer(CHUNK, end_silence_s=0.4, speech_on=0.5, speech_off=0.35)
        feed(ep, [0.9] * 4)
        # 0.4 is under the "start" threshold but above the "stay speaking" one
        assert feed(ep, [0.4] * 30)[1] is None

    def test_already_speaking_skips_the_start_wait(self):
        ep = Endpointer(CHUNK, end_silence_s=0.4, already_speaking=True)
        assert ep.started is True
        _, at = feed(ep, [0.0] * 20)
        assert at == 4                                    # ends on silence alone, never times out

    def test_ptt_hold_keeps_the_command_open_through_silence(self):
        ep = Endpointer(CHUNK, end_silence_s=0.4)
        feed(ep, [0.9] * 3 + [0.0] * 4)        # 0.32s of silence so far, 0.4s would end it
        ep.hold()                              # PTT still down: the silence counter restarts
        assert ep.started and feed(ep, [0.0] * 4)[1] is None

    def test_chunks_without_a_score_are_ignored(self):
        ep = Endpointer(CHUNK, wait_s=0.2)
        for _ in range(10):
            assert ep.update(-1.0) == Endpointer.WAITING
