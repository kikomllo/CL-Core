import os
import subprocess
import sys

import numpy as np
import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC)


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def frame(fill=(70, 0, 0), block=None, h=8, w=6, eyes=False):
    """A tiny sprite: a head of `fill` inside pure black, an optional lit block, optional eye pixels."""
    f = np.zeros((h, w, 4), np.uint8)
    f[1:h - 1, 1:w - 1] = (*fill, 255)
    f[0, :, 3] = f[-1, :, 3] = 255                            # opaque pure black outside (an editor export)
    if block:
        (j0, j1, i0, i1), colour = block
        f[j0:j1, i0:i1] = (*colour, 255)
    if eyes:
        f[2, 2] = (255, 190, 180, 255)
    return f


@pytest.fixture
def small(qapp):
    from ui.clUltronHead import SpriteHead
    t = {"now": 0.0}
    frames = [frame(), frame(block=((4, 5, 1, 3), (120, 20, 20))), frame(block=((4, 6, 1, 4), (200, 40, 40)))]
    head = SpriteHead(frames, clock=lambda: t["now"])
    head.t = t
    return head


class TestTheShippedFrames:
    def test_the_hand_drawn_frames_are_there_in_order_and_the_right_size(self):
        from ui.clUltronHead import load_sprite_frames
        frames = load_sprite_frames()
        assert len(frames) >= 4
        assert all(f.shape == (58, 42, 4) for f in frames)

    def test_the_dashboard_loads_them_as_the_head(self, qapp):
        from ui.clUltronHead import load_head, SpriteHead
        head = load_head()
        assert isinstance(head, SpriteHead) and (head.w, head.h) == (42, 58)

    def test_the_outline_eyes_and_brows_are_identical_in_every_frame_only_the_mouth_area_moves(self, qapp):
        from ui.clUltronHead import load_head
        head = load_head()
        rows = [j for j, _ in head.dynamic]
        assert min(rows) >= 24 and max(rows) <= 50          # the lower face only: nothing above the nose
        assert 60 < len(head.dynamic) < 400

    def test_a_missing_folder_falls_back_to_the_procedural_head(self, qapp, tmp_path):
        from ui.clUltronHead import load_head, UltronHead
        assert isinstance(load_head(str(tmp_path)), UltronHead)

    def test_one_frame_is_not_enough_so_it_falls_back(self, qapp, tmp_path):
        from PyQt6.QtGui import QImage, QColor
        from ui.clUltronHead import load_head, UltronHead
        img = QImage(4, 4, QImage.Format.Format_ARGB32)
        img.fill(QColor(90, 0, 0))
        img.save(str(tmp_path / "idle.png"))
        assert isinstance(load_head(str(tmp_path)), UltronHead)


class TestFrameChoice:
    def test_not_speaking_is_always_the_idle_frame(self, small):
        assert all(small.frame_pos(lv / 10, False) == 0.0 for lv in range(11))

    def test_the_moment_he_speaks_even_quietly_it_is_the_first_talking_frame(self, small):
        assert small.frame_pos(0.0, True) == 1.0

    def test_louder_voices_move_on_through_the_frames_up_to_the_last(self, small):
        vals = [small.frame_pos(lv / 20, True) for lv in range(21)]
        assert all(a <= b for a, b in zip(vals, vals[1:])) and vals[-1] == pytest.approx(small.n - 1)

    def test_two_frames_means_idle_and_talking(self, qapp):
        from ui.clUltronHead import SpriteHead
        head = SpriteHead([frame(), frame(block=((4, 5, 1, 3), (120, 20, 20)))])
        assert head.frame_pos(0.0, True) == head.frame_pos(1.0, True) == 1.0

    def test_blend_crossfades_the_two_frames_around_the_position(self, small):
        a, b = small.frames[1].astype(np.float32), small.frames[2].astype(np.float32)
        assert np.allclose(small.blend(0.0), small.frames[0])
        assert np.allclose(small.blend(1.5), (a + b) / 2)
        assert np.allclose(small.blend(99.0), small.frames[-1]) and np.allclose(small.blend(-3.0), small.frames[0])


