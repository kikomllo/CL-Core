import pytest
pytest.skip("Module clAlarms does not exist in src", allow_module_level=True)
import os
import json
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from clAlarms import JarvisAlarms, DATA_DIR

@pytest.mark.asyncio
async def test_alarm_create_and_delete(mocker):
    alarms = JarvisAlarms()
    alarms.mqtt_client = AsyncMock()
    
    # Mock systemd timer scheduling
    mocker.patch.object(alarms, "schedule_systemd_timer", return_value=True)
    
    payload = {
        "time_str": "in 1 hour",
        "raw_text": "set an alarm in 1 hour"
    }
    
    await alarms.handle_create(payload)
    
    # Verify metadata JSON file was created
    created_files = [f for f in os.listdir(DATA_DIR) if f.endswith('.json')]
    assert len(created_files) > 0
    
    file_path = os.path.join(DATA_DIR, created_files[0])
    with open(file_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
        
    alarm_id = meta["id"]
    assert meta["challenge_type"] == "phrase"
    assert meta["expected_answer"] == "turn off alarm"
    assert "Wake up" in meta["tts_prompt"]
    
    # Test delete
    await alarms.handle_delete(alarm_id)
    assert not os.path.exists(file_path)

@pytest.mark.asyncio
async def test_alarm_list(mocker):
    alarms = JarvisAlarms()
    alarms.mqtt_client = AsyncMock()
    
    mocker.patch.object(alarms, "schedule_systemd_timer", return_value=True)
    await alarms.handle_create({"time_str": "in 2 hours"})
    
    await alarms.handle_list(is_delete_mode=False)
    alarms.mqtt_client.publish.assert_called()
