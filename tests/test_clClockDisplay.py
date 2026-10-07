import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


from ui import clClockDisplay as cd
from ui.clClockDisplay import ClockDisplay, format_parts, load_settings, next_tick_ms


class FakeClock:
    def __init__(self, *args):
        self.now = datetime(*args)

    def __call__(self):
        return self.now


class TestFormatParts:
    def test_24_hour_time_and_the_full_date_in_capitals(self):
        parts = format_parts(datetime(2026, 10, 7, 21, 5, 9), hour24=True, language="en")
        assert parts == ("21", "05", "09", "", "WEDNESDAY · 7 OCTOBER 2026")

    @pytest.mark.parametrize("hour, expected, marker", [
        (0, "12", "AM"), (1, "01", "AM"), (11, "11", "AM"), (12, "12", "PM"), (13, "01", "PM"), (23, "11", "PM"),
    ])
    def test_12_hour_time_with_a_marker(self, hour, expected, marker):
        hours, _, _, am_pm, _ = format_parts(datetime(2026, 10, 7, hour, 30, 0), hour24=False)
        assert (hours, am_pm) == (expected, marker)

    def test_portuguese_names(self):
        assert format_parts(datetime(2026, 10, 7, 12, 0, 0), language="pt")[4] == "QUARTA-FEIRA · 7 OUTUBRO 2026"

    @pytest.mark.parametrize("day, weekday", [(5, "MONDAY"), (6, "TUESDAY"), (7, "WEDNESDAY"), (8, "THURSDAY"), (9, "FRIDAY"), (10, "SATURDAY"), (11, "SUNDAY")])
    def test_every_weekday_name_is_right(self, day, weekday):
        assert format_parts(datetime(2026, 10, day, 12, 0, 0))[4].startswith(weekday)

    def test_every_month_name_is_right(self):
        months = [format_parts(datetime(2026, m, 15, 12, 0, 0))[4].split(" ")[3] for m in range(1, 13)]
        assert months[0] == "JANUARY" and months[5] == "JUNE" and months[11] == "DECEMBER"

    def test_the_date_rolls_over_at_midnight(self):
        before = format_parts(datetime(2026, 10, 7, 23, 59, 59))
        after = format_parts(datetime(2026, 10, 8, 0, 0, 0))
        assert before[4].startswith("WEDNESDAY · 7") and after[4].startswith("THURSDAY · 8")
        assert after[:3] == ("00", "00", "00")

    def test_the_year_rolls_over_too(self):
        assert format_parts(datetime(2027, 1, 1, 0, 0, 0))[4] == "FRIDAY · 1 JANUARY 2027"

    def test_an_unknown_language_falls_back_to_english(self):
        assert format_parts(datetime(2026, 10, 7, 12, 0, 0), language="xx")[4].startswith("WEDNESDAY")


class TestLoadSettings:
    def test_defaults_when_nothing_is_configured(self):
        assert load_settings(None) == {"enabled": True, "hour24": True, "seconds": True, "language": "en", "glow": 1.0}
        assert load_settings({}) == load_settings(None)

    def test_valid_overrides_are_kept(self):
        assert load_settings({"hour24": False, "seconds": False, "language": "pt", "enabled": False, "glow": 0.5}) == {
            "enabled": False, "hour24": False, "seconds": False, "language": "pt", "glow": 0.5}

    def test_partial_overrides_leave_the_rest_alone(self):
        assert load_settings({"seconds": False})["hour24"] is True

    @pytest.mark.parametrize("bad", [{"hour24": "no"}, {"seconds": 0}, {"enabled": None}, {"language": "klingon"}, {"language": 5}, {"glow": "strong"}, {"glow": True}, {"glow": None}])
    def test_wrong_types_fall_back_to_the_defaults(self, bad):
        assert load_settings(bad) == load_settings(None)


