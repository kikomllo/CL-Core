import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils import clLogTail


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _widget(path):
    from ui.clLogWidget import LogWidget
    return LogWidget(log_path=str(path))


def _text(w):
    return w.text_edit.toPlainText()


class TestOpeningStaysCheap:
    """The real log is many MB with single lines of tens of KB (lyrics JSON). Opening used to insert all of it on the UI thread
    (5 s and growing, sometimes a frozen UI)."""

    def test_a_huge_log_opens_with_only_its_tail(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("".join(f"line {i:06d} " + "x" * 80 + "\n" for i in range(60000)), encoding="utf-8")      # ~5.4 MB
        t = time.perf_counter()
        w = _widget(log)
        elapsed = time.perf_counter() - t

        assert "line 059999" in _text(w)
        assert "line 000000" not in _text(w)
        assert w.text_edit.document().blockCount() <= w.INITIAL_TAIL_BYTES // 80
        assert elapsed < 1.0
        w.timer.stop()

    def test_the_first_line_shown_is_whole_not_cut_mid_line(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("".join(f"line {i:06d} END\n" for i in range(20000)), encoding="utf-8")
        w = _widget(log)

        assert all(l.startswith("line ") and l.endswith(" END") for l in _text(w).splitlines() if l)
        w.timer.stop()

    def test_a_small_log_is_shown_whole(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("first\nsecond\n", encoding="utf-8")
        w = _widget(log)

        assert _text(w).splitlines()[:2] == ["first", "second"]
        w.timer.stop()

    def test_a_missing_log_is_fine(self, qapp, tmp_path):
        w = _widget(tmp_path / "nope.log")

        assert _text(w) == ""
        w.timer.stop()


class TestLongLinesAndTheDocumentAreBounded:
    def test_a_lyrics_sized_line_is_cut(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("start\n" + "L" * 40000 + "\nend\n", encoding="utf-8")
        w = _widget(log)

        lines = _text(w).splitlines()

        assert max(len(l) for l in lines) < clLogTail.LINE_CHARS + 60
        assert f"[+{40000 - clLogTail.LINE_CHARS} chars]" in _text(w)
        assert lines[-1] == "end"
        w.timer.stop()

    def test_ansi_colour_codes_are_removed(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("\x1b[36m[DEBUG] cyan\x1b[0m\n", encoding="utf-8")
        w = _widget(log)

        assert _text(w).startswith("[DEBUG] cyan")
        w.timer.stop()

    def test_the_document_keeps_only_the_last_lines(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("seed\n", encoding="utf-8")
        w = _widget(log)
        for chunk in range(5):                              # 5 x 1000 short lines, each read by one poll
            with open(log, "a", encoding="utf-8") as f:
                f.write("".join(f"c{chunk} l{i}\n" for i in range(1000)))
            w.read_logs()

        assert w.text_edit.document().blockCount() <= w.MAX_BLOCKS + 1
        assert "c4 l999" in _text(w) and "seed" not in _text(w)
        w.timer.stop()


class TestFollowingTheLog:
    def test_appended_lines_show_up_once(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("one\n", encoding="utf-8")
        w = _widget(log)
        with open(log, "a", encoding="utf-8") as f:
            f.write("two\nthree\n")

        w.read_logs()
        w.read_logs()

        assert _text(w).splitlines()[:3] == ["one", "two", "three"]
        w.timer.stop()

    def test_a_line_still_being_written_waits_for_its_end(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("one\n", encoding="utf-8")
        w = _widget(log)
        with open(log, "a", encoding="utf-8") as f:
            f.write("half a li")
        w.read_logs()
        assert "half" not in _text(w)

        with open(log, "a", encoding="utf-8") as f:
            f.write("ne\n")
        w.read_logs()

        assert "half a line" in _text(w)
        w.timer.stop()

    def test_a_burst_bigger_than_one_read_shows_its_tail_and_says_so(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("one\n", encoding="utf-8")
        w = _widget(log)
        with open(log, "a", encoding="utf-8") as f:
            f.write("".join(f"burst {i:06d} " + "y" * 300 + "\n" for i in range(1500)))      # ~470 KB at once: more than one read, fewer lines than the cap

        w.read_logs()

        assert "burst 001499" in _text(w)
        assert "burst 000000" not in _text(w)
        assert "KB of log skipped" in _text(w)
        w.timer.stop()

    def test_a_rewritten_log_starts_over(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("old run line\n" * 50, encoding="utf-8")
        w = _widget(log)
        log.write_text("new run\n", encoding="utf-8")          # a restart truncates the log

        w.read_logs()

        assert _text(w).strip() == "new run"
        w.timer.stop()

    def test_the_view_follows_the_bottom(self, qapp, tmp_path):
        from PyQt6.QtCore import Qt
        log = tmp_path / "run.log"
        log.write_text("".join(f"line {i}\n" for i in range(500)), encoding="utf-8")
        w = _widget(log)
        w.resize(500, 300)
        w.show()
        qapp.processEvents()
        bar = w.text_edit.verticalScrollBar()

        with open(log, "a", encoding="utf-8") as f:
            f.write("".join(f"more {i}\n" for i in range(50)))
        w.read_logs()
        qapp.processEvents()

        assert bar.value() >= bar.maximum() - 2
        w.timer.stop()


class TestHiddenMeansNotPolling:
    def test_hiding_stops_the_timer_and_showing_catches_up(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text("one\n", encoding="utf-8")
        w = _widget(log)
        w.show()
        qapp.processEvents()
        assert w.timer.isActive()

        w.hide()
        qapp.processEvents()
        assert not w.timer.isActive()
        with open(log, "a", encoding="utf-8") as f:
            f.write("while hidden\n")
        assert "while hidden" not in _text(w)

        w.show()
        qapp.processEvents()

        assert w.timer.isActive()
        assert "while hidden" in _text(w)
        w.timer.stop()


class TestCondensedView:
    NOISE = '[12:00:00] [12:00:00] [DEBUG-MQTT] [CH: jarvis/sys/audio_vol] {"rms": 0}\n'
    EVENT = "[12:00:01] [12:00:01] [CONTROL] Light turned on\n"

    def test_condensed_is_the_default_and_hides_the_noise(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text(self.NOISE + self.EVENT, encoding="utf-8")
        w = _widget(log)

        assert w.condensed
        assert _text(w).strip() == "[12:00:01] [CONTROL] Light turned on"
        w.timer.stop()

    def test_raw_shows_the_log_as_written(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text(self.NOISE + self.EVENT, encoding="utf-8")
        w = _widget(log)

        w.condensed_btn.setChecked(False)

        assert "audio_vol" in _text(w) and "Light turned on" in _text(w)
        w.timer.stop()

    def test_switching_back_and_forth_does_not_duplicate_lines(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text(self.EVENT, encoding="utf-8")
        w = _widget(log)

        w.condensed_btn.setChecked(False)
        w.condensed_btn.setChecked(True)

        assert _text(w).count("Light turned on") == 1
        w.timer.stop()

    def test_new_lines_follow_the_current_mode(self, qapp, tmp_path):
        log = tmp_path / "run.log"
        log.write_text(self.EVENT, encoding="utf-8")
        w = _widget(log)
        with open(log, "a", encoding="utf-8") as f:
            f.write(self.NOISE + "[12:00:02] [12:00:02] [CONTROL] Light off\n")

        w.read_logs()

        assert "audio_vol" not in _text(w) and "Light off" in _text(w)
        w.timer.stop()


class TestOpenTerminalButton:
    def test_it_opens_a_terminal_in_the_same_mode_as_the_view(self, qapp, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(clLogTail, "open_terminal", lambda raw=False: calls.append(raw) or True)
        w = _widget(tmp_path / "run.log")

        w.terminal_btn.click()
        w.condensed_btn.setChecked(False)
        w.terminal_btn.click()

        assert calls == [False, True]
        w.timer.stop()

    def test_a_machine_without_a_terminal_says_so_instead_of_failing(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setattr(clLogTail, "open_terminal", lambda raw=False: False)
        w = _widget(tmp_path / "run.log")

        w.terminal_btn.click()

        assert "No terminal" in w.terminal_btn.text()
        w.timer.stop()
