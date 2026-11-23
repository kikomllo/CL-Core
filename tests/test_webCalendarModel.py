"""The calendar drawer's date logic (web/js/calendar-model.js), run under node: no browser, no DOM."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "calendar-model.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(expr):
    """Evaluate a JS expression with the model imported as M; returns its JSON."""
    script = f'import * as M from "file:///{MODEL}";\nconsole.log(JSON.stringify({expr}));'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestEventAccessors:
    def test_a_naive_local_datetime_keeps_its_wall_clock(self):
        # what the daemon writes: dt.isoformat() with microseconds and no offset
        assert run('M.timeLabel({start:{dateTime:"2026-11-22T09:05:00.123456"}})') == "09:05"

    def test_an_all_day_date_is_local_midnight_not_utc(self):
        assert run('M.dayKey(M.eventStart({start:{date:"2026-11-22"}}))') == "2026-11-22"
        assert run('M.timeLabel({start:{date:"2026-11-22"}})') == "All day"
        assert run('M.isAllDay({start:{date:"2026-11-22"}})') is True

    def test_unreadable_events_do_not_throw_and_sort_last(self):
        assert run('M.eventStart({start:{dateTime:"nonsense"}})') is None
        assert run('M.eventStart({})') is None
        assert run('M.sortKey({}) === Infinity') is True

    def test_only_local_events_are_editable(self):
        assert run('[M.isEditable({}), M.isEditable({source:"local"}), M.isEditable({source:"google"}), M.isEditable({source:"local",readOnly:true})]') == [True, True, False, False]


class TestGrids:
    def test_november_2026_starts_on_sunday_with_six_leading_blanks(self):
        cells = run('M.monthCells(2026, 10).map(d => d ? new Date(d).getDate() : null)')
        assert cells[:7] == [None] * 6 + [1] and len(cells) % 7 == 0
        assert cells.count(None) == len(cells) - 30

    def test_a_four_week_february_has_four_rows(self):
        assert len(run("M.monthCells(2027, 1)")) == 28            # Feb 2027 starts on a Monday and has 28 days

    def test_the_week_runs_monday_to_sunday(self):
        week = run('M.weekCells(new Date(2026, 10, 22)).map(d => new Date(d).getDate())')    # Sunday 22 Nov
        assert week == [16, 17, 18, 19, 20, 21, 22]


class TestStepping:
    def test_a_month_step_clamps_the_day(self):
        assert run('M.dayKey(M.step(new Date(2026, 0, 31), "month", 1))') == "2026-02-28"
        assert run('M.dayKey(M.step(new Date(2028, 0, 31), "month", 1))') == "2028-02-29"

    def test_week_day_and_year_steps(self):
        assert run('M.dayKey(M.step(new Date(2026, 10, 22), "week", -1))') == "2026-11-15"
        assert run('M.dayKey(M.step(new Date(2026, 11, 31), "day", 1))') == "2027-01-01"
        assert run('M.dayKey(M.step(new Date(2028, 1, 29), "year", 1))') == "2029-02-28"


class TestTitlesAndAgenda:
    def test_titles(self):
        assert run('M.titleFor("month", new Date(2026, 10, 22))') == {"main": "November", "sub": "2026"}
        assert run('M.titleFor("week", new Date(2026, 10, 22))')["main"] == "Nov 16–22"
        assert run('M.titleFor("week", new Date(2026, 10, 30))')["main"] == "Nov 30 – Dec 6"
        assert run('M.titleFor("day", new Date(2026, 10, 22))')["main"] == "Sunday"

    def test_count_labels(self):
        assert run('[0, 1, 4].map(M.countLabel)') == ["NO EVENTS", "1 EVENT", "4 EVENTS"]

    def test_events_group_by_day_in_time_order(self):
        grouped = run('[...M.groupByDay([{id:"b",start:{dateTime:"2026-11-22T18:00:00"}},{id:"a",start:{dateTime:"2026-11-22T09:00:00"}},{id:"x"}])]')
        assert grouped == [["2026-11-22", [{"id": "a", "start": {"dateTime": "2026-11-22T09:00:00"}}, {"id": "b", "start": {"dateTime": "2026-11-22T18:00:00"}}]]]

    def test_the_next_busy_days_skip_empty_and_past_days(self):
        evs = ('[{start:{dateTime:"2026-11-20T10:00:00"}},{start:{dateTime:"2026-11-24T10:00:00"}},{start:{dateTime:"2026-11-24T11:00:00"}},'
               '{start:{dateTime:"2026-11-26T10:00:00"}},{start:{dateTime:"2026-11-30T10:00:00"}}]')
        nxt = run(f'M.nextBusyDays({evs}, new Date(2026, 10, 22)).map(x => [M.dayKey(x.date), x.count])')
        assert nxt == [["2026-11-24", 2], ["2026-11-26", 1]]


class TestCreatePayload:
    def test_matches_what_the_qt_dialog_sends(self):
        assert run('M.createPayload("  Dentist ", "2026-11-23", "09:30")') == {"event": "Dentist", "time_str": "2026-11-23 09:30"}

    def test_an_edit_carries_the_id(self):
        assert run('M.createPayload("Dentist", "2026-11-23", "09:30", "170")')["id"] == "170"

    @pytest.mark.parametrize("args", ['"", "2026-11-23", "09:30"', '"x", "23/11/2026", "09:30"', '"x", "2026-11-23", ""'])
    def test_incomplete_input_is_refused(self, args):
        assert run(f"M.createPayload({args})") is None
