import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _widget(mocker):
    import ui.clNoteWidget as clNoteWidget
    mocker.patch.object(clNoteWidget.ActionRouter, "dispatch", return_value=True)
    return clNoteWidget.NoteWidget()


class TestListHasItsOwnMinimumHeightIndependentOfTheEditor:
    """Live bug (round 2 of the same fix): once the stack stopped
    borrowing the editor page's sizeHint as an accidental floor for the
    list too, the list page felt "eaten" -- a QScrollArea's own sizeHint
    doesn't grow with its scrollable content (it's small regardless of
    how many notes exist), so the list had nothing left keeping it
    comfortably sized once that side effect was gone. It needs an honest
    minimum height of its own."""

    def test_list_page_sizeHint_has_a_sensible_minimum_regardless_of_note_count(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [
            {"id": str(i), "title": f"Note {i}", "text": "x", "time_created": "2026-01-01T00:00:00"}
            for i in range(20)
        ]})

        assert w.list_page.sizeHint().height() >= 160

    def test_list_stays_comfortably_sized_after_visiting_the_editor_and_coming_back(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [
            {"id": str(i), "title": f"Note {i}", "text": "x", "time_created": "2026-01-01T00:00:00"}
            for i in range(20)
        ]})
        list_height_before = w.list_page.sizeHint().height()

        w.open_note("0")
        w.show_list()

        assert w.stack.sizeHint().height() == list_height_before


class TestStackSizeHintTracksOnlyTheCurrentPage:
    """Live bug: the editor's own minimum size was bigger than expected --
    a plain QStackedWidget reports sizeHint()/minimumSizeHint() as the
    size needed to fit its LARGEST page, not just the one currently
    shown, so a tall notes list (many notes) inflated the editor's forced
    minimum size even while the list wasn't visible at all."""

    def test_sizeHint_matches_the_current_page_not_the_larger_one(self, qapp, mocker):
        # Instance-level overrides, not mocker.patch.object(type(...), ...)
        # -- list_page and editor_page are both plain QWidget() instances
        # of the SAME class, so a class-level patch on one would silently
        # also apply to the other. PyQt supports overriding a virtual
        # method per-instance (the same pattern used elsewhere in this
        # codebase for mouseReleaseEvent/focusOutEvent), which keeps the
        # two pages' fake hints genuinely independent.
        from PyQt6.QtCore import QSize
        w = _widget(mocker)
        w.list_page.sizeHint = lambda: QSize(400, 900)
        w.editor_page.sizeHint = lambda: QSize(300, 200)

        w.stack.setCurrentWidget(w.list_page)
        assert w.stack.sizeHint() == QSize(400, 900)

        w.stack.setCurrentWidget(w.editor_page)
        assert w.stack.sizeHint() == QSize(300, 200)

    def test_minimumSizeHint_matches_the_current_page_not_the_larger_one(self, qapp, mocker):
        from PyQt6.QtCore import QSize
        w = _widget(mocker)
        w.list_page.minimumSizeHint = lambda: QSize(400, 900)
        w.editor_page.minimumSizeHint = lambda: QSize(300, 200)

        w.stack.setCurrentWidget(w.editor_page)

        assert w.stack.minimumSizeHint() == QSize(300, 200)


def _focus_out_event():
    from PyQt6.QtGui import QFocusEvent
    from PyQt6.QtCore import Qt
    return QFocusEvent(QFocusEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason)


def _release_event(pos, button):
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent, QPointF, Qt
    return QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(pos), button, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)


class TestPageSwitchTriggersAResize:
    """Live bug: switching between the notes list and the full-note editor
    never triggered any resize at all -- only a data refresh (update_status)
    did. QStackedWidget swapping its current page doesn't itself make the
    wrapper re-fit, so the newly shown page's elements just stayed squeezed
    into whatever size the wrapper happened to already be."""

    def test_opening_a_note_schedules_a_resize(self, qapp, mocker):
        import ui.clNoteWidget as clNoteWidget
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"}]})
        mock_single_shot = mocker.patch.object(clNoteWidget.QTimer, "singleShot")

        w.open_note("n1")

        mock_single_shot.assert_called_once_with(50, w._force_resize)

    def test_going_back_to_the_list_schedules_a_resize(self, qapp, mocker):
        import ui.clNoteWidget as clNoteWidget
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")
        mock_single_shot = mocker.patch.object(clNoteWidget.QTimer, "singleShot")

        w.show_list()

        mock_single_shot.assert_called_once_with(50, w._force_resize)


