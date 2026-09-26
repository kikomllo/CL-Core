"""Tests for the Settings widget's Presence tab and the dashboard's presence/pairing plumbing."""
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def widget(qapp):
    from ui.clSettingsWidget import SettingsWidget
    w = SettingsWidget()
    w.router = MagicMock()
    yield w
    w.update_timer.stop()
    w._pairing_timer.stop()
    w.deleteLater()


PAIRED_HERE = {"present": True, "rssi": -62, "paired": True, "available": True, "reason": None}
WAITING = {"state": "waiting", "code": "ABCD-EFGH-IJKL", "uri": "jarvisbeacon://pair?x", "expires_at": None}


class TestPresenceText:
    def test_each_state(self, qapp):
        from ui.clSettingsWidget import SettingsWidget as S
        assert "Waiting" in S.presence_text(None)
        assert S.presence_text({"paired": False}) == "No device paired."
        assert "here" in S.presence_text(PAIRED_HERE) and "-62" in S.presence_text(PAIRED_HERE)
        assert "away" in S.presence_text({"paired": True, "present": False, "available": True})
        assert "Bluetooth unavailable (Bluetooth is off)" in S.presence_text(
            {"paired": True, "available": False, "reason": "Bluetooth is off"})


class TestPresenceTab:
    def test_the_tab_exists(self, widget):
        names = [widget.tabs.tabText(i) for i in range(widget.tabs.count())]
        assert "Presence" in names

    def test_starts_unpaired_and_quiet(self, widget):
        assert widget.cancel_pair_btn.isHidden()
        assert widget.unpair_btn.isHidden()
        assert widget.pairing_panel.isHidden()

    def test_paired_state_offers_unpair(self, widget):
        widget.update_presence(PAIRED_HERE)
        assert "here" in widget.presence_status.text()
        assert not widget.unpair_btn.isHidden()

    def test_a_waiting_pairing_shows_the_code_and_cancel(self, widget):
        widget.update_pairing(dict(WAITING, expires_at=time.time() + 125))
        assert not widget.pairing_panel.isHidden()
        assert widget.pairing_code.text() == "ABCD-EFGH-IJKL"
        assert not widget.cancel_pair_btn.isHidden() and widget.pair_btn.isHidden()
        assert "2:0" in widget.pairing_hint.text()  # counting down from about 2:05

    def test_finishing_a_pairing_hides_the_code_and_says_why(self, widget):
        widget.update_pairing(dict(WAITING, expires_at=time.time() + 60))
        for state, text in (("paired", "Paired."), ("expired", "timed out"), ("cancelled", "cancelled")):
            widget.update_pairing(dict(WAITING, expires_at=time.time() + 60))
            widget.update_pairing({"state": state})
            assert widget.pairing_panel.isHidden()
            assert text in widget.pairing_message.text()
            assert not widget.pair_btn.isHidden()

    def test_a_failed_pairing_shows_the_reason(self, widget):
        widget.update_pairing({"state": "failed", "reason": "Bluetooth is off"})
        assert "Bluetooth is off" in widget.pairing_message.text()

    def test_buttons_dispatch_the_monitor_actions(self, widget):
        widget.pair_btn.click()
        widget.cancel_pair_btn.click()
        widget.unpair_btn.click()
        assert [c.args[0] for c in widget.router.dispatch.call_args_list] == [
            "monitor.pair", "monitor.cancel", "monitor.unpair"]

    def test_copy_code_puts_the_code_on_the_clipboard(self, widget, qapp):
        widget.update_pairing(dict(WAITING, expires_at=time.time() + 60))
        widget.copy_code_btn.click()
        assert qapp.clipboard().text() == "ABCD-EFGH-IJKL"

    def test_opening_the_widget_replays_only_a_pairing_that_is_still_open(self, widget):
        widget.apply_monitor_state(PAIRED_HERE, dict(WAITING, expires_at=time.time() + 60))
        assert not widget.pairing_panel.isHidden()
        widget.apply_monitor_state(PAIRED_HERE, {"state": "expired"})
        assert widget.pairing_panel.isHidden()

    def test_a_new_pairing_clears_the_old_result_message(self, widget):
        widget.update_pairing({"state": "expired"})
        assert widget.pairing_message.text()
        widget.update_pairing(dict(WAITING, expires_at=time.time() + 60))
        assert widget.pairing_message.text() == ""


class TestDashboardPlumbing:
    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_updates_are_cached_and_forwarded_to_an_open_settings_widget(self, qapp, tmp_path, mocker):
        import clUI
        from ui.clSettingsWidget import SettingsWidget
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with patch.object(clUI, "STATE_FILE", str(tmp_path / "ui_state.json")):
            ui = clUI.JarvisUI()
            ui._handle_presence(PAIRED_HERE)
            ui._handle_pairing(dict(WAITING, expires_at=time.time() + 60))
            assert ui._last_presence == PAIRED_HERE and ui._last_pairing["state"] == "waiting"

            ui.is_fullscreen = True
            ui._toggle_settings()
            settings = ui._settings_widget()
            assert isinstance(settings, SettingsWidget)
            assert "here" in settings.presence_status.text()  # replayed from the cache on open
            assert not settings.pairing_panel.isHidden()

            ui._handle_presence({"paired": False})
            assert settings.presence_status.text() == "No device paired."
            settings.update_timer.stop()

    def test_the_mqtt_thread_forwards_both_topics(self, qapp):
        import clUI
        thread = clUI.MqttThread()
        seen = []
        thread.presence_signal.connect(lambda d: seen.append(("presence", d)))
        thread.pairing_signal.connect(lambda d: seen.append(("pairing", d)))
        thread._handle_presence({"present": True})
        thread._handle_pairing({"state": "waiting"})
        thread._handle_presence("not a dict")
        assert seen == [("presence", {"present": True}), ("pairing", {"state": "waiting"})]
