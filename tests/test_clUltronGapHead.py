import os
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
    from ui.clUltronHead import load_head
    return load_head(kind="gap")


def lit_map(head, level, t=1.0, cell=6):
    """The brightness at the centre of every grid cell of the painted head (max channel)."""
    from PyQt6.QtGui import QImage, QPainter, QColor
    w, h = head.w * cell + 120, head.h * cell + 120
    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    head.show_halo = False
    try:
        head.paint(p, w / 2, h / 2, cell, level, t, None, 1.0)
    finally:
        head.show_halo = True
    p.end()
    b = img.constBits()
    b.setsize(img.sizeInBytes())
    arr = np.frombuffer(b, np.uint8).reshape(h, img.bytesPerLine())[:, : w * 3].reshape(h, w, 3).copy()    # img is freed on return
    x0, y0 = (w - head.w * cell) / 2, (h - head.h * cell) / 2
    return lambda j, i: int(arr[int(y0 + (j + 0.5) * cell), int(x0 + (i + 0.5) * cell)].max())


class TestWhichHead:
    def test_the_gap_head_is_the_default_setting(self, monkeypatch):
        from ui.clUltronHead import head_kind
        monkeypatch.delenv("JARVIS_ULTRON_HEAD", raising=False)
        assert head_kind({}) == "gap" and head_kind(None) == "gap"

    def test_the_setting_picks_the_head_and_an_unknown_value_falls_back(self, monkeypatch):
        from ui.clUltronHead import head_kind
        monkeypatch.delenv("JARVIS_ULTRON_HEAD", raising=False)
        assert head_kind({"ultron_head": "sprite"}) == "sprite" and head_kind({"ultron_head": "stages"}) == "stages"
        assert head_kind({"ultron_head": "nonsense"}) == "gap"

    def test_an_environment_variable_overrides_the_setting(self, monkeypatch):
        from ui.clUltronHead import head_kind
        monkeypatch.setenv("JARVIS_ULTRON_HEAD", "sprite")
        assert head_kind({"ultron_head": "gap"}) == "sprite"

    def test_each_kind_loads_its_own_head(self, qapp):
        from ui.clUltronHead import load_head, SpriteHead, UltronHead
        from ui.clUltronHeadGap import GapHead
        assert type(load_head(kind="gap")) is GapHead
        assert type(load_head(kind="stages")) is UltronHead
        assert type(load_head(kind="sprite")) is SpriteHead

    def test_the_template_ships_the_gap_head(self):
        import json
        cfg = json.load(open(os.path.join(SRC, "..", "config", "core.default.json"), encoding="utf-8"))
        assert cfg["settings"]["ultron_head"] == "gap"