class TestForceResizeDelegatesToTheWrappersGrowOnlyUpdateScaling:
    """Live bug: adding or removing a note reset the whole widget back to
    its natural minimum size, discarding a manual drag-resize (or a size
    restored from ui_state.json) -- _force_resize() used to call
    parentWidget().adjustSize(), which unconditionally snaps to the
    content's natural size. DraggableWidget.update_scaling() is the
    already-correct, grow-only-never-shrink version of this."""

    def test_delegates_to_update_scaling_not_adjustSize(self, qapp, mocker):
        w = _widget(mocker)
        parent = mocker.MagicMock()
        mocker.patch.object(type(w), "parentWidget", return_value=parent)

        w._force_resize()

        parent.update_scaling.assert_called_once()
        parent.adjustSize.assert_not_called()

    def test_does_not_call_adjustSize_on_this_widget_either(self, qapp, mocker):
        """Live bug: switching to the (smaller) editor page left a visible
        gap at the bottom of the widget -- this widget is itself a layout-
        managed child of DraggableWidget, not a free-floating window, so
        calling adjustSize() on it snaps IT down to whichever page's
        sizeHint is currently active (the editor's, smaller than the
        list's) instead of staying stretched to fill however big the
        wrapper actually is."""
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w._force_resize()

        adjust_spy.assert_not_called()

    def test_update_scaling_also_does_not_call_adjustSize(self, qapp, mocker):
        """Live bug (round 2): fixing _force_resize() alone wasn't enough
        -- DraggableWidget.update_scaling() calls content_widget's own
        update_scaling() FIRST, before computing its grow-only resize off
        self.sizeHint(). That method still ended with self.adjustSize(),
        so the editor page briefly stretched correctly and then visibly
        snapped back down a moment later when the deferred resize fired."""
        w = _widget(mocker)
        adjust_spy = mocker.patch.object(w, "adjustSize")

        w.update_scaling()

        adjust_spy.assert_not_called()


class TestNotesScrollAreaIsTransparent:
    """The global QScrollArea{background:transparent} stylesheet only
    covers the scroll area's own frame -- its internal viewport is a
    separate child widget Qt creates itself, doesn't inherit that rule,
    and paints its default (grey) palette background underneath the note
    cards unless explicitly overridden here too."""

    def test_viewport_background_is_set_transparent(self, qapp, mocker):
        w = _widget(mocker)
        assert "transparent" in w.scroll.viewport().styleSheet()


class TestShowEventRequestsTheList:
    def test_showing_the_widget_dispatches_note_list(self, qapp, mocker):
        from PyQt6.QtGui import QShowEvent
        w = _widget(mocker)
        w.showEvent(QShowEvent())
        w.router.dispatch.assert_called_with("note.list")


class TestAddNote:
    def test_dispatches_create_with_empty_text(self, qapp, mocker):
        w = _widget(mocker)
        w.add_note()
        w.router.dispatch.assert_called_with("note.create", text="")

    def test_sets_the_open_next_new_note_flag(self, qapp, mocker):
        w = _widget(mocker)
        assert w._open_next_new_note is False
        w.add_note()
        assert w._open_next_new_note is True

    def test_a_newly_created_note_opens_straight_into_the_editor(self, qapp, mocker):
        w = _widget(mocker)
        w.add_note()

        w.update_status({"notes": [{"id": "n1", "text": "", "time_created": "2026-01-01T00:00:00"}]})

        assert w.stack.currentWidget() is w.editor_page
        assert w._open_note_id == "n1"
        assert w._open_next_new_note is False


