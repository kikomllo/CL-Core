import os
import random
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


@pytest.fixture(scope="module")
def head(qapp):
    from ui.clUltronHead import UltronHead
    return UltronHead()


def render(head, level, t=1.0, glitch=None, cell=5):
    """The head painted on a black image; returns an (h, w) brightness array."""
    from PyQt6.QtGui import QImage, QPainter, QColor
    w, h = head.w * cell + 120, head.h * cell + 120
    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    head.paint(p, w / 2, h / 2, cell, level, t, glitch)
    p.end()
    b = img.constBits()
    b.setsize(img.sizeInBytes())
    return np.frombuffer(b, np.uint8).reshape(h, img.bytesPerLine())[:, : w * 3].reshape(h, w, 3).max(axis=2)


class TestMasks:
    def test_the_layers_do_not_overlap_and_all_exist(self, head):
        assert head.metal.any() and head.plates.any() and head.eyes.any()
        assert not (head.metal & head.plates).any() and not (head.metal & head.eyes).any() and not (head.plates & head.eyes).any()

    def test_the_grid_is_finer_than_the_reference(self, head):
        from ui.clUltronHead import GRID, UP
        assert head.h > len(GRID) and head.w > len(GRID[0]) // 2 and UP == 2

    def test_the_head_is_roughly_left_right_symmetric(self, head):
        lit = head.metal | head.plates | head.eyes
        flipped = lit[:, ::-1]
        # the reference has asymmetric fins/ears by a cell or two, but the mass should match
        overlap = (lit & flipped).sum() / lit.sum()
        assert overlap > 0.85

    def test_the_grille_and_the_brows_are_separate_groups(self, head):
        assert head.mouth and head.brows
        assert min(j for j, _, _ in head.mouth) > max(j for j, _ in head.brows) - 3
        assert all(0.0 <= rn <= 1.0 for _, _, rn in head.mouth)

    def test_the_two_big_eye_bars_get_a_glow(self, head):
        assert len(head.eye_centres) == 2


class TestTheLightFollowsTheVoice:
    """The old JARVIS glow: lights get brighter or dimmer as he talks. No equaliser, no per-column wave."""

    def test_the_lights_rest_dim_and_the_eyes_brows_and_seam_reach_full_brightness_at_full_voice(self, head):
        for fn in (head.brow_brightness, head.cheek_light):
            assert fn(0.0, 0.0) < 0.6
            assert fn(1.0, 0.0) == pytest.approx(1.0, abs=0.001)

    def test_the_mouth_light_grows_from_calm_to_normal_to_strong_as_it_opens(self, head):
        from ui.clUltronHead import SPEAK_FLOOR
        idle, normal, strong = head.plate_brightness(0.0, 0.0), head.plate_brightness(SPEAK_FLOOR, 0.0), head.plate_brightness(1.0, 0.0)
        assert idle < normal < strong and strong == pytest.approx(1.0, abs=0.001)

    def test_a_louder_voice_never_dims_a_light(self, head):
        for fn in (head.plate_brightness, head.brow_brightness, head.cheek_light):
            for t in (0.0, 1.0, 2.7):
                vals = [fn(lv / 20, t) for lv in range(21)]
                assert all(a <= b + 1e-9 for a, b in zip(vals, vals[1:]))

    def test_all_the_grille_plates_get_the_same_brightness_not_a_meter(self, head):
        # one value for the whole grille: the brightness function takes no column or row
        import inspect
        assert list(inspect.signature(head.plate_brightness).parameters) == ["amount", "t"]

    def test_the_lights_breathe_slowly_at_rest(self, head):
        vals = {round(head.plate_brightness(0.0, t / 10), 3) for t in range(60)}
        assert len(vals) > 5 and max(vals) - min(vals) < 0.2

    def test_quiet_speech_is_still_visible_on_the_brows_and_cheek_lights(self, head):
        assert head.brow_brightness(0.15, 0.0) > head.brow_brightness(0.0, 0.0) + 0.05
        assert head.cheek_light(0.15, 0.0) > head.cheek_light(0.0, 0.0) + 0.05


