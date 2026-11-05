"""The "gap" Ultron head: the procedural head as it was when the mouth was a lit grille whose dark centre gap opens.

Idle shows the full dim mouth. As he talks: the lights get a little brighter (the grille plates only slightly, `PLATE_GAIN`),
the bar of plates under the dark notch between the two big grille plates climbs up into it (the lit mouth grows upwards) and the notch gets wider (the plates at its
sides go dark), the cells inside the lower face's outline swell sideways a little, and a seam two cells inside the jawline
lights up. The outline never moves or reacts. Nothing slides and nothing fades: every cell is either lit or dark (pixel art), so the lips move in whole-cell steps.

It subclasses the stages head (clUltronHead.UltronHead) only to share the grid, the masks, the cached metal layer and the glow;
everything that is the gap behaviour lives here. Selected with settings.ultron_head = "gap" (see clUltronHead.load_head).
"""
import math
from typing import List, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter

from ui.clUltronHead import (CHEEK_BULGE, CHEEK_ROWS_ABOVE, DIM_METAL, EYE_CORE, MOUTH_FROM, PLATE_HOT, UltronHead, _edge,
                             build_masks)

GAP_REST = 0.0                # where the bar under the centre gap begins at rest, in cells, relative to its drawn top...
GAP_RISE = 2.6                # ...and how far it climbs into the notch at full voice (the notch is 3 cells tall)
GAP_DROP = 2.6                # the lower lip of the lit mouth: how far it drops below the drawn grille at full voice (the reverse of the top)
GAP_WIDEN = 3.0               # how many cells the gap widens sideways at full voice (its edge plates go dark)
GAP_EDGE_DEPTH = 4            # how many plate cells deep, on each side of the notch, can go dark
PLATE_GAIN = 0.3              # the grille plates brighten only a little with the voice


