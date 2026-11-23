"""Every widget's minimum size is fit to its content (web/js/ui.js `fitMin`), and the Spotify widget's seek row steps aside for the volume fader.
The measurement itself needs a real layout engine; it was checked by rendering all nine widgets at their measured minimums in the desktop shell
(JARVIS_SHELL_TEST, see docs/web_ui_plan.md). These tests keep the rules that make that measurement meaningful from being undone."""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


UI = read("web", "js", "ui.js")
CSS = read("web", "style.css")


class TestFitToContent:
    def test_no_widget_carries_a_guessed_fixed_minimum_any_more(self):
        for path in glob.glob(os.path.join(ROOT, "web", "js", "*.js")):
            src = open(path, encoding="utf-8").read()
            assert not re.search(r"\bminW\s*:|\bminH\s*:", src), os.path.basename(path)

    def test_the_minimum_is_measured_when_a_window_opens_again_after_its_data_arrives_and_when_a_resize_starts(self):
        assert "requestAnimationFrame(() => applyMin(el))" in UI
        assert "setTimeout(() => applyMin(el), 1500)" in UI
        assert re.search(r'pointerdown", \(e\) => \{\s*applyMin\(el\);', UI)             # the resize grip

    def test_the_scrolling_regions_every_widget_has_are_all_declared_elastic(self):
        # a scrolling region in a widget must be in ELASTIC, or its whole content would count as the minimum (a log is thousands of lines tall)
        elastic = re.search(r"const ELASTIC = \[(.*?)\];", UI, re.S).group(1)
        for cls in (".mod-list", ".rem-grid", ".set-pages", ".tabs-pill", ".t-screen", ".cl-screen"):
            assert cls in elastic, cls

    def test_the_measurement_puts_the_dom_back(self):
        assert "saved[i][0].style.cssText = saved[i][1]" in UI

    def test_titles_headings_and_buttons_never_wrap_so_the_minimum_includes_their_whole_text(self):
        for sel in (r"\.win-title", r"\.mod-title", r"\.btn"):
            rule = re.search(sel + r" \{[^}]*\}", CSS).group(0)
            assert "white-space: nowrap" in rule, sel


class TestHoverOverlaysCountTowardsTheMinimum:
    def test_the_volume_bar_reserves_its_own_footprint_while_measuring(self):
        # drawn absolutely it takes no room by itself: at the old minimum (214 px) it sat 12 px INTO the repeat button's column
        assert re.search(r'const OVERLAYS = \["\.fader"\];', UI)
        assert "reserve = o.offsetWidth + edge + OVERLAY_GAP * u - padRight" in UI

    def test_the_reservation_comes_from_the_overlay_itself_not_from_a_padding_added_to_the_widget(self):
        # measuring only: nothing in the stylesheet pads the media widget's top row for the fader
        assert not re.search(r"\.m-top[^{]*\{[^}]*padding-right", CSS)
        assert "touch(host, { paddingRight:" in UI                         # applied to the live DOM during the measurement and restored with it


class TestSeekRowStepsAsideForTheFader:
    def test_while_the_fader_shows_the_seek_row_leaves_room_for_it(self):
        # the fader is drawn over the right edge (8 + 28 px): without this the mute button sits on the song's total time
        assert re.search(r"\.media:hover \.m-seek \{ padding-right: calc\(26 \* var\(--u\)\); \}", CSS)

    def test_the_shift_is_animated_like_the_fader_itself(self):
        rule = re.search(r"\.m-seek \{[^}]*\}", CSS).group(0)
        assert "transition: padding-right .2s" in rule
