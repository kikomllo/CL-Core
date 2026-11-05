import os
import random
import sys

import numpy as np
import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC)


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="module")
def head(qapp):
    from ui.clUltronHead import UltronHead
    return UltronHead()


def paint_max(head, level, glitch=None, flicker=1.0, cell=5):
    from PyQt6.QtGui import QImage, QPainter, QColor
    img = QImage(head.w * cell + 120, head.h * cell + 120, QImage.Format.Format_RGB888)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    head.paint(p, img.width() / 2, img.height() / 2, cell, level, 1.0, glitch, flicker)
    p.end()
    b = img.constBits()
    b.setsize(img.sizeInBytes())
    return np.frombuffer(b, np.uint8).reshape(img.height(), img.bytesPerLine())[:, : img.width() * 3].max()


def lit_map(head, level, t=1.0, cell=6, speaking=True):
    """Which gap cells / cheek cells are drawn how, read from the painted image (cell centres)."""
    from PyQt6.QtGui import QImage, QPainter, QColor
    w, h = head.w * cell + 120, head.h * cell + 120
    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    head.show_halo = False
    try:
        head.paint(p, w / 2, h / 2, cell, level, t, None, 1.0, speaking)
    finally:
        head.show_halo = True
    p.end()
    b = img.constBits()
    b.setsize(img.sizeInBytes())
    arr = np.frombuffer(b, np.uint8).reshape(h, img.bytesPerLine())[:, : w * 3].reshape(h, w, 3).copy()    # img is freed on return
    x0, y0 = (w - head.w * cell) / 2, (h - head.h * cell) / 2
    return lambda j, i: int(arr[int(y0 + (j + 0.5) * cell), int(x0 + (i + 0.5) * cell)].max())


class TestTheMouthStages:
    """A normal talking animation: a small closed mouth at idle; the moment he speaks, even quietly, the normal mouth; louder
    voices make the lit mouth taller and brighter. (Lit = open: a big lit mouth at rest that shrank when he talked was the bug.)"""

    def lit_cells(self, head, amount):
        return {(j, i) for j, i, _ in head.mouth if head.mouth_lit(head.mouth_open[(j, i)], amount) > 0.5}

    def test_the_amount_is_zero_unless_he_is_speaking_and_at_least_the_floor_when_he_is(self, head):
        from ui.clUltronHead import SPEAK_FLOOR
        assert all(head.mouth_amount(lv / 10, False) == 0.0 for lv in range(11))
        assert head.mouth_amount(0.0, True) == SPEAK_FLOOR
        vals = [head.mouth_amount(lv / 20, True) for lv in range(21)]
        assert all(a <= b for a, b in zip(vals, vals[1:])) and vals[-1] == pytest.approx(1.0)

    def test_idle_is_a_small_closed_mouth_in_the_middle_not_a_slit_and_not_the_full_shape(self, head):
        idle = self.lit_cells(head, 0.0)
        assert 0.08 * len(head.mouth) < len(idle) < 0.4 * len(head.mouth)
        rows = [j for j, _ in idle]
        assert max(rows) - min(rows) >= 3                                       # more than a one or two row line
        cx = [i for _, i in idle]
        assert abs(sum(cx) / len(cx) - head.mouth_centre) < 1.5                  # centred

    def test_the_moment_he_speaks_even_quietly_the_whole_normal_mouth_is_lit(self, head):
        from ui.clUltronHead import SPEAK_FLOOR
        assert len(self.lit_cells(head, SPEAK_FLOOR)) == len(head.mouth)
        assert len(self.lit_cells(head, head.mouth_amount(0.0, True))) == len(head.mouth)

    def test_the_lit_mouth_only_ever_grows_as_it_opens(self, head):
        previous = set()
        for lv in range(0, 21):
            now = self.lit_cells(head, lv / 20)
            assert previous <= now
            previous = now

    def test_the_extra_rows_make_the_loud_mouth_taller_below_the_grille_deepest_in_the_middle(self, head):
        from ui.clUltronHead import SPEAK_FLOOR
        assert head.mouth_ext
        plate_bottom = {}
        for j, i, _ in head.mouth:
            plate_bottom[i] = max(plate_bottom.get(i, -1), j)
        assert all(j > plate_bottom[i] and head.metal[j, i] and not head._rim[j, i] for j, i, _ in head.mouth_ext)
        assert all(ext_at > SPEAK_FLOOR for _, _, ext_at in head.mouth_ext)
        depth = {}
        for j, i, _ in head.mouth_ext:
            depth[i] = depth.get(i, 0) + 1
        mid = round(head.mouth_centre)
        assert depth.get(mid, 0) >= max(depth.values()) and depth.get(mid, 0) > depth.get(min(depth), 0)

    def test_the_extra_rows_light_one_after_another_and_all_at_full_voice(self, head):
        from ui.clUltronHead import SPEAK_FLOOR
        lit = lambda amount: sum(1 for _, _, e in head.mouth_ext if head.ext_lit(e, amount) > 0.5)
        assert lit(SPEAK_FLOOR) == 0 and lit(1.0) == len(head.mouth_ext)
        assert lit(0.45) < lit(0.75) < lit(1.0) or lit(0.45) < lit(1.0)

    def test_painted_idle_is_small_normal_when_speaking_and_biggest_when_loud(self, head):
        def lit_pixels(level, speaking):
            m = lit_map(head, level, speaking=speaking)
            cells = [(j, i) for j, i, _ in head.mouth] + [(j, i) for j, i, _ in head.mouth_ext]
            return sum(1 for j, i in cells if m(j, i) > 90)
        idle, quiet, loud = lit_pixels(0.0, False), lit_pixels(0.0, True), lit_pixels(1.0, True)
        assert idle < quiet < loud and quiet > 1.8 * idle

    def test_nothing_slides_cells_light_where_they_are(self, head):
        m = lit_map(head, 1.0)
        assert all(m(j, i) > 90 for j, i, _ in head.mouth[::5])


