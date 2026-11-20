"""The Ultron emblem at the centre of the dashboard (settings.theme "ultron"): a red dot-matrix head, redrawn from the
owner's pixel-art reference, smoothed to a finer grid.

It is never cut apart. Talking happens *inside* the head: the lit plates of the grille (the "teeth") act as a live level
meter that follows the voice, the eye bars flare, and the brow plates brighten, while the silhouette stays exactly as it
is. (An earlier concept dropped a jaw on speech, which sliced the head in two.)

Cheap on purpose: the dim metal (the bulk of the cells) is painted once into a pixmap per size; each frame only the lit
plates and the eyes (a few hundred dots) are drawn. Pure numpy for the mask maths, no scipy in the UI process.
Colours are raw QColors on purpose: this is already the Ultron palette, and the skin's recolouring would turn the dim
reds into near-black.
"""
import math
import os
import random
import time
from typing import List, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QRadialGradient, QBrush

# The head on a 32 x 32 grid: '#' metal, '.' empty / enclosed plate, '+' eye bar.
GRID = [
    "................................",
    "................................",
    "............######..............",
    "..........##########............",
    ".........############...........",
    "........##############..........",
    ".......#+############+#.........",
    ".....#.##+##########+##.#.......",
    ".....#.##+##########+##.#.......",
    ".....#.##+##########+##.#.......",
    ".....#.##+##########+##.#.......",
    ".....#.##+##########+##.#.......",
    ".....#.#+############+#.#.......",
    ".....#.################.#.......",
    ".....###.############.###.......",
    ".....####..########..####.......",
    ".....####....####....####.......",
    ".....######...##...######.......",
    ".....########+##+########.......",
    "......####.##+##+##.#####.......",
    "......#.#...######...#.#........",
    "......#.#....####....#.#........",
    ".......##....#..#....##.........",
    "........##..........##..........",
    ".........#...#..#...#...........",
    ".........##..####..##...........",
    "..........##########............",
    "...........########.............",
    "............######..............",
    ".............####...............",
    "................................",
    "................................",
]
UP = 2                        # 32 -> 64 cells: finer dots, diagonals instead of stairs
MOUTH_FROM = 0.58             # plates below this fraction of the head's height are the grille (the talking meter)

DIM_METAL = (255, 20, 26)     # scaled by brightness
PLATE_HOT = (255, 52, 52)
EYE_CORE = (255, 175, 160)
HALO_PEAK_ALPHA = 80          # the halo behind the head (before the level scales it)
CORE_RADIUS = 0.30            # the idle mouth is the middle of the grille inside this fraction of its radius (a small closed mouth)
SPEAK_FLOOR = 0.25            # while he is speaking, even the quietest voice opens the mouth to its normal (full) shape: this share of the range
EXT_ROWS = 3                  # the loud mouth grows this many rows taller (lit cells below the grille, deepest in the middle)
OPEN_SOFT = 0.15              # softness of the opening edge
CHEEK_BULGE = 0.6             # how far (in cells) the lower face swells outwards at full voice, following the mouth opening
CHEEK_ROWS_ABOVE = 3          # the lower-face region starts this many rows above the grille and runs down to the chin


def _blur(a: np.ndarray, sigma: float) -> np.ndarray:
    radius = int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k /= k.sum()
    a = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 0, a)
    return np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 1, a)


def _fine(mask: np.ndarray) -> np.ndarray:
    big = np.kron(mask.astype(np.float32), np.ones((UP, UP), np.float32))
    return _blur(big, 0.55 * UP) > 0.5


def _enclosed(white: np.ndarray) -> np.ndarray:
    """White cells that cannot reach the border: the plates (brow chevrons, grille) as opposed to the background."""
    h, w = white.shape
    outside = np.zeros_like(white)
    stack = [(y, x) for y in range(h) for x in range(w) if (y in (0, h - 1) or x in (0, w - 1)) and white[y, x]]
    for y, x in stack:
        outside[y, x] = True
    while stack:
        y, x = stack.pop()
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < h and 0 <= nx < w and white[ny, nx] and not outside[ny, nx]:
                outside[ny, nx] = True
                stack.append((ny, nx))
    return white & ~outside