class TestEasing:
    def test_the_first_call_jumps_to_the_target(self, small):
        assert small.step(2.0, 5.0) == 2.0

    def test_it_opens_faster_than_it_closes(self, small):
        small.step(0.0, 0.0)
        up = small.step(2.0, 0.05) - 0.0
        small._pos, small._last = 2.0, 0.0
        down = 2.0 - small.step(0.0, 0.05)
        assert up > down > 0

    def test_it_never_overshoots_and_eventually_arrives(self, small):
        small.step(0.0, 0.0)
        for n in range(1, 200):
            pos = small.step(2.0, n * 0.02)
            assert 0.0 <= pos <= 2.0
        assert pos == pytest.approx(2.0, abs=0.01)

    def test_the_speed_does_not_depend_on_the_frame_rate(self, small):
        small.step(0.0, 0.0)
        for n in range(1, 11):
            small.step(2.0, n * 0.01)
        fine = small._pos
        small._pos, small._last = 0.0, 0.0
        small.step(2.0, 0.10)
        assert fine == pytest.approx(small._pos, abs=0.03)


class TestWhatIsDrawn:
    def test_black_and_transparent_pixels_are_outside_the_head(self, small):
        assert not small.lit[0].any() and not small.lit[-1].any()          # the pure-black border
        assert small.lit[1:-1, 1:-1].all()

    def test_only_pixels_that_differ_between_frames_are_dynamic(self, small):
        assert small.dynamic and all(4 <= j < 6 and 1 <= i < 4 for j, i in small.dynamic)
        assert (1, 1) not in small.dynamic

    def test_the_bright_pink_pixels_are_the_eyes(self, qapp):
        from ui.clUltronHead import SpriteHead
        head = SpriteHead([frame(eyes=True), frame(eyes=True, block=((4, 5, 1, 3), (120, 20, 20)))])
        assert head.eye_cells == [(2, 2)]

    def test_frames_of_different_sizes_are_refused(self, qapp):
        from ui.clUltronHead import SpriteHead
        with pytest.raises(ValueError):
            SpriteHead([frame(), frame(h=9)])

    def paint(self, head, level, speaking=True, glitch=None, flicker=1.0, cell=6):
        from PyQt6.QtGui import QImage, QPainter, QColor
        head._last = None
        head.show_halo = False
        w, h = head.w * cell + 60, head.h * cell + 60
        img = QImage(w, h, QImage.Format.Format_RGB888)
        img.fill(QColor(0, 0, 0))
        p = QPainter(img)
        head.paint(p, w / 2, h / 2, cell, level, 1.0, glitch, flicker, speaking)
        p.end()
        b = img.constBits()
        b.setsize(img.sizeInBytes())
        arr = np.frombuffer(b, np.uint8).reshape(h, img.bytesPerLine())[:, : w * 3].reshape(h, w, 3).copy()
        x0, y0 = (w - head.w * cell) / 2, (h - head.h * cell) / 2
        return arr, (lambda j, i: arr[int(y0 + (j + 0.5) * cell), int(x0 + (i + 0.5) * cell)].astype(int))

    def test_the_unchanging_pixels_stay_where_they_are_when_he_talks(self, small):
        from ui.clUltronHead import BRIGHT_LIFT
        _, quiet = self.paint(small, 0.0, speaking=False)
        _, loud = self.paint(small, 1.0)
        for j, i in ((1, 1), (1, 4), (6, 1), (6, 4)):
            assert quiet(j, i).max() > 0 and loud(j, i).max() > 0                  # still drawn in the same place
            assert loud(j, i)[0] <= quiet(j, i)[0] * (1 + BRIGHT_LIFT) + 6          # only brightened, by at most the lift

    def test_the_mouth_pixels_take_the_colours_of_the_frames(self, small):
        _, idle = self.paint(small, 0.0, speaking=False)
        _, loud = self.paint(small, 1.0)
        assert tuple(idle(5, 1)) == (70, 0, 0) or idle(5, 1).max() < 90
        assert loud(5, 1)[0] > 150                                         # the last frame's brighter block

    def test_the_border_of_the_canvas_is_never_painted(self, small):
        arr, _ = self.paint(small, 0.5)
        h, w = arr.shape[:2]
        cell, x0, y0 = 6, (w - small.w * 6) / 2, (h - small.h * 6) / 2
        assert arr[: int(y0 + 3)].max() == 0 and arr[int(h - y0 - 3):].max() == 0

    def test_the_static_layer_is_painted_once_and_reused(self, small):
        self.paint(small, 0.0)
        first = small._static
        self.paint(small, 1.0)
        assert small._static is first
        self.paint(small, 1.0, cell=7)
        assert small._static is not first

    def test_a_flicker_dims_the_head_and_a_glitch_shifts_rows(self, small):
        full, _ = self.paint(small, 0.0, speaking=False)
        dim, _ = self.paint(small, 0.0, speaking=False, flicker=0.3)
        assert dim.max() < full.max() * 0.6
        glitched, _ = self.paint(small, 0.0, speaking=False, glitch=[2] * small.h)
        assert np.abs(glitched.astype(int) - full.astype(int)).max() > 30


