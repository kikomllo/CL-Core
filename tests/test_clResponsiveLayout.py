"""The dashboard follows web-style responsive rules: one fluid scale unit that ignores orientation, a dock that
wraps below the text bar when the side margin is too narrow, and widgets that are always fully inside the window."""
import os
import sys

import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

# landscape, portrait, small, big, odd aspect ratios
SIZES = [(1920, 1080), (1366, 768), (2560, 1440), (3840, 2160), (1080, 1920), (768, 1366), (1440, 2560),
         (720, 1280), (1280, 1024), (1024, 1280), (3440, 1440), (1200, 1200)]


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="module")
def _window(qapp, tmp_path_factory):
    """One dashboard for the whole file (building JarvisUI per test took ~20 s each)."""
    import clUI
    from PyQt6.QtCore import Qt
    with patch.object(clUI, "STATE_FILE", str(tmp_path_factory.mktemp("state") / "state.json")),             patch.object(clUI.MqttThread, "start"):
        window = clUI.JarvisUI()
        window.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        window.is_fullscreen = True
        window.setMinimumSize(0, 0)
        window.setMaximumSize(16777215, 16777215)
        window.show()
        yield window
        window.hide()
        window.deleteLater()


@pytest.fixture
def ui(_window):
    _window._widgets_canvas = None
    yield _window
    for widget_id in list(_window.active_widgets):
        _window.close_draggable_widget(widget_id)
    _window._closed_widget_geometry.clear()       # else the next test reopens at this test's size


def fit(window, w, h):
    """Puts the window on a (fake) w x h monitor the way set_ui_mode("set_fullscreen") does."""
    from PyQt6.QtCore import QRect
    from clUIScaler import UIScaler

    class Screen:
        def geometry(self):
            return QRect(0, 0, w, h)

        def name(self):
            return "fake"

    scaler = UIScaler.get()
    scaler.monitors = [{"index": 0, "width": w, "height": h, "scale": scaler.scale_for(w, h)}]
    scaler.active_monitor = 0
    window.screen = lambda: Screen()
    window.resizeUnscaled(w, h)
    window.refresh_layout(force_monitor_idx=0)


class TestScale:
    @pytest.mark.parametrize("w,h", SIZES)
    def test_same_scale_whichever_way_the_screen_is_turned(self, qapp, w, h):
        from clUIScaler import UIScaler
        assert UIScaler.get().scale_for(w, h) == UIScaler.get().scale_for(h, w)

    def test_portrait_1080p_is_not_shrunk_to_a_strip(self, qapp):
        from clUIScaler import UIScaler
        assert UIScaler.get().scale_for(1080, 1920) == 1.0

    def test_landscape_scale_unchanged(self, qapp):
        from clUIScaler import UIScaler
        s = UIScaler.get()
        assert s.scale_for(1920, 1080) == 1.0
        assert s.scale_for(1366, 768) == pytest.approx(768 / 1080)
        assert s.scale_for(3840, 2160) == 2.0
        assert s.scale_for(3440, 1440) == pytest.approx(1440 / 1080)   # ultrawide: height limits it


class TestFullscreenWindowSize:
    @pytest.mark.parametrize("w,h", [(1366, 768), (1080, 1920), (768, 1366)])
    def test_resize_unscaled_fills_the_screen_at_any_scale(self, qapp, w, h):
        """QWidget.resize is patched to multiply by the UI scale; the fullscreen window must bypass that, or on
        a 1366x768 monitor it came out 971x546 (and on a 1080x1920 one 607x1080, top-left corner only)."""
        from PyQt6.QtWidgets import QWidget
        from clUIScaler import UIScaler
        import clUI  # noqa: F401  (installs the scaler patches once; calling inject_scaler again would double-wrap them)
        scaler = UIScaler.get()
        scaler.monitors = [{"index": 0, "width": w, "height": h, "scale": scaler.scale_for(w, h)}]
        scaler.active_monitor = 0
        widget = QWidget()
        widget.resizeUnscaled(w, h)
        assert (widget.width(), widget.height()) == (w, h)

    def test_set_fullscreen_uses_the_unscaled_resize(self):
        import inspect
        import clUI
        src = inspect.getsource(clUI.JarvisUI.set_ui_mode)
        assert "self.resizeUnscaled(geom.width(), geom.height())" in src


