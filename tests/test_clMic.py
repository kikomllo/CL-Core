import pytest

@pytest.mark.skip(reason="Blocks for keyboard input")
def test_pynput():
    from pynput import keyboard
    def on_press(key):
        print(f"Pressed {key}")
    def on_release(key):
        if key == keyboard.Key.esc:
            return False
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from unittest.mock import MagicMock, patch
import numpy as np

class TestMicEdgeCases:
    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_state_machine_transitions(self, mock_wakeword, mock_pyaudio, mock_mqtt):
        """Test the mic state machine based on incoming MQTT messages."""
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        # Debug flags are loaded from the real config/core.json at construction
        # time, so their actual on-disk value (e.g. left on from manually
        # exploring the Debug tab) must not leak into this test's expectations.
        sensor.debug_wakeword_logging = False
        sensor.debug_wakeword_saving = False
        sensor.capture_wakeword_positive = False

        # Helper to simulate incoming MQTT messages
        def send_mqtt(topic, payload):
            msg = MagicMock()
            msg.topic = topic
            msg.payload = payload.encode('utf-8')
            sensor._on_mqtt_message(sensor.mqtt, None, msg)
            
        # Initial state should be idle-like
        assert sensor.is_processing is False
        assert sensor.tts_busy is False
        
        # Test TTS active locks the mic
        send_mqtt("jarvis/sys/tts_state", '{"state": "active"}')
        assert sensor.tts_busy is True
        
        # Test TTS idle unlocks the mic
        send_mqtt("jarvis/sys/tts_state", '{"state": "idle"}')
        assert sensor.tts_busy is False
        
        # Test audio processing locks the system
        send_mqtt("jarvis/sys/audio_process", '{"state": "idle"}')
        assert sensor.is_processing is False
        
        # Test explicit mic open (simulated push-to-talk)
        send_mqtt("jarvis/sys/mic_open", '{}')
        assert sensor.pending_active_window is True
        
        # Test true PTT logic
        send_mqtt("jarvis/sys/mic_control", '{"action": "ptt_start"}')
        assert sensor.ptt_active is True
        assert sensor.pending_active_window is True
        
        send_mqtt("jarvis/sys/mic_control", '{"action": "ptt_stop"}')
        assert sensor.ptt_active is False
        
        # Test attention mode
        send_mqtt("jarvis/sys/mic_control", '{"action": "attention_on"}')
        assert sensor.attention_mode is True
        send_mqtt("jarvis/sys/mic_control", '{"action": "attention_off"}')
        assert sensor.attention_mode is False

        # Test live input device switch request (Settings UI -> mic_control)
        send_mqtt("jarvis/sys/mic_control", '{"action": "set_input_device", "device_name": "USB Mic"}')
        assert sensor.pending_input_device_change is True
        assert sensor.target_input_device == "USB Mic"

        # Test live debug flag toggles (Settings UI -> debug_control) -- logging
        # and saving are independent, since the logs are console spam the user
        # may want off while still saving clips (or vice versa).
        assert sensor.debug_wakeword_logging is False
        send_mqtt("jarvis/sys/debug_control", '{"flag": "wakeword_debug_logging", "enabled": true}')
        assert sensor.debug_wakeword_logging is True
        send_mqtt("jarvis/sys/debug_control", '{"flag": "wakeword_debug_logging", "enabled": false}')
        assert sensor.debug_wakeword_logging is False

        assert sensor.debug_wakeword_saving is False
        send_mqtt("jarvis/sys/debug_control", '{"flag": "wakeword_debug_saving", "enabled": true}')
        assert sensor.debug_wakeword_saving is True

        # Turning it back off mid-attempt discards whatever was buffered
        # rather than saving a stale clip once saving resumes later.
        sensor._wakeword_debug_frames.append(np.zeros(10, dtype=np.int16))
        send_mqtt("jarvis/sys/debug_control", '{"flag": "wakeword_debug_saving", "enabled": false}')
        assert sensor.debug_wakeword_saving is False
        assert sensor._wakeword_debug_frames == []

        # Test the training-capture flag toggles independently
        assert sensor.capture_wakeword_positive is False
        send_mqtt("jarvis/sys/debug_control", '{"flag": "capture_wakeword_positive", "enabled": true}')
        assert sensor.capture_wakeword_positive is True

        # Buffered frames must survive disabling ONE flag while the other is
        # still on -- only clear once BOTH capture reasons are off.
        sensor.debug_wakeword_saving = True
        sensor._wakeword_debug_frames.append(np.zeros(10, dtype=np.int16))
        send_mqtt("jarvis/sys/debug_control", '{"flag": "capture_wakeword_positive", "enabled": false}')
        assert sensor.capture_wakeword_positive is False
        assert len(sensor._wakeword_debug_frames) == 1
        send_mqtt("jarvis/sys/debug_control", '{"flag": "wakeword_debug_saving", "enabled": false}')
        assert sensor._wakeword_debug_frames == []


