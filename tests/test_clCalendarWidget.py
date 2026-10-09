import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


_APP = None


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    global _APP
    app = _APP = QApplication.instance() or QApplication(sys.argv)   # held at module level: a collected QApplication can't be rebuilt
    import clUI  # installs the stylesheet / scaling hooks once, like the real app
    return app


_panels = []  # keep every panel alive for the whole test


@pytest.fixture(autouse=True)
def _close_panels():
    yield
    while _panels:
        _panels.pop().close()


def _calendar_in_panel(qapp, width, panel_height=1000, year=2026, month=10, day=9, mode="month"):
    """The calendar the way the drawer uses it: no stretch, with a stretchy widget below."""
    from PyQt6.QtWidgets import QWidget, QVBoxLayout
    from clCalendarWidget import CalendarWidget
    panel = QWidget()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    _panels.append(panel)
    cal = CalendarWidget()
    cal.current_date = datetime(year, month, day, 12, 0)
    cal.set_mode(mode)
    below = QWidget()
    lay.addWidget(cal)
    lay.addWidget(below, stretch=1)
    panel.resize(width, panel_height)
    panel.show()
    qapp.processEvents()
    return panel, cal, below


def _ev(i, title, iso, source=None, all_day=False):
    e = {"id": str(i), "summary": title, "start": {"date": iso[:10]} if all_day else {"dateTime": iso}}
    if source:
        e["source"] = source
    return e


class TestMonthDaysAreSquares:
    @pytest.mark.parametrize("width", [300, 350, 400, 480])
    def test_every_day_cell_is_square(self, qapp, width):
        panel, cal, _ = _calendar_in_panel(qapp, width)

        assert len(cal._cells) == 31
        for cell in cal._cells:
            assert abs(cell.width() - cell.height()) <= 1, (cell.width(), cell.height())

    def test_cells_get_taller_when_the_panel_gets_wider(self, qapp):
        _, narrow, _ = _calendar_in_panel(qapp, 300)
        _, wide, _ = _calendar_in_panel(qapp, 450)

        assert wide._cells[0].height() > narrow._cells[0].height()


class TestCalendarFitsItsContent:
    def test_height_hugs_the_month_and_the_rest_goes_to_the_widget_below(self, qapp):
        panel, cal, below = _calendar_in_panel(qapp, 400, panel_height=1000)

        assert cal.height() == cal.fitted_height(400)
        assert cal.height() + below.height() == 1000
        assert cal.height() < 500

    def test_a_four_week_month_is_shorter_than_a_six_week_one(self, qapp):
        # Feb 2027 starts on a Monday and has 28 days -> 4 rows; Aug 2026 -> 6 rows.
        _, four, _ = _calendar_in_panel(qapp, 400, year=2027, month=2)
        _, six, _ = _calendar_in_panel(qapp, 400, year=2026, month=8)

        assert four._week_rows() == 4 and six._week_rows() == 6
        assert four.height() < six.height()

    def test_switching_months_refits_the_height(self, qapp):
        panel, cal, below = _calendar_in_panel(qapp, 400, year=2026, month=8)
        six_rows = cal.height()

        cal.go_to_month(2)  # Feb 2026: Sun start, 28 days -> 4 rows
        qapp.processEvents()

        assert cal.height() < six_rows
        assert cal.height() + below.height() == 1000

    def test_week_is_one_row_and_day_hides_the_grid(self, qapp):
        _, cal, below = _calendar_in_panel(qapp, 400)
        month_h = cal.height()

        cal.set_mode("week")
        qapp.processEvents()
        week_h = cal.height()
        assert len(cal._cells) == 7
        assert week_h < month_h

        cal.set_mode("day")
        qapp.processEvents()
        assert cal._cells == [] or not cal.stack.isVisible()
        assert not cal.stack.isVisible()
        assert cal.height() < week_h
        assert cal.height() + below.height() == 1000

    def test_the_year_picker_is_as_tall_as_a_five_week_month(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400, year=2026, month=10)   # Oct 2026 is a 5-row month
        month_h = cal.height()

        cal.set_mode("year")
        qapp.processEvents()

        assert cal.height() == month_h


class TestModes:
    def test_the_pill_and_the_mode_stay_in_step(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)
        assert cal.segments.value() == "month"

        cal.segments._buttons["week"].click()
        assert cal.current_mode == "week" and cal.segments.value() == "week"
        cal.segments._buttons["day"].click()
        assert cal.current_mode == "day" and cal.segments.value() == "day"

    def test_the_title_opens_the_year_picker_and_a_month_leads_back(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)

        cal.btn_title.click()
        assert cal.current_mode == "year"
        assert cal.segments.value() is None
        assert cal.btn_title.text() == "2026"

        cal.go_to_month(3)
        assert cal.current_mode == "month"
        assert cal.current_date.month == 3

    def test_titles_per_mode(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)   # Fri 9 Oct 2026
        assert cal.btn_title.text() == "October" and cal.lbl_year.fullText() == "2026"
        cal.set_mode("week")
        assert cal.btn_title.text() == "Oct 5 – 11" and cal.lbl_year.fullText() == "W41"
        cal.set_mode("day")
        assert cal.btn_title.text() == "Friday" and cal.lbl_year.fullText() == "Oct 9, 2026"

    def test_a_week_that_spans_two_months_names_both(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400, year=2026, month=10, day=1, mode="week")   # Mon Sep 28
        assert cal.btn_title.text() == "Sep 28 – Oct 4"

    def test_long_titles_use_a_smaller_font_so_the_header_never_outgrows_the_drawer(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)
        month_style = cal.btn_title.styleSheet()
        cal.set_mode("week")
        assert cal.btn_title.styleSheet() != month_style
        assert cal.minimumSizeHint().width() <= 400


