import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _widget(mocker):
    import ui.clLightControlWidget as clLightControlWidget
    mocker.patch.object(clLightControlWidget.ActionRouter, "dispatch", return_value=True)
    return clLightControlWidget.LightControlWidget()


class TestWidgetSizeSourceOfTruth:
    """Live bug: this widget is a layout-managed child of DraggableWidget,
    not a free-floating window -- calling self.adjustSize() anywhere in its
    refresh path snaps it straight to its own natural sizeHint, discarding
    a manual drag-resize or a size restored from ui_state.json. Found via
    the identical, already-fixed issue in the todo/notes widgets: three
    separate self.adjustSize() calls had survived here (in update_status(),
    _delete_light(), and even _force_resize() itself, contradicting that
    method's own comment) despite the pattern already being established
    elsewhere."""

    def test_update_status_does_not_call_adjustSize(self, qapp, mocker):
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w.update_status({"network": "TestNet", "lights": [
            {"name": "Lamp", "is_on": True, "offline": False},
        ]})

        adjust_spy.assert_not_called()

    def test_delete_light_does_not_call_adjustSize(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"network": "TestNet", "lights": [
            {"name": "Lamp", "is_on": True, "offline": False},
        ]})
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w._delete_light("lamp")

        adjust_spy.assert_not_called()

    def test_force_resize_does_not_call_adjustSize(self, qapp, mocker):
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w._force_resize()

        adjust_spy.assert_not_called()

    def test_force_resize_delegates_to_the_wrappers_grow_only_update_scaling(self, qapp, mocker):
        w = _widget(mocker)
        parent = mocker.MagicMock()
        mocker.patch.object(type(w), "parentWidget", return_value=parent)

        w._force_resize()

        parent.update_scaling.assert_called_once()
        parent.adjustSize.assert_not_called()

    def test_update_scaling_does_not_call_adjustSize_on_self(self, qapp, mocker):
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w.update_scaling()

        adjust_spy.assert_not_called()