class TestTheCheeksFollowTheMouth:
    def test_nothing_moves_when_nobody_is_talking(self, head):
        for _, _, u, weight, _, _ in head.cheek_cells[::7]:
            assert head.cheek_shift(u, weight, 0.0) == 0.0

    def test_the_outline_never_moves_and_never_changes_brightness(self, head):
        outline = [c for c in head.cheek_cells if c[4]]
        assert len(outline) > 30
        assert all(c[3] == 0.0 for c in outline)                        # zero movement weight
        assert all(head.cheek_shift(c[2], c[3], 1.0) == 0.0 for c in outline)
        quiet, loud = lit_map(head, 0.0, speaking=False), lit_map(head, 1.0)
        assert all(abs(quiet(c[0], c[1]) - loud(c[0], c[1])) <= 6 for c in outline[::4])

    def test_the_inside_of_the_lower_face_swells_outwards_a_little_and_joins_at_its_edges(self, head):
        from ui.clUltronHead import CHEEK_BULGE
        c0, c1 = head.cheek_rows
        moving = [c for c in head.cheek_cells if c[3] > 0]
        assert len(moving) > 100
        for j, i, u, weight, _, _ in head.cheek_cells:
            shift = head.cheek_shift(u, weight, 1.0)
            assert shift * u >= 0 and abs(shift) <= CHEEK_BULGE + 1e-9
            if j in (c0, c1):
                assert abs(shift) < 0.02
        centre = [c for c in head.cheek_cells if abs(c[2]) < 0.05]
        assert all(abs(head.cheek_shift(c[2], c[3], 1.0)) < 0.03 for c in centre)

    def test_it_moves_more_the_louder_he_talks_and_towards_the_sides(self, head):
        c = max((c for c in head.cheek_cells if c[3] > 0), key=lambda c: abs(c[2]) * c[3])
        vals = [abs(head.cheek_shift(c[2], c[3], lv / 10)) for lv in range(11)]
        assert all(a <= b for a, b in zip(vals, vals[1:])) and vals[-1] > 0.2

    def test_both_cheeks_exist_and_match_in_size(self, head):
        left = sum(1 for c in head.cheek_cells if c[2] < 0)
        right = sum(1 for c in head.cheek_cells if c[2] > 0)
        assert left > 60 and right > 60 and 0.7 < left / right < 1.4

    def test_the_cheek_plates_beside_the_mouth_are_separate_from_the_mouth_on_both_sides(self, head):
        assert len(head.cheek_plates) >= 4
        assert {i < head.mouth_centre for _, i in head.cheek_plates} == {True, False}
        mouth = {(j, i) for j, i, _ in head.mouth}
        assert not (set(head.cheek_plates) & mouth)
        assert all(abs(i - head.mouth_centre) > head.mouth_halfwidth * 0.9 for _, i in head.cheek_plates)

    def test_the_cheek_plates_are_lit_at_idle_and_in_every_mouth_state(self, head):
        for speaking, level in ((False, 0.0), (True, 0.0), (True, 0.6), (True, 1.0)):
            m = lit_map(head, level, speaking=speaking)
            assert all(m(j, i) > 60 for j, i in head.cheek_plates), (speaking, level)

    def test_the_cheek_lights_rest_lit_and_brighten_with_the_voice(self, head):
        vals = [head.cheek_light(lv / 10, 1.0) for lv in range(11)]
        assert 0.35 < vals[0] < 0.7 and vals[-1] == pytest.approx(1.0, abs=0.001) and all(a <= b for a, b in zip(vals, vals[1:]))

    def test_the_cheek_metal_is_drawn_live_not_baked_into_the_static_layer(self, head):
        cheek = {(c[0], c[1]) for c in head.cheek_cells}
        baked = {(int(j), int(i)) for j, i in zip(*np.where(head.metal & ~head.cheek_mask))}
        assert cheek and not (cheek & baked)


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def simulate(cfg, seconds, state="IDLE", fps=15, seed=1):
    """Run Glitch over simulated time; returns (events, frames), events = [(start second, kind)]."""
    from ui.clUltronHead import Glitch
    clock = FakeClock()
    g = Glitch(cfg, random.Random(seed), clock)
    events, frames, active = [], [], False
    for n in range(int(seconds * fps)):
        clock.t = 100.0 + n / fps
        rows, flick = g.update(state, 58)
        frames.append((rows, flick))
        if rows is not None and not active:
            events.append((round(clock.t - 100.0, 2), "hard" if g.hard else "soft"))
        active = rows is not None
    return events, frames


