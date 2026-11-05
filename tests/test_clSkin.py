import colorsys
import itertools
import os
import subprocess
import sys

import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC)

import clSkin
from clSkin import recolor_css, ultron_rgb


def hsv(rgb):
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    return h * 360.0, s, v


class TestUltronMapping:
    def test_the_orange_accents_become_red(self):
        for orange in [(255, 170, 0), (255, 140, 0), (230, 92, 0), (255, 119, 0), (255, 204, 0)]:
            h, s, v = hsv(ultron_rgb(*orange))
            assert (h >= 350 or h <= 12) and s >= 0.7, (orange, h, s)

    def test_the_yellower_the_accent_the_lighter_the_red(self):
        deep = ultron_rgb(255, 120, 0)
        hot = ultron_rgb(255, 204, 0)
        assert hsv(hot)[1] < hsv(deep)[1]

    def test_cream_and_warm_grey_text_become_steel(self):
        for cream in [(255, 230, 204), (168, 154, 140), (138, 122, 102)]:
            r, g, b = ultron_rgb(*cream)
            assert b > r and 190 <= hsv((r, g, b))[0] <= 240            # cool, never warm

    def test_dark_brown_backgrounds_become_near_black(self):
        for brown in [(20, 10, 0), (25, 12, 3), (35, 18, 5), (10, 5, 0)]:
            out = ultron_rgb(*brown)
            assert max(out) < 40 and out[2] >= out[0]                    # dark and a touch cold

    def test_other_hues_and_neutrals_pass_through(self):
        for keep in [(255, 255, 255), (0, 0, 0), (120, 120, 120), (30, 215, 96), (88, 101, 242), (255, 27, 45), (136, 255, 136)]:
            assert ultron_rgb(*keep) == keep

    def test_it_is_idempotent_over_the_whole_colour_cube(self):
        for rgb in itertools.product(range(0, 256, 7), repeat=3):
            once = ultron_rgb(*rgb)
            assert ultron_rgb(*once) == once, rgb

    def test_channels_stay_in_range(self):
        for rgb in itertools.product(range(0, 256, 15), repeat=3):
            assert all(0 <= c <= 255 for c in ultron_rgb(*rgb))


class TestRecolorCss:
    def test_the_jarvis_skin_changes_nothing(self):
        css = "QPushButton { color: #ffaa00; background: rgba(255, 150, 0, 80); }"
        assert recolor_css(css, "jarvis") == css

    def test_hex_and_rgba_are_recoloured(self):
        out = recolor_css("a { color: #ffaa00; background: rgba(255, 150, 0, 80); border: 1px solid rgba(255,170,0,0.3); }", "ultron")
        assert "#ffaa00" not in out and "255, 150, 0" not in out and "255, 170, 0" not in out

    def test_an_accent_gets_a_more_opaque_alpha_because_red_is_dimmer_than_amber(self):
        out = recolor_css("a { background: rgba(255, 150, 0, 80); border: 1px solid rgba(255,170,0,0.3); c: rgba(255,150,0,200); "
                          "d: rgba(255,150,0,50%); }", "ultron")
        assert ", 144)" in out and ", 0.54)" in out and ", 255)" in out and ", 90%)" in out       # 80*1.8, 0.3*1.8, capped, 50%*1.8

    def test_other_colours_keep_their_alpha(self):
        out = recolor_css("a { background: rgba(20, 10, 0, 180); color: rgba(255, 230, 204, 180); x: rgba(255,255,255,0.12); }", "ultron")
        assert out.count(", 180)") == 2 and ", 0.12)" in out

    def test_an_opaque_accent_is_not_dimmed_or_exceeded(self):
        assert recolor_css("rgba(255, 150, 0, 255)", "ultron").endswith(", 255)")

    def test_gradient_stops_and_rgb_are_recoloured(self):
        out = recolor_css("qlineargradient(x1:0, stop:0 #ff8c00, stop:1 rgb(255, 204, 0))", "ultron")
        assert "#ff8c00" not in out and "255, 204, 0" not in out

    def test_object_name_selectors_and_other_text_are_left_alone(self):
        css = "QLabel#abcdef { font-size: 12px; } QWidget { color: white; }"
        assert recolor_css(css, "ultron") == css

    def test_recolouring_twice_is_the_same_as_once(self):
        css = "x { color: #ffe6cc; background: rgba(20, 10, 0, 180); border: 1px solid rgba(255,150,0,80); }"
        once = recolor_css(css, "ultron")
        assert recolor_css(once, "ultron") == once

    def test_empty_input_is_safe(self):
        assert recolor_css("", "ultron") == "" and recolor_css(None, "ultron") is None


