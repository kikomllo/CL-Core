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