class TestGlowSetting:
    def test_integers_are_accepted(self):
        assert load_settings({"glow": 2})["glow"] == 2.0

    def test_zero_turns_it_off(self):
        assert load_settings({"glow": 0})["glow"] == 0.0

    def test_it_is_clamped_to_a_sane_range(self):
        assert load_settings({"glow": -4})["glow"] == 0.0
        assert load_settings({"glow": 99})["glow"] == cd.GLOW_MAX


class TestNextTick:
    def test_aims_just_after_the_next_second_boundary(self):
        ms = next_tick_ms(datetime(2026, 1, 1, 12, 0, 0, 250_000), with_seconds=True)
        assert 750 < ms <= 760

    def test_exactly_on_a_second_waits_a_full_one(self):
        assert 1000 < next_tick_ms(datetime(2026, 1, 1, 12, 0, 0, 0), True) <= 1010

    def test_without_seconds_it_waits_for_the_next_minute(self):
        ms = next_tick_ms(datetime(2026, 1, 1, 12, 0, 30, 0), with_seconds=False)
        assert 30_000 < ms <= 30_010

    def test_the_last_second_of_a_minute(self):
        assert next_tick_ms(datetime(2026, 1, 1, 12, 0, 59, 500_000), with_seconds=False) <= 510


def _clock(qapp, when=(2026, 10, 7, 21, 47, 32), **settings):
    fake = FakeClock(*when)
    return ClockDisplay(settings=settings, now_fn=fake), fake


class TestBehaviour:
    def test_hidden_until_told_to_show(self, qapp):
        clock, _ = _clock(qapp)
        assert clock.isHidden()

    def test_does_not_intercept_the_mouse(self, qapp):
        from PyQt6.QtCore import Qt
        clock, _ = _clock(qapp)
        assert clock.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def test_disabled_in_the_settings_it_never_shows(self, qapp):
        clock, _ = _clock(qapp, enabled=False)
        clock.show_if_enabled()
        assert clock.isHidden()

    def test_the_timer_only_runs_while_shown(self, qapp):
        clock, _ = _clock(qapp)
        assert not clock._timer.isActive()

        clock.show()
        assert clock._timer.isActive()

        clock.hide()
        assert not clock._timer.isActive()

    def test_a_tick_picks_up_the_new_time_and_reschedules(self, qapp, mocker):
        clock, fake = _clock(qapp)
        clock.show()
        fake.now = datetime(2026, 10, 7, 21, 47, 33)
        update = mocker.patch.object(clock, "update")

        clock._tick()

        assert clock._parts[2] == "33"
        update.assert_called_once()
        assert clock._timer.isActive()
        clock.hide()

    def test_nothing_is_repainted_when_the_shown_text_did_not_change(self, qapp, mocker):
        """Seconds off: a tick inside the same minute changes nothing visible."""
        clock, fake = _clock(qapp, seconds=False)
        clock.show()
        fake.now = datetime(2026, 10, 7, 21, 47, 50)
        update = mocker.patch.object(clock, "update")

        clock._tick()

        update.assert_not_called()
        clock.hide()

    def test_a_new_minute_is_repainted_even_without_seconds(self, qapp, mocker):
        clock, fake = _clock(qapp, seconds=False)
        clock.show()
        fake.now = datetime(2026, 10, 7, 21, 48, 0)
        update = mocker.patch.object(clock, "update")

        clock._tick()

        update.assert_called_once()
        clock.hide()

    def test_showing_again_after_being_hidden_catches_up_to_the_current_time(self, qapp):
        clock, fake = _clock(qapp)
        clock.show()
        clock.hide()
        fake.now = datetime(2026, 10, 8, 0, 3, 4)

        clock.show()

        assert clock._parts[:3] == ("00", "03", "04") and clock._parts[4].startswith("THURSDAY")
        clock.hide()