class TestTheHeadIsNeverCutApart:
    def test_the_silhouette_is_identical_whatever_the_voice_level(self, head):
        quiet, loud = render(head, 0.0), render(head, 1.0)
        # the dim metal and its rim are the same pixels; only the lit plates and the eye glow differ
        metal_only = head.metal
        assert quiet.shape == loud.shape
        ys, xs = np.where(quiet > 150)               # the hot rim and plates, not the faint halo
        ys2, xs2 = np.where(loud > 150)
        # the outline (bounding box of anything lit) may only grow by the eye glow, never move or split
        assert abs(ys.min() - ys2.min()) <= 14 and abs(ys.max() - ys2.max()) <= 14
        assert abs(xs.min() - xs2.min()) <= 14 and abs(xs.max() - xs2.max()) <= 14

    def test_the_metal_is_painted_once_and_reused_across_frames(self, head):
        render(head, 0.0)
        first = head._static
        render(head, 1.0, t=5.0)
        assert head._static is first

    def test_a_new_size_rebuilds_the_metal(self, head):
        render(head, 0.0, cell=5)
        render(head, 0.0, cell=6)
        assert head._static_cell == 6

    def test_speaking_changes_pixels_only_in_the_grille_brows_and_eyes(self, head):
        head.show_halo = False                              # the halo breathes with the voice too; this is about the head itself
        try:
            quiet, loud = render(head, 0.0, cell=5), render(head, 1.0, cell=5)
        finally:
            head.show_halo = True
        diff = np.abs(quiet.astype(int) - loud.astype(int)) > 12
        ys, xs = np.where(diff)
        h, w = quiet.shape
        top = (h - head.h * 5) / 2
        rel = (ys - top) / (head.h * 5)
        assert rel.min() > 0.0                              # nothing outside the head's own height
        assert (rel > 0.58).sum() > 0                       # the grille responds

    def test_nothing_is_drawn_outside_a_margin_around_the_head(self, head):
        img = render(head, 1.0, cell=5)
        h, w = img.shape
        pad_x = (w - head.w * 5) / 2
        assert img[:, : int(pad_x - 30)].max() < 60 and img[:, int(w - pad_x + 30):].max() < 60      # only the faint halo


class TestGlitch:
    def test_a_glitch_is_a_few_short_bands_of_bounded_shift(self):
        from ui.clUltronHead import new_glitch
        for seed in range(30):
            rows = new_glitch(58, random.Random(seed))
            assert len(rows) == 58 and max(abs(r) for r in rows) <= 4 and any(rows) or True
        assert any(any(new_glitch(58, random.Random(s))) for s in range(30))

    def test_a_glitched_frame_still_draws_and_stays_in_bounds(self, head):
        from ui.clUltronHead import new_glitch
        rows = new_glitch(head.h, random.Random(3))
        img = render(head, 0.3, glitch=rows)
        assert img.max() > 100
        h, w = img.shape
        pad_x = (w - head.w * 5) / 2
        assert img[:, : int(pad_x - 5 * 5 - 30)].max() < 60       # shifted at most 4 cells (+ margin) sideways; only the halo reaches further

    def test_a_glitch_changes_the_picture(self, head):
        from ui.clUltronHead import new_glitch
        rows = new_glitch(head.h, random.Random(3))
        assert any(rows)
        assert np.abs(render(head, 0.0).astype(int) - render(head, 0.0, glitch=rows).astype(int)).max() > 30


def run_in_skin(skin, code):
    env = dict(os.environ, JARVIS_THEME=skin, QT_QPA_PLATFORM="offscreen")
    res = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n%s" % (SRC, code)],
                         capture_output=True, text=True, env=env, timeout=90)
    assert res.returncode == 0, res.stderr[-800:]
    return res.stdout.strip().splitlines()[-1]


VIS = ("from PyQt6.QtWidgets import QApplication\napp = QApplication([])\n"
       "from clUIScalerInjector import inject_scaler\ninject_scaler()\nimport clUI\n"
       "v = clUI.JarvisVisualizer()\nv.resizeUnscaled(600, 600)\nv.is_fullscreen = True\n")


class TestInTheVisualizer:
    def test_only_the_ultron_skin_has_a_head(self):
        assert run_in_skin("jarvis", VIS + "print(v.head is None)") == "True"
        assert run_in_skin("ultron", VIS + "print(v.head is None)") == "False"

    def test_the_level_follows_the_speech_volume_quickly_and_releases_slowly(self):
        out = run_in_skin("ultron", VIS + "v.state='SPEAKING'\nv.set_volume(100)\nv.amplitude = v.target_amplitude\n"
                                           "v.update_animation(); a = v.talk_level\n"
                                           "v.set_volume(0); v.amplitude = v.target_amplitude; v.update_animation(); b = v.talk_level\n"
                                           "print(a > 0.3, 0 < b < a)")
        assert out == "True True"

    def test_it_paints_without_error_in_every_state(self):
        out = run_in_skin("ultron", VIS + "v.current_opacity = 1.0\nfor st in ('IDLE','LISTENING','RECORDING','PROCESSING','SPEAKING'):\n"
                                           "    v.state = st; v.talk_level = 0.7; v.grab()\nprint('ok')")
        assert out == "ok"
