"""Lyrics settings and layout maths (web/js/lyrics-model.js under node): how many lines before / after, the font factor, and the shift that keeps
the padding below the column the default layout had."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "web", "js", "lyrics-model.js").replace("\\", "/")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def call(fn, *args):
    script = f'import * as M from "file:///{MODEL}";\nconsole.log(JSON.stringify(M.{fn}(...{json.dumps(list(args))})))'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestClamp:
    @pytest.mark.parametrize("n, expected", [(1, 1), (0, 0), (5, 5), (9, 5), (-3, 0), (2.6, 3), ("3", 3), (None, 1), ("x", 1), ("", 1)])
    def test_count(self, n, expected):
        assert call("clampCount", n) == expected

    @pytest.mark.parametrize("z, expected", [(1, 1), (0.1, 0.8), (5, 1.6), (1.234, 1.23), ("1.2", 1.2), (None, 1), ("x", 1), (0, 1), (-1, 1)])
    def test_size(self, z, expected):
        assert call("clampSize", z) == expected


class TestParse:
    def test_defaults_are_the_old_layout(self):
        assert call("parseSettings", None) == {"before": 1, "after": 1, "size": 1}
        assert call("parseSettings", "junk") == {"before": 1, "after": 1, "size": 1}

    def test_each_field_falls_back_alone(self):
        assert call("parseSettings", {"before": 3, "after": "x", "size": 9}) == {"before": 3, "after": 1, "size": 1.6}

    def test_zero_lines_is_allowed(self):
        assert call("parseSettings", {"before": 0, "after": 0}) == {"before": 0, "after": 0, "size": 1}


def row_px(d):
    return 26 if d <= 0 else max(8, 14 * 0.88 ** (d - 1))


def gap_at(d, size=1):
    return 0.6 * row_px(d) * size


def reach(before, size):
    return 26 * size * 1.15 / 2 + sum(gap_at(d, size) + row_px(d) * size * 1.15 for d in range(1, before + 1))


REF = 26 * 1.15 / 2 + 4 + 14 * 1.15


class TestGapAndFade:
    def test_the_gap_follows_the_font_size_of_its_row(self):
        assert call("gapAt", 1) == pytest.approx(8.4) and call("gapAt", 1, 1.5) == pytest.approx(12.6)
        assert call("gapAt", 4) < call("gapAt", 2) < call("gapAt", 1)

    def test_rows_fade_with_distance_but_stay_readable(self):
        o = [call("opacityAt", d) for d in range(0, 7)]
        assert o[0] == o[1] == 1 and o[2] == pytest.approx(0.8) and o == sorted(o, reverse=True)
        assert min(o) == 0.35


class TestRowSize:
    def test_current_and_first_neighbour_keep_the_old_sizes(self):
        assert call("rowPx", 0) == 26 and call("rowPx", 1) == 14

    def test_it_shrinks_gradually_with_distance(self):
        sizes = [call("rowPx", d) for d in range(1, 6)]
        assert sizes == sorted(sizes, reverse=True) and len(set(sizes)) == 5
        assert sizes == pytest.approx([row_px(d) for d in range(1, 6)])

    def test_it_never_gets_unreadable(self):
        assert call("rowPx", 40) == 8


class TestBottomShift:
    def test_default_layout_keeps_its_padding_below_despite_the_wider_gap(self):
        """The gap grew from 4 to 8.4, which would push the default column 4.4 px down: the shift undoes it."""
        assert call("bottomShift", {"before": 1, "size": 1}) == pytest.approx(-4.4, abs=0.01)

    @pytest.mark.parametrize("before, size", [(0, 1), (2, 1), (4, 1), (5, 1), (1, 1.5), (3, 0.8), (5, 1.6)])
    def test_the_column_bottom_never_moves(self, before, size):
        """Whatever the settings, the bottom edge (half the current line + the previous rows) lands where the default layout had it."""
        shift = call("bottomShift", {"before": before, "size": size})
        assert reach(before, size) + shift == pytest.approx(REF, abs=0.01)

    def test_extra_previous_lines_raise_the_column(self):
        assert call("bottomShift", {"before": 3, "size": 1}) < call("bottomShift", {"before": 2, "size": 1}) < call("bottomShift", {"before": 1, "size": 1})

    def test_no_previous_lines_lowers_it(self):
        assert call("bottomShift", {"before": 0, "size": 1}) > 0

    def test_a_bigger_font_raises_it(self):
        assert call("bottomShift", {"before": 1, "size": 1.5}) < call("bottomShift", {"before": 1, "size": 0.8})

    def test_next_lines_do_not_matter(self):
        """The upcoming lines stretch the top only: the shift is a function of the previous lines and the font."""
        assert call("bottomShift", {"before": 1, "size": 1, "after": 5}) == call("bottomShift", {"before": 1, "size": 1})


class TestPlan:
    """The column goes in the gap between the visualizer (visTop) and the highest pill (maxBottom), both in design px from the centre line."""
    S = {"before": 1, "after": 1, "size": 1}

    def test_roomy_gap_keeps_the_default_place(self):
        p = call("plan", self.S, {"visTop": -200, "maxBottom": 100})
        assert p["fit"] == 1 and p["bottom"] == pytest.approx(REF, abs=0.01) and (p["before"], p["after"]) == (1, 1)

    def test_pills_closer_than_the_default_pull_it_up(self):
        assert call("plan", self.S, {"visTop": -200, "maxBottom": 20})["bottom"] == 20

    def test_a_tall_column_is_lowered_towards_the_pills_before_anything_shrinks(self):
        big = {"before": 4, "after": 4, "size": 1}
        need = call("nominalHeight", big)
        p = call("plan", big, {"visTop": -(need - REF - 10), "maxBottom": 80})
        assert p["fit"] == 1 and REF < p["bottom"] <= 80 and p["bottom"] == pytest.approx(REF + 10, abs=0.01)

    def test_the_bottom_never_goes_past_the_pill_padding(self):
        for h in (60, 120, 200, 400):
            p = call("plan", {"before": 5, "after": 5, "size": 1.6}, {"visTop": -h, "maxBottom": 50})
            assert p["bottom"] <= 50 + 1e-9

    def test_too_small_a_gap_shrinks_then_drops_rows_but_keeps_context(self):
        s = {"before": 5, "after": 5, "size": 1}
        p = call("plan", s, {"visTop": -100, "maxBottom": 60})
        assert p["fit"] <= 0.75 and 1 <= p["before"] < 5 and 1 <= p["after"] < 5
        assert call("nominalHeight", {**p, "size": p["fit"]}) <= 160 + 1e-6

    def test_the_font_shrinks_below_75_percent_before_the_last_context_row_goes(self):
        """At 160 % in a short window one row each side still fits once the font is shrunk further, so they stay."""
        p = call("plan", {"before": 4, "after": 4, "size": 1.6}, {"visTop": -20, "maxBottom": 62})
        assert (p["before"], p["after"]) == (1, 1) and p["fit"] < 0.75

    def test_a_moderately_small_gap_only_shrinks(self):
        s = {"before": 3, "after": 3, "size": 1}
        need = call("nominalHeight", s)
        p = call("plan", s, {"visTop": -(need * 0.9 - 40), "maxBottom": 40})
        assert 0.75 < p["fit"] < 1 and (p["before"], p["after"]) == (3, 3)

    def test_it_degrades_to_the_current_line_alone(self):
        p = call("plan", {"before": 5, "after": 5, "size": 1}, {"visTop": 0, "maxBottom": 10})
        assert (p["before"], p["after"]) == (0, 0) and p["fit"] == 0.45