class TestSelectionAndNavigation:
    def test_clicking_a_day_selects_it_and_says_so(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)
        seen = []
        cal.day_selected_signal.connect(seen.append)

        next(c for c in cal._cells if c.date.day == 14).click()

        assert cal.current_date.date().isoformat() == "2026-10-14"
        assert [d.date().isoformat() for d in seen] == ["2026-10-14"]

    def test_exactly_one_cell_is_the_selected_one(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)
        next(c for c in cal._cells if c.date.day == 20).click()
        qapp.processEvents()

        selected = [c for c in cal._cells if "ff8c00" in c.styleSheet().lower()]
        assert [c.date.day for c in selected] == [20]

    def test_clicking_a_day_in_week_mode_selects_it(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400, mode="week")
        cal._cells[0].click()   # Monday Oct 5

        assert cal.current_date.date().isoformat() == "2026-10-05"

    def test_next_and_previous_step_by_the_mode(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)
        cal.btn_next.click()
        assert (cal.current_date.year, cal.current_date.month, cal.current_date.day) == (2026, 11, 9)
        cal.btn_prev.click(); cal.btn_prev.click()
        assert (cal.current_date.month, cal.current_date.day) == (9, 9)

        cal.set_mode("week"); cal.btn_next.click()
        assert cal.current_date.date().isoformat() == "2026-09-16"
        cal.set_mode("day"); cal.btn_prev.click()
        assert cal.current_date.date().isoformat() == "2026-09-15"
        cal.set_mode("year"); cal.btn_next.click()
        assert cal.current_date.year == 2027

    def test_stepping_a_month_clamps_the_day(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400, year=2026, month=1, day=31)
        cal.btn_next.click()
        assert (cal.current_date.month, cal.current_date.day) == (2, 28)

    def test_stepping_across_the_year_boundary(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400, year=2026, month=12, day=15)
        cal.btn_next.click()
        assert (cal.current_date.year, cal.current_date.month) == (2027, 1)
        cal.btn_prev.click(); cal.btn_prev.click()
        assert (cal.current_date.year, cal.current_date.month) == (2026, 11)

    def test_today_jumps_back_and_leaves_the_year_picker(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400, year=2024, month=3)
        cal.set_mode("year")
        seen = []
        cal.day_selected_signal.connect(seen.append)

        cal.btn_today.click()

        assert cal.current_mode == "month"
        assert cal.current_date.date() == datetime.now().date()
        assert len(seen) == 1


class TestEventMarks:
    def test_local_events_get_solid_bars_and_google_ones_hollow(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)
        cal.load_events({"events": [
            _ev(1, "Dentist", "2026-10-09T09:00:00"),
            _ev(2, "Standup", "2026-10-09T10:30:00", source="google"),
            _ev(3, "Day off", "2026-10-12", source="google", all_day=True),
        ]})

        by_day = {c.date.day: c for c in cal._cells}
        assert by_day[9].bars.bars() == [False, True]
        assert by_day[12].bars.bars() == [True]
        assert by_day[10].bars.bars() == []

    def test_at_most_three_bars_show(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)
        cal.load_events({"events": [_ev(i, f"E{i}", "2026-10-09T0%d:00:00" % (i + 1)) for i in range(5)]})

        assert len(next(c for c in cal._cells if c.date.day == 9).bars.bars()) == 3

    def test_aware_and_all_day_events_render_in_every_mode(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)
        cal.load_events({"events": [
            _ev(1, "Holiday", "2026-10-09", source="google", all_day=True),
            _ev(2, "Call", "2026-10-09T09:00:00+00:00", source="google"),
        ]})
        for mode in ("month", "week", "day", "year"):
            cal.set_mode(mode)


class TestEventDialog:
    def test_a_new_event_defaults_to_the_selected_day(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)
        next(c for c in cal._cells if c.date.day == 22).click()

        cal.open_event_input(None)

        assert cal.input_date.date().toString("yyyy-MM-dd") == "2026-10-22"
        assert cal.btn_dialog_delete.isHidden()

    def test_editing_prefills_and_offers_delete(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)

        cal.open_event_input(_ev(7, "Dentist", "2026-10-14T09:30:00"))

        assert cal.input_title.text() == "Dentist"
        assert cal.input_date.date().toString("yyyy-MM-dd") == "2026-10-14"
        assert cal.input_time.time().toString("HH:mm") == "09:30"
        assert not cal.btn_dialog_delete.isHidden()
        assert cal.editing_event_id == "7"


class TestSyncIndicator:
    def test_hidden_until_the_google_integration_sets_it(self, qapp):
        _, cal, _ = _calendar_in_panel(qapp, 400)
        assert cal.lbl_sync.isHidden()

        cal.set_sync_status("2 min")
        assert not cal.lbl_sync.isHidden() and "2 min" in cal.lbl_sync.text()

        cal.set_sync_status(None)
        assert cal.lbl_sync.isHidden()


class TestRerenderLeavesNoStrayWindows:
    def test_changing_month_never_makes_old_cells_top_level_windows(self, qapp):
        """See the drawer test: a parentless cell is a window, and Qt quits when no window is visible."""
        from PyQt6.QtWidgets import QApplication
        _, cal, _ = _calendar_in_panel(qapp, 400)
        before = set(QApplication.topLevelWidgets())

        cal.shift(1)
        cal.set_mode("week")
        cal.set_mode("month")

        assert set(QApplication.topLevelWidgets()) - before == set()
