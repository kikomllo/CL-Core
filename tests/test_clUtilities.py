import pytest
import os
import sys
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from clUtilities import JarvisUtilities


@pytest.fixture
def utilities():
    u = JarvisUtilities()
    u.mqtt_client = AsyncMock()
    return u


class TestScheduleRouting:
    """schedule_systemd_timer must dispatch to the right OS-specific scheduler."""

    def test_routes_to_windows_task_scheduler_on_win32(self, utilities, mocker):
        mocker.patch("clUtilities.sys.platform", "win32")
        mock_schedule = mocker.patch.object(utilities, "_schedule_windows_task", return_value=True)
        scheduled_time = datetime(2026, 6, 1, 7, 30, 0)

        result = utilities.schedule_systemd_timer("jarvis-alarm", "clAlarmTrigger.py", "123", scheduled_time)

        assert result is True
        mock_schedule.assert_called_once()
        args = mock_schedule.call_args[0]
        assert args[0] == "jarvis-alarm"
        assert args[1] == "123"
        assert args[3] == scheduled_time

    def test_uses_systemd_run_on_linux(self, utilities, mocker):
        mocker.patch("clUtilities.sys.platform", "linux")
        mock_run = mocker.patch("clUtilities.subprocess.run")
        scheduled_time = datetime(2026, 6, 1, 7, 30, 0)

        result = utilities.schedule_systemd_timer("jarvis-alarm", "clAlarmTrigger.py", "123", scheduled_time)

        assert result is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "systemd-run"
        assert "--on-calendar=2026-06-01 07:30:00" in cmd
        assert "jarvis-alarm-123" in cmd


class TestWindowsTaskScheduling:
    """_schedule_windows_task builds a Task Scheduler XML trigger fired at
    real wall-clock time (surviving DST shifts and system sleep), instead of
    the old time.sleep()-in-a-detached-process approach."""

    def _capture_xml(self, mocker):
        written = {}
        real_fdopen = os.fdopen

        def fake_fdopen(fd, mode, encoding=None):
            f = real_fdopen(fd, mode, encoding=encoding)
            original_write = f.write

            def spy_write(data):
                written["xml"] = written.get("xml", "") + data
                return original_write(data)

            f.write = spy_write
            return f

        mocker.patch("clUtilities.os.fdopen", side_effect=fake_fdopen)
        return written

    def test_creates_task_with_wall_clock_trigger_and_wake_settings(self, utilities, mocker):
        written = self._capture_xml(mocker)
        mock_run = mocker.patch("clUtilities.subprocess.run")
        scheduled_time = datetime(2026, 6, 1, 7, 30, 0)

        result = utilities._schedule_windows_task("jarvis-alarm", "999", r"C:\path\clAlarmTrigger.py", scheduled_time)

        assert result is True
        xml = written["xml"]
        assert "<StartBoundary>2026-06-01T07:30:00</StartBoundary>" in xml
        assert "<EndBoundary>2026-06-01T08:30:00</EndBoundary>" in xml
        assert "<WakeToRun>true</WakeToRun>" in xml
        assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
        assert "999" in xml

        create_cmd = mock_run.call_args[0][0]
        assert create_cmd[:3] == ["schtasks", "/Create", "/TN"]
        assert "jarvis-alarm-999" in create_cmd

    def test_escapes_xml_special_characters_in_path(self, utilities, mocker):
        written = self._capture_xml(mocker)
        mocker.patch("clUtilities.subprocess.run")

        utilities._schedule_windows_task(
            "jarvis-reminder", "1", r"C:\Users\Test & Co\clReminderTrigger.py", datetime(2026, 1, 1, 9, 0, 0)
        )

        assert "Test &amp; Co" in written["xml"]
        assert "Test & Co" not in written["xml"]

    def test_returns_false_and_still_cleans_up_temp_file_on_schtasks_failure(self, utilities, mocker):
        import subprocess
        mocker.patch(
            "clUtilities.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "schtasks", stderr="access denied")
        )
        mock_remove = mocker.patch("clUtilities.os.remove")

        result = utilities._schedule_windows_task("jarvis-alarm", "1", "trigger.py", datetime.now())

        assert result is False
        mock_remove.assert_called_once()

    def test_task_name_format(self):
        assert JarvisUtilities._windows_task_name("jarvis-alarm", "12345") == "jarvis-alarm-12345"