class TestMicInputDeviceSwitching:
    """A device change picked in Settings must apply to the running mic
    without a service restart -- the MQTT handler only flags the request;
    listen()'s loop performs the actual stream swap on the main thread."""

    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_switch_input_device_reopens_stream_on_resolved_index(self, mock_wakeword, mock_pyaudio, mock_mqtt):
        from clMic import VoiceSensor
        sensor = VoiceSensor()

        old_stream = MagicMock()
        sensor.mic_stream = old_stream
        new_stream = MagicMock()
        sensor.audio.open.return_value = new_stream

        with patch('utils.clAudioDevices.resolve_capture_device_index', return_value=3) as mock_resolve:
            sensor._switch_input_device("USB Mic")

        old_stream.stop_stream.assert_called_once()
        old_stream.close.assert_called_once()
        mock_resolve.assert_called_once_with(sensor.audio, "USB Mic")
        _, open_kwargs = sensor.audio.open.call_args
        assert open_kwargs["input_device_index"] == 3
        assert sensor.mic_stream is new_stream

    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_switch_input_device_falls_back_to_default_on_open_failure(self, mock_wakeword, mock_pyaudio, mock_mqtt):
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        sensor.mic_stream = MagicMock()

        good_stream = MagicMock()
        sensor.audio.open.side_effect = [Exception("device busy"), good_stream]

        with patch('utils.clAudioDevices.resolve_capture_device_index', return_value=3):
            sensor._switch_input_device("Unplugged Mic")

        assert sensor.audio.open.call_count == 2
        assert sensor.mic_stream is good_stream

    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_switch_input_device_resets_vad_state(self, mock_wakeword, mock_pyaudio, mock_mqtt):
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        sensor.mic_stream = MagicMock()
        sensor.ambient_noise_buffer.append(500)
        sensor.fast_ema = 123.0

        with patch('utils.clAudioDevices.resolve_capture_device_index', return_value=None):
            sensor._switch_input_device(None)

        assert len(sensor.ambient_noise_buffer) == 0
        assert sensor.fast_ema is None
        sensor.oww_model.reset.assert_called()


