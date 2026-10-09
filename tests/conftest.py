import os

import pytest
import asyncio


@pytest.fixture(scope="session", autouse=True)
def _no_windows_on_screen():
    """Widget tests show real top-level windows (drawers, panels, dialogs); on a desktop session that
    flashes dozens of them and can freeze the machine. Flag every window a test shows as
    WA_DontShowOnScreen: it still gets real geometry, layout, painting and grab(), it just never
    reaches the screen. JARVIS_TEST_GUI=1 turns this off to watch the tests."""
    if os.environ.get("JARVIS_TEST_GUI") == "1":
        yield
        return
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QWidget

    originals = {name: getattr(QWidget, name) for name in
                 ("setVisible", "show", "showNormal", "showMaximized", "showFullScreen", "showMinimized")}

    def hidden(name):
        def wrapper(self, *args, **kwargs):
            if self.isWindow():
                self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            return originals[name](self, *args, **kwargs)
        return wrapper

    for name in originals:
        setattr(QWidget, name, hidden(name))
    yield
    for name, fn in originals.items():
        setattr(QWidget, name, fn)
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