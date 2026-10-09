import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


_APP = None


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    global _APP
    app = _APP = QApplication.instance() or QApplication(sys.argv)   # held at module level: a collected QApplication can't be rebuilt
    import clUI  # installs the stylesheet hook (clUIScalerInjector) once, like the real app
    return app


def _resolve(layers, active=(), names=("QFrame", "QWidget"), object_name=""):
    import clSmoothBorders as sb
    return sb.resolve(tuple(layers), set(names), object_name, frozenset(active))


class TestCascade:
    def test_shorthand_border_and_radius(self, qapp):
        st = _resolve(["QFrame { border: 1px solid rgba(255, 170, 0, 0.25); border-radius: 8px; }"])
        assert st.radii == [8.0] * 4
        vis = st.visible()
        assert set(vis) == {"top", "right", "bottom", "left"}
        assert vis["top"][0] == 1.0
        assert vis["top"][1].alpha() == 64          # float alpha is 0-1, as in Qt

    def test_integer_alpha_is_0_to_255(self, qapp):
        st = _resolve(["QFrame { border: 1px solid rgba(255, 150, 0, 80); }"])
        assert st.visible()["left"][1].alpha() == 80

    def test_no_border_means_nothing_visible(self, qapp):
        assert _resolve(["QFrame { border: none; border-radius: 8px; }"]).visible() == {}
        assert _resolve(["QFrame { border-radius: 8px; }"]).visible() == {}

    def test_own_sheet_beats_ancestors_which_beat_the_application(self, qapp):
        st = _resolve(["QFrame { border: 1px solid #111111; }", "QFrame { border: 1px solid #222222; }",
                       "QFrame { border: 1px solid #333333; }"])
        assert st.visible()["top"][1].name() == "#333333"

    def test_more_specific_selector_wins_within_a_sheet(self, qapp):
        st = _resolve(["#card { border: 1px solid #aaaaaa; } QFrame { border: 1px solid #bbbbbb; }"], object_name="card")
        assert st.visible()["top"][1].name() == "#aaaaaa"

    def test_pseudo_states_apply_only_when_active(self, qapp):
        css = "QFrame { border: 1px solid #111111; } QFrame:hover { border: 1px solid #ffffff; }"
        assert _resolve([css]).visible()["top"][1].name() == "#111111"
        assert _resolve([css], active=("hover",)).visible()["top"][1].name() == "#ffffff"

    def test_per_side_border_overrides_the_shorthand(self, qapp):
        st = _resolve(["QFrame { border: 1px solid #111111; border-left: 4px solid #ff8c00; }"])
        assert st.visible()["left"][0] == 4.0
        assert st.visible()["top"][0] == 1.0

    def test_bare_declarations_apply_to_the_widget(self, qapp):
        st = _resolve(["background: red; border: 2px solid #123456; border-radius: 5px;"])
        assert st.radii == [5.0] * 4 and st.visible()["top"][0] == 2.0

    def test_unparseable_border_color_is_flagged_not_guessed(self, qapp):
        st = _resolve(["QFrame { border: 1px solid qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #fff,stop:1 #000); }"])
        assert st.ok is False


_KEEP = []   # hosts must outlive the helper that builds them, or their children are deleted


@pytest.fixture(autouse=True)
def _release_hosts():
    yield
    while _KEEP:
        _KEEP.pop().close()


def _frame_pixels(qapp, css, mode_enabled):
    """Render `QFrame#t{css}` on a dark host with the engine on or off; returns the QImage."""
    import clSmoothBorders as sb
    from PyQt6.QtWidgets import QWidget, QFrame
    old = sb.ENABLED
    sb.ENABLED = mode_enabled
    try:
        host = QWidget()
        host.setStyleSheet("background:#000000;")
        host.setFixedSize(80, 60)
        f = QFrame(host)
        f.setObjectName("t")
        f.setGeometry(10, 10, 60, 40)
        f.setStyleSheet("QFrame#t{" + css + "}")
        host.show()
        qapp.processEvents()
        img = host.grab().toImage()
        _KEEP.append(host)
        return img, f
    finally:
        sb.ENABLED = old