def build_masks() -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(metal, plates, eyes) as boolean grids of the same size, cropped to the head."""
    cells = np.array([list(r) for r in GRID])
    black, gray, white = cells == "#", cells == "+", cells == "."
    detail = _enclosed(white)
    body = _fine(black | gray | detail)
    plates = _fine(detail) & body
    eyes = _fine(gray) & body
    ys, xs = np.where(body)
    y0, y1, x0, x1 = ys.min() - 1, ys.max() + 2, xs.min() - 1, xs.max() + 2
    sl = (slice(y0, y1), slice(x0, x1))
    body, plates, eyes = body[sl], plates[sl], eyes[sl]
    return body & ~(plates | eyes), plates, eyes


def _edge(mask: np.ndarray) -> np.ndarray:
    p = np.pad(mask, 1)
    inner = p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return mask & ~inner


def _smoothstep(x: float) -> float:
    x = 0.0 if x < 0 else 1.0 if x > 1 else x
    return x * x * (3 - 2 * x)


class UltronHead:
    def __init__(self) -> None:
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
        self._build_opening()
        ey, ex = np.where(self.eyes)
        self.eye_cells = [(int(j), int(i)) for j, i in zip(ey, ex)]
        self.eye_centres = self._eye_centres()
        self._build_cheeks(mouth_row)
        self._static: Optional[QPixmap] = None
        self._static_cell = 0
        self._margin = 0
        self._glow: Optional[QPixmap] = None
        self._halo: Optional[QPixmap] = None
        self.show_halo = True
        self.halo_scale = 1.0                      # <1 when the host window is smaller than the halo (the overlay box)

    def _split_cheek_plates(self) -> None:
        """The plates below the brows are the grille (the big connected group, which is the mouth) and the small separate plates
        on each side of it: the cheek plates. Those are part of the face, not the mouth: always lit (like the brows), brighter
        and dimmer with the voice (the cheek lights of the film), never part of the mouth's opening. Without this split the
        mouth stage at idle, which lights only the middle of the grille, would darken them and 'remove the cheeks'."""
        cells = {(j, i): rn for j, i, rn in self.mouth}
        seen, groups = set(), []
        for start in sorted(cells):
            if start in seen:
                continue
            group, stack = [], [start]
            seen.add(start)
            while stack:
                y, x = stack.pop()
                group.append((y, x))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        n = (y + dy, x + dx)
                        if n in cells and n not in seen:
                            seen.add(n)
                            stack.append(n)
            groups.append(group)
        main = set(max(groups, key=len))
        self.mouth = [(j, i, rn) for (j, i), rn in cells.items() if (j, i) in main]
        self.cheek_plates = sorted(c for g in groups for c in g if c not in main)

    def _build_opening(self) -> None:
        """The talking mouth is the lit grille, in stages (a normal talking animation): silent, it is a small closed mouth, the
        middle of the grille; the moment he speaks (even quietly) it is its normal full shape; as the voice rises the lit mouth
        grows taller (extra lit cells below the grille) and brighter. Nothing slides: cells light and go dark.
        `mouth_open[(row, col)]` (0..1) says how far from the middle a grille cell is (the idle mouth is the cells at 0), and
        `mouth_ext` are the extra cells (row, col, level at which they light, 0.25..1) that make the loud mouth taller."""
        self._split_cheek_plates()
        cols = [i for _, i, _ in self.mouth]
        rows = [j for j, _, _ in self.mouth]
        mx0, mx1 = min(cols), max(cols)
        self.mouth_centre = (mx0 + mx1) / 2.0
        self.mouth_halfwidth = max(1.0, (mx1 - mx0) / 2.0)
        c0 = int(round(self.mouth_centre))
        run = [j for j in range(min(rows), max(rows) + 1) if self.plates[j, c0] or self.plates[j, c0 - 1]]
        self.mouth_middle_row = (min(run) + max(run)) / 2.0 + 0.5 if run else (min(rows) + max(rows)) / 2.0 + 0.5
        ry = max(1.0, max(abs(j + 0.5 - self.mouth_middle_row) for j, _, _ in self.mouth))
        self.mouth_open = {}
        for j, i, _ in self.mouth:
            r = math.hypot((j + 0.5 - self.mouth_middle_row) / ry, (i + 0.5 - self.mouth_centre) / self.mouth_halfwidth)
            self.mouth_open[(j, i)] = min(1.0, max(0.0, (r - CORE_RADIUS) / (1.0 - CORE_RADIUS)))
        # the extra rows of the loud mouth: below the lowest plate of each column, deepest in the middle
        self.mouth_ext = []
        for i in range(self.w):
            col = [j for j, ii, _ in self.mouth if ii == i]
            if not col:
                continue
            taper = max(0.0, 1.0 - ((i - self.mouth_centre) / (self.mouth_halfwidth * 0.85)) ** 2)
            depth = int(round(EXT_ROWS * taper))
            for k in range(1, depth + 1):
                j = max(col) + k
                if j < self.h and self.metal[j, i] and not self._rim[j, i]:
                    self.mouth_ext.append((j, i, SPEAK_FLOOR + (1.0 - SPEAK_FLOOR) * k / EXT_ROWS))

    def _build_cheeks(self, mouth_row: int) -> None:
        """The lower face (from just above the grille down to the chin): the metal inside the outline swells a little sideways as
        the mouth opens (more towards the sides; the outline itself, the centre line and the top and bottom edges of the region
        stay put, so it stays joined to the rest of the head)."""
        mr0 = min(j for j, _, _ in self.mouth)
        c0, c1 = mr0 - CHEEK_ROWS_ABOVE, self.h - 1
        region = np.zeros_like(self.metal)
        region[c0:c1 + 1, :] = self.metal[c0:c1 + 1, :]
        self.cheek_mask = region
        self.cheek_rows = (c0, c1)
        # the cheek lights run along the jawline: the outline of the lower face plus a parallel seam two cells inside it
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
            # the outline (depth 1) never moves; cells deeper inside move progressively more
            weight = 0.0 if rim else math.sin(math.pi * (j - c0) / max(1, c1 - c0)) * min(1.0, (depth - 1) / 3.0)
            v = (0.78 if rim else 0.20 + 0.18 * (1 - j / self.h)) * self._shimmer[j, i]
            self.cheek_cells.append((j, i, u, weight, rim, v))

    def _eye_centres(self) -> List[Tuple[float, float]]:
        """The middle of each eye bar (8-connected groups of eye cells), for the glow."""
        seen, out = set(), []
        cells = set(self.eye_cells)
        for start in sorted(cells):
            if start in seen:
                continue
            group, stack = [], [start]
            seen.add(start)
            while stack:
                y, x = stack.pop()
                group.append((y, x))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        n = (y + dy, x + dx)
                        if n in cells and n not in seen:
                            seen.add(n)
                            stack.append(n)
            if len(group) >= 8:                          # the big bars at the temples; the tiny nose dots get no glow
                out.append((sum(g[0] for g in group) / len(group), sum(g[1] for g in group) / len(group)))
        return out

    # -- brightness and movement model (pure, so it can be tested) --------------------------------------------------------
    @staticmethod
    def light(level: float, t: float, rest: float, gain: float = 1.0) -> float:
        """The old JARVIS glow: a light that rests at `rest` (with a slow breathing) and gets brighter as the voice gets louder
        (`gain` < 1 for a light that should only brighten a little)."""
        idle = rest + 0.06 * math.sin(t * 1.3)
        return min(1.0, idle + (1.0 - idle) * gain * max(0.0, level) ** 0.85)

    def plate_brightness(self, amount: float, t: float) -> float:
        """The lit mouth: calm at rest, normal when he starts speaking, stronger the more it opens (`amount`, see mouth_amount)."""
        return self.light(amount, t, 0.6)

    def brow_brightness(self, level: float, t: float) -> float:
        return self.light(level, t, 0.5)

    def cheek_light(self, level: float, t: float) -> float:
        """The cheek plates beside the mouth (the cheek lights of the film): lit at rest, brighter and dimmer with the voice."""
        return self.light(level, t, 0.5)

    @staticmethod
    def mouth_amount(level: float, speaking: bool) -> float:
        """How far the mouth is open, 0..1: closed while he is not speaking; once he speaks it is at least SPEAK_FLOOR (the normal
        mouth), whatever the quietest syllable, and opens further as the voice rises."""
        if not speaking:
            return 0.0
        return SPEAK_FLOOR + (1.0 - SPEAK_FLOOR) * min(1.0, max(0.0, level)) ** 0.85

    @staticmethod
    def mouth_lit(open_at: float, amount: float) -> float:
        """How lit (0 dark .. 1 lit) a grille cell is: the idle mouth (amount 0) is only the middle cells (open_at 0); the normal
        full shape is reached at SPEAK_FLOOR."""
        return _smoothstep((amount / SPEAK_FLOOR - open_at) / OPEN_SOFT + 1.0)

    @staticmethod
    def ext_lit(ext_at: float, amount: float) -> float:
        """How lit one of the extra rows of the loud mouth is: they light one after another as the mouth opens past normal."""
        return _smoothstep((amount - ext_at) / 0.1 + 1.0)

    def cheek_shift(self, u: float, weight: float, level: float) -> float:
        """Sideways movement (in cells) of a lower-face dot at normalised offset u (-1..1) from the centre line, following the
        mouth opening: outwards, more towards the sides, nothing for the outline (weight 0), the centre line or the region's edges."""
        return u * weight * CHEEK_BULGE * max(0.0, level)

    # -- painting -------------------------------------------------------------------------------------------------------
    def _build_static(self, cell: int) -> None:
        """The dim metal with a brighter rim, painted once per size, plus its halo."""
        margin = 2 * cell
        wpx, hpx = self.w * cell + 2 * margin, self.h * cell + 2 * margin
        img = QImage(wpx, hpx, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        gap = max(1.0, cell * 0.18)
        ys, xs = np.where(self.metal & ~self.cheek_mask)       # the cheeks are drawn live (they move)
        for j, i in zip(ys, xs):
            rim = self._rim[j, i]
            v = (0.78 if rim else 0.20 + 0.18 * (1 - j / self.h)) * self._shimmer[j, i]
            r = QRectF(margin + i * cell + gap / 2, margin + j * cell + gap / 2, cell - gap, cell - gap)
            if rim:
                p.setBrush(QColor(255, 20, 25, 70))
                p.drawRoundedRect(r.adjusted(-1.2, -1.2, 1.2, 1.2), cell * 0.4, cell * 0.4)
            p.setBrush(QColor(int(DIM_METAL[0] * v), int(DIM_METAL[1] * v), int(DIM_METAL[2] * v)))
            p.drawRoundedRect(r, cell * 0.3, cell * 0.3)
        p.end()
        self._static, self._static_cell, self._margin = QPixmap.fromImage(img), cell, margin
        self._build_glow_and_halo(cell)

    def _build_glow_and_halo(self, cell: int) -> None:
        """The soft glow behind the eyes and the halo behind the whole head (shared with SpriteHead)."""
        # soft glow used behind the eyes
        size = cell * 14
        g = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        g.fill(Qt.GlobalColor.transparent)
        gp = QPainter(g)
        grad = QRadialGradient(size / 2, size / 2, size / 2)
        grad.setColorAt(0.0, QColor(255, 30, 30, 200))
        grad.setColorAt(0.45, QColor(255, 30, 30, 70))
        grad.setColorAt(1.0, QColor(255, 30, 30, 0))
        gp.setPen(Qt.PenStyle.NoPen)
        gp.setBrush(QBrush(grad))
        gp.drawEllipse(0, 0, size, size)
        gp.end()
        self._glow = QPixmap.fromImage(g)
        UltronHead._build_halo(self, cell)

    def _build_halo(self, cell: int) -> None:
        # the soft red halo behind the whole head (it replaces the old bloom): a gaussian that reaches zero at its rim
        hs = int(max(self.w, self.h) * cell * 1.9)
        hi = QImage(hs, hs, QImage.Format.Format_ARGB32_Premultiplied)
        hi.fill(Qt.GlobalColor.transparent)
        hp = QPainter(hi)
        halo = QRadialGradient(hs / 2, hs / 2, hs / 2)
        rim = math.exp(-3.5)
        for k in range(17):
            t = k / 16
            reach = getattr(self, "halo_reach", 1.0)       # <1 (the overlay box): the same glow, but its tail fades inside the box
            fall = (math.exp(-3.5 * (t * reach) ** 2) - rim) / (1.0 - rim)
            if reach < 1.0:
                u = min(1.0, max(0.0, (t - 0.6) / 0.4))
                fall *= 1.0 - u * u * (3 - 2 * u)
            halo.setColorAt(t, QColor(255, int(60 - 40 * t), 20, int(HALO_PEAK_ALPHA * fall)))
        hp.setPen(Qt.PenStyle.NoPen)
        hp.setBrush(QBrush(halo))
        hp.drawRect(0, 0, hs, hs)
        hp.end()
        self._halo = QPixmap.fromImage(hi)
        self._halo_reach_built = getattr(self, "halo_reach", 1.0)

    def size_px(self, cell: int) -> Tuple[int, int]:
        return self.w * cell, self.h * cell

    def paint(self, p: QPainter, cx: float, cy: float, cell: int, level: float, t: float,
              glitch_rows: Optional[List[int]] = None, flicker: float = 1.0, speaking: bool = True) -> None:
        """Draw the head centred on (cx, cy). `level` 0..1 is the voice; `speaking` says he is talking (the mouth is closed
        otherwise); `glitch_rows` is a per-row sideways shift in cells (None = clean)."""
        cell = max(2, int(cell))
        if self._static is None or cell != self._static_cell:
            self._build_static(cell)
        base_opacity = p.opacity()
        p.setOpacity(base_opacity * flicker)                  # a hard flicker stutters the whole head's brightness
        wpx, hpx = self.size_px(cell)
        x0, y0 = cx - wpx / 2.0, cy - hpx / 2.0
        amount = self.mouth_amount(level, speaking)
        shift = glitch_rows or []
        off = lambda j: (shift[j] if j < len(shift) else 0) * cell

        # halo behind the head: faint at rest, stronger as the voice rises
        if self.show_halo:
            halo = _halo_pixmap(self, cell)
            hs = halo.width() * self.halo_scale
            p.save()
            p.setOpacity(p.opacity() * (0.55 + 0.45 * level))
            p.drawPixmap(QRectF(cx - hs / 2, cy - hs / 2, hs, hs), halo, QRectF(halo.rect()))
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

        # lower face: the cells inside the outline follow the mouth opening; the outline stays
        for j, i, u, weight, rim, v in self.cheek_cells:
            dx = self.cheek_shift(u, weight, amount)
            r = QRectF(x0 + (i + dx) * cell + off(j) + gap / 2, y0 + j * cell + gap / 2, cell - gap, cell - gap)
            if rim:
                p.setBrush(QColor(255, 20, 25, 70))
                p.drawRoundedRect(r.adjusted(-1.2, -1.2, 1.2, 1.2), cell * 0.4, cell * 0.4)
            p.setBrush(QColor(int(DIM_METAL[0] * v), int(DIM_METAL[1] * v), int(DIM_METAL[2] * v)))
            p.drawRoundedRect(r, cell * 0.3, cell * 0.3)

        cb = self.cheek_light(amount, t)                            # the cheek plates: part of the face, always lit
        for j, i in self.cheek_plates:
            dot(j, i, cb * self._shimmer[j, i], PLATE_HOT)

        bb = self.brow_brightness(level, t)
        for j, i in self.brows:
            dot(j, i, bb * self._shimmer[j, i], PLATE_HOT)
        pb = self.plate_brightness(amount, t)
        for j, i, _rn in self.mouth:                                # the mouth: small and calm at rest, normal when he speaks
            lit_amount = self.mouth_lit(self.mouth_open[(j, i)], amount)
            sh = self._shimmer[j, i]
            dark = (0.20 + 0.18 * (1 - j / self.h)) * sh
            lit_b = pb * sh
            col = tuple(DIM_METAL[n] * dark * (1 - lit_amount) + PLATE_HOT[n] * lit_b * lit_amount for n in range(3))
            r = QRectF(x0 + i * cell + off(j) + gap / 2, y0 + j * cell + gap / 2, cell - gap, cell - gap)
            if lit_b * lit_amount > 0.6:
                p.setBrush(QColor(255, 20, 25, int(110 * lit_b * lit_amount)))
                p.drawRoundedRect(r.adjusted(-1.3, -1.3, 1.3, 1.3), cell * 0.4, cell * 0.4)
            p.setBrush(QColor(int(col[0]), int(col[1]), int(col[2])))
            p.drawRoundedRect(r, cell * 0.28, cell * 0.28)
        for j, i, ext_at in self.mouth_ext:                         # and taller as the voice rises
            e = self.ext_lit(ext_at, amount)
            if e > 0.02:
                sh = self._shimmer[j, i]
                dark = (0.20 + 0.18 * (1 - j / self.h)) * sh
                col = tuple(DIM_METAL[n] * dark * (1 - e) + PLATE_HOT[n] * pb * sh * e for n in range(3))
                r = QRectF(x0 + i * cell + off(j) + gap / 2, y0 + j * cell + gap / 2, cell - gap, cell - gap)
                if pb * e > 0.6:
                    p.setBrush(QColor(255, 20, 25, int(110 * pb * e)))
                    p.drawRoundedRect(r.adjusted(-1.3, -1.3, 1.3, 1.3), cell * 0.4, cell * 0.4)
                p.setBrush(QColor(int(col[0]), int(col[1]), int(col[2])))
                p.drawRoundedRect(r, cell * 0.28, cell * 0.28)

        for j, i in self.eye_cells:
            dot(j, i, 1.0, EYE_CORE)
        p.setOpacity(base_opacity)


# Glitch timing, in seconds / milliseconds, overridable with settings.ultron_glitch in core.json. Time based (not per frame),
# so it behaves the same at 8 or 60 fps. A *soft* glitch is a few row bands sliding sideways for a moment; a *hard* flicker is
# rarer, longer and rougher (more bands, bigger shifts, the whole head stuttering in brightness).
GLITCH_DEFAULTS = {
    "enabled": True,
    "idle_every_s": [3.0, 9.0],           # between soft glitches at rest or while listening
    "speaking_every_s": [6.0, 15.0],      # fewer while he talks, so it does not fight the meter
    "processing_every_s": [0.7, 2.0],     # busy: often
    "soft_ms": [110, 320],
    "soft_shift": 3,                      # max sideways shift in cells
    "hard_every_s": [15.0, 45.0],         # the rare harder flicker, at a random moment
    "hard_ms": [500, 1500],
    "hard_shift": 9,
}


def new_glitch(height_cells: int, rng: random.Random, hard: bool = False, max_shift: Optional[int] = None) -> List[int]:
    """Per-row sideways shifts (in cells) for a few bands: the digital glitch. `hard` = more, taller bands, bigger shifts."""
    limit = max_shift if max_shift is not None else (9 if hard else 3)
    rows = [0] * height_cells
    for _ in range(rng.randint(6, 12) if hard else rng.randint(2, 4)):
        start, size = rng.randint(0, max(0, height_cells - 4)), rng.randint(2, 10 if hard else 6)
        shift = rng.randint(-limit, limit)
        for j in range(start, min(height_cells, start + size)):
            rows[j] = shift
    return rows


class Glitch:
    """Decides when the head glitches. `update(state, rows)` returns (per-row shifts or None, brightness multiplier)."""

    def __init__(self, cfg: Optional[dict] = None, rng: Optional[random.Random] = None, clock=time.monotonic) -> None:
        self.cfg = {**GLITCH_DEFAULTS, **(cfg or {})}
        self.rng = rng or random.Random()
        self.clock = clock
        self.until = 0.0
        self.hard = False
        self.next_regen = 0.0
        self.rows: Optional[List[int]] = None
        self.flicker = 1.0
        now = clock()
        self.next_soft = now + self._span("idle_every_s")
        self.next_hard = now + self._span("hard_every_s")

    def _span(self, key: str) -> float:
        lo, hi = self.cfg[key]
        return self.rng.uniform(lo, hi)

    def _gap_key(self, state: str) -> str:
        return {"PROCESSING": "processing_every_s", "SPEAKING": "speaking_every_s"}.get(state, "idle_every_s")

    def update(self, state: str, height: int) -> Tuple[Optional[List[int]], float]:
        if not self.cfg["enabled"]:
            return None, 1.0
        now = self.clock()
        if now < self.until:                                          # a glitch is running: reshuffle it every few frames
            if now >= self.next_regen:
                limit = self.cfg["hard_shift" if self.hard else "soft_shift"]
                self.rows = new_glitch(height, self.rng, hard=self.hard, max_shift=int(limit))
                if self.hard:
                    self.flicker = 0.12 if self.rng.random() < 0.2 else self.rng.uniform(0.35, 1.0)
                else:
                    self.flicker = self.rng.uniform(0.85, 1.0)
                self.next_regen = now + (0.05 if self.hard else 0.07)
            return self.rows, self.flicker
        self.rows, self.flicker = None, 1.0
        lo, hi = self.cfg[self._gap_key(state)]
        if self.next_soft - now > hi:                                 # the state got busier: do not wait out the old gap
            self.next_soft = now + self.rng.uniform(lo, hi)
        if now >= self.next_hard:
            self.hard = True
            self.until = now + self._span("hard_ms") / 1000.0
            self.next_hard = self.until + self._span("hard_every_s")
            self.next_soft = max(self.next_soft, self.until + lo)
            self.next_regen = 0.0
        elif now >= self.next_soft:
            self.hard = False
            self.until = now + self._span("soft_ms") / 1000.0
            self.next_soft = self.until + self.rng.uniform(lo, hi)
            self.next_regen = 0.0
        return None, 1.0


# ======================================================================================================================
# Sprite mode: the head is the owner's hand-drawn frames (assets/themes/ultron/head/*.png, 42 x 58, one pixel per dot).
# The code does not interpret them: every pixel is one dot with exactly the colour drawn. It only picks / cross-fades the
# frames by the voice and adds the glow, the glitch and the flicker. Pixels that are the same in every frame (outline, eyes,
# brows ...) are painted once into a cached pixmap; only the pixels that differ between frames are painted per frame.
# ======================================================================================================================
HEAD_FRAMES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "themes", "ultron", "head"))
OUTSIDE_BLACK = 6                # a sprite pixel this dark (or transparent) is outside the head and is not drawn
BRIGHT_LIFT = 0.45               # how much brighter the whole head is at the loudest, beyond the first talking frame (louder = wider = brighter)
GROW_SX = 1.12                   # beyond the drawn frames the mouth keeps growing with the voice: the same shape stretched, up to this much
GROW_SY = 1.45                   # wider and this much taller (about its top) at the loudest. The first talking frame is never changed.
MOUTH_RED = 95                   # a sprite pixel with at least this much red (and less than OUTLINE_RED), in the mouth rows, is lit mouth
OUTLINE_RED = 140                # (the metal is ~50-67, the faint ring inside the outline 88, the mouth 98-111; the outline, eyes and nose dots are brighter)
MOUTH_TOP = 0.62                 # the mouth lives below this fraction of the sprite's height (above it: eyes, brows, nose dots)
PULSE_QUIET = 0.94                # while he speaks the whole head is this size at the quietest syllable...
PULSE_LOUD = 1.14                 # ...and this size at the loudest (times the visualizer's own idle/speaking scale)
BREATH = 0.012                    # at rest it only breathes, this much
FRAME_FILES = ("idle", "talk_1", "talk_2", "talk_3", "talk_4", "talk_5", "talk_6")