class TestGlitchTiming:
    def test_it_glitches_at_rest_between_the_configured_gaps(self):
        events, _ = simulate({"hard_every_s": [9999, 9999], "idle_every_s": [4.0, 6.0]}, 120)
        assert len(events) >= 12
        gaps = [b[0] - a[0] for a, b in zip(events, events[1:])]
        assert all(g >= 4.0 for g in gaps) and all(g <= 6.0 + 0.6 for g in gaps)   # the gap plus the glitch's own length

    def test_nothing_happens_before_the_first_gap(self):
        _, frames = simulate({"hard_every_s": [9999, 9999], "idle_every_s": [5.0, 6.0]}, 4.5)
        assert all(rows is None and flick == 1.0 for rows, flick in frames)

    def test_a_soft_glitch_is_short_and_its_shifts_are_bounded(self):
        cfg = {"hard_every_s": [9999, 9999], "idle_every_s": [3.0, 3.0], "soft_ms": [200, 200], "soft_shift": 3}
        events, frames = simulate(cfg, 30)
        assert events and all(kind == "soft" for _, kind in events)
        longest, run = 0, 0
        for rows, flick in frames:
            run = run + 1 if rows is not None else 0
            longest = max(longest, run)
            if rows is not None:
                assert max(abs(r) for r in rows) <= 3 and 0.85 <= flick <= 1.0
        assert 2 <= longest <= 5                         # ~200 ms at 15 fps

    def test_the_rare_hard_flicker_is_longer_rougher_and_stutters_the_brightness(self):
        cfg = {"idle_every_s": [9999, 9999], "hard_every_s": [5.0, 5.0], "hard_ms": [1000, 1000], "hard_shift": 9}
        events, frames = simulate(cfg, 60)
        assert events and all(kind == "hard" for _, kind in events)
        shifts = [max(abs(r) for r in rows) for rows, _ in frames if rows is not None]
        flickers = [f for rows, f in frames if rows is not None]
        assert 3 < max(shifts) <= 9
        assert min(flickers) < 0.8 and all(0.1 <= f <= 1.0 for f in flickers)
        assert sum(1 for rows, _ in frames if rows is not None) >= 10 * len(events) * 0.8     # ~1 s per flicker at 15 fps

    def test_a_hard_flicker_comes_at_a_random_moment_not_a_fixed_one(self):
        gaps = []
        for seed in range(6):
            events, _ = simulate({"idle_every_s": [9999, 9999], "hard_every_s": [10.0, 40.0], "hard_ms": [300, 300]}, 300, seed=seed)
            gaps += [round(b[0] - a[0]) for a, b in zip(events, events[1:])]
        assert len(set(gaps)) > 8 and min(gaps) >= 10 and max(gaps) <= 41

    def test_with_the_defaults_both_kinds_happen_over_a_few_minutes(self):
        events, _ = simulate({}, 600)
        kinds = [k for _, k in events]
        assert kinds.count("soft") > 20 and kinds.count("hard") >= 3

    def test_it_can_be_switched_off(self):
        events, frames = simulate({"enabled": False, "idle_every_s": [0.5, 0.5], "hard_every_s": [1, 1]}, 30)
        assert not events and all(f == (None, 1.0) for f in frames)

    def test_processing_glitches_far_more_often_than_speaking(self):
        busy, _ = simulate({"hard_every_s": [9999, 9999]}, 200, state="PROCESSING")
        talk, _ = simulate({"hard_every_s": [9999, 9999]}, 200, state="SPEAKING")
        assert len(busy) > 3 * len(talk) and len(talk) >= 5

    def test_a_busy_state_does_not_wait_out_a_long_idle_gap(self):
        from ui.clUltronHead import Glitch
        clock = FakeClock()
        g = Glitch({"hard_every_s": [9999, 9999], "idle_every_s": [60.0, 60.0], "processing_every_s": [1.0, 2.0]}, random.Random(2), clock)
        g.update("IDLE", 58)
        clock.t += 1.0
        started = None
        for n in range(150):
            clock.t += 0.05
            rows, _ = g.update("PROCESSING", 58)
            if rows is not None:
                started = n * 0.05
                break
        assert started is not None and started < 3.0

    def test_the_hard_glitch_has_more_and_bigger_bands_than_the_soft_one(self):
        from ui.clUltronHead import new_glitch
        soft = [sum(1 for r in new_glitch(58, random.Random(s)) if r) for s in range(40)]
        hard = [sum(1 for r in new_glitch(58, random.Random(s), hard=True) if r) for s in range(40)]
        assert sum(hard) > 2 * sum(soft)
        assert max(max(abs(r) for r in new_glitch(58, random.Random(s), hard=True)) for s in range(40)) > 4

    def test_a_flicker_value_dims_the_whole_head(self, head):
        assert paint_max(head, 0.0, flicker=0.3) < paint_max(head, 0.0, flicker=1.0) * 0.6
