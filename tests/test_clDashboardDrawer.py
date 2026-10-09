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


@pytest.fixture(autouse=True)
def no_mqtt(mocker):
    """Every widget in the drawer asks for its data over MQTT when built; never reach the live broker."""
    return mocker.patch("utils.clActionRouter.ActionRouter.dispatch", return_value=True)


_widgets = []


@pytest.fixture(autouse=True)
def _close_widgets():
    yield
    while _widgets:
        _widgets.pop().close()


def _ev(i, title, iso, source=None, all_day=False):
    e = {"id": str(i), "summary": title, "start": {"date": iso[:10]} if all_day else {"dateTime": iso}}
    if source:
        e["source"] = source
    return e


EVENTS = {"events": [
    _ev(1, "Dentist", "2026-10-09T09:00:00"),
    _ev(2, "Standup", "2026-10-09T10:30:00", source="google"),
    _ev(3, "Gym", "2026-10-09T18:00:00"),
    _ev(4, "Day off", "2026-10-12", source="google", all_day=True),
    _ev(5, "Team sync", "2026-10-12T10:00:00"),
    _ev(6, "Birthday dinner", "2026-10-14T19:30:00"),
    _ev(7, "Flight", "2026-10-20T06:15:00", source="google"),
]}


def _agenda(qapp, day=9):
    from ui.clDashboardDrawer import DayAgenda
    a = DayAgenda()
    _widgets.append(a)
    a.resize(354, 500)
    a.load_events(EVENTS)
    a.set_specific_day(datetime(2026, 10, day, 12, 0))
    return a


def _rows(agenda):
    from ui.clDashboardDrawer import EventRow
    return agenda.scroll_content.findChildren(EventRow)


def _next_rows(agenda):
    from ui.clDashboardDrawer import NextDayRow
    return agenda.findChildren(NextDayRow)


class TestDayAgenda:
    def test_header_names_the_day_and_counts_its_events(self, qapp):
        a = _agenda(qapp)
        assert a.lbl_title.text() == "Fri 9"
        assert a.lbl_count.text() == "3 EVENTS"

    def test_singular_and_empty_counts(self, qapp):
        assert _agenda(qapp, day=14).lbl_count.text() == "1 EVENT"
        assert _agenda(qapp, day=10).lbl_count.text() == "NO EVENTS"

    def test_the_days_events_show_in_time_order(self, qapp):
        rows = _rows(_agenda(qapp))
        assert [r.title_lbl.fullText() for r in rows] == ["Dentist", "Standup", "Gym"]
        assert [r.time_lbl.text() for r in rows] == ["09:00", "10:30", "18:00"]

    def test_an_all_day_event_says_so(self, qapp):
        rows = _rows(_agenda(qapp, day=12))
        assert rows[0].title_lbl.fullText() == "Day off"
        assert rows[0].time_lbl.text() == "All day"

    def test_local_events_are_solid_and_google_ones_hollow_with_a_tag(self, qapp):
        local, google, _ = _rows(_agenda(qapp))
        assert local.dot.hollow() is False and local.tag is None
        assert google.dot.hollow() is True and google.tag.text() == "GOOGLE"

    def test_clicking_a_local_event_asks_to_edit_it(self, qapp):
        a = _agenda(qapp)
        seen = []
        a.edit_event_signal.connect(seen.append)

        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        row = _rows(a)[0]
        row.show()
        QTest.mouseClick(row, Qt.MouseButton.LeftButton)

        assert [e["id"] for e in seen] == ["1"]

    def test_google_events_are_read_only(self, qapp):
        a = _agenda(qapp)
        seen = []
        a.edit_event_signal.connect(seen.append)
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        google = _rows(a)[1]
        google.show()

        QTest.mouseClick(google, Qt.MouseButton.LeftButton)

        assert google.editable is False
        assert seen == []

    def test_the_next_busy_days_are_listed_with_counts(self, qapp):
        rows = _next_rows(_agenda(qapp))
        assert [r.label.text() for r in rows] == ["Mon 12 · 2 events", "Wed 14 · 1 event"]

    def test_clicking_a_next_day_asks_for_that_day(self, qapp):
        a = _agenda(qapp)
        seen = []
        a.day_requested.connect(seen.append)
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        row = _next_rows(a)[0]
        row.show()

        QTest.mouseClick(row, Qt.MouseButton.LeftButton)

        assert [d.date().isoformat() for d in seen] == ["2026-10-12"]

    def test_the_plus_button_asks_to_add(self, qapp):
        a = _agenda(qapp)
        seen = []
        a.add_event_signal.connect(lambda: seen.append(True))
        a.btn_add.click()
        assert seen == [True]

    def test_rerendering_does_not_leave_old_rows_behind(self, qapp):
        a = _agenda(qapp)
        a.load_events(EVENTS)
        a.load_events(EVENTS)
        assert len(_rows(a)) == 3
        assert len(_next_rows(a)) == 2

    def test_events_with_unreadable_times_are_skipped(self, qapp):
        a = _agenda(qapp)
        a.load_events({"events": EVENTS["events"] + [{"id": "x", "summary": "Broken"}]})
        assert len(_rows(a)) == 3


