import pytest
import os
import json
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from clReminders import JarvisReminders, DATA_DIR

@pytest.mark.asyncio
async def test_handle_create_generates_audio(mocker):
    reminders = JarvisReminders()
    reminders.mqtt_client = AsyncMock()
    
    # Mock systemd timer scheduling
    mocker.patch.object(reminders, "schedule_systemd_timer", return_value=True)
    
    # Mock edge_tts so network call isn't actually made in unit test
    mock_communicate = AsyncMock()
    mocker.patch("edge_tts.Communicate", return_value=mock_communicate)
    
    reminder_id = "test_reminder_123"
    payload = {
        "reminder_id": reminder_id,
        "task": "wash the dishes",
        "time": "15 minutes",
        "raw_text": "remind me in 15 minutes to wash the dishes"
    }
    
    await reminders.handle_create(payload)
    
    # Verify metadata JSON file was created
    meta_path = os.path.join(DATA_DIR, f"{reminder_id}.json")
    assert os.path.exists(meta_path)
    
    with open(meta_path, "r") as f:
        meta = json.load(f)
        
    assert meta["id"] == reminder_id
    assert meta["text"] == "wash the dishes"
    assert meta["audio_path"] == os.path.join(DATA_DIR, f"{reminder_id}.mp3")
    
    # Cleanup
    if os.path.exists(meta_path):
        os.remove(meta_path)
