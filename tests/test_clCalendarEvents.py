import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils import clCalendarEvents as ce


def _local(dt_iso):
    return {"start": {"dateTime": dt_iso}}


class TestEventStart:
    def test_naive_datetime_is_taken_as_local(self):
        assert ce.event_start(_local("2026-10-12T09:30:00")) == datetime(2026, 10, 12, 9, 30)

    def test_aware_datetime_is_converted_to_local_wall_clock_not_just_stripped(self):
        # The old widget code did .replace(tzinfo=None), which shows a Google event
        # from another timezone at the wrong hour.
        utc = datetime(2026, 10, 12, 9, 30, tzinfo=timezone.utc)
        expected = utc.astimezone().replace(tzinfo=None)

        assert ce.event_start(_local("2026-10-12T09:30:00+00:00")) == expected
        assert ce.event_start(_local("2026-10-12T09:30:00Z")) == expected

    def test_all_day_event_starts_at_local_midnight(self):
        ev = {"start": {"date": "2026-10-12"}, "end": {"date": "2026-10-13"}}

        assert ce.event_start(ev) == datetime(2026, 10, 12)
        assert ce.event_end(ev) == datetime(2026, 10, 13)

    def test_unreadable_events_yield_none_instead_of_raising(self):
        assert ce.event_start({}) is None
        assert ce.event_start({"start": "garbage"}) is None
        assert ce.event_start({"start": {"dateTime": "not a date"}}) is None


class TestAllDayAndLabels:
    def test_is_all_day_only_for_date_only_starts(self):
        assert ce.is_all_day({"start": {"date": "2026-10-12"}})
        assert not ce.is_all_day(_local("2026-10-12T09:30:00"))
        assert not ce.is_all_day({})

    def test_time_label(self):
        assert ce.time_label(_local("2026-10-12T14:05:00")) == "02:05 PM"
        assert ce.time_label(_local("2026-10-12T14:05:00"), "%H:%M") == "14:05"
        assert ce.time_label({"start": {"date": "2026-10-12"}}) == "All day"


class TestUpcoming:
    NOW = datetime(2026, 10, 12, 12, 0)

    def test_timed_events_must_be_in_the_future(self):
        assert ce.is_upcoming(_local("2026-10-12T13:00:00"), self.NOW)
        assert not ce.is_upcoming(_local("2026-10-12T11:00:00"), self.NOW)

    def test_all_day_events_stay_upcoming_through_their_day(self):
        # Midnight start is already "past" at noon, but the day isn't over.
        assert ce.is_upcoming({"start": {"date": "2026-10-12"}}, self.NOW)
        assert not ce.is_upcoming({"start": {"date": "2026-10-11"}}, self.NOW)

    def test_unreadable_event_is_not_upcoming(self):
        assert not ce.is_upcoming({}, self.NOW)


class TestSortingAndDays:
    def test_sort_key_orders_by_start_and_puts_unreadable_last(self):
        late, early, bad = _local("2026-10-12T18:00:00"), _local("2026-10-12T08:00:00"), {}

        assert sorted([bad, late, early], key=ce.sort_key) == [early, late, bad]

    def test_starts_on(self):
        ev = _local("2026-10-12T23:30:00")

        assert ce.starts_on(ev, datetime(2026, 10, 12).date())
        assert not ce.starts_on(ev, datetime(2026, 10, 13).date())
        assert not ce.starts_on({}, datetime(2026, 10, 12).date())


class TestEditability:
    def test_events_without_a_source_are_local_and_editable(self):
        assert ce.event_source({}) == ce.SOURCE_LOCAL
        assert ce.is_editable({})

    def test_google_events_are_read_only_until_write_back_exists(self):
        assert not ce.is_editable({"source": "google"})

    def test_read_only_flag_wins(self):
        assert not ce.is_editable({"source": "local", "readOnly": True})