def _drawer(qapp, height=1080):
    from ui.clDashboardDrawer import DashboardDrawer
    d = DashboardDrawer()
    _widgets.append(d)
    d.resize(400, height)
    d.calendar.current_date = datetime(2026, 10, 9, 12, 0)
    d.calendar.update_ui()
    d.up_next.set_specific_day(d.calendar.current_date)
    d.calendar.load_events(EVENTS)
    d.up_next.load_events(EVENTS)
    d.show()
    qapp.processEvents()
    return d


class TestDrawerWiring:
    def test_selecting_a_day_in_the_calendar_shows_it_in_the_sheet(self, qapp):
        d = _drawer(qapp)
        next(c for c in d.calendar._cells if c.date.day == 12).click()
        assert d.up_next.lbl_title.text() == "Mon 12"
        assert d.up_next.lbl_count.text() == "2 EVENTS"

    def test_a_next_day_link_moves_the_calendar_selection(self, qapp):
        d = _drawer(qapp)
        _next_rows(d.up_next)[0].clicked.emit(datetime(2026, 10, 12))
        assert d.calendar.current_date.date().isoformat() == "2026-10-12"
        assert d.up_next.lbl_title.text() == "Mon 12"

    def test_plus_opens_a_new_event_for_the_selected_day(self, qapp, mocker):
        d = _drawer(qapp)
        next(c for c in d.calendar._cells if c.date.day == 14).click()
        spy = mocker.spy(d.calendar, "open_event_input")

        d.up_next.btn_add.click()

        spy.assert_called_once_with(None)
        assert d.calendar.input_date.date().toString("yyyy-MM-dd") == "2026-10-14"

    def test_clicking_an_event_opens_it_for_editing(self, qapp):
        d = _drawer(qapp)
        _rows(d.up_next)[0].clicked.emit(EVENTS["events"][0])
        assert d.calendar.editing_event_id == "1"
        assert d.calendar.input_title.text() == "Dentist"

    def test_the_calendar_hugs_its_content_and_the_sheet_takes_the_rest(self, qapp):
        d = _drawer(qapp)
        margins = d._layout.contentsMargins()
        assert d.calendar.height() + d.sheet.height() == d.height() - margins.top() - margins.bottom()
        assert d.calendar.height() < 450
        assert d.sheet.height() > 550

    def test_day_mode_gives_the_sheet_even_more_room(self, qapp):
        d = _drawer(qapp)
        before = d.sheet.height()
        d.calendar.set_mode("day")
        qapp.processEvents()
        assert d.sheet.height() > before

    def test_the_old_interface_clUI_relies_on_is_still_there(self, qapp):
        d = _drawer(qapp)
        for name in ("calendar", "up_next", "carousel", "media_pill"):
            assert hasattr(d, name)
        for name in ("stack", "todo_widget", "reminder_widget", "media_widget", "lights_widget", "update_indicator"):
            assert hasattr(d.carousel, name)
        assert callable(d.calendar.load_events) and callable(d.up_next.load_events) and callable(d.update_scaling)
        d.update_scaling()


class TestDock:
    def test_four_round_buttons_pick_the_page(self, qapp):
        d = _drawer(qapp)
        c = d.carousel
        assert len(c.dock_buttons) == 4
        c.dock_buttons[2].click()
        assert c.stack.currentIndex() == 2
        assert [b._selected for b in c.dock_buttons] == [False, False, True, False]
        assert c.lbl_indicator.text() == "MEDIA"

    def test_changing_the_page_from_code_updates_the_dock(self, qapp):
        d = _drawer(qapp)
        d.carousel.stack.setCurrentIndex(3)
        assert d.carousel.dock_buttons[3]._selected
        assert d.carousel.lbl_indicator.text() == "LIGHTS"

    def test_next_and_previous_wrap(self, qapp):
        d = _drawer(qapp)
        d.carousel.prev_slide()
        assert d.carousel.stack.currentIndex() == 3
        d.carousel.next_slide()
        assert d.carousel.stack.currentIndex() == 0

    def test_the_todo_button_shows_how_many_tasks_are_open(self, qapp):
        d = _drawer(qapp)
        d.carousel.todo_widget.update_status({"todos": [
            {"id": "1", "task": "a", "list_name": "L", "completed": False},
            {"id": "2", "task": "b", "list_name": "L", "completed": True},
            {"id": "3", "task": "c", "list_name": "M", "completed": False},
        ]})
        assert d.carousel.dock_buttons[0].badge() == 2

        d.carousel.todo_widget.update_status({"todos": []})
        assert d.carousel.dock_buttons[0].badge() == 0