class TestBorderIsDrawnOnceAndSmoothly:
    CSS = "background: #808080; border: 1px solid rgba(255, 170, 0, 0.25); border-radius: 12px;"

    def test_a_straight_edge_matches_qts_colour_not_a_doubled_one(self, qapp):
        # Regression: the override that hides Qt's own border must really win, otherwise Qt's
        # border and ours stack and every border comes out about twice as bright.
        px = qapp.primaryScreen().devicePixelRatio()
        s = max(1, round(px))
        off, _ = _frame_pixels(qapp, self.CSS, False)
        on, _ = _frame_pixels(qapp, self.CSS, True)
        x, y = 10 * s, 30 * s
        a, b = off.pixelColor(x, y), on.pixelColor(x, y)
        assert abs(a.red() - b.red()) <= 3 and abs(a.green() - b.green()) <= 3

    def test_the_engine_marks_and_hides_qts_border(self, qapp):
        _, f = _frame_pixels(qapp, self.CSS, True)
        assert "border-color: transparent" in f.styleSheet()
        assert f._sb is not None

    def test_reapplying_a_sheet_that_was_read_back_does_not_stack_overrides(self, qapp):
        # clTodoWidget does setStyleSheet(w.styleSheet() + "..."), so the hook sees its own block.
        import clSmoothBorders as sb
        _, f = _frame_pixels(qapp, self.CSS, True)
        f.setStyleSheet(f.styleSheet() + " QFrame#t { color: red; }")
        f.setStyleSheet(f.styleSheet() + " QFrame#t { color: blue; }")
        assert f.styleSheet().count(sb.MARK) == 1
        assert "color: blue" in f.styleSheet() and "color: red" in f.styleSheet()

    def test_corner_is_close_to_an_ideal_antialiased_corner(self, qapp):
        from PyQt6.QtCore import Qt, QRectF
        from PyQt6.QtGui import QImage, QPainter, QPainterPath, QColor
        if round(qapp.primaryScreen().devicePixelRatio()) != 1:
            pytest.skip("pixel comparison assumes a 1x display")
        css = "background: #808080; border: 1px solid #ffffff; border-radius: 12px;"
        on, _ = _frame_pixels(qapp, css, True)
        off, _ = _frame_pixels(qapp, css, False)
        S, W, H, R = 16, 80, 60, 12
        ref = QImage(W * S, H * S, QImage.Format.Format_RGB32)
        ref.fill(QColor("#000"))
        p = QPainter(ref)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer, inner = QPainterPath(), QPainterPath()
        outer.addRoundedRect(QRectF(10 * S, 10 * S, 60 * S, 40 * S), R * S, R * S)
        inner.addRoundedRect(QRectF(11 * S, 11 * S, 58 * S, 38 * S), (R - 1) * S, (R - 1) * S)
        p.fillPath(outer, QColor("#fff"))
        p.fillPath(inner, QColor("#808080"))
        p.end()
        ref = ref.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)

        def worst(img):
            return max(abs(img.pixelColor(x, y).red() - ref.pixelColor(x, y).red())
                       for x in range(10, 10 + R + 4) for y in range(10, 10 + R + 4))
        assert worst(on) <= 40
        assert worst(on) < worst(off)


class TestStatesAndDefaults:
    def test_a_checked_buttons_own_border_is_hidden_from_qt_and_drawn_by_us(self, qapp):
        from PyQt6.QtWidgets import QPushButton, QWidget
        host = QWidget()
        host.setStyleSheet("background:#000000;")
        host.setFixedSize(60, 40)
        b = QPushButton(host)
        b.setObjectName("b")
        b.setCheckable(True)
        b.setChecked(True)
        b.setGeometry(5, 5, 50, 30)
        b.setStyleSheet("QPushButton#b { background: #000000; border: 1px solid #333333; border-radius: 8px; }"
                        "QPushButton#b:checked { border: 1px solid rgba(255, 0, 0, 128); }")
        host.show()
        qapp.processEvents()
        s = max(1, round(qapp.primaryScreen().devicePixelRatio()))
        c = host.grab().toImage().pixelColor(5 * s, 20 * s)
        # one translucent red layer over black is ~128 red; a stacked second layer would be ~192
        assert 115 <= c.red() <= 140

    def test_a_widget_with_no_radius_is_left_to_qt(self, qapp):
        from PyQt6.QtWidgets import QFrame
        f = QFrame()
        f.setStyleSheet("QFrame { border: 1px solid #ffffff; }")
        assert "border-color: transparent" not in f.styleSheet()
        assert f.__dict__.get("_sb") is None

    def test_buttons_styled_only_by_the_global_stylesheet_are_covered(self, qapp):
        from PyQt6.QtWidgets import QPushButton
        from clTheme import Theme
        qapp.setStyleSheet(Theme.get_global_stylesheet())
        try:
            b = QPushButton("x")
            assert b.__dict__.get("_sb") is not None
            assert "border-color: transparent" in b.styleSheet()
        finally:
            qapp.setStyleSheet("")

    def test_differently_coloured_sides_are_each_painted(self, qapp):
        # Left accent border (the event cards): the left side keeps its own colour.
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QImage, QPainter
        import clSmoothBorders as sb
        img = QImage(60, 40, QImage.Format.Format_RGB32)
        img.fill(QColor("#000000"))
        p = QPainter(img)
        sb.paint_border(p, QRectF(0, 0, 60, 40), [8.0] * 4,
                        {"top": (1.0, QColor("#336699")), "right": (1.0, QColor("#336699")),
                         "bottom": (1.0, QColor("#336699")), "left": (4.0, QColor("#ff8c00"))})
        p.end()
        assert img.pixelColor(1, 20).red() > 240            # accent
        assert img.pixelColor(30, 0).blue() > 140 and img.pixelColor(30, 0).red() < 80   # top