class TestResponsiveLayout:
    @pytest.mark.parametrize("w,h", SIZES)
    def test_every_control_is_inside_the_window(self, ui, w, h):
        fit(ui, w, h)
        for name in ("text_input", "pill_mic", "pill_speaker", "btn_calendar", "clock_display"):
            geom = getattr(ui, name).geometry()
            assert geom.left() >= 0 and geom.right() <= w and geom.top() >= 0 and geom.bottom() <= h, (name, geom)
        for pill in ui.widget_toggle_pills:
            geom = pill.geometry()
            assert geom.left() >= 0 and geom.right() <= w and geom.top() >= 0 and geom.bottom() <= h, geom

    @pytest.mark.parametrize("w,h", SIZES)
    def test_the_dock_never_overlaps_the_text_bar_or_audio_pills(self, ui, w, h):
        fit(ui, w, h)
        bar = ui.text_input.geometry()
        pills = [ui.pill_mic.geometry(), ui.pill_speaker.geometry()]
        for pill in ui.widget_toggle_pills:
            assert not pill.geometry().intersects(bar), (w, h)
            for audio in pills:
                assert not pill.geometry().intersects(audio), (w, h)

    @pytest.mark.parametrize("w,h", [(1080, 1920), (768, 1366), (1440, 2560), (720, 1280), (1024, 1280)])
    def test_narrow_windows_wrap_the_dock_above_the_bar(self, ui, w, h):
        fit(ui, w, h)
        assert ui.layout_compact
        bar = ui.text_input.geometry()
        for pill in ui.widget_toggle_pills:
            assert pill.geometry().bottom() < bar.top()

    @pytest.mark.parametrize("w,h", [(1920, 1080), (1366, 768), (2560, 1440), (3840, 2160), (3440, 1440)])
    def test_wide_windows_keep_the_dock_beside_the_bar(self, ui, w, h):
        fit(ui, w, h)
        assert not ui.layout_compact
        bar = ui.text_input.geometry()
        for pill in ui.widget_toggle_pills:
            assert pill.geometry().right() < bar.left()

    @pytest.mark.parametrize("w,h", SIZES)
    def test_clock_sits_above_the_orb_not_over_it(self, ui, w, h):
        fit(ui, w, h)
        assert ui.clock_display.geometry().center().y() < h // 2

    def test_1080p_layout_is_unchanged(self, ui):
        fit(ui, 1920, 1080)
        assert ui.text_input.geometry().getRect() == (660, 1000, 600, 40)
        assert ui.clock_display.y() + ui.clock_display.height() // 2 > 0
        assert ui.btn_calendar.geometry().getRect() == (1890, 500, 30, 80)


class TestWidgetsStayInView:
    def spawn(self, ui):
        from ui.clTodoWidget import TodoWidget
        ui.spawn_widget("widget_todo_list", "To-Do List", TodoWidget())
        return ui.active_widgets["widget_todo_list"]

    @pytest.mark.parametrize("w,h", [(1080, 1920), (768, 1366), (1366, 768), (720, 1280)])
    def test_a_widget_saved_on_a_wide_monitor_is_pulled_into_a_narrow_one(self, ui, w, h):
        fit(ui, 1920, 1080)
        widget = self.spawn(ui)
        widget.move(1500, 600)
        fit(ui, w, h)
        assert ui.rect().contains(widget.geometry()), (w, h, widget.geometry())

    def test_a_widget_keeps_its_proportions_across_an_orientation_change(self, ui):
        """Sizes follow the one scale uniformly; they must never be stretched per axis."""
        fit(ui, 1920, 1080)
        widget = self.spawn(ui)
        ratio = widget.width() / widget.height()
        fit(ui, 1080, 1920)
        assert widget.width() / widget.height() == pytest.approx(ratio, rel=0.05)

    def test_a_remembered_geometry_from_a_bigger_monitor_reopens_in_view(self, ui):
        fit(ui, 1920, 1080)
        widget = self.spawn(ui)
        widget.move(1500, 600)
        ui.close_draggable_widget("widget_todo_list")
        fit(ui, 1080, 1920)
        widget = self.spawn(ui)
        assert ui.rect().contains(widget.geometry()), widget.geometry()

    def test_a_widget_inside_the_window_is_left_alone(self, ui):
        fit(ui, 1920, 1080)
        widget = self.spawn(ui)
        widget.move(300, 200)
        fit(ui, 1920, 1080)
        assert widget.pos().x() == 300 and widget.pos().y() == 200

    def test_the_tiny_overlay_box_never_crushes_widget_positions(self, ui):
        """During overlay <-> fullscreen the window is briefly the small overlay box: that must not move anything."""
        fit(ui, 1920, 1080)
        widget = self.spawn(ui)
        widget.move(1500, 600)
        ui.resizeUnscaled(300, 300)            # the window is not near the (fake) screen's size
        ui.refresh_layout(force_monitor_idx=0)
        assert (widget.x(), widget.y()) == (1500, 600)
