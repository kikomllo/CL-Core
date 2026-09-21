import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _widget(mocker):
    import ui.clTodoWidget as clTodoWidget
    mocker.patch.object(clTodoWidget.ActionRouter, "dispatch", return_value=True)
    return clTodoWidget.TodoWidget()


class TestSizeHintReflectsNaturalSizeOnly:
    """Live bug: sizeHint() used to unconditionally floor at (350, 400) --
    but DraggableWidget.update_scaling()'s grow-only logic consults
    sizeHint() on EVERY status refresh (every task added/completed/
    deleted), not just once. That meant a size the user deliberately
    dragged smaller than the floor (or one ui_state.json had legitimately
    saved smaller) kept getting silently grown back up to the floor the
    very next time the todo list refreshed, defeating ui_state.json as
    the source of truth. The floor is now applied only once, at spawn
    time, via get_standalone_min_size() in clUI.py's spawn_widget() --
    sizeHint() itself must report the plain natural value so an already-
    set (restored or manual) size is never fought after the fact."""

    def test_sizeHint_is_the_plain_natural_value(self, qapp, mocker):
        from PyQt6.QtCore import QSize
        from PyQt6.QtWidgets import QWidget
        w = _widget(mocker)
        mocker.patch.object(QWidget, "sizeHint", return_value=QSize(169, 43))

        hint = w.sizeHint()

        assert hint.width() == 169
        assert hint.height() == 43

    def test_get_standalone_min_size_still_provides_the_comfortable_floor(self, qapp, mocker):
        # Consumed by clUI.py's spawn_widget() as a one-time default for a
        # brand-new widget with no saved ui_state.json size yet.
        w = _widget(mocker)

        assert w.get_standalone_min_size() == (350, 400)


def _release_event(pos, button):
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent, QPointF, Qt
    return QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(pos), button, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)


class TestUpdateStatusResizesAfterRebuild:
    """Live bug: the widget is first shown near-empty (before the real
    todo/status reply arrives over MQTT), sized for that -- without
    forcing a resize after tabs are actually populated, the wrapper never
    grows to fit them, squeezing every tab into scroll buttons with
    truncated labels instead."""

    def test_update_status_schedules_a_deferred_resize(self, qapp, mocker):
        import ui.clTodoWidget as clTodoWidget
        w = _widget(mocker)
        mock_single_shot = mocker.patch.object(clTodoWidget.QTimer, "singleShot")

        w.update_status({"todos": [{"id": "1", "task": "Buy milk", "list_name": "My To-Do List", "completed": False}]})

        mock_single_shot.assert_called_once()
        delay, callback = mock_single_shot.call_args[0]
        assert delay == 50
        assert callback == w._force_resize

    def test_force_resize_does_not_call_adjustSize_on_this_widget(self, qapp, mocker):
        """Live bug (found via the identical issue in the notes widget):
        this widget is a layout-managed child of DraggableWidget, not a
        free-floating window. Calling adjustSize() on it here snaps it to
        its own current sizeHint right before the wrapper measures it in
        update_scaling(), fighting the wrapper's actual (larger, grow-
        only) size."""
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w._force_resize()

        adjust_spy.assert_not_called()

    def test_force_resize_delegates_to_the_wrappers_grow_only_update_scaling(self, qapp, mocker):
        """Live bug: adding or removing a task reset the whole widget back
        to its natural minimum size, discarding a manual drag-resize (or a
        size restored from ui_state.json) -- _force_resize() used to call
        parentWidget().adjustSize(), which unconditionally snaps to the
        content's natural size. DraggableWidget.update_scaling() is the
        already-correct, grow-only-never-shrink version of this."""
        w = _widget(mocker)
        parent = mocker.MagicMock()
        mocker.patch.object(type(w), "parentWidget", return_value=parent)

        w._force_resize()

        parent.update_scaling.assert_called_once()
        parent.adjustSize.assert_not_called()

    def test_update_scaling_also_does_not_call_adjustSize(self, qapp, mocker):
        """DraggableWidget.update_scaling() calls this widget's own
        update_scaling() FIRST, before computing its grow-only resize off
        self.sizeHint() -- that method must not snap this widget to its
        own sizeHint either, or the same fight happens one level up."""
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w.update_scaling()

        adjust_spy.assert_not_called()

    def test_update_status_itself_does_not_call_adjustSize(self, qapp, mocker):
        """Live bug: update_status() called self.adjustSize() directly,
        immediately snapping this widget to its natural sizeHint on every
        task added/completed/deleted -- before the deferred _force_resize()
        (which correctly only delegates to the wrapper's grow-only
        update_scaling()) ever got a chance to run. This silently discarded
        a manual drag-resize or a size restored from ui_state.json on every
        single status refresh."""
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w.update_status({"todos": [{"id": "1", "task": "Buy milk", "list_name": "My To-Do List", "completed": False}]})

        adjust_spy.assert_not_called()