class TestBrighterAsItOpens:
    """More volume = a wider mouth = a brighter head, all rising together."""

    paint = TestWhatIsDrawn.paint

    def total(self, head, level, speaking=True):
        arr, _ = self.paint(head, level, speaking=speaking)
        return int(arr.astype(np.int64).sum())

    def test_the_drive_follows_how_open_the_mouth_is(self, small):
        vals = [small.drive(p / 4, 0.0) for p in range(0, 9)]
        assert vals[0] == 0.0 and vals[-1] == pytest.approx(1.0) and all(a <= b for a, b in zip(vals, vals[1:]))

    def test_the_users_own_voice_only_moves_the_glow_a_little_while_the_mouth_is_idle(self, small):
        assert 0.0 < small.drive(0.0, 1.0) <= 0.5 and small.drive(0.0, 0.0) == 0.0

    def test_the_whole_picture_gets_brighter_the_louder_it_is(self, small):
        totals = [self.total(small, lv / 4) for lv in range(5)]
        assert all(a < b for a, b in zip(totals, totals[1:]))
        assert totals[-1] > 1.1 * self.total(small, 0.0, speaking=False)

    def test_the_mouth_pixels_get_brighter_as_the_mouth_opens(self, small):
        _, idle = self.paint(small, 0.0, speaking=False)
        _, quiet = self.paint(small, 0.0)
        _, loud = self.paint(small, 1.0)
        assert idle(4, 1)[0] < quiet(4, 1)[0] < loud(4, 1)[0]

    def test_a_still_pixel_also_lifts_but_never_past_white(self, small):
        _, idle = self.paint(small, 0.0, speaking=False)
        _, loud = self.paint(small, 1.0)
        assert idle(1, 1)[0] < loud(1, 1)[0] <= 255

    def test_the_lift_is_off_at_idle(self, small):
        a, _ = self.paint(small, 0.0, speaking=False)
        b, _ = self.paint(small, 0.9, speaking=False)               # a loud mic changes nothing but the faint glow
        assert abs(int(a.astype(np.int64).sum()) - int(b.astype(np.int64).sum())) < 0.02 * int(a.astype(np.int64).sum())


def run_in_skin(code):
    env = dict(os.environ, JARVIS_THEME="ultron", QT_QPA_PLATFORM="offscreen", JARVIS_ULTRON_HEAD="sprite")
    res = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n%s" % (SRC, code)],
                         capture_output=True, text=True, env=env, timeout=90)
    assert res.returncode == 0, res.stderr[-800:]
    return res.stdout.strip().splitlines()[-1]