class TestMicNativeRateFallback:
    """WASAPI (and some other backends) reject 16kHz outright on devices
    whose mix format is 44100/48000Hz -- confirmed live: a G435 headset's
    WASAPI endpoint (defaultSampleRate 48000) raised OSError('Invalid
    sample rate') and crash-looped clMic.py forever once selected.
    _open_stream must fall back to the device's own rate; _read_chunk then
    resamples back down to 16kHz/self.CHUNK so the rest of the wake-word/
    VAD/Whisper pipeline -- which hard-requires 16kHz frames -- never has
    to change."""

    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_16khz_success_needs_no_fallback(self, mock_wakeword, mock_pyaudio, mock_mqtt):
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        sensor.audio.open.return_value = MagicMock()

        sensor._open_stream(None)

        assert sensor.audio.open.call_count == 1
        assert sensor.native_rate == sensor.RATE
        assert sensor.native_chunk == sensor.CHUNK

    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_falls_back_to_native_rate_for_a_specific_device(self, mock_wakeword, mock_pyaudio, mock_mqtt):
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        good_stream = MagicMock()
        sensor.audio.open.side_effect = [OSError("Invalid sample rate"), good_stream]
        sensor.audio.get_device_info_by_index.return_value = {"defaultSampleRate": 48000.0}

        with patch('utils.clAudioDevices.resolve_capture_device_index', return_value=3):
            sensor._open_stream("Microphone (G435 Wireless Gaming Headset)")

        assert sensor.audio.open.call_count == 2
        first_kwargs = sensor.audio.open.call_args_list[0].kwargs
        second_kwargs = sensor.audio.open.call_args_list[1].kwargs
        assert first_kwargs["rate"] == sensor.RATE
        assert second_kwargs["rate"] == 48000
        assert second_kwargs["frames_per_buffer"] == round(sensor.CHUNK * 48000 / sensor.RATE)
        assert sensor.native_rate == 48000
        assert sensor.mic_stream is good_stream

    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_falls_back_to_default_device_native_rate_when_no_index(self, mock_wakeword, mock_pyaudio, mock_mqtt):
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        sensor.audio.open.side_effect = [OSError("Invalid sample rate"), MagicMock()]
        sensor.audio.get_default_input_device_info.return_value = {"defaultSampleRate": 44100.0}

        with patch('utils.clAudioDevices.resolve_capture_device_index', return_value=None):
            sensor._open_stream(None)

        assert sensor.native_rate == 44100
        sensor.audio.get_default_input_device_info.assert_called_once()


class TestMicReadChunkResampling:
    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_no_resampling_needed_returns_raw_bytes_unchanged(self, mock_wakeword, mock_pyaudio, mock_mqtt):
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        sensor.native_rate = sensor.RATE
        sensor.native_chunk = sensor.CHUNK
        raw = np.zeros(sensor.CHUNK, dtype=np.int16).tobytes()
        sensor.mic_stream = MagicMock()
        sensor.mic_stream.read.return_value = raw

        result = sensor._read_chunk()

        assert result == raw
        sensor.mic_stream.read.assert_called_once_with(sensor.CHUNK, exception_on_overflow=False)

    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_resamples_native_rate_chunk_down_to_expected_size(self, mock_wakeword, mock_pyaudio, mock_mqtt):
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        sensor.native_rate = 48000
        sensor.native_chunk = int(round(sensor.CHUNK * 48000 / sensor.RATE))
        raw = (np.sin(np.linspace(0, 20 * np.pi, sensor.native_chunk)) * 10000).astype(np.int16).tobytes()
        sensor.mic_stream = MagicMock()
        sensor.mic_stream.read.return_value = raw

        result = sensor._read_chunk()
        resampled = np.frombuffer(result, dtype=np.int16)

        assert len(resampled) == sensor.CHUNK
        sensor.mic_stream.read.assert_called_once_with(sensor.native_chunk, exception_on_overflow=False)


class TestWakewordClipSaving:
    """Debug tab toggles: 'Wake Word Diagnostics' saves every attempt to
    data/scratch for troubleshooting; 'Save successful wake word triggers'
    saves only genuine hits to data/training_capture for future retraining.
    Both write through the same _write_wakeword_frames_to_wav helper."""

    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_debug_clip_written_to_scratch(self, mock_wakeword, mock_pyaudio, mock_mqtt, tmp_path):
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        sensor.base_dir = str(tmp_path / "src")
        sensor._wakeword_debug_frames = [np.zeros(1280, dtype=np.int16)]

        sensor._save_wakeword_debug_clip()

        files = list((tmp_path / "data" / "scratch").glob("wakeword_debug_*.wav"))
        assert len(files) == 1

    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_positive_clip_written_to_training_capture(self, mock_wakeword, mock_pyaudio, mock_mqtt, tmp_path):
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        sensor.base_dir = str(tmp_path / "src")
        sensor._wakeword_debug_frames = [np.ones(1280, dtype=np.int16) * 100]

        sensor._save_wakeword_positive_clip()

        files = list((tmp_path / "data" / "training_capture" / "wakeword_positive").glob("wakeword_positive_*.wav"))
        assert len(files) == 1