class GapHead(UltronHead):
    def __init__(self) -> None:                                     # (deliberately does not call UltronHead.__init__)
        self.metal, self.plates, self.eyes = build_masks()
        self.h, self.w = self.metal.shape
        lit = self.metal | self.plates | self.eyes
        self._rim = _edge(lit)
        rng = np.random.default_rng(11)
        self._shimmer = 0.88 + 0.12 * rng.random(lit.shape)          # fixed per-dot variation: a lit matrix, not a flat fill
        mouth_row = int(self.h * MOUTH_FROM)
        py, px = np.where(self.plates)
        top, bottom = py[py >= mouth_row].min(), py[py >= mouth_row].max()
        # (row, col, row_norm) of the grille plates: row_norm is 0 at their bottom edge and 1 at their top
        self.mouth = [(int(j), int(i), (bottom - j) / max(1, bottom - top)) for j, i in zip(py, px) if j >= mouth_row]
        self.brows = [(int(j), int(i)) for j, i in zip(py, px) if j < mouth_row]
        self._build_gap()
        self._build_lower_lip()
        ey, ex = np.where(self.eyes)
        self.eye_cells = [(int(j), int(i)) for j, i in zip(ey, ex)]
        self.eye_centres = self._eye_centres()
        self._build_cheeks(mouth_row)
        self._static = None
        self._static_cell = 0
        self._margin = 0
        self._glow = None
        self._halo = None
        self.show_halo = True

    # -- geometry ---------------------------------------------------------------------------------------------------------
    def _build_gap(self) -> None:
        """The mouth's centre gap: the dark notch between the two big grille plates, closed underneath by a bar of plates.
        It opens as he talks, by *lighting and darkening cells*: the bar climbs upwards into the notch (the lit mouth grows taller, most in the
        middle) and the plates at the notch's sides go dark; at rest the notch is as drawn.
        `gap_cols` are the bar columns (column, bar top, taper, cells), `gap_edges` the side plates (row, column, depth)."""
        cols = [i for _, i, _ in self.mouth]
        rows = [j for j, _, _ in self.mouth]
        mx0, mx1, mr0 = min(cols), max(cols), min(rows)
        self.mouth_centre = (mx0 + mx1) / 2.0
        self.mouth_halfwidth = max(1.0, (mx1 - mx0) / 2.0)
        c = self.mouth_centre
        self.gap_cols = []
        for i in range(self.w):
            if abs(i - c) > 6:
                continue
            col = np.where(self.plates[:, i])[0]
            col = col[col >= mr0 + 4]
            if not len(col):
                continue
            top = int(col.min())
            if not (self.metal[top - 1, i] and self.metal[top - 2, i]):          # needs a notch directly above the bar
                continue
            bottom = top
            while bottom + 1 < self.h and self.plates[bottom + 1, i]:
                bottom += 1
            n_top = top
            while n_top - 1 >= mr0 and self.metal[n_top - 1, i]:
                n_top -= 1
            taper = max(0.35, 1.0 - ((i - c) / 5.0) ** 2)
            cells = [(j, bool(self.plates[j, i])) for j in range(max(n_top, top - 3), bottom + 1)]
            self.gap_cols.append((i, top, taper, cells))
        self.gap_edges = []
        if self.gap_cols:
            top_max = max(g[1] for g in self.gap_cols)
            c0 = int(round(c))
            for r in range(mr0, top_max):
                if not self.metal[r, c0]:                          # the centre tooth: no notch on this row
                    continue
                left = c0
                while left - 1 >= 0 and self.metal[r, left - 1]:
                    left -= 1
                right = c0
                while right + 1 < self.w and self.metal[r, right + 1]:
                    right += 1
                for depth in range(GAP_EDGE_DEPTH):
                    for col_, sign in ((left - 1 - depth, -1), (right + 1 + depth, 1)):
                        if 0 <= col_ < self.w and self.plates[r, col_]:
                            self.gap_edges.append((r, col_, depth))
        self.gap_cells = {(j, i) for i, _, _, cells in self.gap_cols for j, _ in cells} | {(r, i) for r, i, _ in self.gap_edges}

    def _build_lower_lip(self) -> None:
        """The bottom lip of the lit mouth, the reverse of the top one: below each grille column are metal cells (not the
        outline) that light up as the voice rises, so the bottom edge of the mouth drops, most in the middle (`taper` 1) and less
        towards the sides. `lower_cols` are (column, drawn bottom row, taper, rows below it)."""
        self.lower_cols = []
        reach = int(math.ceil(GAP_DROP))
        for i in sorted({i for _, i, _ in self.mouth}):
            bottom = max(j for j, ii, _ in self.mouth if ii == i)
            taper = max(0.0, 1.0 - ((i - self.mouth_centre) / (self.mouth_halfwidth * 0.85)) ** 2)
            rows = [j for j in range(bottom + 1, min(self.h, bottom + 1 + reach))
                    if self.metal[j, i] and not self._rim[j, i] and not self.plates[j, i]]
            if taper > 0 and rows:
                self.lower_cols.append((i, bottom, taper, rows))
        self.lower_cells = {(j, i) for i, _, _, rows in self.lower_cols for j in rows}

    def _build_cheeks(self, mouth_row: int) -> None:
        """The lower face (from just above the grille down to the chin): the metal inside the outline swells a little sideways as
        he talks (more towards the sides; the outline itself, the centre line and the top and bottom edges of the region stay
        put, so it stays joined to the rest of the head). It also carries the cheek lights: a seam two cells inside the jawline
        that brightens and dims with the voice."""
        mr0 = min(j for j, _, _ in self.mouth)
        c0, c1 = mr0 - CHEEK_ROWS_ABOVE, self.h - 1
        region = np.zeros_like(self.metal)
        region[c0:c1 + 1, :] = self.metal[c0:c1 + 1, :]
        self.cheek_mask = region
        self.cheek_rows = (c0, c1)
        lit = self.metal | self.plates | self.eyes
        ys, xs = np.where(region)
        self.cheek_cells = []
        for j, i in zip(ys, xs):
            j, i = int(j), int(i)
            u = (i - self.mouth_centre) / (self.w / 2.0)               # -1 at the left edge of the face, +1 at the right
            rim = bool(self._rim[j, i])
            side = -1 if u < 0 else 1
            depth, x = 0, i                                            # lit cells between this one and the outside, going outwards
            while 0 <= x < self.w and lit[j, x] and depth < 6:
                depth += 1
                x += side
            weight = 0.0 if rim else math.sin(math.pi * (j - c0) / max(1, c1 - c0)) * min(1.0, (depth - 1) / 3.0)
            outer = i + 2 * side
            seam = (not rim) and 0 <= outer < self.w and not lit[j, outer] and lit[j, i + side]
            v = (0.78 if rim else 0.20 + 0.18 * (1 - j / self.h)) * self._shimmer[j, i]
            self.cheek_cells.append((j, i, u, weight, rim, v, bool(seam)))

    # -- brightness and movement model (pure, so it can be tested) --------------------------------------------------------
    def plate_brightness(self, level: float, t: float) -> float:
        """The grille plates barely change: it is the gap that opens."""
        return self.light(level, t, 0.55, PLATE_GAIN)

    def seam_brightness(self, level: float, t: float) -> float:
        """The cheek lights (the seam inside the jawline): dim at rest, bright when he talks."""
        return self.light(level, t, 0.26)

    @staticmethod
    def gap_edge(top: int, taper: float, level: float) -> float:
        """Where (in rows) the bar under the centre gap begins for a bar column whose drawn top row is `top`: higher (the lit
        mouth grows upwards) as the voice rises, most in the middle (`taper` 1) and less towards the sides."""
        return top + (GAP_REST - GAP_RISE * max(0.0, level)) * taper

    @staticmethod
    def gap_lit(j: int, edge: float) -> float:
        """Cell row j under the top lip: 0 (dark gap) or 1 (lit plate), a hard step: lit once the bar's edge reaches its middle."""
        return 1.0 if (j + 0.5) >= edge else 0.0

    @staticmethod
    def lower_edge(bottom: int, taper: float, level: float) -> float:
        """Where (in rows) the lit mouth ends under a grille column whose drawn bottom row is `bottom`: lower as the voice
        rises, the reverse of gap_edge."""
        return bottom + 1 + GAP_DROP * max(0.0, level) * taper

    @staticmethod
    def lower_lit(j: int, edge: float) -> float:
        """Cell row j under the grille: 1 (lit) once the lower lip has dropped past its middle, else 0 (dark metal)."""
        return 1.0 if (j + 0.5) <= edge else 0.0

    @staticmethod
    def gap_side_lit(depth: int, level: float) -> float:
        """A side plate `depth` cells from the notch edge: lit (1) or dark (0), the gap widening by GAP_WIDEN cells at full voice."""
        return 1.0 if (depth + 0.5) >= GAP_WIDEN * max(0.0, level) else 0.0

    # -- painting -------------------------------------------------------------------------------------------------------
    def paint(self, p: QPainter, cx: float, cy: float, cell: int, level: float, t: float,
              glitch_rows: Optional[List[int]] = None, flicker: float = 1.0, speaking: bool = True) -> None:
        """Draw the head centred on (cx, cy). `level` 0..1 is the voice (`speaking` is accepted for the common interface and
        ignored: this head follows the level alone); `glitch_rows` is a per-row sideways shift in cells (None = clean)."""
        cell = max(2, int(cell))
        if self._static is None or cell != self._static_cell:
            self._build_static(cell)
        base_opacity = p.opacity()
        p.setOpacity(base_opacity * flicker)                  # a hard flicker stutters the whole head's brightness
        wpx, hpx = self.size_px(cell)
        x0, y0 = cx - wpx / 2.0, cy - hpx / 2.0
        shift = glitch_rows or []
        off = lambda j: (shift[j] if j < len(shift) else 0) * cell

        # halo behind the head: faint at rest, stronger as the voice rises
        if self.show_halo:
            hs = self._halo.width()
            p.save()
            p.setOpacity(p.opacity() * (0.55 + 0.45 * level))
            p.drawPixmap(QRectF(cx - hs / 2, cy - hs / 2, hs, hs), self._halo, QRectF(self._halo.rect()))
            p.restore()

        # dim metal: the whole pixmap, or in horizontal bands when glitching
        sx, sy = x0 - self._margin, y0 - self._margin
        if not shift:
            p.drawPixmap(QPointF(sx, sy), self._static)
        else:
            groups = []                                  # [first row, end row, shift] runs of equal shift
            for j in range(self.h):
                o = off(j)
                if groups and groups[-1][2] == o:
                    groups[-1][1] = j + 1
                else:
                    groups.append([j, j + 1, o])
            for j0, j1, o in groups:
                top = 0 if j0 == 0 else self._margin + j0 * cell
                bot = self._static.height() if j1 >= self.h else self._margin + j1 * cell
                p.drawPixmap(QPointF(sx + o, sy + top), self._static, QRectF(0, top, self._static.width(), bot - top))

        p.setPen(Qt.PenStyle.NoPen)
        gap = max(1.0, cell * 0.18)

        # eye glow, flaring with the voice
        gscale = 1.0 + 0.45 * level
        for (ej, ei) in self.eye_centres:
            gs = self._glow.width() * gscale
            cxp = x0 + (ei + 0.5) * cell + off(int(ej))
            cyp = y0 + (ej + 0.5) * cell
            p.drawPixmap(QRectF(cxp - gs / 2, cyp - gs / 2, gs, gs), self._glow, QRectF(self._glow.rect()))

        def dot(j: int, i: int, b: float, core: Tuple[int, int, int], dx: float = 0.0, dy: float = 0.0) -> None:
            r = QRectF(x0 + (i + dx) * cell + off(j) + gap / 2, y0 + (j + dy) * cell + gap / 2, cell - gap, cell - gap)
            if b > 0.6:
                p.setBrush(QColor(255, 20, 25, int(110 * b)))
                p.drawRoundedRect(r.adjusted(-1.3, -1.3, 1.3, 1.3), cell * 0.4, cell * 0.4)
            p.setBrush(QColor(int(core[0] * b), int(core[1] * b), int(core[2] * b)))
            p.drawRoundedRect(r, cell * 0.28, cell * 0.28)

        pb = self.plate_brightness(level, t)
        lower_lit = set()                                           # the lower lip: metal cells below the grille that are lit now
        for i, bottom, taper, rows in self.lower_cols:
            edge = self.lower_edge(bottom, taper, level)
            lower_lit.update((j, i) for j in rows if self.lower_lit(j, edge))

        # lower face: the cells inside the outline follow the mouth opening; the outline stays; the seam dots are lights
        sb = self.seam_brightness(level, t)
        for j, i, u, weight, rim, v, seam in self.cheek_cells:
            if (j, i) in lower_lit:
                continue
            dx = self.cheek_shift(u, weight, level)
            if seam:
                dot(j, i, sb * self._shimmer[j, i], PLATE_HOT, dx=dx)
            else:
                r = QRectF(x0 + (i + dx) * cell + off(j) + gap / 2, y0 + j * cell + gap / 2, cell - gap, cell - gap)
                if rim:
                    p.setBrush(QColor(255, 20, 25, 70))
                    p.drawRoundedRect(r.adjusted(-1.2, -1.2, 1.2, 1.2), cell * 0.4, cell * 0.4)
                p.setBrush(QColor(int(DIM_METAL[0] * v), int(DIM_METAL[1] * v), int(DIM_METAL[2] * v)))
                p.drawRoundedRect(r, cell * 0.3, cell * 0.3)

        bb = self.brow_brightness(level, t)
        for j, i in self.brows:
            dot(j, i, bb * self._shimmer[j, i], PLATE_HOT)
        for j, i, _rn in self.mouth:
            if (j, i) not in self.gap_cells:
                dot(j, i, pb * self._shimmer[j, i], PLATE_HOT)
        for j, i in lower_lit:
            dot(j, i, pb * self._shimmer[j, i], PLATE_HOT)

        def blended(j: int, i: int, lit_amount: float, is_plate: bool) -> None:
            """A gap cell between a dark metal dot (lit_amount 0) and a lit plate dot (1)."""
            sh = self._shimmer[j, i]
            if lit_amount < 0.02 and not is_plate:
                return                                               # a notch cell that stays dark is just the metal underneath
            dark = (0.20 + 0.18 * (1 - j / self.h)) * sh
            lit_b = pb * sh
            col = tuple(DIM_METAL[n] * dark * (1 - lit_amount) + PLATE_HOT[n] * lit_b * lit_amount for n in range(3))
            r = QRectF(x0 + i * cell + off(j) + gap / 2, y0 + j * cell + gap / 2, cell - gap, cell - gap)
            if lit_b * lit_amount > 0.6:
                p.setBrush(QColor(255, 20, 25, int(110 * lit_b * lit_amount)))
                p.drawRoundedRect(r.adjusted(-1.3, -1.3, 1.3, 1.3), cell * 0.4, cell * 0.4)
            p.setBrush(QColor(int(col[0]), int(col[1]), int(col[2])))
            p.drawRoundedRect(r, cell * 0.28, cell * 0.28)

        for i, top, taper, cells in self.gap_cols:                  # the mouth's centre gap opening and closing
            edge = self.gap_edge(top, taper, level)
            for j, is_plate in cells:
                blended(j, i, self.gap_lit(j, edge), is_plate)
        for j, i, depth in self.gap_edges:
            blended(j, i, self.gap_side_lit(depth, level), True)

        for j, i in self.eye_cells:
            dot(j, i, 1.0, EYE_CORE)
        p.setOpacity(base_opacity)
