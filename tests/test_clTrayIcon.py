"""Tests for the tray icon's supervisor handshake. on_connect() doesn't touch
`self` at all, so it's called unbound here -- no QApplication/display needed."""
import json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from clTrayIcon import TrayApp


class TestModuleReadyHandshake:
    def test_announces_ready_on_successful_connect(self):
        """Without this, clJarvis.py's supervisor (which lists 'Tray Icon' in
        NATIVE_SERVICES/EXPECTED_MODULES) waits forever for a module_ready
        that never comes, so 'ALL SYSTEMS GO' / jarvis/sys/ecosystem_online
        never fires."""
        client = MagicMock()

        TrayApp.on_connect(None, client, userdata=None, flags=None, reason_code=0, properties=None)

        client.publish.assert_called_once_with(
            "jarvis/sys/module_ready", json.dumps({"module": "tray icon"}), qos=1
        )

    def test_does_not_announce_ready_on_failed_connect(self):
        client = MagicMock()

        TrayApp.on_connect(None, client, userdata=None, flags=None, reason_code=1, properties=None)

        client.publish.assert_not_called()
