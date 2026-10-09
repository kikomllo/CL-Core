"""Calendar event accessors shared by the dashboard widgets and the daemon-side utilities.

Events use Google Calendar's resource shape (`start`/`end` hold either `dateTime` or, for all-day
events, `date`). Always read them through these helpers so Google-sourced events (all-day, tz-aware)
work wherever locally created ones do.
"""
from datetime import datetime, time, date

SOURCE_LOCAL = "local"
SOURCE_GOOGLE = "google"


def _parse_when(when):
    """`{"dateTime": ...}` or `{"date": ...}` -> naive local datetime, or None if unreadable."""
    if not isinstance(when, dict):
        return None
    try:
        if when.get("dateTime"):
            dt = datetime.fromisoformat(when["dateTime"].replace("Z", "+00:00"))
            # Convert aware times to local wall-clock; naive ones are already local.
            return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
        if when.get("date"):
            return datetime.combine(date.fromisoformat(when["date"]), time.min)
    except (ValueError, TypeError):
        pass
    return None


def event_start(ev):
    return _parse_when(ev.get("start"))


def event_end(ev):
    return _parse_when(ev.get("end"))


def is_all_day(ev):
    start = ev.get("start")
    return isinstance(start, dict) and not start.get("dateTime") and bool(start.get("date"))


def event_source(ev):
    return ev.get("source") or SOURCE_LOCAL


def is_editable(ev):
    """Local events can be edited/deleted from the dashboard; Google ones are read-only until write-back exists."""
    return event_source(ev) == SOURCE_LOCAL and not ev.get("readOnly")


def sort_key(ev):
    return event_start(ev) or datetime.max


def starts_on(ev, day):
    """True if the event starts on `day` (a date)."""
    start = event_start(ev)
    return start is not None and start.date() == day


def is_upcoming(ev, now):
    """Timed events count once they're in the future; all-day ones stay up through their day."""
    start = event_start(ev)
    if start is None:
        return False
    return start.date() >= now.date() if is_all_day(ev) else start > now


def time_label(ev, fmt="%I:%M %p"):
    start = event_start(ev)
    if start is None:
        return ""
    return "All day" if is_all_day(ev) else start.strftime(fmt)