class TestListPreviewRows:
    def test_creates_one_row_per_note(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [
            {"id": "n1", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"},
            {"id": "n2", "text": "Call mom", "time_created": "2026-01-02T00:00:00"},
        ]})
        assert set(w.note_rows.keys()) == {"n1", "n2"}

    def test_title_comes_from_the_title_field_and_subtitle_from_the_bodys_first_line(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [
            {"id": "n1", "title": "Groceries", "text": "Milk, eggs, bread\nAlso coffee filters", "time_created": "2026-01-01T00:00:00"},
        ]})
        row = w.note_rows["n1"]
        assert row["title_lbl"].text() == "Groceries"
        assert row["subtitle_lbl"].text() == "Milk, eggs, bread"
        # isVisibleTo(), not isVisible() -- a row not attached to a shown
        # top-level window reads isVisible()==False regardless of the
        # label's own setVisible() state.
        assert row["subtitle_lbl"].isVisibleTo(row["widget"]) is True

    def test_a_titled_note_with_no_body_text_has_no_visible_subtitle(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "title": "Groceries", "text": "", "time_created": "2026-01-01T00:00:00"}]})
        row = w.note_rows["n1"]
        assert row["title_lbl"].text() == "Groceries"
        assert row["subtitle_lbl"].isVisibleTo(row["widget"]) is False

    def test_note_with_no_title_shows_a_placeholder_title(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "title": "", "text": "", "time_created": "2026-01-01T00:00:00"}]})
        row = w.note_rows["n1"]
        assert row["title_lbl"].text() == "New note"

    def test_removes_rows_for_notes_no_longer_present(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"}]})
        assert "n1" in w.note_rows

        w.update_status({"notes": []})

        assert w.note_rows == {}

    def test_shows_empty_label_when_there_are_no_notes(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": []})
        assert w._empty_lbl is not None
        assert w._empty_lbl.text() == "No notes yet."

    def test_empty_label_is_removed_once_a_note_exists(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": []})
        assert w._empty_lbl is not None

        w.update_status({"notes": [{"id": "n1", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"}]})

        assert w._empty_lbl is None


class TestOpeningAndClosingANote:
    def test_clicking_a_row_opens_the_editor_with_its_title_and_text(self, qapp, mocker):
        from PyQt6.QtCore import Qt, QPoint
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "title": "Groceries", "text": "Milk, eggs, bread", "time_created": "2026-01-01T00:00:00"}]})
        row = w.note_rows["n1"]["widget"]

        row.mouseReleaseEvent(_release_event(QPoint(5, 5), Qt.MouseButton.LeftButton))

        assert w.stack.currentWidget() is w.editor_page
        assert w.title_input.text() == "Groceries"
        assert w.editor_text.toPlainText() == "Milk, eggs, bread"
        assert w._open_note_id == "n1"

    def test_delete_button_does_not_also_open_the_note(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"}]})

        w.note_rows["n1"]["delete_btn"].click()

        assert w.stack.currentWidget() is w.list_page
        w.router.dispatch.assert_called_with("note.delete", id="n1")

    def test_back_button_saves_the_title_and_text_and_returns_to_the_list(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "title": "Groceries", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")
        w.editor_text.setPlainText("Buy milk and eggs")

        w.back_btn.click()

        w.router.dispatch.assert_called_with("note.update", id="n1", title="Groceries", text="Buy milk and eggs")
        assert w.stack.currentWidget() is w.list_page
        assert w._open_note_id is None

    def test_losing_focus_in_the_editor_also_saves(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "title": "Groceries", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")
        w.editor_text.setPlainText("Buy milk and eggs")

        w.editor_text.focusOutEvent(_focus_out_event())

        w.router.dispatch.assert_called_with("note.update", id="n1", title="Groceries", text="Buy milk and eggs")

    def test_losing_focus_on_the_title_field_also_saves(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "title": "Groceries", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")
        w.title_input.setText("Shopping list")

        w.title_input.focusOutEvent(_focus_out_event())

        w.router.dispatch.assert_called_with("note.update", id="n1", title="Shopping list", text="Buy milk")

    def test_deleting_the_currently_open_note_returns_to_the_list(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")

        w._delete_note("n1")

        assert w.stack.currentWidget() is w.list_page
        assert w._open_note_id is None


class TestAbandoningAnEmptyNoteDiscardsIt:
    """Live bug: "+ Add Note" immediately creates and persists an empty
    note file. Going back without ever typing into it used to save that
    empty text right back, leaving a permanent, invisible-until-you-look
    "New note" ghost entry in the list forever. Going back on a still-
    empty note now deletes it instead of saving it."""

    def test_going_back_on_a_newly_created_untouched_note_deletes_it(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")

        w.back_btn.click()

        w.router.dispatch.assert_called_with("note.delete", id="n1")
        assert w.stack.currentWidget() is w.list_page

    def test_going_back_on_a_note_left_blank_after_editing_also_deletes_it(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "Buy milk", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")
        w.editor_text.setPlainText("   ")  # cleared down to just whitespace

        w.back_btn.click()

        w.router.dispatch.assert_called_with("note.delete", id="n1")

    def test_going_back_on_a_note_with_real_text_but_no_title_saves_it_as_unnamed(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")
        w.editor_text.setPlainText("Buy milk")

        w.back_btn.click()

        w.router.dispatch.assert_called_with("note.update", id="n1", title="Unnamed", text="Buy milk")

    def test_losing_focus_on_an_untouched_empty_note_does_not_delete_it(self, qapp, mocker):
        """Only the explicit back button discards an empty note -- a plain
        focus-out (e.g. briefly alt-tabbing away from a freshly opened,
        not-yet-typed-into note) must not destroy it out from under the
        user before they've had a chance to type anything. A harmless
        resave of the same empty text is fine; a delete is not."""
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")

        w.editor_text.focusOutEvent(_focus_out_event())

        w.router.dispatch.assert_called_with("note.update", id="n1", title="Unnamed", text="")


class TestUntitledNoteIsSavedAsUnnamed:
    """The user's explicit requirement: create a new note, write something
    in the body, don't add a title -- it should save with the title
    "Unnamed" rather than an empty title. A note with a real title the
    user actually typed is never overridden."""

    def test_saving_a_note_with_text_but_no_title_defaults_the_title_to_unnamed(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "title": "", "text": "", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")
        w.editor_text.setPlainText("Remember to water the plants")

        w.back_btn.click()

        w.router.dispatch.assert_called_with("note.update", id="n1", title="Unnamed", text="Remember to water the plants")

    def test_a_note_with_a_real_title_is_not_overridden(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "title": "", "text": "", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")
        w.title_input.setText("Plant care")
        w.editor_text.setPlainText("Remember to water the plants")

        w.back_btn.click()

        w.router.dispatch.assert_called_with("note.update", id="n1", title="Plant care", text="Remember to water the plants")

    def test_a_title_that_is_only_whitespace_also_defaults_to_unnamed(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "title": "", "text": "", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")
        w.title_input.setText("   ")
        w.editor_text.setPlainText("Remember to water the plants")

        w.back_btn.click()

        w.router.dispatch.assert_called_with("note.update", id="n1", title="Unnamed", text="Remember to water the plants")


class TestUpdateStatusDoesNotClobberAnActiveEdit:
    def test_does_not_overwrite_the_open_editor_while_it_has_focus(self, qapp, mocker):
        """A status refresh triggered by an unrelated note being
        created/deleted elsewhere must not clobber text the user is
        actively typing in the currently-open note."""
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "Original", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")
        w.editor_text.setPlainText("Original, being edited")
        mocker.patch.object(type(w.editor_text), "hasFocus", return_value=True)

        w.update_status({"notes": [{"id": "n1", "text": "Original", "time_created": "2026-01-01T00:00:00"}]})

        assert w.editor_text.toPlainText() == "Original, being edited"

    def test_does_update_the_open_editor_when_it_does_not_have_focus(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"notes": [{"id": "n1", "text": "Original", "time_created": "2026-01-01T00:00:00"}]})
        w.open_note("n1")

        w.update_status({"notes": [{"id": "n1", "text": "Updated elsewhere", "time_created": "2026-01-01T00:00:00"}]})

        assert w.editor_text.toPlainText() == "Updated elsewhere"
