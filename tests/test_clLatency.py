import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils.clLatency import LatencyTrace


class TestLatencyTrace:
    def test_gaps_are_labelled_by_the_stage_they_end_at(self):
        trace = LatencyTrace({"t_speech_end": 100.0, "t_dispatch": 102.1, "t_stt_done": 102.6})
        trace.mark("t_daemon_recv", 102.7)
        summary = trace.summary()
        assert "endpoint=2.10s" in summary
        assert "whisper=0.50s" in summary      # skips absent stages in between
        assert "mqtt=0.10s" in summary
        assert "TOTAL speech_end->daemon_recv: 2.70s" in summary

    def test_first_mark_wins(self):
        trace = LatencyTrace()
        trace.mark("t_action", 5.0)
        trace.mark("t_action", 9.0)
        assert trace.t["t_action"] == 5.0

    def test_ignores_unknown_and_non_numeric_carried_fields(self):
        trace = LatencyTrace({"t_dispatch": 1.0, "evil": 2.0, "t_stt_recv": "x"})
        assert set(trace.t) == {"t_dispatch"}

    def test_summary_is_empty_with_fewer_than_two_stages(self):
        assert LatencyTrace().summary() == ""
        assert LatencyTrace({"t_dispatch": 1.0}).summary() == ""

    def test_missing_carried_trace_is_fine(self):
        trace = LatencyTrace(None)
        trace.mark("t_daemon_recv", 10.0)
        trace.mark("t_routed", 10.05)
        assert "route=0.05s" in trace.summary()

    def test_reports_whether_tts_was_cached(self):
        trace = LatencyTrace({"t_tts_req": 1.0, "t_audio": 1.5})
        trace.cached = True
        assert "(tts cached)" in trace.summary()