class TestSelection:
    def test_unknown_names_fall_back_to_jarvis(self, monkeypatch):
        monkeypatch.setenv("JARVIS_THEME", "nonsense")
        assert clSkin._load_skin() == "jarvis"

    def test_the_environment_wins_over_the_config(self, monkeypatch):
        monkeypatch.setenv("JARVIS_THEME", "ultron")
        assert clSkin._load_skin() == "ultron"

    def test_the_default_is_jarvis(self, monkeypatch):
        monkeypatch.delenv("JARVIS_THEME", raising=False)
        monkeypatch.setattr(clSkin, "_CORE_JSON", os.path.join(SRC, "nonexistent.json"))
        assert clSkin._load_skin() == "jarvis"


def run_in_skin(skin, code):
    """Runs `code` in a fresh interpreter (the skin is chosen at import time) and returns its stdout."""
    env = dict(os.environ, JARVIS_THEME=skin, QT_QPA_PLATFORM="offscreen")
    res = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n%s" % (SRC, code)],
                         capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, res.stderr[-800:]
    return res.stdout.strip()


class TestQtIntegration:
    def test_jarvis_keeps_the_stock_qcolor_and_theme(self):
        out = run_in_skin("jarvis", "from clSkinQt import QColor\nfrom PyQt6.QtGui import QColor as Q\nfrom clTheme import Theme\n"
                                    "print(QColor is Q, Theme.C_PRIMARY)")
        assert out == "True #ffaa00"

    def test_ultron_recolours_theme_constants_and_painted_colours(self):
        out = run_in_skin("ultron", "from clSkinQt import QColor\nfrom clTheme import Theme\n"
                                    "c = QColor(255, 170, 0, 140); d = QColor('#ff8c00'); e = QColor(200, 200, 200)\n"
                                    "print(c.alpha(), c.red() > 200 and c.green() < 90, d.name(), e.name(), Theme.C_PRIMARY, Theme.C_BG_PANEL)")
        alpha, red, name, grey, primary, panel = out.split(" ", 5)
        assert alpha == "252" and red == "True" and name != "#ff8c00" and grey == "#c8c8c8"
        assert primary != "#ffaa00" and primary.startswith("#ff") and "20, 10, 0" not in panel

    def test_ultron_recolours_every_widget_stylesheet(self):
        out = run_in_skin("ultron",
                          "from PyQt6.QtWidgets import QApplication, QLabel\napp = QApplication([])\n"
                          "from clUIScalerInjector import inject_scaler\ninject_scaler()\n"
                          "w = QLabel('x'); w.setStyleSheet('color: #ffaa00; background: rgba(255, 150, 0, 80);')\n"
                          "print(w.styleSheet())")
        assert "#ffaa00" not in out and "255, 150, 0" not in out and ", 144)" in out

    def test_copies_and_global_colours_pass_through(self):
        out = run_in_skin("ultron", "from clSkinQt import QColor\nfrom PyQt6.QtCore import Qt\n"
                                    "a = QColor(Qt.GlobalColor.blue); b = QColor(QColor(1, 2, 3)); print(a.name(), b.name())")
        assert out == "#0000ff #010203"

    def test_painted_alpha_follows_the_same_rule(self):
        out = run_in_skin("ultron", "from clSkinQt import QColor\n"
                                    "print(QColor(255, 150, 0, 80).alpha(), QColor(20, 10, 0, 180).alpha(), QColor(255, 150, 0).alpha())")
        assert out == "144 180 255"


class TestThemedBlip:
    def _manager(self, skin):
        from unittest.mock import patch
        with patch('clTTS.ConfigLoader') as MockLoader, patch.object(clSkin, "ACTIVE", skin):
            MockLoader.return_value.load_json.side_effect = lambda name: (
                {"settings": {"silent_mode": False}} if name == "core.json" else {"mqtt": {}})
            from clTTS import TTSManager
            return TTSManager()

    def test_ultron_has_its_own_chirp(self):
        assert self._manager("ultron").blip_path.endswith(os.path.join("themes", "ultron", "blip.wav"))

    def test_jarvis_keeps_the_original_chirp(self):
        assert self._manager("jarvis").blip_path.endswith("blip.mp3")

    def test_the_shipped_chirp_is_a_short_mono_wav(self):
        import wave
        import numpy as np
        path = os.path.join(SRC, "..", "assets", "themes", "ultron", "blip.wav")
        with wave.open(path, "rb") as w:
            assert w.getnchannels() == 1 and w.getsampwidth() == 2
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16) / 32768.0
            assert 0.25 <= len(audio) / w.getframerate() <= 0.7
        assert 0.5 < np.abs(audio).max() < 1.0 and np.isfinite(audio).all()
