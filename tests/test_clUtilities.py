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
    async def test_handle_reminder_create_deletes_scratch_audio_after_copying(self, utilities, mocker, isolated_dirs, tmp_path):
        """clWhisper.py's per-command scratch WAV otherwise accumulates
        forever (nothing else ever deleted it) -- once a voice-message
        reminder has its own permanent copy, the scratch original is no
        longer needed and clUtilities.py should remove it itself."""
        _, reminders_dir = isolated_dirs
        scratch_dir = tmp_path / "scratch"
        scratch_dir.mkdir()
        mocker.patch("clUtilities.SCRATCH_DIR", str(scratch_dir))
        mocker.patch.object(utilities, "schedule_systemd_timer", return_value=True)

        audio_src = scratch_dir / "voice_command_test.wav"
        audio_src.write_bytes(b"fake-wav-bytes")

        await utilities.handle_reminder_create({
            "time": "in 5 minutes",
            "task": "check the oven",
            "reminder_id": "reminder_audio_test",
            "audio_path": str(audio_src),
        })

        audio_dest = reminders_dir / "reminder_audio_test.wav"
        assert audio_dest.exists()
        assert audio_dest.read_bytes() == b"fake-wav-bytes"
        assert not audio_src.exists()

    @pytest.mark.asyncio
    async def test_handle_reminder_create_does_not_delete_audio_outside_scratch(self, utilities, mocker, isolated_dirs, tmp_path):
        """A payload-supplied audio_path pointing anywhere other than
        data/scratch/ must never be deleted -- it isn't clUtilities.py's to
        clean up, and tmp_audio_path comes straight from an MQTT payload."""
        _, reminders_dir = isolated_dirs
        scratch_dir = tmp_path / "scratch"
        scratch_dir.mkdir()
        mocker.patch("clUtilities.SCRATCH_DIR", str(scratch_dir))
        mocker.patch.object(utilities, "schedule_systemd_timer", return_value=True)

        outside_audio = tmp_path / "not_scratch" / "some_audio.wav"
        outside_audio.parent.mkdir()
        outside_audio.write_bytes(b"fake-wav-bytes")

        await utilities.handle_reminder_create({
            "time": "in 5 minutes",
            "task": "check the oven",
            "reminder_id": "reminder_audio_test_2",
            "audio_path": str(outside_audio),
        })

        assert outside_audio.exists()

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


class TestTodoCreateSilentFlag:
    """A task typed into the UI is already visible on screen the instant
    it's added -- a spoken confirmation on top of that only makes sense for
    voice-added tasks, where it's the only feedback the user gets."""

    @pytest.fixture
    def isolated_todos_dir(self, tmp_path, mocker):
        todos_dir = tmp_path / "todos"
        todos_dir.mkdir()
        mocker.patch("clUtilities.TODOS_DIR", str(todos_dir))
        return todos_dir

    def _speak_calls(self, utilities):
        return [
            c for c in utilities.mqtt_client.publish.call_args_list
            if c.args[0] == "jarvis/sys/speak"
        ]

    @pytest.mark.asyncio
    async def test_silent_true_skips_the_spoken_confirmation(self, utilities, isolated_todos_dir):
        await utilities.handle_todo_create({"task": "Buy milk", "silent": True})

        assert self._speak_calls(utilities) == []

    @pytest.mark.asyncio
    async def test_silent_false_still_speaks_the_confirmation(self, utilities, isolated_todos_dir):
        await utilities.handle_todo_create({"task": "Buy milk", "silent": False})

        assert len(self._speak_calls(utilities)) == 1

    @pytest.mark.asyncio
    async def test_omitting_silent_defaults_to_speaking(self, utilities, isolated_todos_dir):
        """Voice-originated creates never pass silent at all -- must keep
        speaking by default, not just when explicitly told to."""
        await utilities.handle_todo_create({"task": "Buy milk"})

        assert len(self._speak_calls(utilities)) == 1


class TestTodoCompleteCanMarkIncomplete:
    """Unchecking a completed task in the UI now marks it incomplete again
    instead of deleting it -- handle_todo_complete takes a completed flag
    rather than always hardcoding True, so the one action covers both
    directions of the toggle."""

    @pytest.fixture
    def isolated_todos_dir(self, tmp_path, mocker):
        todos_dir = tmp_path / "todos"
        todos_dir.mkdir()
        mocker.patch("clUtilities.TODOS_DIR", str(todos_dir))
        return todos_dir

    def _write_todo(self, todos_dir, todo_id, completed):
        (todos_dir / f"{todo_id}.json").write_text(
            json.dumps({"id": todo_id, "task": "Buy milk", "completed": completed}),
            encoding="utf-8",
        )

    def _read_todo(self, todos_dir, todo_id):
        return json.loads((todos_dir / f"{todo_id}.json").read_text(encoding="utf-8"))

    @pytest.mark.asyncio
    async def test_completed_false_marks_an_incomplete_task_instead_of_deleting(self, utilities, isolated_todos_dir):
        self._write_todo(isolated_todos_dir, "1", completed=True)

        await utilities.handle_todo_complete("1", False)

        assert (isolated_todos_dir / "1.json").exists()
        assert self._read_todo(isolated_todos_dir, "1")["completed"] is False

    @pytest.mark.asyncio
    async def test_completed_true_marks_it_complete(self, utilities, isolated_todos_dir):
        self._write_todo(isolated_todos_dir, "1", completed=False)

        await utilities.handle_todo_complete("1", True)

        assert self._read_todo(isolated_todos_dir, "1")["completed"] is True

    @pytest.mark.asyncio
    async def test_omitting_completed_defaults_to_true(self, utilities, isolated_todos_dir):
        """Existing callers (e.g. a voice command) only ever pass an id --
        must keep completing by default, not just when told to explicitly."""
        self._write_todo(isolated_todos_dir, "1", completed=False)

        await utilities.handle_todo_complete("1")

        assert self._read_todo(isolated_todos_dir, "1")["completed"] is True


