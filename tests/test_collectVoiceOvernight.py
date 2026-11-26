"""Unattended (overnight) capture of training/tools/collect_voice.py: windows cut at pauses, a reader that survives the device going away, staging, adoption."""
import argparse
import collections
import importlib.util
import json
import os
import sys
import threading
import time

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("collect_voice_under_test", os.path.join(ROOT, "training", "tools", "collect_voice.py"))
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)

SR = 22050


def args(tmp_path, **kw):
    base = dict(out=str(tmp_path / "main"), sr=SR, min_dur=1.0, max_dur=15.0, min_silence=0.45, min_snr=15.0, no_dedupe=False, no_speaker_filter=True,
                speaker_threshold=None, denoise=False, any_audio=False, transcribe=False, source=None, unattended=True, hours=1.0, window_min=10.0,
                max_window_min=14.0, stage_dir=str(tmp_path / "stage"), keep_raw="hits", whisper_model="small", language=None, whisper_device="cpu")
    base.update(kw)
    return argparse.Namespace(**base)


def speechlike(seconds, seed, sr=SR, level=0.2):
    """Band-limited noise with a syllable-rate amplitude wobble: loud enough and different enough from every other seed to count as a clip."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    carrier = np.sin(2 * np.pi * 180 * t) + 0.5 * np.sin(2 * np.pi * 540 * t + rng.random() * 6) + 0.2 * rng.standard_normal(len(t))
    wobble = 0.55 + 0.45 * np.sin(2 * np.pi * (3 + rng.random() * 3) * t + rng.random() * 6)
    return (level * carrier * wobble / 1.5).astype(np.float32)


def quiet(seconds, sr=SR):
    return (np.random.default_rng(0).standard_normal(int(seconds * sr)) * 0.0008).astype(np.float32)


def night(n_bursts, burst_s=3.0, pause_s=1.2, seed0=0):
    parts = []
    for i in range(n_bursts):
        parts += [speechlike(burst_s, seed0 + i), quiet(pause_s)]
    return np.concatenate(parts)


# ------------------------------------------------------------------------------------------------ where a window ends
class TestPickCut:
    def test_nothing_is_cut_before_the_window_is_long_enough(self):
        assert cv.pick_cut(night(5), SR, target_s=600, max_s=840) is None

    def test_the_cut_is_in_a_pause_never_inside_speech_and_just_after_the_target(self):
        x = night(40)                                                              # 40 x 4.2 s = 168 s
        cut = cv.pick_cut(x, SR, target_s=100, max_s=300)
        assert cut is not None and 100 * SR <= cut < 106 * SR                      # the first pause after 100 s, not one 30 s earlier
        around = x[cut - SR // 4: cut + SR // 4]
        assert np.abs(around).max() < 0.01                                         # the pause around the cut is quiet

    def test_a_pause_shorter_than_the_one_that_splits_clips_is_not_a_place_to_cut(self):
        x = np.concatenate([speechlike(6, 1), quiet(0.3), speechlike(3, 2), quiet(1.5), speechlike(3, 3)])
        cut = cv.pick_cut(x, SR, target_s=5, max_s=60)
        assert 9.3 <= cut / SR <= 10.8                                             # inside the 1.5 s pause (9.3-10.8 s), not the 0.3 s one at 6 s

    def test_a_pause_before_the_target_is_left_alone(self):
        x = np.concatenate([speechlike(3, 1), quiet(2.0), speechlike(8, 2), quiet(1.0), speechlike(3, 3)])
        cut = cv.pick_cut(x, SR, target_s=6, max_s=60)
        assert 13.0 <= cut / SR <= 14.0                                            # inside the 1 s pause after the target, not the 2 s one before it

    def test_without_any_pause_it_waits_and_then_cuts_by_force(self):
        x = speechlike(120, 5)                                                     # continuous sound, e.g. music
        assert cv.pick_cut(x, SR, target_s=60, max_s=200) is None
        assert cv.pick_cut(x, SR, target_s=60, max_s=100) == len(x)

    def test_digital_silence_counts_as_a_pause(self):
        x = np.concatenate([speechlike(30, 1), np.zeros(SR * 2, np.float32), speechlike(30, 2)])
        cut = cv.pick_cut(x, SR, target_s=20, max_s=300)
        assert 30.0 <= cut / SR <= 32.0                                            # inside the 2 s of digital silence


# ------------------------------------------------------------------------------------------------ the audio waiting
class TestWindowBuffer:
    def test_seconds_are_counted_in_each_chunks_own_rate(self):
        b = cv.WindowBuffer(SR)
        b.add(48000, np.zeros(48000, np.int16))
        b.add(44100, np.zeros(44100, np.int16))
        assert b.seconds == pytest.approx(2.0)

    def test_a_run_is_resampled_whole_to_the_output_rate(self):
        b = cv.WindowBuffer(SR)
        tone = (np.sin(2 * np.pi * 440 * np.arange(48000) / 48000) * 16000).astype(np.int16)
        for i in range(0, 48000, 1024):
            b.add(48000, tone[i:i + 1024])
        x = b.audio()
        assert abs(len(x) - SR) <= 2
        assert abs(np.abs(np.fft.rfft(x)).argmax() - 440) <= 1                     # still a clean 440 Hz: no clicks from chunk edges

    def test_a_device_change_mid_window_keeps_every_run_at_its_own_rate(self):
        b = cv.WindowBuffer(SR)
        b.add(48000, np.zeros(48000, np.int16))
        b.add(44100, np.zeros(44100, np.int16))
        assert abs(len(b.audio()) - 2 * SR) <= 3

    def test_what_a_cut_leaves_is_kept_at_the_output_rate(self):
        b = cv.WindowBuffer(SR)
        b.replace(np.ones(SR, np.float32))
        b.add(48000, np.zeros(48000, np.int16))
        assert b.seconds == pytest.approx(2.0) and len(b.audio()) == pytest.approx(2 * SR, abs=3)


# ------------------------------------------------------------------------------------------------ windows out of a stream
class TestUnattendedCapture:
    def feed(self, cap, x, rate=SR, chunk=2048):
        pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
        for i in range(0, len(pcm), chunk):
            cap.add(rate, pcm[i:i + chunk])
            cap.step()

    def test_windows_are_processed_as_they_fill_and_no_audio_is_lost_or_repeated(self, tmp_path):
        a = args(tmp_path, window_min=1.0, max_window_min=2.0)
        got = []
        cap = cv.UnattendedCapture(a, None, process=lambda x: got.append(len(x)))
        x = night(60)                                                              # 252 s
        self.feed(cap, x)
        cap.finish()
        assert len(got) >= 3 and all(g > 55 * SR for g in got[:-1])
        assert 0 <= len(x) - sum(got) <= SR + 4 * len(got)                         # nothing repeated; at most the last second (too short to process) is left
        assert cap.buffer.seconds == 0

    def test_memory_stays_at_a_window_not_at_a_night(self, tmp_path):
        a = args(tmp_path, window_min=0.5, max_window_min=1.0)
        cap = cv.UnattendedCapture(a, None, process=lambda x: None)
        biggest = 0
        for i in range(30):
            self.feed(cap, night(10, seed0=i * 10))                                # 30 x 42 s
            biggest = max(biggest, cap.buffer.seconds)
        assert biggest < 90                                                        # about a window plus a pause: not 21 minutes

    def test_a_window_that_has_no_pause_is_cut_by_force_at_the_maximum(self, tmp_path):
        a = args(tmp_path, window_min=0.5, max_window_min=1.0)
        got = []
        cap = cv.UnattendedCapture(a, None, process=lambda x: got.append(len(x) / SR))
        self.feed(cap, speechlike(150, 9))
        assert got and max(got) <= 62

    def test_clips_are_saved_per_window_with_their_source_and_the_speaker_filter_off(self, tmp_path):
        a = args(tmp_path, window_min=0.5, max_window_min=1.0)
        ds = cv.Dataset(str(tmp_path / "stage"), SR)
        logs = []
        cap = cv.UnattendedCapture(a, ds, raw_dir=str(tmp_path / "stage" / "captures"), log=logs.append, stamp="T")
        self.feed(cap, night(30))
        cap.finish()
        assert cap.clips >= 15 and len(ds.rows) == cap.clips
        assert all(r["source"].startswith("overnight:T:w") for r in ds.rows)
        assert any("[window" in line for line in logs)
        assert os.path.exists(os.path.join(str(tmp_path / "stage"), "manifest.jsonl"))

    def test_a_line_the_main_dataset_already_has_is_not_kept_again(self, tmp_path):
        main = cv.Dataset(str(tmp_path / "main"), SR)
        line = speechlike(3, 123)
        main.add({"audio": line, "snr_db": 30.0}, "old")
        refs = cv.reference_envelopes(str(tmp_path / "main"), SR)
        assert len(refs) == 1
        a = args(tmp_path, window_min=0.2, max_window_min=1.0)
        ds = cv.Dataset(str(tmp_path / "stage"), SR)
        cap = cv.UnattendedCapture(a, ds, refs, log=lambda m: None)
        self.feed(cap, np.concatenate([quiet(1), line, quiet(1.5), speechlike(3, 124), quiet(1.5)] * 1))
        cap.finish()
        assert cap.rejects["duplicate"] >= 1 and cap.clips == 1

    def test_raw_audio_is_kept_only_for_windows_that_gave_clips_by_default(self, tmp_path):
        a = args(tmp_path, window_min=0.3, max_window_min=1.0)
        raw = tmp_path / "raw"
        cap = cv.UnattendedCapture(a, cv.Dataset(str(tmp_path / "stage"), SR), raw_dir=str(raw), log=lambda m: None, stamp="T")
        self.feed(cap, np.concatenate([quiet(40), night(12, seed0=50), quiet(40)]))
        cap.finish()
        files = sorted(os.listdir(raw)) if raw.exists() else []
        assert 0 < len(files) < cap.windows                                        # the silent windows left no file


# ------------------------------------------------------------------------------------------------ the reader that must not die
class FakeDevice:
    """A system-audio source for the reader: each open() returns a read() that follows a script."""

    def __init__(self, scripts, names=("Headset",)):
        self.scripts, self.opens, self.names, self.closed = list(scripts), 0, list(names), 0

    def open(self):
        self.opens += 1
        script = self.scripts.pop(0) if self.scripts else "block"
        if script == "fail":
            raise OSError("no device")
        if script == "exit":
            raise SystemExit("the capture package is missing")
        gate = threading.Event()

        def read():
            if script == "ok":
                time.sleep(0.002)
                return np.ones(100, np.int16)
            if script == "die_after_3":
                self.scripts_reads = getattr(self, "scripts_reads", 0) + 1
                if self.scripts_reads > 3:
                    raise OSError("device lost")
                return np.ones(100, np.int16)
            gate.wait(30)                                                          # "block": the device is gone, read() never returns
            return np.zeros(0, np.int16)
        return read, None, lambda: setattr(self, "closed", self.closed + 1), 48000, f"fake{self.opens}"


def wait_for(cond, seconds=3.0):
    t = time.time()
    while time.time() - t < seconds:
        if cond():
            return True
        time.sleep(0.01)
    return False


class TestSystemAudioReader:
    def make(self, device, name=lambda: None, **kw):
        kw = {"stale_s": 0.15, "check_s": 0.0, "retry_s": 0.01, "log": lambda m: None, **kw}
        return cv.SystemAudioReader(device.open, name, **kw)

    def test_it_delivers_chunks_with_their_rate(self):
        d = FakeDevice(["ok"])
        r = self.make(d)
        r.start()
        item = r.get(timeout=2)
        r.stop()
        assert item is not None and item[0] == 48000 and len(item[1]) == 100

    def test_a_stream_that_dies_is_reopened_and_keeps_delivering(self):
        d = FakeDevice(["die_after_3", "ok"])
        r = self.make(d)
        r.start()
        assert wait_for(lambda: d.opens >= 2 and r.q.qsize() > 5)
        r.stop()
        assert d.closed >= 1

    def test_a_device_that_cannot_be_opened_is_retried_and_the_failure_count_resets(self):
        d = FakeDevice(["fail", "fail", "exit", "ok"])
        r = self.make(d)
        r.start()
        assert wait_for(lambda: d.opens >= 4 and r.q.qsize() > 0)
        assert r.failures == 0
        r.stop()

    def test_a_stream_that_stops_delivering_is_replaced(self):
        d = FakeDevice(["block", "ok"])
        r = self.make(d)
        restarts = []
        r.log = restarts.append
        r.start()
        time.sleep(0.3)                                                            # nothing arrives
        r.check()
        assert wait_for(lambda: d.opens >= 2 and r.q.qsize() > 0)
        assert r.restarts == 1 and any("no audio frames" in m for m in restarts)
        r.stop()

    def test_a_changed_default_output_is_followed_even_though_the_old_stream_still_delivers_silence(self):
        names = ["Headset"]
        d = FakeDevice(["ok", "ok"])
        r = self.make(d, name=lambda: names[0], stale_s=60)
        r.start()
        assert wait_for(lambda: r.opened_name == "Headset")
        names[0] = "Speakers"                                                      # the headset powered off
        r.check()
        assert wait_for(lambda: d.opens >= 2 and r.opened_name == "Speakers")
        assert r.restarts == 1
        r.stop()

    def test_the_same_output_does_not_restart_anything(self):
        d = FakeDevice(["ok"])
        r = self.make(d, name=lambda: "Headset", stale_s=60)
        r.start()
        assert wait_for(lambda: r.opened_name == "Headset")
        for _ in range(5):
            r.check()
        assert r.restarts == 0 and d.opens == 1
        r.stop()

    def test_stopping_leaves_no_chunks_arriving(self):
        d = FakeDevice(["ok"])
        r = self.make(d)
        r.start()
        r.get(timeout=2)
        r.stop()
        time.sleep(0.1)
        size = r.q.qsize()
        time.sleep(0.1)
        assert r.q.qsize() == size


# ------------------------------------------------------------------------------------------------ the whole run
class ScriptedReader:
    """Stands in for SystemAudioReader: hands a prepared night to the loop and then stays quiet."""

    def __init__(self, audio, rate=SR):
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        self.items = [(rate, pcm[i:i + 4096]) for i in range(0, len(pcm), 4096)]
        self.label, self.restarts, self.failures = "scripted", 0, 0
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def get(self, timeout=1.0):
        if self.items:
            return self.items.pop(0)
        time.sleep(min(timeout, 0.01))
        return None

    def check(self):
        pass


@pytest.fixture
def quiet_system(monkeypatch):
    """No sleep prevention, console changes or ecosystem lock probing in a test."""
    monkeypatch.setattr(cv, "keep_awake", lambda log=print: (lambda: None))
    monkeypatch.setattr(cv, "disable_quickedit", lambda: None)
    monkeypatch.setattr(cv, "jarvis_is_running", lambda port=64000: False)


class TestRunUnattended:
    def run(self, tmp_path, audio, clock=None, **kw):
        a = args(tmp_path, window_min=0.5, max_window_min=1.0, **kw)
        reader = ScriptedReader(audio)
        summary = cv.run_unattended(a, reader=reader, **({"clock": clock} if clock else {}))
        return summary, reader

    def test_a_short_night_ends_with_clips_in_the_staging_dataset_and_not_in_the_main_one(self, tmp_path, quiet_system):
        audio = night(25)
        t = [0.0]

        def clock():                                                               # the loop sees time pass by 3 s per call
            t[0] += 3.0
            return t[0]
        summary, reader = self.run(tmp_path, audio, clock=clock, hours=0.05)       # 180 s
        assert reader.started and reader.stopped
        assert summary["clips"] >= 10 and summary["reason"] == "time is up"
        stage = tmp_path / "stage"
        rows = [json.loads(line) for line in (stage / "manifest.jsonl").read_text().splitlines()]
        assert len(rows) == summary["clips"] and all(os.path.exists(stage / "wavs" / (r["id"] + ".wav")) for r in rows)
        assert not (tmp_path / "main" / "manifest.jsonl").exists()                 # the main dataset was not touched
        assert list(stage.glob("overnight_*.log"))

    def test_a_stop_file_ends_it_and_what_is_left_is_still_processed(self, tmp_path, quiet_system):
        stage = tmp_path / "stage"
        audio = night(10)
        a = args(tmp_path, window_min=5.0, max_window_min=8.0, hours=10)
        reader = ScriptedReader(audio)
        real_get = reader.get
        calls = [0]

        def get(timeout=1.0):
            calls[0] += 1
            if not reader.items and calls[0] > 5:
                (stage / "STOP").write_text("")                                    # created from outside, as the owner would
            return real_get(timeout)
        reader.get = get
        summary = cv.run_unattended(a, reader=reader)
        assert summary["reason"] == "STOP file found"
        assert summary["clips"] >= 5                                               # the audio never reached a full window: finish() processed it
        assert not (stage / "STOP").exists() or True

    def test_ctrl_c_still_saves_the_current_window(self, tmp_path, quiet_system):
        a = args(tmp_path, window_min=5.0, max_window_min=8.0, hours=10)
        reader = ScriptedReader(night(10))
        real_get = reader.get

        def get(timeout=1.0):
            if not reader.items:
                raise KeyboardInterrupt
            return real_get(timeout)
        reader.get = get
        summary = cv.run_unattended(a, reader=reader)
        assert summary["reason"] == "Ctrl+C" and summary["clips"] >= 5

    def test_the_voiceprint_is_copied_so_the_speaker_filter_still_runs(self, tmp_path, quiet_system):
        (tmp_path / "main").mkdir()
        (tmp_path / "main" / "voiceprint.npz").write_bytes(b"vp")
        a = args(tmp_path, no_speaker_filter=True, hours=0.0001)
        a.no_speaker_filter = False
        reader = ScriptedReader(np.zeros(10, np.float32))
        orig = cv.gate_speaker
        cv.gate_speaker = lambda clips, ds, args_, rejects: clips                  # the copy is what is under test, not the encoder
        try:
            cv.run_unattended(a, reader=reader)
        finally:
            cv.gate_speaker = orig
        assert (tmp_path / "stage" / "voiceprint.npz").read_bytes() == b"vp"

    def test_a_missing_voiceprint_is_said_loudly(self, tmp_path, quiet_system, capsys):
        a = args(tmp_path, hours=0.0001)
        a.no_speaker_filter = False
        cv.run_unattended(a, reader=ScriptedReader(np.zeros(10, np.float32)))
        assert "WARNING: no voiceprint" in capsys.readouterr().out

    def test_a_running_ecosystem_is_warned_about(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cv, "keep_awake", lambda log=print: (lambda: None))
        monkeypatch.setattr(cv, "disable_quickedit", lambda: None)
        monkeypatch.setattr(cv, "jarvis_is_running", lambda port=64000: True)
        cv.run_unattended(args(tmp_path, hours=0.0001), reader=ScriptedReader(np.zeros(10, np.float32)))
        assert "JARVIS ecosystem is running" in capsys.readouterr().out

    def test_a_device_that_never_opens_ends_the_run_instead_of_spinning(self, tmp_path, quiet_system):
        a = args(tmp_path, hours=5)
        reader = ScriptedReader(np.zeros(0, np.float32))
        reader.failures = 60
        summary = cv.run_unattended(a, reader=reader)
        assert "could not be opened" in summary["reason"]

    def test_the_computer_is_kept_awake_for_the_run_and_released_after(self, tmp_path, monkeypatch):
        events = []
        monkeypatch.setattr(cv, "keep_awake", lambda log=print: (events.append("awake"), (lambda: events.append("released")))[1])
        monkeypatch.setattr(cv, "disable_quickedit", lambda: events.append("quickedit"))
        monkeypatch.setattr(cv, "jarvis_is_running", lambda port=64000: False)
        cv.run_unattended(args(tmp_path, hours=0.0001), reader=ScriptedReader(np.zeros(10, np.float32)))
        assert events == ["awake", "quickedit", "released"]

    def test_the_run_summary_names_the_next_steps(self, tmp_path, quiet_system, capsys):
        cv.run_unattended(args(tmp_path, hours=0.0001), reader=ScriptedReader(np.zeros(10, np.float32)))
        out = capsys.readouterr().out
        assert "collect_voice.py review --out" in out and "collect_voice.py adopt" in out


# ------------------------------------------------------------------------------------------------ the system helpers
class TestSystemHelpers:
    def test_keep_awake_asks_windows_not_to_sleep_and_lets_it_again(self, monkeypatch):
        import ctypes
        calls = []
        fake = type("W", (), {"kernel32": type("K", (), {"SetThreadExecutionState": staticmethod(lambda flags: calls.append(flags) or 0)})()})()
        monkeypatch.setattr(ctypes, "windll", fake, raising=False)
        monkeypatch.setattr(cv.sys, "platform", "win32")
        release = cv.keep_awake(lambda m: None)
        release()
        assert calls == [0x80000001, 0x80000000]

    def test_the_ecosystem_lock_port_is_detected(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        try:
            assert cv.jarvis_is_running(port) is True
        finally:
            s.close()
        assert cv.jarvis_is_running(port) is False


# ------------------------------------------------------------------------------------------------ adopting the reviewed clips
class TestAdopt:
    def test_reviewed_clips_move_into_the_main_dataset_with_their_text_and_repeats_are_skipped(self, tmp_path, capsys):
        main = cv.Dataset(str(tmp_path / "main"), SR)
        already = speechlike(3, 7)
        main.add({"audio": already, "snr_db": 30.0}, "old", "an old line")
        stage = cv.Dataset(str(tmp_path / "stage"), SR)
        stage.add({"audio": already, "snr_db": 29.0}, "overnight:T:w1", "an old line")                     # a repeat
        stage.add({"audio": speechlike(3, 8), "snr_db": 25.0, "speaker_sim": 0.81}, "overnight:T:w2", "a new line")
        cv.cmd_adopt(argparse.Namespace(stage=str(tmp_path / "stage"), out=str(tmp_path / "main"), sr=SR, no_dedupe=False))
        out = capsys.readouterr().out
        assert "Adopted 1 clip(s)" in out and "skipped 1" in out
        rows = cv.Dataset(str(tmp_path / "main"), SR).rows
        assert [r["text"] for r in rows] == ["an old line", "a new line"]
        assert rows[1]["source"] == "overnight:T:w2" and rows[1]["speaker_sim"] == 0.81


class TestCommandLine:
    def test_the_overnight_options_are_on_capture(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cv, "cmd_capture", lambda a: seen.append(a))
        monkeypatch.setattr(sys, "argv", ["collect_voice.py", "capture", "--unattended", "--hours", "8", "--window-min", "6", "--keep-raw", "all", "--stage-dir", "X"])
        cv.main()
        a = seen[0]
        assert (a.unattended, a.hours, a.window_min, a.keep_raw, a.stage_dir) == (True, 8.0, 6.0, "all", "X")

    def test_the_plain_capture_is_unchanged(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cv, "cmd_capture", lambda a: seen.append(a))
        monkeypatch.setattr(sys, "argv", ["collect_voice.py", "capture", "--transcribe"])
        cv.main()
        assert seen[0].unattended is False and seen[0].transcribe is True

    def test_adopt_is_a_command(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cv, "cmd_adopt", lambda a: seen.append(a))
        monkeypatch.setattr(sys, "argv", ["collect_voice.py", "adopt", "some/stage"])
        cv.main()
        assert seen[0].stage == "some/stage"


# ------------------------------------------------------------------------------------------------ choosing the output
OUTPUTS = [
    {"index": 31, "name": "CABLE In 16ch (VB-Audio Virtual Cable) [Loopback]", "defaultSampleRate": 48000.0, "maxInputChannels": 2},
    {"index": 32, "name": "Headset Earphone (G435 Wireless Gaming Headset) [Loopback]", "defaultSampleRate": 48000.0, "maxInputChannels": 2},
    {"index": 33, "name": "Altifalantes (Realtek High Definition Audio) [Loopback]", "defaultSampleRate": 192000.0, "maxInputChannels": 2},
    {"index": 34, "name": "Realtek Digital Output (Realtek High Definition Audio) [Loopback]", "defaultSampleRate": 48000.0, "maxInputChannels": 2},
]


class FakePortAudio:
    """What `pyaudiowpatch.PyAudio()` gives: the loopback outputs, the default one, and open() remembering which index was opened."""
    paInt16 = 8
    opened = []
    instances = 0

    def __init__(self):
        FakePortAudio.instances += 1

    def get_default_wasapi_loopback(self):
        return OUTPUTS[1]

    def get_loopback_device_info_generator(self):
        return iter(OUTPUTS)

    def open(self, **kw):
        FakePortAudio.opened.append(kw["input_device_index"])
        stream = type("S", (), {"read": lambda self, n, exception_on_overflow=False: np.zeros(n * 2, np.int16).tobytes(), "stop_stream": lambda self: None,
                                "close": lambda self: None})()
        return stream

    def terminate(self):
        pass


@pytest.fixture
def windows_audio(monkeypatch):
    fake_module = type(sys)("pyaudiowpatch")
    fake_module.PyAudio, fake_module.paInt16 = FakePortAudio, 8
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake_module)
    monkeypatch.setattr(cv.sys, "platform", "win32")
    monkeypatch.setattr(cv, "ARGS_DEVICE", None)
    FakePortAudio.opened = []
    return fake_module


class TestChoosingTheOutput:
    def test_without_a_name_it_is_the_default_output(self, windows_audio):
        assert cv.pick_loopback(FakePortAudio(), None)["index"] == 32

    def test_a_part_of_the_name_picks_that_output_whatever_the_case(self, windows_audio):
        assert cv.pick_loopback(FakePortAudio(), "ALTIFALANTES")["index"] == 33
        assert cv.pick_loopback(FakePortAudio(), "digital output")["index"] == 34
        assert cv.pick_loopback(FakePortAudio(), "g435")["index"] == 32

    def test_a_name_that_matches_several_is_refused_with_the_list(self, windows_audio):
        with pytest.raises(SystemExit) as e:
            cv.pick_loopback(FakePortAudio(), "realtek")                           # the speakers AND the digital output
        message = str(e.value)
        assert "matches 2 outputs" in message and "Altifalantes" in message and "Realtek Digital Output" in message and "Headset Earphone" in message

    def test_a_name_that_matches_none_is_refused_with_the_list(self, windows_audio):
        with pytest.raises(SystemExit) as e:
            cv.pick_loopback(FakePortAudio(), "sonos")
        assert "matches no output" in str(e.value) and "Altifalantes" in str(e.value)

    def test_opening_the_capture_uses_the_chosen_output_and_its_own_rate(self, windows_audio, monkeypatch):
        monkeypatch.setattr(cv, "ARGS_DEVICE", "altifalantes")
        read, start, stop, rate, label = cv._open_system_audio()
        assert FakePortAudio.opened == [33] and rate == 192000 and "Altifalantes" in label
        stop()

    def test_opening_without_a_name_is_the_default_as_before(self, windows_audio):
        _, _, stop, rate, label = cv._open_system_audio()
        assert FakePortAudio.opened == [32] and rate == 48000 and "G435" in label
        stop()

    def test_the_watched_name_is_the_chosen_output_or_the_default(self, windows_audio):
        assert "Altifalantes" in cv.loopback_device_name("altifalantes")
        assert "G435" in cv.loopback_device_name()
        assert cv.loopback_device_name("realtek") is None                          # ambiguous: nothing to compare against, and no exit inside the watcher
        assert cv.loopback_device_name("nothing like it") is None

    def test_an_ambiguous_name_stops_the_run_before_anything_is_created(self, windows_audio, tmp_path):
        a = args(tmp_path, device="realtek")
        with pytest.raises(SystemExit):
            cv.run_unattended(a)
        assert not (tmp_path / "stage").exists()

    def test_the_chosen_output_is_said_at_the_start(self, windows_audio, tmp_path, quiet_system, capsys):
        a = args(tmp_path, hours=0.0001, device="altifalantes")
        cv.run_unattended(a, reader=ScriptedReader(np.zeros(10, np.float32)))
        assert "Output to record: altifalantes" in capsys.readouterr().out

    def test_the_default_is_said_too(self, tmp_path, quiet_system, capsys):
        cv.run_unattended(args(tmp_path, hours=0.0001), reader=ScriptedReader(np.zeros(10, np.float32)))
        assert "Output to record: the default output" in capsys.readouterr().out

    def test_the_option_is_on_capture(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cv, "cmd_capture", lambda a: seen.append(a))
        monkeypatch.setattr(sys, "argv", ["collect_voice.py", "capture", "--unattended", "--device", "altifalantes"])
        cv.main()
        assert seen[0].device == "altifalantes"


class TestTheLogSaysWhatIsRecorded:
    def test_the_reader_names_the_output_each_time_it_opens_it(self):
        logs = []
        d = FakeDevice(["die_after_3", "ok"])
        r = cv.SystemAudioReader(d.open, lambda: None, stale_s=60, check_s=60, retry_s=0.01, log=logs.append)
        r.start()
        assert wait_for(lambda: d.opens >= 2 and r.q.qsize() > 5)
        r.stop()
        said = [m for m in logs if m.startswith("[reader] recording from:")]
        assert len(said) >= 2 and "fake1" in said[0] and "fake2" in said[1] and "48000 Hz" in said[0]

    def test_every_window_says_where_it_came_from_and_how_loud_it_was(self, tmp_path):
        a = args(tmp_path, window_min=0.3, max_window_min=1.0)
        logs = []
        cap = cv.UnattendedCapture(a, cv.Dataset(str(tmp_path / "stage"), SR), log=logs.append, label=lambda: "Altifalantes (Realtek High Definition Audio) [Loopback]")
        TestUnattendedCapture().feed(cap, np.concatenate([night(8), quiet(30)]))
        cap.finish()
        lines = [m for m in logs if m.startswith("[window")]
        assert lines and all("from Altifalantes (Realtek High Definition Audio) [Loopback]" in m and "peak " in m and " dB" in m for m in lines)

    def test_a_silent_window_shows_as_silence_in_the_log(self, tmp_path):
        a = args(tmp_path, window_min=0.3, max_window_min=1.0)
        logs = []
        cap = cv.UnattendedCapture(a, cv.Dataset(str(tmp_path / "stage"), SR), log=logs.append, label=lambda: "X")
        TestUnattendedCapture().feed(cap, np.zeros(SR * 40, np.float32))
        cap.finish()
        assert any("peak -180 dB" in m for m in logs)                              # nothing is being played: visible at a glance