class TestTaskRowClickHandling:
    """Left click anywhere on a task row toggles complete in both directions
    (checked <-> unchecked), except over the task text itself -- that's
    selectable now, so left-clicking it must not also flip the checkbox.
    Right click deletes, but only an already-completed task -- an
    incomplete one has nothing to delete via right click, left click
    already covers marking it complete.

    Right click goes through Qt's own customContextMenuRequested signal,
    not a RightButton check inside mouseReleaseEvent -- an earlier version
    tried the latter and it silently landed as a plain left-click toggle
    in the real app instead (Qt generates its own context-menu event for a
    right click independently of the press/release pair, so intercepting
    it inside mouseReleaseEvent was racing that, not replacing it). Tests
    must emit the real signal, not call mouseReleaseEvent directly with a
    RightButton event -- that bypasses Qt's actual event routing entirely
    and would have made the broken version look like it worked."""

    def _row(self, qapp, mocker, completed=False):
        from PyQt6.QtWidgets import QCheckBox, QLabel
        w = _widget(mocker)
        w.update_status({"todos": [
            {"id": "t1", "task": "Buy milk", "list_name": "My To-Do List", "completed": completed},
        ]})
        page = w.tabs.widget(0)
        row = page.scroll_layout.itemAt(0).widget()
        chk = row.findChild(QCheckBox)
        lbl = row.findChild(QLabel)
        return w, row, chk, lbl

    def test_label_text_is_selectable(self, qapp, mocker):
        from PyQt6.QtCore import Qt
        w, row, chk, lbl = self._row(qapp, mocker)
        assert lbl.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse

    def test_left_click_on_blank_row_space_toggles_complete(self, qapp, mocker):
        from PyQt6.QtCore import Qt, QPoint
        w, row, chk, lbl = self._row(qapp, mocker, completed=False)
        assert chk.isChecked() is False

        row.mouseReleaseEvent(_release_event(QPoint(0, 0), Qt.MouseButton.LeftButton))

        assert chk.isChecked() is True
        w.router.dispatch.assert_called_with("todo.complete", id="t1", completed=True)

    def test_left_click_on_a_completed_task_marks_it_incomplete_not_delete(self, qapp, mocker):
        # Left click is now a full toggle in both directions -- unchecking a
        # completed task marks it incomplete again rather than deleting it
        # (only right click on a completed task deletes).
        from PyQt6.QtCore import Qt, QPoint
        w, row, chk, lbl = self._row(qapp, mocker, completed=True)
        assert chk.isChecked() is True

        row.mouseReleaseEvent(_release_event(QPoint(0, 0), Qt.MouseButton.LeftButton))

        assert chk.isChecked() is False
        w.router.dispatch.assert_called_with("todo.complete", id="t1", completed=False)

    def test_left_click_on_the_label_does_not_toggle(self, qapp, mocker):
        from PyQt6.QtCore import Qt, QPoint
        w, row, chk, lbl = self._row(qapp, mocker, completed=False)

        lbl.mouseReleaseEvent(_release_event(QPoint(2, 2), Qt.MouseButton.LeftButton))

        assert chk.isChecked() is False

    def test_right_click_on_blank_row_space_dispatches_delete(self, qapp, mocker):
        from PyQt6.QtCore import Qt, QPoint
        w, row, chk, lbl = self._row(qapp, mocker, completed=True)
        assert row.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu

        row.customContextMenuRequested.emit(QPoint(0, 0))

        w.router.dispatch.assert_called_with("todo.delete", id="t1")

    def test_right_click_on_the_label_also_dispatches_delete(self, qapp, mocker):
        from PyQt6.QtCore import Qt, QPoint
        w, row, chk, lbl = self._row(qapp, mocker, completed=True)
        assert lbl.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu

        lbl.customContextMenuRequested.emit(QPoint(2, 2))

        w.router.dispatch.assert_called_with("todo.delete", id="t1")

    def test_right_click_does_not_also_toggle_the_checkbox(self, qapp, mocker):
        from PyQt6.QtCore import QPoint
        w, row, chk, lbl = self._row(qapp, mocker, completed=False)

        row.customContextMenuRequested.emit(QPoint(0, 0))

        assert chk.isChecked() is False

    def test_right_click_on_an_incomplete_task_does_not_delete_it(self, qapp, mocker):
        # An incomplete task has nothing to "reverse" -- only a completed
        # task's right click means delete (undoing a check with no
        # "mark incomplete" action available). Right-clicking any other,
        # unrelated incomplete task on screen must not delete it either.
        from PyQt6.QtCore import QPoint
        w, row, chk, lbl = self._row(qapp, mocker, completed=False)

        row.customContextMenuRequested.emit(QPoint(0, 0))
        lbl.customContextMenuRequested.emit(QPoint(2, 2))

        w.router.dispatch.assert_not_called()