def _halo_pixmap(head, cell: int) -> QPixmap:
    """The halo, rebuilt when the host asked for a different reach (head.halo_reach)."""
    if getattr(head, "_halo_reach_built", None) != getattr(head, "halo_reach", 1.0):
        UltronHead._build_halo(head, cell)
    return head._halo


def pulse_scale(level: float, speaking: bool, t: float) -> float:
    """How big the whole head is, relative to the visualizer's state scale: at rest it only breathes; while he speaks it
    gets bigger and smaller with the voice (PULSE_QUIET at the quietest syllable up to PULSE_LOUD at the loudest). Without
    this the head feels dead."""
    if not speaking:
        return 1.0 + BREATH * math.sin(t * 1.3)
    return PULSE_QUIET + (PULSE_LOUD - PULSE_QUIET) * min(1.0, max(0.0, level)) ** 0.85


def _qimage_to_rgba(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine())[:, : img.width() * 4].reshape(img.height(), img.width(), 4).copy()


class SpriteHead:
    """The head drawn from sprite frames: frame 0 is idle, frame 1 the quietest speech, the last the loudest."""

    def __init__(self, frames: List[np.ndarray], clock=time.monotonic) -> None:
        if len(frames) < 2:
            raise ValueError("a sprite head needs at least an idle frame and one talking frame")
        if len({f.shape for f in frames}) != 1:
            raise ValueError("all the head frames must be the same size")
        self.frames = np.stack(frames).astype(np.uint8)                       # n, h, w, 4 (RGBA)
        # Outside the head is transparent *or* pure black (an editor export has no transparency): neither is drawn.
        outside = self.frames[..., :3].max(axis=3) <= OUTSIDE_BLACK
        self.frames[..., 3] = np.where(outside, 0, self.frames[..., 3])
        self.n, self.h, self.w = self.frames.shape[:3]
        alpha = self.frames[..., 3] > 0
        self.lit = alpha.any(axis=0)
        rgb = self.frames[..., :3].astype(np.float32)
        self.varying = (np.any(rgb != rgb[0], axis=3).any(axis=0) | (alpha != alpha[0]).any(axis=0)) & self.lit
        ys, xs = np.where(self.varying)
        self.dynamic = list(zip(ys.tolist(), xs.tolist()))
        first = self.frames[0]
        eyes = (first[..., 0] >= 230) & (first[..., 1] >= 150) & (first[..., 2] >= 140) & (first[..., 3] > 0) & ~self.varying
        self.eye_cells = [(int(j), int(i)) for j, i in zip(*np.where(eyes))]
        self.eye_centres = UltronHead._eye_centres(self)
        self._build_growth()
        self._clock = clock
        self._pos = 0.0
        self._last: Optional[float] = None
        self._static: Optional[QPixmap] = None
        self._static_cell = 0
        self._margin = 0
        self._glow: Optional[QPixmap] = None
        self._halo: Optional[QPixmap] = None
        self.show_halo = True
        self.halo_scale = 1.0                      # <1 when the host window is smaller than the halo (the overlay box)

    def _build_growth(self) -> None:
        """Works out where the mouth lives in the sprite, so it can be stretched beyond the drawn frames: the lit mouth pixels in
        the lower face (above it are the eyes, brows and nose dots), without the small separate plates beside it (the cheek
        plates), without the outline, and only where the stretched mouth may go (the metal inside the face, away from the outside)."""
        top = int(self.h * MOUTH_TOP)
        rows = np.zeros((self.h, self.w), bool)
        rows[top:] = True
        in_range = (self.frames[..., 0] >= MOUTH_RED) & (self.frames[..., 0] < OUTLINE_RED) & (self.frames[..., 3] > 0)
        any_mouth = in_range.any(axis=0) & rows
        seen, groups = set(), []
        for start in zip(*np.where(any_mouth)):
            if start in seen:
                continue
            group, stack = [], [start]
            seen.add(start)
            while stack:
                y, x = stack.pop()
                group.append((y, x))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        n = (y + dy, x + dx)
                        if 0 <= n[0] < self.h and 0 <= n[1] < self.w and any_mouth[n] and n not in seen:
                            seen.add(n)
                            stack.append(n)
            groups.append(group)
        self._mouth_zone = np.zeros((self.h, self.w), bool)
        self.grow_region = np.zeros((self.h, self.w), bool)
        self._metal_row = np.tile(np.array([65.0, 0.0, 0.0]), (self.h, 1))
        if not groups:
            return
        main = max(groups, key=len)
        mrows = [j for j, _ in main]
        mcols = [i for _, i in main]
        self.mouth_bounds = (min(mrows), max(mrows), min(mcols), max(mcols))
        zone = np.zeros((self.h, self.w), bool)
        zone[max(0, min(mrows) - 1):max(mrows) + 2, max(0, min(mcols) - 1):max(mcols) + 2] = True
        for g in groups:                                                     # the cheek plates and any other small plates
            if g is main:
                continue
            for (y, x) in g:
                zone[max(0, y - 1):y + 2, max(0, x - 1):x + 2] = False
        self._mouth_zone = zone & rows
        padded = np.pad(self.lit, 1)
        interior = self.lit & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
        reach = np.zeros((self.h, self.w), bool)
        reach[max(0, min(mrows) - 1):max(mrows) + 8, max(0, min(mcols) - 3):max(mcols) + 4] = True
        for g in groups:
            if g is main:
                continue
            for (y, x) in g:
                reach[max(0, y - 1):y + 2, max(0, x - 1):x + 2] = False
        self.grow_region = reach & rows & interior & (self.frames[0][..., 0] < OUTLINE_RED)
        # the colour of the bare metal in each row of the lower face (what a pixel shows when the mouth is not on it)
        first = self.frames[0]
        for j in range(self.h):
            cols = np.where(self.grow_region[j] & (first[j, :, 0] < MOUTH_RED))[0]
            if len(cols):
                self._metal_row[j] = np.median(first[j, cols, :3].astype(float), axis=0)

    # -- which frame, and smoothing -----------------------------------------------------------------------------------
    def frame_pos(self, level: float, speaking: bool) -> float:
        """Where in the frame list the head should be (0 = idle). Not speaking: idle. The moment he speaks, even the quietest
        syllable, it is at least frame 1; louder voices move on through the frames up to the last."""
        if not speaking:
            return 0.0
        if self.n == 2:
            return 1.0
        return 1.0 + (self.n - 2) * min(1.0, max(0.0, level)) ** 0.85

    def step(self, target: float, now: float) -> float:
        """Eases the position towards the target in real time (quick to open, slower to close), whatever the frame rate."""
        if self._last is None:
            self._pos = target
        else:
            dt = max(0.0, min(0.25, now - self._last))
            tau = 0.06 if target > self._pos else 0.16
            self._pos += (target - self._pos) * (1.0 - math.exp(-dt / tau))
        self._last = now
        return self._pos

    def drive(self, pos: float, level: float) -> float:
        """0..1: how open the mouth is (its position in the frame list), so the brightness, the eye glow and the halo rise
        and fall with the mouth. While he is not speaking the mouth stays at idle and only the user's own voice (`level`, the
        mic) moves the glow a little."""
        opened = pos / max(1.0, self.n - 1.0)
        return max(opened, 0.5 * min(1.0, max(0.0, level)) if pos < 0.05 else 0.0)

    def grow_amount(self, pos: float) -> float:
        """0..1: how far the mouth has grown beyond the drawn frames. Nothing up to the first talking frame (which stays exactly
        as drawn), then rising with the voice to the full growth at the last frame."""
        if self.n <= 2:
            return 0.0
        return min(1.0, max(0.0, (pos - 1.0) / (self.n - 2.0)))

    def grown(self, cur: np.ndarray, g: float):
        """The mouth stretched beyond the drawn frames at growth `g` (0..1): the *same shape*, scaled about its top (so it grows
        downwards and sideways, never up into the nose), wider by up to GROW_SX and taller by up to GROW_SY. Returns
        (rows, cols, colours) of the pixels to repaint: everywhere the stretched or the drawn mouth is. A stretch, not a dilation:
        growing the pixels outwards would fill the gap between the two lobes and turn the mouth into a block."""
        if g <= 0.01:
            return None
        mask = ((cur[..., 0] >= MOUTH_RED) & (cur[..., 0] < OUTLINE_RED) & (cur[..., 3] > 0) & self._mouth_zone).astype(np.float32)
        ys, xs = np.nonzero(mask)
        if not len(ys):
            return None
        top, bottom = float(ys.min()), float(ys.max())
        anchor_row, anchor_col = top + 0.1 * (bottom - top), float(xs.mean())
        sx, sy = 1.0 + g * (GROW_SX - 1.0), 1.0 + g * (GROW_SY - 1.0)
        cand = self.grow_region | (mask > 0)
        cj, ci = np.nonzero(cand)
        sj = anchor_row + (cj - anchor_row) / sy                      # where each target pixel comes from in the drawn mouth
        si = anchor_col + (ci - anchor_col) / sx
        j0 = np.clip(np.floor(sj).astype(int), 0, self.h - 2)
        i0 = np.clip(np.floor(si).astype(int), 0, self.w - 2)
        fj, fi = np.clip(sj - j0, 0.0, 1.0), np.clip(si - i0, 0.0, 1.0)

        def sample(arr):
            a = arr[j0, i0] * ((1 - fj) * (1 - fi))[..., None] if arr.ndim == 3 else arr[j0, i0] * (1 - fj) * (1 - fi)
            b = arr[j0, i0 + 1] * ((1 - fj) * fi)[..., None] if arr.ndim == 3 else arr[j0, i0 + 1] * (1 - fj) * fi
            c = arr[j0 + 1, i0] * (fj * (1 - fi))[..., None] if arr.ndim == 3 else arr[j0 + 1, i0] * fj * (1 - fi)
            d = arr[j0 + 1, i0 + 1] * (fj * fi)[..., None] if arr.ndim == 3 else arr[j0 + 1, i0 + 1] * fj * fi
            return a + b + c + d

        cover = sample(mask)
        colour = sample(cur[..., :3].astype(np.float32) * mask[..., None]) / np.maximum(cover, 1e-3)[..., None]
        edge = np.clip((cover - 0.25) / 0.5, 0.0, 1.0)
        edge = edge * edge * (3.0 - 2.0 * edge)
        out = self._metal_row[cj] * (1.0 - edge)[..., None] + colour * edge[..., None]
        draw = (edge > 0.02) | (mask[cj, ci] > 0)
        return cj[draw], ci[draw], out[draw]

    def blend(self, pos: float) -> np.ndarray:
        """The frame at `pos`: a cross-fade of the two frames around it (h, w, 4 floats)."""
        pos = min(float(self.n - 1), max(0.0, pos))
        k = int(math.floor(pos))
        k2 = min(self.n - 1, k + 1)
        frac = pos - k
        return self.frames[k].astype(np.float32) * (1.0 - frac) + self.frames[k2].astype(np.float32) * frac

    # -- painting -------------------------------------------------------------------------------------------------------
    def _build_static(self, cell: int) -> None:
        margin = 2 * cell
        img = QImage(self.w * cell + 2 * margin, self.h * cell + 2 * margin, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        gap = max(1.0, cell * 0.18)
        first = self.frames[0]
        for j, i in zip(*np.where(self.lit & ~self.varying)):
            r_, g_, b_, a_ = (int(v) for v in first[j, i])
            r = QRectF(margin + i * cell + gap / 2, margin + j * cell + gap / 2, cell - gap, cell - gap)
            bright = max(r_, g_, b_) / 255.0
            if bright > 0.55:                                   # the bright dots (outline, eyes) carry a faint halo
                p.setBrush(QColor(255, 20, 25, int(120 * (bright - 0.5))))
                p.drawRoundedRect(r.adjusted(-1.2, -1.2, 1.2, 1.2), cell * 0.4, cell * 0.4)
            p.setBrush(QColor(r_, g_, b_, a_))
            p.drawRoundedRect(r, cell * 0.3, cell * 0.3)
        p.end()
        self._static, self._static_cell, self._margin = QPixmap.fromImage(img), cell, margin
        UltronHead._build_glow_and_halo(self, cell)

    def size_px(self, cell: int) -> Tuple[int, int]:
        return self.w * cell, self.h * cell

    def paint(self, p: QPainter, cx: float, cy: float, cell: int, level: float, t: float,
              glitch_rows: Optional[List[int]] = None, flicker: float = 1.0, speaking: bool = True) -> None:
        cell = max(2, int(cell))
        if self._static is None or cell != self._static_cell:
            self._build_static(cell)
        pos = self.step(self.frame_pos(level, speaking), self._clock())
        drive = self.drive(pos, level)
        base_opacity = p.opacity()
        p.setOpacity(base_opacity * flicker)
        wpx, hpx = self.size_px(cell)
        x0, y0 = cx - wpx / 2.0, cy - hpx / 2.0
        shift = glitch_rows or []
        off = lambda j: (shift[j] if j < len(shift) else 0) * cell

        if self.show_halo:                                        # the halo behind the head: faint, swelling with the mouth
            halo = _halo_pixmap(self, cell)
            hs = halo.width() * self.halo_scale
            p.save()
            p.setOpacity(p.opacity() * (0.6 + 0.4 * drive))
            p.drawPixmap(QRectF(cx - hs / 2, cy - hs / 2, hs, hs), halo, QRectF(halo.rect()))
            p.restore()

        sx, sy = x0 - self._margin, y0 - self._margin
        groups = []                                                # horizontal runs of equal glitch shift: [first row, end row, shift]
        for j in range(self.h):
            o = off(j) if shift else 0
            if groups and groups[-1][2] == o:
                groups[-1][1] = j + 1
            else:
                groups.append([j, j + 1, o])

        def draw_static() -> None:
            if len(groups) == 1 and groups[0][2] == 0:
                p.drawPixmap(QPointF(sx, sy), self._static)
                return
            for j0, j1, o in groups:
                top = 0 if j0 == 0 else self._margin + j0 * cell
                bot = self._static.height() if j1 >= self.h else self._margin + j1 * cell
                p.drawPixmap(QPointF(sx + o, sy + top), self._static, QRectF(0, top, self._static.width(), bot - top))

        draw_static()
        g = self.grow_amount(pos)
        lift = BRIGHT_LIFT * g                                     # the whole head brightens as the mouth opens wider (not with the mic); none at talk_1
        if lift > 0.01:
            p.save()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.setOpacity(p.opacity() * lift)
            draw_static()
            p.restore()

        p.setPen(Qt.PenStyle.NoPen)
        gscale = 1.0 + 0.35 * drive                                # the eyes flare as the mouth opens
        for (ej, ei) in self.eye_centres:
            gs = self._glow.width() * gscale
            cxp, cyp = x0 + (ei + 0.5) * cell + off(int(ej)), y0 + (ej + 0.5) * cell
            p.drawPixmap(QRectF(cxp - gs / 2, cyp - gs / 2, gs, gs), self._glow, QRectF(self._glow.rect()))

        gap = max(1.0, cell * 0.18)
        cur = self.blend(pos)
        for j, i in self.dynamic:                                  # the pixels that change between frames
            r_, g_, b_, a_ = (int(round(v)) for v in cur[j, i])
            if a_ < 8:
                continue
            r_, g_, b_ = (min(255, int(round(c * (1.0 + lift)))) for c in (r_, g_, b_))     # the mouth lifts with the rest
            r = QRectF(x0 + i * cell + off(j) + gap / 2, y0 + j * cell + gap / 2, cell - gap, cell - gap)
            if max(r_, g_, b_) / 255.0 > 0.6:
                p.setBrush(QColor(255, 20, 25, 70))
                p.drawRoundedRect(r.adjusted(-1.3, -1.3, 1.3, 1.3), cell * 0.4, cell * 0.4)
            p.setBrush(QColor(r_, g_, b_, a_))
            p.drawRoundedRect(r, cell * 0.28, cell * 0.28)
        grown = self.grown(cur, g)                                 # beyond the drawn frames: the mouth keeps growing with the voice
        if grown is not None:
            gy, gx, colours = grown
            for j, i, c in zip(gy.tolist(), gx.tolist(), colours.tolist()):
                col = [int(round(min(255.0, c[n] * (1.0 + lift)))) for n in range(3)]
                r = QRectF(x0 + i * cell + off(j) + gap / 2, y0 + j * cell + gap / 2, cell - gap, cell - gap)
                p.setBrush(QColor(col[0], col[1], col[2], 255))
                p.drawRoundedRect(r, cell * 0.28, cell * 0.28)
        p.setOpacity(base_opacity)


def load_sprite_frames(directory: str = HEAD_FRAMES_DIR) -> List[np.ndarray]:
    """The frames present in `directory` (idle, talk_1, talk_2 ...), in order; stops at the first missing one."""
    frames = []
    for name in FRAME_FILES:
        path = os.path.join(directory, name + ".png")
        if not os.path.exists(path):
            break
        img = QImage(path)
        if img.isNull():
            raise ValueError(f"cannot read the head frame {path}")
        frames.append(_qimage_to_rgba(img))
    return frames


HEAD_KINDS = ("gap", "stages", "sprite")
DEFAULT_HEAD = "gap"


def head_kind(settings: dict) -> str:
    """settings.ultron_head: which head the dashboard draws. "gap" (default): the procedural head whose dark centre gap opens as
    he talks (ui/clUltronHeadGap.py); "stages": the procedural head with a small idle mouth that opens in stages; "sprite": the
    hand-drawn frames in assets/themes/ultron/head/. An unknown value falls back to the default."""
    forced = os.environ.get("JARVIS_ULTRON_HEAD")                  # handy for trying the heads side by side (and for the tests)
    if forced in HEAD_KINDS:
        return forced
    kind = (settings or {}).get("ultron_head", DEFAULT_HEAD)
    return kind if kind in HEAD_KINDS else DEFAULT_HEAD


def load_head(directory: str = HEAD_FRAMES_DIR, kind: str = "sprite"):
    """The head the dashboard draws, see head_kind. "sprite" falls back to the stages head if its frames are missing."""
    if kind == "gap":
        from ui.clUltronHeadGap import GapHead
        return GapHead()
    if kind == "stages":
        return UltronHead()
    try:
        frames = load_sprite_frames(directory)
        if len(frames) >= 2:
            return SpriteHead(frames)
    except Exception:
        pass
    return UltronHead()