class TestTheCentreGap:
    def test_the_gap_and_its_bar_exist_around_the_middle_of_the_mouth(self, head):
        assert len(head.gap_cols) >= 6 and head.gap_edges
        cols = [g[0] for g in head.gap_cols]
        assert min(cols) < head.mouth_centre < max(cols)

    def test_the_bar_climbs_upwards_as_the_voice_rises_highest_in_the_middle(self, head):
        mid = min(head.gap_cols, key=lambda g: abs(g[0] - head.mouth_centre))
        side = max(head.gap_cols, key=lambda g: abs(g[0] - head.mouth_centre))
        edges = [head.gap_edge(mid[1], mid[2], lv / 10) for lv in range(11)]
        assert all(a > b for a, b in zip(edges, edges[1:]))
        assert (mid[1] - head.gap_edge(mid[1], mid[2], 1.0)) > (side[1] - head.gap_edge(side[1], side[2], 1.0)) > 0

    def test_at_rest_the_gap_is_as_drawn_and_the_bar_never_climbs_out_of_the_notch(self, head):
        for i, top, taper, cells in head.gap_cols:
            assert head.gap_edge(top, taper, 0.0) == top
            notch_top = min(j for j, plate in cells)
            assert head.gap_edge(top, taper, 1.0) >= notch_top - 0.5        # it stays inside the cells that are drawn for it

    def test_a_cell_is_dark_above_the_edge_and_lit_below_it_with_no_fade(self, head):
        assert head.gap_lit(10, 20.4) == 0.0 and head.gap_lit(30, 20.4) == 1.0
        assert head.gap_lit(20, 20.4) == 1.0 and head.gap_lit(19, 20.4) == 0.0          # the row whose middle is past the edge
        for lv in range(21):                                                              # pixel art: only ever 0 or 1
            assert head.gap_side_lit(2, lv / 20) in (0.0, 1.0)
            assert head.lower_lit(48, head.lower_edge(45, 1.0, lv / 20)) in (0.0, 1.0)

    def test_the_bottom_lip_drops_as_the_voice_rises_the_reverse_of_the_top(self, head):
        assert head.lower_cols
        mid = min(head.lower_cols, key=lambda g: abs(g[0] - head.mouth_centre))
        side = max(head.lower_cols, key=lambda g: abs(g[0] - head.mouth_centre))
        edges = [head.lower_edge(mid[1], mid[2], lv / 10) for lv in range(11)]
        assert edges[0] == mid[1] + 1 and all(a < b for a, b in zip(edges, edges[1:]))
        assert (head.lower_edge(mid[1], mid[2], 1.0) - mid[1] - 1) > (head.lower_edge(side[1], side[2], 1.0) - side[1] - 1) > 0
        for i, bottom, taper, rows in head.lower_cols:
            assert all(head.lower_lit(j, head.lower_edge(bottom, taper, 0.0)) == 0.0 for j in rows)      # dark at rest

    def test_painted_the_bottom_lip_lights_cells_below_the_grille_when_he_talks(self, head):
        quiet, loud = lit_map(head, 0.0), lit_map(head, 1.0)
        mid = min(head.lower_cols, key=lambda g: abs(g[0] - head.mouth_centre))
        i, bottom, _, rows = mid
        assert sum(loud(j, i) for j in rows) > sum(quiet(j, i) for j in rows) + 100
        outline = [(j, i) for j, i in zip(*np.where(head._rim))]
        assert not (set(map(tuple, outline)) & head.lower_cells)                          # the outline is never part of the lip

    def test_the_gap_widens_sideways_with_the_voice(self, head):
        vals = [head.gap_side_lit(0, lv / 10) for lv in range(11)]
        assert vals[0] == 1.0 and vals[-1] < 0.5 and all(a >= b for a, b in zip(vals, vals[1:]))
        assert head.gap_side_lit(1, 0.5) > head.gap_side_lit(0, 0.5)

    def test_painted_the_lit_mouth_grows_upwards_and_the_side_plates_go_dark_when_he_talks(self, head):
        quiet, loud = lit_map(head, 0.0), lit_map(head, 1.0)
        mid = min(head.gap_cols, key=lambda g: abs(g[0] - head.mouth_centre))
        i, top, _, cells = mid
        notch = [j for j, plate in cells if not plate]
        assert sum(loud(j, i) for j in notch) > sum(quiet(j, i) for j in notch) + 150
        edge_cells = [(r, c) for r, c, d in head.gap_edges if d == 0]
        assert sum(quiet(r, c) for r, c in edge_cells) > sum(loud(r, c) for r, c in edge_cells) + 100

    def test_the_full_dim_mouth_is_lit_at_idle(self, head):
        m = lit_map(head, 0.0)
        cells = [(j, i) for j, i, _ in head.mouth]
        assert sum(1 for j, i in cells if m(j, i) > 60) > 0.6 * len(cells)


class TestTheLights:
    def test_the_plates_barely_brighten_the_brows_and_seam_do(self, head):
        assert head.plate_brightness(1.0, 0.0) - head.plate_brightness(0.0, 0.0) < 0.2
        for fn in (head.brow_brightness, head.seam_brightness):
            assert fn(0.0, 0.0) < 0.6 and fn(1.0, 0.0) == pytest.approx(1.0, abs=0.001)

    def test_a_louder_voice_never_dims_a_light(self, head):
        for fn in (head.plate_brightness, head.brow_brightness, head.seam_brightness):
            vals = [fn(lv / 20, 1.0) for lv in range(21)]
            assert all(a <= b + 1e-9 for a, b in zip(vals, vals[1:]))

    def test_the_seam_rests_dimmer_than_the_plates(self, head):
        assert head.seam_brightness(0.0, 1.0) < head.plate_brightness(0.0, 1.0)


