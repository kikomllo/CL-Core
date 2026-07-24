import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_mqtt(mocker):
    """
    Globally mocks aiomqtt.Client to prevent real network calls.
    Returns the AsyncMock instance representing the client context.
    """
    mock_client_class = mocker.patch("aiomqtt.Client")
    mock_client_instance = AsyncMock()
    
    mock_client_class.return_value.__aenter__.return_value = mock_client_instance
    return mock_client_instance

@pytest.fixture
def message_stream():
    """Helper to simulate an incoming stream of MQTT messages, then cleanly exit."""
    def _create_stream(messages: list):
        async def async_generator():
            for topic, payload in messages:
                msg = MagicMock()
                msg.topic.value = topic
                msg.payload.decode.return_value = payload
                yield msg
            
            raise asyncio.CancelledError()
            
        return async_generator()
    return _create_stream