class TestPlacement:
    def _scale(self):
        from clUIScaler import UIScaler
        return UIScaler.get().scale

    def test_centred_with_the_time_starting_258_px_down(self, qapp):
        s = self._scale()
        clock, _ = _clock(qapp)

        clock.place(1920, 1080)

        assert clock.geometry().center().x() in (959, 960)
        assert clock.y() + s(cd.GLOW_MARGIN) == s(cd.TOP)

    def test_follows_the_window_width(self, qapp):
        clock, _ = _clock(qapp)
        clock.place(2560, 1440)
        assert clock.geometry().center().x() in (1279, 1280)

    def test_the_content_ends_above_where_the_orb_begins(self, qapp):
        """At 1080p the idle orb's ring starts at y = 460."""
        s = self._scale()
        clock, _ = _clock(qapp)
        clock.place(1920, 1080)

        content_bottom = clock.y() + s(cd.GLOW_MARGIN) + clock.content_height()

        assert content_bottom <= s(460)


class TestPainting:
    """Rendered into an image and inspected, since the clock is painted by hand."""

    def _render(self, clock):
        from PyQt6.QtGui import QImage
        clock.place(1920, 1080)
        image = QImage(clock.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        clock.render(image)
        return image

    def _time_extent(self, clock):
        from PyQt6.QtGui import QFontMetrics
        from clUIScaler import UIScaler
        s = UIScaler.get().scale
        metrics = QFontMetrics(ClockDisplay._font(s(cd.TIME_PX)))
        width = metrics.horizontalAdvance(f"{clock._parts[0]}:{clock._parts[1]}")
        left = (clock.width() - width) / 2
        return left, left + width

    def _differing_columns(self, a, b):
        columns = {x for x in range(a.width()) for y in range(a.height()) if a.pixel(x, y) != b.pixel(x, y)}
        return min(columns), max(columns)

    def test_the_colon_does_not_blink(self, qapp):
        """Two consecutive seconds may differ only where the seconds are drawn,
        i.e. to the right of the hours and minutes -- never in the colon."""
        clock, fake = _clock(qapp, when=(2026, 10, 7, 21, 47, 10))
        first = self._render(clock)
        fake.now = datetime(2026, 10, 7, 21, 47, 11)
        clock._refresh()
        second = self._render(clock)

        _, time_right = self._time_extent(clock)
        low, _ = self._differing_columns(first, second)

        assert low > time_right

    def test_seconds_off_draws_nothing_to_the_right_of_the_time(self, qapp):
        clock, _ = _clock(qapp, seconds=False)
        image = self._render(clock)
        _, time_right = self._time_extent(clock)

        beyond = [(x, y) for x in range(int(time_right) + 40, image.width()) for y in range(0, image.height() // 2) if (image.pixel(x, y) >> 24) > 20]

        assert beyond == []

    def test_seconds_on_draws_to_the_right_of_the_time(self, qapp):
        clock, _ = _clock(qapp, seconds=True)
        image = self._render(clock)
        _, time_right = self._time_extent(clock)

        beyond = [(x, y) for x in range(int(time_right) + 5, int(time_right) + 80) for y in range(image.height() // 2) if (image.pixel(x, y) >> 24) > 60]

        assert beyond

    def test_the_twelve_hour_marker_is_drawn_left_of_the_time(self, qapp):
        clock, _ = _clock(qapp, hour24=False, seconds=False)
        image = self._render(clock)
        time_left, _ = self._time_extent(clock)

        left_of_time = [(x, y) for x in range(max(0, int(time_left) - 90), int(time_left) - 5) for y in range(image.height() // 2) if (image.pixel(x, y) >> 24) > 60]

        assert left_of_time

    def test_it_paints_something_and_stays_inside_its_own_bounds(self, qapp):
        clock, _ = _clock(qapp)
        image = self._render(clock)

        opaque = sum(1 for x in range(image.width()) for y in range(image.height()) if (image.pixel(x, y) >> 24) > 100)

        assert opaque > 500

    def _touched(self, image):
        return sum(1 for x in range(image.width()) for y in range(image.height()) if (image.pixel(x, y) >> 24) > 0)

    def test_the_glow_spreads_a_halo_around_the_digits(self, qapp):
        with_glow = self._touched(self._render(_clock(qapp, glow=1.0)[0]))
        without = self._touched(self._render(_clock(qapp, glow=0)[0]))

        assert with_glow > without * 1.3

    def test_a_stronger_glow_is_brighter_at_the_same_spot(self, qapp):
        from PyQt6.QtGui import QFontMetrics
        from clUIScaler import UIScaler
        faint, strong = _clock(qapp, glow=0.5)[0], _clock(qapp, glow=2.0)[0]
        faint_image, strong_image = self._render(faint), self._render(strong)
        s = UIScaler.get().scale
        # a point a little outside the digits, in the halo
        x = int(self._time_extent(faint)[0]) - s(6)
        y = s(cd.GLOW_MARGIN) + s(cd.TIME_PX) // 2

        assert (strong_image.pixel(x, y) >> 24) > (faint_image.pixel(x, y) >> 24)

    @pytest.mark.parametrize("settings", [{}, {"glow": 0}, {"seconds": False}, {"hour24": False}, {"language": "pt"}, {"hour24": False, "seconds": False, "language": "pt"}])
    def test_every_setting_combination_paints_without_error(self, qapp, settings):
        clock, _ = _clock(qapp, **settings)
        self._render(clock)


class TestInTheDashboard:
    """The clock is a child of the main window, stacked right above the visualizer."""

    @pytest.fixture
    def ui(self, qapp, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")
        mocker.patch.object(clUI, "STATE_FILE", os.path.join(tempfile.mkdtemp(), "ui_state.json"))
        window = clUI.JarvisUI()
        window.setMinimumSize(0, 0)
        window.setMaximumSize(16777215, 16777215)
        window.resize(1920, 1080)
        return window

    def test_it_exists_and_starts_hidden(self, ui):
        assert isinstance(ui.clock_display, ClockDisplay)
        assert ui.clock_display.isHidden()

    def test_it_is_stacked_above_the_visualizer_and_below_later_widgets(self, ui):
        siblings = [c for c in ui.children() if c in (ui.visualizer, ui.clock_display, ui.lyrics_display, ui.text_input)]
        assert siblings.index(ui.visualizer) < siblings.index(ui.clock_display) < siblings.index(ui.lyrics_display)

    def test_the_layout_pass_places_it(self, ui):
        from clUIScaler import UIScaler
        s = UIScaler.get().scale
        ui.refresh_layout()

        assert ui.clock_display.geometry().center().x() in (ui.width() // 2 - 1, ui.width() // 2)
        assert ui.clock_display.y() + s(cd.GLOW_MARGIN) == s(cd.TOP)

    def test_the_overlay_square_has_no_clock(self, ui):
        ui.clock_display.show()
        assert not ui.clock_display.isHidden()

        ui.set_ui_mode("set_overlay")

        assert ui.clock_display.isHidden()

    def test_settings_come_from_core_json(self, qapp, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")
        mocker.patch.object(clUI, "STATE_FILE", os.path.join(tempfile.mkdtemp(), "ui_state.json"))
        mocker.patch.object(clUI.ConfigLoader, "load_json", return_value={"settings": {"clock_settings": {"seconds": False, "language": "pt"}}})

        window = clUI.JarvisUI()

        assert window.clock_display.settings["seconds"] is False
        assert window.clock_display.settings["language"] == "pt"

    def test_a_broken_core_json_still_gives_a_working_clock(self, qapp, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")
        mocker.patch.object(clUI, "STATE_FILE", os.path.join(tempfile.mkdtemp(), "ui_state.json"))
        mocker.patch.object(clUI.ConfigLoader, "load_json", side_effect=ValueError("corrupt"))

        window = clUI.JarvisUI()

        assert window.clock_display.settings == load_settings(None)


class TestGaussianBlur:
    def _impulse(self, size=101):
        import numpy as np
        grid = np.zeros((size, size))
        grid[size // 2, size // 2] = 1.0
        return grid

    def test_a_point_spreads_symmetrically_around_where_it_was(self):
        import numpy as np
        blurred = cd.gaussian_blur(self._impulse(), 5)
        centre = blurred.shape[0] // 2

        assert blurred[centre, centre] == blurred.max()
        assert np.allclose(blurred, blurred[::-1, ::-1])
        assert np.allclose(blurred, blurred.T)

    def test_the_total_weight_is_kept_when_it_stays_inside(self):
        assert cd.gaussian_blur(self._impulse(), 5).sum() == pytest.approx(1.0, abs=1e-6)

    def test_it_falls_off_smoothly_without_steps(self):
        blurred = cd.gaussian_blur(self._impulse(), 6)
        row = blurred[blurred.shape[0] // 2, blurred.shape[1] // 2:]

        assert all(a >= b for a, b in zip(row, row[1:]))  # monotonic
        assert max(a - b for a, b in zip(row, row[1:])) < row[0] * 0.2  # no cliff

    def test_a_wider_sigma_is_flatter(self):
        narrow = cd.gaussian_blur(self._impulse(), 3).max()
        wide = cd.gaussian_blur(self._impulse(), 9).max()
        assert wide < narrow

    def test_sigma_zero_changes_nothing(self):
        import numpy as np
        grid = self._impulse()
        assert np.array_equal(cd.gaussian_blur(grid, 0), grid)


class TestGlowIsSmooth:
    """The halo used to be the digits repeated at a few radii, which showed as
    visible steps of opacity. It is now a real gaussian."""

    def _render(self, clock):
        from PyQt6.QtGui import QImage
        clock.place(1920, 1080)
        image = QImage(clock.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        clock.render(image)
        return image

    def test_opacity_rises_gradually_towards_the_digits(self, qapp):
        from PyQt6.QtGui import QFontMetrics
        from clUIScaler import UIScaler
        clock, _ = _clock(qapp, seconds=False)
        image = self._render(clock)
        s = UIScaler.get().scale
        metrics = QFontMetrics(ClockDisplay._font(s(cd.TIME_PX)))
        first_digit_centre = int((clock.width() - metrics.horizontalAdvance("21:47")) / 2 + metrics.horizontalAdvance("2") / 2)

        column = [(image.pixel(first_digit_centre, y) >> 24) for y in range(image.height())]
        ink_top = next(y for y, alpha in enumerate(column) if alpha > 200)
        halo = column[:ink_top - 4]  # the last few pixels are the digit's own antialiased edge

        assert max(halo) > 5  # there is a halo
        rises = [b - a for a, b in zip(halo, halo[1:])]
        assert all(step >= 0 for step in rises)  # never dips on the way in
        assert max(rises) <= 3  # and never jumps in a step

    def test_the_halo_is_computed_once_per_minute_not_every_second(self, qapp, mocker):
        clock, fake = _clock(qapp, when=(2026, 10, 7, 21, 47, 10))
        self._render(clock)
        first = clock._glow_cache
        fake.now = datetime(2026, 10, 7, 21, 47, 11)
        clock._refresh()
        self._render(clock)

        assert clock._glow_cache is first  # same object: nothing recomputed

        fake.now = datetime(2026, 10, 7, 21, 48, 0)
        clock._refresh()
        self._render(clock)

        assert clock._glow_cache is not first

    def test_changing_the_strength_recomputes_it(self, qapp):
        clock, _ = _clock(qapp)
        self._render(clock)
        first = clock._glow_cache

        clock.settings["glow"] = 2.0
        self._render(clock)

        assert clock._glow_cache is not first

    def test_the_halo_fits_inside_the_widget(self, qapp):
        """A halo clipped by the widget's edge would show as a hard line."""
        from clUIScaler import UIScaler
        s = UIScaler.get().scale
        clock, _ = _clock(qapp)
        image = self._render(clock)

        top_row = [(image.pixel(x, 0) >> 24) for x in range(image.width())]

        assert max(top_row) <= 2
        assert s(cd.GLOW_MARGIN) >= 3 * s(max(sigma for sigma, _, _ in cd.GLOW_LAYERS)) * 0.8
