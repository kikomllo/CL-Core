import pytest
import os
import sys
import numpy as np
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

@pytest.fixture(scope="module")
@patch("clWhisper.ConfigLoader")
@patch("clWhisper.WhisperModel")
def whisper_engine(mock_model_class, mock_config_class):
    mock_config_instance = mock_config_class.return_value
    mock_config_instance.load_json.return_value = {"settings": {"hardware": "cpu"}}
    
    from clWhisper import InferenceEngine
    return InferenceEngine()

class TestWhisperFilters:
    """Validates the Whisper Hallucination Trap safely filters garbage output."""

    @pytest.mark.parametrize("transcribed_text, expected_output", [
        # --- 1. VALID COMMANDS ---
        ("Turn on the desk light.", "Turn on the desk light."),
        ("Play some jazz music.", "Play some jazz music."),
        ("Dim the lights to 50 percent.", "Dim the lights to 50 percent."),
        
        # --- 2. HALLUCINATIONS (Should be purged) ---
        (" Thank you for watching this video! Subscribe to the channel.", ""),
        ("Please like and subscribe for more.", ""),
        ("Be careful with this video.", ""),
        ("See you in the next one. Take care. Bye bye.", ""),
        ("Translated by amara.org", ""),
        ("Thanks for watching!", "")
    ])
    def test_hallucination_trap(self, whisper_engine, transcribed_text, expected_output):
        # Create a dummy audio array to satisfy the method signature
        dummy_audio = np.zeros(16000, dtype=np.float32)

        # Mock what the neural network *would* have returned
        mock_segment = MagicMock()
        mock_segment.text = transcribed_text
        whisper_engine.model.transcribe.return_value = ([mock_segment], None)
        
        result = whisper_engine.transcribe(dummy_audio)
        assert result == expected_output, f"Failed filtering on: '{transcribed_text}'"