class TestNoteHandlers:
    """The quick-notes widget: create/update/delete/list, mirroring the
    todo handlers' shape but simpler -- a note is just free text with no
    completed state or list grouping."""

    @pytest.fixture
    def isolated_notes_dir(self, tmp_path, mocker):
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        mocker.patch("clUtilities.NOTES_DIR", str(notes_dir))
        return notes_dir

    def _write_note(self, notes_dir, note_id, text, title="", time_created="2026-01-01T00:00:00"):
        (notes_dir / f"{note_id}.json").write_text(
            json.dumps({"id": note_id, "title": title, "text": text, "time_created": time_created}),
            encoding="utf-8",
        )

    def _read_note(self, notes_dir, note_id):
        return json.loads((notes_dir / f"{note_id}.json").read_text(encoding="utf-8"))

    def _status_calls(self, utilities):
        return [
            c for c in utilities.mqtt_client.publish.call_args_list
            if c.args[0] == "jarvis/sys/note/status"
        ]

    @pytest.mark.asyncio
    async def test_create_writes_a_note_file_and_republishes_the_list(self, utilities, isolated_notes_dir):
        await utilities.handle_note_create({"text": "Buy milk"})

        files = list(isolated_notes_dir.glob("*.json"))
        assert len(files) == 1
        assert json.loads(files[0].read_text(encoding="utf-8"))["text"] == "Buy milk"
        assert len(self._status_calls(utilities)) == 1

    @pytest.mark.asyncio
    async def test_create_allows_an_empty_note(self, utilities, isolated_notes_dir):
        """The "+ Add Note" flow creates an empty note immediately, then the
        user types into it and it autosaves on focus-out -- creation must
        not require text up front like todo does."""
        await utilities.handle_note_create({})

        files = list(isolated_notes_dir.glob("*.json"))
        assert len(files) == 1
        assert json.loads(files[0].read_text(encoding="utf-8"))["text"] == ""

    @pytest.mark.asyncio
    async def test_creating_two_notes_back_to_back_gets_distinct_ids(self, utilities, isolated_notes_dir):
        """Unlike a todo (created after the user finishes typing a task
        name), a note is created the instant "+ Add Note" is clicked --
        second-precision ids (like todo's) could collide if two notes are
        added within the same second."""
        await utilities.handle_note_create({"text": "First"})
        await utilities.handle_note_create({"text": "Second"})

        files = list(isolated_notes_dir.glob("*.json"))
        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_update_changes_the_text(self, utilities, isolated_notes_dir):
        self._write_note(isolated_notes_dir, "1", "Old text", title="Groceries")

        await utilities.handle_note_update("1", "Groceries", "New text")

        assert self._read_note(isolated_notes_dir, "1")["text"] == "New text"

    @pytest.mark.asyncio
    async def test_update_changes_the_title(self, utilities, isolated_notes_dir):
        self._write_note(isolated_notes_dir, "1", "Buy milk", title="")

        await utilities.handle_note_update("1", "Groceries", "Buy milk")

        assert self._read_note(isolated_notes_dir, "1")["title"] == "Groceries"

    @pytest.mark.asyncio
    async def test_create_stores_the_title(self, utilities, isolated_notes_dir):
        await utilities.handle_note_create({"title": "Groceries", "text": "Buy milk"})

        files = list(isolated_notes_dir.glob("*.json"))
        assert json.loads(files[0].read_text(encoding="utf-8"))["title"] == "Groceries"

    @pytest.mark.asyncio
    async def test_delete_removes_the_file(self, utilities, isolated_notes_dir):
        self._write_note(isolated_notes_dir, "1", "Buy milk")

        await utilities.handle_note_delete("1")

        assert not (isolated_notes_dir / "1.json").exists()

    @pytest.mark.asyncio
    async def test_list_sorts_newest_first_by_creation_time(self, utilities, isolated_notes_dir):
        # Sorted by time_created (stable across edits), not last-updated --
        # editing a note must not make it jump around the list mid-type.
        self._write_note(isolated_notes_dir, "1", "Oldest", time_created="2026-01-01T00:00:00")
        self._write_note(isolated_notes_dir, "2", "Newest", time_created="2026-01-03T00:00:00")
        self._write_note(isolated_notes_dir, "3", "Middle", time_created="2026-01-02T00:00:00")

        await utilities.handle_note_list()

        payload = json.loads(self._status_calls(utilities)[-1].args[1])
        assert [n["id"] for n in payload["notes"]] == ["2", "3", "1"]