class TestDeleteCurrentListSelectsARemainingRealList:
    """delete_current_list() used to compare tabs.count() > 1 AFTER already
    removing the deleted tab -- so with exactly two lists, deleting one left
    exactly one real list remaining, tripped that '> 1' check into the
    "no lists left" branch anyway, and mislabeled current_list_name as the
    "+" sentinel even though a perfectly real list was still showing. The
    next task added anywhere then silently saved under a list literally
    named "+" instead of whatever list was actually selected on screen."""

    def _two_list_widget(self, mocker):
        w = _widget(mocker)
        w.update_status({"todos": [
            {"id": "a1", "task": "Task A", "list_name": "List A", "completed": False},
            {"id": "b1", "task": "Task B", "list_name": "List B", "completed": False},
        ]})
        return w

    def _select_tab(self, w, list_name):
        for i in range(w.tabs.count()):
            if w.tabs.tabText(i) == list_name:
                w.tabs.setCurrentIndex(i)
                return
        raise AssertionError(f"no tab named {list_name!r}")

    def test_deleting_one_of_two_lists_selects_the_other_by_name(self, qapp, mocker):
        w = self._two_list_widget(mocker)
        self._select_tab(w, "List A")

        w.delete_current_list()

        assert w.current_list_name == "List B"
        assert w.tabs.tabText(w.tabs.currentIndex()) == "List B"

    def test_a_task_added_after_deleting_a_list_saves_under_the_remaining_list(self, qapp, mocker):
        w = self._two_list_widget(mocker)
        self._select_tab(w, "List A")
        w.delete_current_list()

        w.task_input.setText("Notes widget")
        w.submit_task()

        w.router.dispatch.assert_called_with("todo.create", task="Notes widget", list_name="List B", silent=True)

    def test_deleting_the_only_list_falls_back_to_the_plus_sentinel(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"todos": [
            {"id": "a1", "task": "Task A", "list_name": "List A", "completed": False},
        ]})
        self._select_tab(w, "List A")
        mocker.patch.object(w, "prompt_new_list")

        w.delete_current_list()

        assert w.current_list_name == "+"
        w.prompt_new_list.assert_called_once()


class TestStatusRefreshDoesNotBounceBackToTheFirstList:
    """Rebuilding tabs from a fresh status payload used to clear() and
    re-add() them without blocking signals -- adding the very first tab
    back to a freshly emptied QTabWidget fires currentChanged(0)
    synchronously (Qt's own auto-select-on-first-add behavior), which
    on_tab_changed() picked up and used to silently overwrite
    current_list_name with whatever list happened to be first in this
    particular payload -- before update_status()'s own "restore the
    previous selection" loop even ran. Every status refresh (i.e. every
    single task interaction: checking, deleting, adding) was bouncing the
    view back to the first list as a result, no matter which list a task
    actually belonged to."""

    def test_refreshing_status_while_on_the_second_list_stays_on_it(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"todos": [
            {"id": "a1", "task": "Task A", "list_name": "List A", "completed": False},
            {"id": "b1", "task": "Task B", "list_name": "List B", "completed": False},
        ]})
        for i in range(w.tabs.count()):
            if w.tabs.tabText(i) == "List B":
                w.tabs.setCurrentIndex(i)
                break

        # A second refresh, as if the backend just republished after some
        # interaction with a task on "List B" -- list ordering unchanged,
        # "List A" still first in the payload.
        w.update_status({"todos": [
            {"id": "a1", "task": "Task A", "list_name": "List A", "completed": False},
            {"id": "b1", "task": "Task B", "list_name": "List B", "completed": True},
        ]})

        assert w.current_list_name == "List B"
        assert w.tabs.tabText(w.tabs.currentIndex()) == "List B"