class FakeVad:
    """Scripted speech probabilities, one per chunk, then silence forever."""
    def __init__(self, probs):
        self.probs, self.i, self.resets = list(probs), 0, 0

    def reset(self):
        self.resets += 1

    def process(self, chunk):
        p = self.probs[self.i] if self.i < len(self.probs) else 0.0
        self.i += 1
        return p


class TestRecordCommandEndpointing:
    """_record_command ends a command on the neural endpointer (Silero), falling back to volume."""

    @pytest.fixture
    def sensor(self):
        with patch('clMic.mqtt_client.Client'), patch('clMic.pyaudio.PyAudio'), \
             patch('clMic.VoiceSensor._init_wakeword'):
            from clMic import VoiceSensor
            s = VoiceSensor()
        s.attention_mode = False
        s.mic_stream = MagicMock()
        s.mic_stream.get_read_available.return_value = 0
        s._publish = MagicMock()
        s._chunk = np.zeros(1280, dtype=np.int16)
        s._read_chunk = lambda *a, **k: s._chunk.tobytes()
        s.vad_cfg = {"engine": "silero", "end_silence_ms": 700, "speech_threshold": 0.5}
        return s

    @staticmethod
    def dispatched_chunks(sensor):
        import base64
        calls = [c for c in sensor._publish.call_args_list if c.args[0] == "jarvis/sys/audio_process"]
        assert len(calls) == 1, "expected exactly one audio dispatch"
        audio = np.frombuffer(base64.b64decode(calls[0].args[1]["audio_b64"]), dtype=np.float32)
        return len(audio) // 1280, calls[0].args[1]["trace"]

    def test_command_is_sent_shortly_after_the_speech_stops(self, sensor):
        sensor.vad = FakeVad([0.0] * 3 + [0.9] * 6)
        sensor._record_command(115, 110, bypass_wakeword=True)

        chunks, trace = self.dispatched_chunks(sensor)
        assert chunks == 3 + 6 + 9                           # 9 silent chunks = 0.72s >= 700ms
        assert sensor.vad.resets == 1
        assert trace["t_dispatch"] >= trace["t_speech_end"]

    def test_a_pause_inside_a_command_does_not_cut_it(self, sensor):
        sensor.vad = FakeVad([0.9] * 4 + [0.0] * 5 + [0.9] * 4)
        sensor._record_command(115, 110, bypass_wakeword=True, is_already_speaking=True)

        chunks, _ = self.dispatched_chunks(sensor)
        assert chunks == 4 + 5 + 4 + 9

    def test_longer_end_silence_setting_is_respected(self, sensor):
        sensor.vad_cfg["end_silence_ms"] = 1200
        sensor.vad = FakeVad([0.9] * 4)
        sensor._record_command(115, 110, bypass_wakeword=True, is_already_speaking=True)

        chunks, _ = self.dispatched_chunks(sensor)
        assert chunks == 4 + 15                              # 15 x 80ms = 1.2s

    def test_nothing_said_closes_the_mic_without_sending_audio(self, sensor):
        sensor.vad = FakeVad([])
        result = sensor._record_command(115, 110, bypass_wakeword=False)

        assert result == ""
        topics = [c.args[0] for c in sensor._publish.call_args_list]
        assert "jarvis/sys/audio_process" not in topics
        assert sensor._publish.call_args_list[-1].args == ("jarvis/sys/mic_state", {"state": "idle"})

    def test_a_bare_wake_word_is_still_recognised_without_whisper(self, sensor):
        sensor.vad = FakeVad([])
        result = sensor._record_command(115, 110, bypass_wakeword=False, wakeword_triggered=True)

        assert result == "hey jarvis"

    def test_push_to_talk_is_never_cut_by_silence(self, sensor):
        sensor.vad = FakeVad([0.9] * 3)
        sensor.ptt_active = True
        counter = {"n": 0}
        real_read = sensor._read_chunk

        def release_after_40(*a, **k):
            counter["n"] += 1
            if counter["n"] > 40:
                sensor.ptt_active = False
            return real_read()
        sensor._read_chunk = release_after_40

        sensor._record_command(115, 110, bypass_wakeword=True)

        chunks, _ = self.dispatched_chunks(sensor)
        assert chunks >= 40                                  # ~3.2s of silence did not end it

    def test_falls_back_to_volume_thresholds_without_a_vad(self, sensor):
        sensor.vad = None
        loud = np.full(1280, 8000, dtype=np.int16)
        quiet = np.zeros(1280, dtype=np.int16)
        feed = iter([loud] * 5 + [quiet] * 200)
        sensor._read_chunk = lambda *a, **k: next(feed).tobytes()

        sensor._record_command(115, 110, bypass_wakeword=True)

        chunks, _ = self.dispatched_chunks(sensor)
        assert 5 < chunks < 70                               # ended by the old silence window

    def test_vad_settings_default_when_not_configured(self, sensor):
        cfg = sensor._load_vad_settings()
        assert cfg["engine"] in ("silero", "rms") and isinstance(cfg["end_silence_ms"], int)
        assert 0.0 < cfg["speech_threshold"] < 1.0