class TestCancelScheduledTimer:
    """Cancelling an alarm/reminder must actually tear down whatever the OS
    scheduled -- Task Scheduler on Windows, systemd on Linux."""

    def test_windows_deletes_via_schtasks(self, mocker):
        mocker.patch("clUtilities.sys.platform", "win32")
        mock_run = mocker.patch("clUtilities.subprocess.run")

        JarvisUtilities.cancel_scheduled_timer("jarvis-alarm", "42")

        cmd = mock_run.call_args[0][0]
        assert cmd == ["schtasks", "/Delete", "/TN", "jarvis-alarm-42", "/F"]

    def test_linux_stops_and_resets_systemd_unit(self, mocker):
        mocker.patch("clUtilities.sys.platform", "linux")
        mock_run = mocker.patch("clUtilities.subprocess.run")

        JarvisUtilities.cancel_scheduled_timer("jarvis-reminder", "42")

        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["systemctl", "--user", "stop", "jarvis-reminder-42.timer"] in calls
        assert ["systemctl", "--user", "stop", "jarvis-reminder-42.service"] in calls
        assert ["systemctl", "--user", "reset-failed", "jarvis-reminder-42.*"] in calls

    def test_never_raises_even_if_subprocess_missing(self, mocker):
        mocker.patch("clUtilities.sys.platform", "win32")
        mocker.patch("clUtilities.subprocess.run", side_effect=FileNotFoundError("schtasks not found"))

        # Must not propagate -- callers (handle_alarm_delete etc.) don't expect this to raise.
        JarvisUtilities.cancel_scheduled_timer("jarvis-alarm", "42")


class TestAlarmReminderDeleteWiring:
    """The delete handlers must route cancellation through cancel_scheduled_timer
    (whichever OS backs it) rather than assuming systemctl exists.

    ALARMS_DIR/REMINDERS_DIR are redirected to a tmp_path for every test here --
    they normally point at data/alarms and data/reminders, which per CLAUDE.md
    hold live per-user runtime state, not test fixtures."""

    @pytest.fixture(autouse=True)
    def isolated_dirs(self, tmp_path, mocker):
        alarms_dir = tmp_path / "alarms"
        reminders_dir = tmp_path / "reminders"
        alarms_dir.mkdir()
        reminders_dir.mkdir()
        mocker.patch("clUtilities.ALARMS_DIR", str(alarms_dir))
        mocker.patch("clUtilities.REMINDERS_DIR", str(reminders_dir))
        return alarms_dir, reminders_dir

    @pytest.mark.asyncio
    async def test_handle_alarm_delete_cancels_and_removes_file(self, utilities, mocker, isolated_dirs):
        alarms_dir, _ = isolated_dirs
        mock_cancel = mocker.patch.object(utilities, "cancel_scheduled_timer")
        alarm_path = alarms_dir / "alarm_test_1.json"
        alarm_path.write_text(json.dumps({"id": "alarm_test_1"}))

        await utilities.handle_alarm_delete("alarm_test_1")

        mock_cancel.assert_called_once_with("jarvis-alarm", "alarm_test_1")
        assert not alarm_path.exists()

    @pytest.mark.asyncio
    async def test_handle_reminder_delete_cancels_and_removes_file(self, utilities, mocker, isolated_dirs):
        _, reminders_dir = isolated_dirs
        mock_cancel = mocker.patch.object(utilities, "cancel_scheduled_timer")
        reminder_path = reminders_dir / "reminder_test_1.json"
        reminder_path.write_text(json.dumps({"id": "reminder_test_1"}))

        await utilities.handle_reminder_delete("reminder_test_1")

        mock_cancel.assert_called_once_with("jarvis-reminder", "reminder_test_1")
        assert not reminder_path.exists()

    @pytest.mark.asyncio
    async def test_handle_alarm_delete_all_cancels_every_alarm(self, utilities, mocker, isolated_dirs):
        alarms_dir, _ = isolated_dirs
        mock_cancel = mocker.patch.object(utilities, "cancel_scheduled_timer")
        paths = []
        for aid in ("alarm_all_1", "alarm_all_2"):
            p = alarms_dir / f"{aid}.json"
            p.write_text(json.dumps({"id": aid}))
            paths.append(p)

        await utilities.handle_alarm_delete("all")

        cancelled_ids = {c.args[1] for c in mock_cancel.call_args_list}
        assert cancelled_ids == {"alarm_all_1", "alarm_all_2"}
        assert all(not p.exists() for p in paths)
