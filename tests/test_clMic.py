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

class TestMicEdgeCases:
    @patch('clMic.mqtt_client.Client')
    @patch('clMic.pyaudio.PyAudio')
    @patch('clMic.VoiceSensor._init_wakeword')
    def test_state_machine_transitions(self, mock_wakeword, mock_pyaudio, mock_mqtt):
        """Test the mic state machine based on incoming MQTT messages."""
        from clMic import VoiceSensor
        sensor = VoiceSensor()
        
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
