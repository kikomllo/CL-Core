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
    from ui.clUltronHead import load_head, SpriteHead
    h = load_head()
    assert isinstance(h, SpriteHead)
    return h


def lit_count(head, pos):
    """The pixels that are lit mouth at frame position `pos`, with the stretch applied."""
    from ui.clUltronHead import MOUTH_RED, OUTLINE_RED
    cur = head.blend(pos)
    base = (cur[..., 0] >= MOUTH_RED) & (cur[..., 0] < OUTLINE_RED) & (cur[..., 3] > 0) & head._mouth_zone
    res = head.grown(cur, head.grow_amount(pos))
    if res is None:
        return int(base.sum()), base
    jj, ii, cols = res
    lit = base.copy()
    for j, i, c in zip(jj, ii, cols):
        lit[j, i] = c[0] >= MOUTH_RED
    return int(lit.sum()), lit


class TestGrowthAmount:
    def test_nothing_up_to_the_first_talking_frame_then_rising_to_one(self, head):
        assert head.grow_amount(0.0) == 0.0 and head.grow_amount(1.0) == 0.0
        vals = [head.grow_amount(1.0 + k / 10 * (head.n - 2)) for k in range(11)]
        assert all(a <= b for a, b in zip(vals, vals[1:])) and vals[-1] == pytest.approx(1.0)

    def test_two_frames_never_grow(self, qapp):
        from ui.clUltronHead import SpriteHead
        f = np.zeros((20, 10, 4), np.uint8)
        f[2:18, 2:8] = (70, 0, 0, 255)
        g = f.copy()
        g[10:14, 3:7] = (110, 20, 20, 255)
        assert SpriteHead([f, g]).grow_amount(1.0) == 0.0


class TestTheFirstTalkingFrameStaysAsDrawn:
    def test_no_growth_pixels_at_idle_or_at_the_first_talking_frame(self, head):
        for pos in (0.0, 1.0):
            assert head.grown(head.blend(pos), head.grow_amount(pos)) is None

    def test_painted_it_is_the_drawn_sprite(self, head):
        from PyQt6.QtGui import QImage, QPainter, QColor
        cell = 8
        head._last = None
        head.show_halo = False
        w, h = head.w * cell + 40, head.h * cell + 40
        img = QImage(w, h, QImage.Format.Format_RGB888)
        img.fill(QColor(0, 0, 0))
        p = QPainter(img)
        head.paint(p, w / 2, h / 2, cell, 0.0, 1.0, None, 1.0, True)      # quietest speech = talking frame 1
        p.end()
        head.show_halo = True
        b = img.constBits()
        b.setsize(img.sizeInBytes())
        arr = np.frombuffer(b, np.uint8).reshape(h, img.bytesPerLine())[:, : w * 3].reshape(h, w, 3).copy()
        x0, y0 = (w - head.w * cell) / 2, (h - head.h * cell) / 2
        frame1 = head.frames[1]
        for j, i in head.dynamic[::6]:
            got = arr[int(y0 + (j + 0.5) * cell), int(x0 + (i + 0.5) * cell)].astype(int)
            assert np.abs(got - frame1[j, i, :3].astype(int)).max() <= 3


class TestTheMouthGrowsBeyondTheFrames:
    def test_the_stretch_only_ever_adds_to_the_drawn_mouth_more_the_louder_it_is(self, head):
        from ui.clUltronHead import MOUTH_RED, OUTLINE_RED
        extra = []
        for k in range(11):
            pos = 1.0 + k / 10 * (head.n - 2)
            cur = head.blend(pos)
            drawn = int(((cur[..., 0] >= MOUTH_RED) & (cur[..., 0] < OUTLINE_RED) & (cur[..., 3] > 0) & head._mouth_zone).sum())
            extra.append(lit_count(head, pos)[0] - drawn)
        assert extra[0] == 0 and extra[-1] > 20                              # nothing at the first frame, a real growth at the loudest
        assert sum(extra[-4:]) / 4 > sum(extra[:4]) / 4 + 30                 # and it keeps growing with the voice (resampling a stretched
        assert min(extra[1:]) >= 0                                           # edge may dip a few pixels between neighbouring steps)

    def test_it_is_the_same_shape_stretched_not_a_solid_block(self, head):
        n, lit = lit_count(head, head.n - 1)
        ys, xs = np.nonzero(lit)
        box = (ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1)
        assert n / box < 0.85                                                # a dilated blob would fill its bounding box
        n0, lit0 = lit_count(head, 1.0)
        ys0, xs0 = np.nonzero(lit0)
        box0 = (ys0.max() - ys0.min() + 1) * (xs0.max() - xs0.min() + 1)
        assert abs(n / box - n0 / box0) < 0.2                                # the fill ratio stays about the same: same shape

    def test_it_grows_downwards_and_sideways_never_up_into_the_nose(self, head):
        _, small = lit_count(head, 1.0)
        _, big = lit_count(head, head.n - 1)
        top_small, top_big = np.nonzero(small)[0].min(), np.nonzero(big)[0].min()
        assert top_big >= top_small - 1
        assert np.nonzero(big)[0].max() > np.nonzero(small)[0].max()
        assert np.nonzero(big)[1].min() <= np.nonzero(small)[1].min() and np.nonzero(big)[1].max() >= np.nonzero(small)[1].max()

    def test_the_outline_the_eyes_and_the_cheek_plates_are_never_touched(self, head):
        res = head.grown(head.blend(head.n - 1), 1.0)
        jj, ii, _ = res
        first = head.frames[0]
        assert (first[jj, ii, 0] < 140).all()                                # nothing that was outline, eye or nose dot
        assert head.grow_region[jj, ii].sum() + head._mouth_zone[jj, ii].sum() > 0
        assert ii.min() >= 3 and ii.max() <= head.w - 4                      # the side plates beside the mouth stay clear
        padded = np.pad(head.lit, 1)
        interior = head.lit & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
        assert interior[jj, ii].all()                                        # nothing next to the outside

    def test_the_stretch_stays_inside_the_head_at_every_level(self, head):
        for k in range(11):
            pos = 1.0 + k / 10 * (head.n - 2)
            res = head.grown(head.blend(pos), head.grow_amount(pos))
            if res is not None:
                jj, ii, cols = res
                assert head.lit[jj, ii].all() and (cols >= 0).all() and (cols <= 255.0).all()

    def test_a_missing_mouth_never_crashes(self, qapp):
        from ui.clUltronHead import SpriteHead
        flat = np.zeros((20, 10, 4), np.uint8)
        flat[2:18, 2:8] = (60, 0, 0, 255)
        head = SpriteHead([flat, flat.copy(), flat.copy()])
        assert head.grown(head.blend(2.0), 1.0) is None