class TestMediaPill:
    def _pill(self, qapp):
        from ui.clDrawerControls import MediaPill
        p = MediaPill()
        _widgets.append(p)
        return p

    def test_shows_what_is_playing(self, qapp):
        p = self._pill(qapp)
        p.resize(354, 64)
        p.update_status({"title": "Midnight City", "artist": "M83", "status": "Playing"})
        assert p.title_lbl.fullText() == "Midnight City"
        assert p.artist_lbl.fullText() == "M83"
        assert p.status == "Playing"

    def test_says_so_when_nothing_is_playing(self, qapp):
        p = self._pill(qapp)
        p.update_status({})
        assert p.title_lbl.fullText() == "Nothing playing"
        assert p.status == "Paused"

    def test_play_pause_flips_at_once_and_sends_toggle(self, qapp, no_mqtt):
        p = self._pill(qapp)
        p.update_status({"status": "Paused"})

        p.play_btn.click()

        assert p.status == "Playing"
        no_mqtt.assert_called_with("spotify.control", action="toggle", silent=True)
        p.play_btn.click()
        assert p.status == "Paused"

    def test_previous_sends_prev(self, qapp, no_mqtt):
        p = self._pill(qapp)
        p.prev_btn.click()
        no_mqtt.assert_called_with("spotify.control", action="prev", silent=True)

    def test_a_long_title_is_elided_not_stretched(self, qapp):
        p = self._pill(qapp)
        p.resize(300, 64)
        p.update_status({"title": "A" * 120, "artist": "x"})
        p.show()
        qapp.processEvents()
        assert p.minimumSizeHint().width() < 300
        assert p.title_lbl.text().endswith("…")
        assert p.title_lbl.fullText() == "A" * 120


class TestControls:
    def test_the_segmented_pill_keeps_exactly_one_segment_on(self, qapp):
        from ui.clDrawerControls import SegmentedControl
        seg = SegmentedControl([("a", "A"), ("b", "B"), ("c", "C")])
        _widgets.append(seg)
        seg.set_value("b")
        assert seg.value() == "b"
        seg.set_value("c")
        assert [k for k, b in seg._buttons.items() if b.isChecked()] == ["c"]
        seg.set_value(None)
        assert seg.value() is None

    def test_clicking_a_segment_announces_it(self, qapp):
        from ui.clDrawerControls import SegmentedControl
        seg = SegmentedControl([("a", "A"), ("b", "B")])
        _widgets.append(seg)
        seen = []
        seg.changed.connect(seen.append)
        seg._buttons["b"].click()
        assert seen == ["b"] and seg.value() == "b"

    def test_round_button_badges_are_clamped_and_clearable(self, qapp):
        from ui.clDrawerControls import RoundButton
        b = RoundButton(44, "todos.svg", 20)
        _widgets.append(b)
        b.set_badge(7)
        assert b.badge() == 7
        b.set_badge(-3)
        assert b.badge() == 0

    def test_round_button_opts_out_of_the_global_square_button_style(self, qapp):
        from ui.clDrawerControls import RoundButton
        b = RoundButton(44, "todos.svg", 20)
        _widgets.append(b)
        assert "border: none" in b.styleSheet() and "background: transparent" in b.styleSheet()

    def test_round_buttons_paint_without_error_in_every_state(self, qapp):
        from ui.clDrawerControls import RoundButton
        for kind in ("dock", "add", "solid", "ghost"):
            b = RoundButton(40, "play.svg", 14, kind=kind)
            _widgets.append(b)
            b.set_badge(5)
            b.set_selected(True)
            b.show()
            b.grab()

    def test_an_elided_label_asks_for_as_much_width_as_a_plain_label_so_short_text_is_never_clipped(self, qapp):
        # Regression: it once measured the bare text, a few px short of QLabel's own hint, so "2026" lost its last digit.
        from PyQt6.QtWidgets import QLabel
        from clTheme import Theme
        from ui.clDrawerControls import ElidedLabel
        sheet = Theme.get_style("DrawerYearLabel")
        elided, plain = ElidedLabel("2026"), QLabel("2026")
        _widgets.extend([elided, plain])
        elided.setStyleSheet(sheet)
        plain.setStyleSheet(sheet)

        assert elided.sizeHint().width() == plain.sizeHint().width()
        assert elided.minimumSizeHint().width() == 0

    def test_day_bars_and_dots_report_their_marks(self, qapp):
        from ui.clDrawerControls import DayBars, EventDot
        bars = DayBars()
        bars.set_bars([False, True, False, True, False])
        assert bars.bars() == [False, True, False]
        assert EventDot(hollow=True).hollow() and not EventDot().hollow()


class TestRerenderLeavesNoStrayWindows:
    def test_replacing_rows_never_makes_them_top_level_windows(self, qapp):
        """Reparenting a visible row to None turns it into a parentless window; hiding one of those while the
        main window is hidden (the fullscreen swap) made Qt quit the whole UI process."""
        from PyQt6.QtWidgets import QApplication
        a = _agenda(qapp)
        a.show()
        qapp.processEvents()
        before = set(QApplication.topLevelWidgets())

        a.load_events(EVENTS)
        a.set_specific_day(datetime(2026, 10, 12, 12, 0))

        assert set(QApplication.topLevelWidgets()) - before == set()