class TestTheCheeks:
    def test_nothing_moves_when_nobody_is_talking(self, head):
        for _, _, u, weight, _, _, _ in head.cheek_cells[::7]:
            assert head.cheek_shift(u, weight, 0.0) == 0.0

    def test_the_outline_never_moves_and_never_changes_brightness(self, head):
        outline = [c for c in head.cheek_cells if c[4]]
        assert len(outline) > 30 and all(c[3] == 0.0 for c in outline)
        assert not any(c[6] for c in outline)                              # no light effect on the outline: the seam is separate
        quiet, loud = lit_map(head, 0.0), lit_map(head, 1.0)
        assert all(abs(quiet(c[0], c[1]) - loud(c[0], c[1])) <= 6 for c in outline[::4])

    def test_the_inside_of_the_lower_face_swells_outwards_a_little_and_joins_at_its_edges(self, head):
        from ui.clUltronHead import CHEEK_BULGE
        c0, c1 = head.cheek_rows
        assert len([c for c in head.cheek_cells if c[3] > 0]) > 100
        for j, i, u, weight, _, _, _ in head.cheek_cells:
            shift = head.cheek_shift(u, weight, 1.0)
            assert shift * u >= 0 and abs(shift) <= CHEEK_BULGE + 1e-9
            if j in (c0, c1):
                assert abs(shift) < 0.02

    def test_the_cheek_lights_are_a_seam_inside_the_outline_on_both_sides(self, head):
        seam = [c for c in head.cheek_cells if c[6]]
        assert len(seam) > 20 and {c[2] < 0 for c in seam} == {True, False} and not any(c[4] for c in seam)

    def test_both_cheeks_exist_and_match_in_size(self, head):
        left = sum(1 for c in head.cheek_cells if c[2] < 0)
        right = sum(1 for c in head.cheek_cells if c[2] > 0)
        assert left > 60 and right > 60 and 0.7 < left / right < 1.4

    def test_the_cheek_metal_is_drawn_live_not_baked_into_the_static_layer(self, head):
        cheek = {(c[0], c[1]) for c in head.cheek_cells}
        baked = {(int(j), int(i)) for j, i in zip(*np.where(head.metal & ~head.cheek_mask))}
        assert cheek and not (cheek & baked)


class TestPaintingAndTheDashboard:
    def test_it_paints_at_every_level_with_a_glitch_and_a_flicker(self, head, qapp):
        from PyQt6.QtGui import QImage, QPainter, QColor
        from ui.clUltronHead import new_glitch
        import random
        for level in (0.0, 0.5, 1.0):
            img = QImage(400, 500, QImage.Format.Format_RGB888)
            img.fill(QColor(0, 0, 0))
            p = QPainter(img)
            head.paint(p, 200, 250, 6, level, 1.0, new_glitch(head.h, random.Random(1)), 0.8, True)
            p.end()

    def test_the_halo_swells_with_the_voice(self, head):
        quiet, loud = lit_map_with_halo(head, 0.0), lit_map_with_halo(head, 1.0)
        assert loud > quiet

    def test_the_dashboard_uses_it_by_default(self):
        import subprocess
        env = dict(os.environ, JARVIS_THEME="ultron", QT_QPA_PLATFORM="offscreen")
        env.pop("JARVIS_ULTRON_HEAD", None)
        code = ("import sys; sys.path.insert(0, %r)\n"
                "import json\nfrom ui.clUltronHead import head_kind\n"
                "print(head_kind({}))" % SRC)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
        assert out.stdout.strip().splitlines()[-1] == "gap"


def lit_map_with_halo(head, level):
    from PyQt6.QtGui import QImage, QPainter, QColor
    img = QImage(500, 600, QImage.Format.Format_RGB888)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    head.paint(p, 250, 300, 6, level, 1.0, None, 1.0)
    p.end()
    b = img.constBits()
    b.setsize(img.sizeInBytes())
    arr = np.frombuffer(b, np.uint8).reshape(600, img.bytesPerLine())[:, : 500 * 3].reshape(600, 500, 3)
    return int(arr[:60].astype(np.int64).sum() + arr[-60:].astype(np.int64).sum())      # only the halo reaches the far rows