import collections
import json
import time


class FakeOww:
    """Scripted wake-word scores, one per predict() call (then 0)."""
    def __init__(self, scores):
        self.scores, self.i, self.seen, self.resets = list(scores), 0, [], 0
        self.prediction_buffer = {"hey_jarvis": collections.deque([0.0], maxlen=30)}

    def predict(self, chunk):
        self.seen.append(np.array(chunk))
        self.prediction_buffer["hey_jarvis"].append(self.scores[self.i] if self.i < len(self.scores) else 0.0)
        self.i += 1

    def reset(self):
        self.resets += 1


class FakeAec:
    available = True

    def __init__(self):
        self.calls = []

    def process(self, mic, far):
        self.calls.append((mic.copy(), far.copy()))
        return np.zeros_like(mic)


class TestBargeIn:
    """Interrupting JARVIS while it speaks: echo-cancelled wake word, PTT, and keeping its own voice
    at a level where that is possible."""

    @pytest.fixture
    def sensor(self, tmp_path):
        with patch('clMic.mqtt_client.Client'), patch('clMic.pyaudio.PyAudio'), \
             patch('clMic.VoiceSensor._init_wakeword'):
            from clMic import VoiceSensor
            s = VoiceSensor()
        from utils.clEchoGuard import EchoGuard
        s.guard = EchoGuard(state_path=str(tmp_path / "state.json"))
        s.barge_cfg = {"enabled": True, "auto_limit": True, "wake_threshold": 0.6, "hits": 1, "target_esr_db": 4.0}
        s.aec = None
        s.mic_stream = MagicMock()
        s.mic_stream.get_read_available.return_value = 0
        s._publish = MagicMock()
        s.oww_model = FakeOww([])
        s.tts_busy = True
        s.attention_mode = False
        s._chunk = np.full(1280, 500, dtype=np.int16)
        s._read_chunk = lambda *a, **k: s._chunk.tobytes()
        return s

    @staticmethod
    def send(sensor, topic, payload):
        msg = MagicMock()
        msg.topic = topic
        msg.payload = payload.encode('utf-8')
        sensor._on_mqtt_message(sensor.mqtt, None, msg)

    @staticmethod
    def published(sensor, topic):
        return [c.args[1] for c in sensor._publish.call_args_list if c.args[0] == topic]

    def test_the_wake_word_over_speech_stops_it_and_hands_over_the_audio(self, sensor):
        sensor.oww_model = FakeOww([0.1, 0.9])
        assert [sensor._barge_in_step() for _ in range(2)] == [False, True]

        assert self.published(sensor, "jarvis/sys/tts_stop") == ["stop"]
        assert sensor.tts_busy is False
        assert len(sensor._forced_wake) == 2                    # the cleaned audio around the trigger
        assert sensor.bargein_hold_until > time.time()

    def test_a_single_strong_chunk_is_enough_because_the_wake_word_peaks_briefly(self, sensor):
        sensor.oww_model = FakeOww([0.0, 0.7])
        assert [sensor._barge_in_step() for _ in range(2)] == [False, True]

    def test_requiring_consecutive_chunks_can_be_configured(self, sensor):
        sensor.barge_cfg["hits"] = 2
        sensor.oww_model = FakeOww([0.9, 0.1, 0.9, 0.1, 0.9, 0.9])   # only the last two are consecutive
        assert [sensor._barge_in_step() for _ in range(6)] == [False] * 5 + [True]

    def test_scores_that_would_wake_it_normally_do_not_interrupt(self, sensor):
        sensor.oww_model = FakeOww([0.55] * 6)                  # above the usual 0.5, under the barge-in 0.6
        assert not any(sensor._barge_in_step() for _ in range(6))
        assert self.published(sensor, "jarvis/sys/tts_stop") == []

    def test_the_wake_word_model_hears_the_echo_cancelled_audio(self, sensor):
        sensor.aec = FakeAec()
        sensor.ref.add(time.time() - 0.2, 0, np.full(16000, 4000, dtype=np.int16))   # JARVIS is speaking
        sensor.oww_model = FakeOww([0.0])

        sensor._barge_in_step()

        assert not sensor.oww_model.seen[0].any()                # the fake canceller returns silence
        mic, far = sensor.aec.calls[0]
        assert (mic == 500).all() and far.any()                  # given the real mic and what was played

    def test_without_a_reference_the_mic_goes_to_the_wake_word_untouched(self, sensor):
        sensor.aec = FakeAec()
        sensor.oww_model = FakeOww([0.0])

        sensor._barge_in_step()

        assert sensor.aec.calls == [] and (sensor.oww_model.seen[0] == 500).all()

    def test_push_to_talk_cuts_off_speech(self, sensor):
        self.send(sensor, "jarvis/sys/mic_control", '{"action": "ptt_start"}')

        assert self.published(sensor, "jarvis/sys/tts_stop") == ["stop"]
        assert sensor.tts_busy is False and sensor.ptt_active is True

    def test_push_to_talk_while_silent_does_not_send_a_stop(self, sensor):
        sensor.tts_busy = False
        self.send(sensor, "jarvis/sys/mic_control", '{"action": "ptt_start"}')
        assert self.published(sensor, "jarvis/sys/tts_stop") == []

    def test_the_idle_that_follows_an_interruption_does_not_disturb_the_new_recording(self, sensor):
        sensor._interrupt_speech()
        self.send(sensor, "jarvis/sys/tts_state", '{"state": "idle"}')

        sensor.mic_stream.read.assert_not_called()               # no draining of audio being recorded
        assert sensor.oww_model.resets == 0

    def test_a_normal_idle_still_resets_and_drains(self, sensor):
        sensor.mic_stream.get_read_available.return_value = 1280
        self.send(sensor, "jarvis/sys/tts_state", '{"state": "idle"}')

        sensor.mic_stream.read.assert_called()
        assert sensor.oww_model.resets == 1 and sensor.tts_busy is False

    def test_a_follow_up_window_is_not_opened_over_an_interruption(self, sensor):
        sensor._interrupt_speech()
        self.send(sensor, "jarvis/sys/mic_control", '{"action": "request_reply"}')
        assert sensor.awaiting_reply is False

    def test_a_follow_up_window_still_opens_normally(self, sensor):
        self.send(sensor, "jarvis/sys/mic_control", '{"action": "request_reply"}')
        assert sensor.awaiting_reply is True

    def test_the_echo_reference_from_the_tts_service_is_stored(self, sensor):
        import base64
        pcm = np.arange(1, 801, dtype=np.int16)
        self.send(sensor, "jarvis/sys/tts_ref", json.dumps(
            {"t0": 1000.0, "off": 0, "pcm": base64.b64encode(pcm.tobytes()).decode()}))
        np.testing.assert_array_equal(sensor.ref.get(1000.0, 800), pcm)

    def test_a_speaker_too_loud_to_interrupt_gets_a_quieter_jarvis(self, sensor):
        sensor.ambient_plain = 0.003
        for _ in range(2):                                       # two replies, each ~10x the voice level
            sensor._echo_samples = [0.3] * 30
            sensor._finish_echo_measurement()

        assert sensor.tts_gain < 1.0
        control = self.published(sensor, "jarvis/sys/tts_control")
        assert control and control[-1]["action"] == "set_gain" and control[-1]["gain"] == pytest.approx(sensor.tts_gain)
        assert len(self.published(sensor, "jarvis/sys/speak")) == 1    # told once, not every time

    def test_a_quiet_speaker_is_left_alone(self, sensor):
        sensor.ambient_plain = 0.003
        for _ in range(3):
            sensor._echo_samples = [0.006] * 30
            sensor._finish_echo_measurement()
        assert sensor.tts_gain == 1.0 and self.published(sensor, "jarvis/sys/tts_control") == []

    def test_an_interrupted_reply_is_not_used_to_judge_the_volume(self, sensor):
        sensor._interrupted = True
        sensor._echo_samples = [0.5] * 30
        sensor._finish_echo_measurement()
        sensor._echo_samples = [0.5] * 30
        sensor._finish_echo_measurement()
        assert sensor.guard.echo_unit is not None and sensor._interrupted is False
        assert sensor.guard._echo_count == 1                     # only the second, uninterrupted one counted

    def test_auto_limit_can_be_turned_off(self, sensor):
        sensor.barge_cfg["auto_limit"] = False
        for _ in range(3):
            sensor._echo_samples = [0.5] * 30
            sensor._finish_echo_measurement()
        assert sensor.tts_gain == 1.0

    def test_recorded_commands_are_echo_cancelled_too(self, sensor):
        """The first moments after an interruption still carry the end of JARVIS's own voice."""
        import base64
        sensor.tts_busy = False
        sensor.vad_cfg = {"engine": "silero", "end_silence_ms": 700, "speech_threshold": 0.5}
        sensor.vad = FakeVad([0.9] * 3)
        sensor.aec = FakeAec()
        sensor.ref.add(time.time() - 0.5, 0, np.full(32000, 4000, dtype=np.int16))

        sensor._record_command(115, 110, bypass_wakeword=True, is_already_speaking=True)

        calls = [c for c in sensor._publish.call_args_list if c.args[0] == "jarvis/sys/audio_process"]
        audio = np.frombuffer(base64.b64decode(calls[0].args[1]["audio_b64"]), dtype=np.float32)
        assert len(sensor.aec.calls) > 0 and not audio.any()

    def test_the_level_of_the_users_voice_is_learned_from_commands(self, sensor):
        sensor.tts_busy = False
        sensor.vad_cfg = {"engine": "silero", "end_silence_ms": 700, "speech_threshold": 0.5}
        sensor._chunk = np.full(1280, 1000, dtype=np.int16)      # ~0.03 of full scale
        for _ in range(3):
            sensor.vad = FakeVad([0.9] * 8)
            sensor._record_command(115, 110, bypass_wakeword=True)
        assert sensor.guard.voice == pytest.approx(1000 / 32768, rel=0.01)