class TestInTheVisualizer:
    VIS = ("from PyQt6.QtWidgets import QApplication\napp = QApplication([])\n"
           "from clUIScalerInjector import inject_scaler\ninject_scaler()\nimport clUI\n"
           "v = clUI.JarvisVisualizer()\nv.resizeUnscaled(600, 600)\nv.is_fullscreen = True\nv.current_opacity = 1.0\n")

    def test_the_visualizer_uses_the_sprite_head_and_paints_in_every_state(self):
        out = run_in_skin(self.VIS + "print(type(v.head).__name__)\n"
                                      "for st in ('IDLE','LISTENING','RECORDING','PROCESSING','SPEAKING'):\n"
                                      "    v.state = st; v.talk_level = 0.7; v.grab()\nprint('ok')")
        assert out == "ok"
        assert run_in_skin(self.VIS + "print(type(v.head).__name__)") == "SpriteHead"


class TestTheWholeHeadPulsesWithTheVoice:
    def test_at_rest_it_only_breathes(self):
        from ui.clUltronHead import pulse_scale, BREATH
        vals = [pulse_scale(0.0, False, t / 5) for t in range(80)]
        assert all(abs(v - 1.0) <= BREATH + 1e-9 for v in vals) and max(vals) - min(vals) > BREATH

    def test_the_mic_does_not_make_it_pulse_while_he_is_silent(self):
        from ui.clUltronHead import pulse_scale
        assert pulse_scale(0.0, False, 1.0) == pulse_scale(1.0, False, 1.0)

    def test_speaking_it_is_smaller_when_quiet_and_bigger_when_loud(self):
        from ui.clUltronHead import pulse_scale, PULSE_QUIET, PULSE_LOUD
        quiet, loud = pulse_scale(0.0, True, 0.0), pulse_scale(1.0, True, 0.0)
        assert quiet == pytest.approx(PULSE_QUIET) and loud == pytest.approx(PULSE_LOUD)
        assert quiet < 1.0 < loud

    def test_a_louder_voice_never_makes_it_smaller(self):
        from ui.clUltronHead import pulse_scale
        vals = [pulse_scale(lv / 20, True, 3.0) for lv in range(21)]
        assert all(a <= b for a, b in zip(vals, vals[1:]))

    def test_the_swing_is_big_enough_to_see(self):
        from ui.clUltronHead import pulse_scale
        assert pulse_scale(1.0, True, 0.0) / pulse_scale(0.0, True, 0.0) > 1.15

    PULSE_PROBE = r'''
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
app = QApplication([])
from clUIScalerInjector import inject_scaler
inject_scaler()
from clUIScaler import UIScaler
UIScaler.scale = lambda self, v: int(round(v))      # 1080p scale
import clUI
from PyQt6.QtGui import QImage
import numpy as np
v = clUI.JarvisVisualizer()
v.resizeUnscaled(640, 560)
v.is_fullscreen = True
v.current_opacity = 1.0
v.setStyleSheet('background: #000000;')
v.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


def height(level, state):
    v.state = state
    v.talk_level = level
    v.current_scale = 1.0
    v.time_offset = 1.0
    v.glitch_rows = None
    v.flicker = 1.0
    im = v.grab().toImage().convertToFormat(QImage.Format.Format_RGB888)
    ptr = im.constBits()
    ptr.setsize(im.sizeInBytes())
    a = np.frombuffer(ptr, np.uint8).reshape(im.height(), im.bytesPerLine())[:, :im.width() * 3].reshape(im.height(), im.width(), 3)
    m = a.max(axis=2) > 150
    m[:40, :] = False
    ys = np.where(m)[0]
    return int(ys.max() - ys.min())


print(height(0.0, 'SPEAKING'), height(1.0, 'SPEAKING'), height(0.0, 'IDLE'))
'''

    def test_in_the_dashboard_the_head_is_drawn_bigger_when_the_voice_is_louder(self):
        quiet, loud, idle = (int(x) for x in run_in_skin(self.PULSE_PROBE).split())
        assert quiet < idle < loud and loud > 1.12 * quiet
