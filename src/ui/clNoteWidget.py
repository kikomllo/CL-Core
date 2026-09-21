import logging
from clTheme import Theme
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QPushButton, QTextEdit, QLineEdit, QStackedWidget
)
from PyQt6.QtCore import Qt, QTimer, QSize
from utils.clActionRouter import ActionRouter
from clUIScaler import UIScaler

def s(val):
    return UIScaler.get().scale(val)

class _CurrentPageOnlyStack(QStackedWidget):
    """A plain QStackedWidget reports its sizeHint/minimumSizeHint as the
    size needed to fit its LARGEST page, not just the one currently shown
    -- so the editor's own minimum size was always being inflated by
    however big the notes list happened to need (more notes -> taller
    list -> bigger forced minimum on the editor too, even while the list
    wasn't even visible). Overridden here to track only the current page."""

    def sizeHint(self):
        current = self.currentWidget()
        return current.sizeHint() if current is not None else super().sizeHint()

    def minimumSizeHint(self):
        current = self.currentWidget()
        return current.minimumSizeHint() if current is not None else super().minimumSizeHint()

class NoteWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(s(15), s(8), s(15), s(10))
        self.layout.setSpacing(s(8))

        self.stack = _CurrentPageOnlyStack()
        self.layout.addWidget(self.stack, stretch=1)

        self._build_list_page()
        self._build_editor_page()
        self.stack.setCurrentWidget(self.list_page)

        self.note_rows = {}
        self._notes_by_id = {}
        self._empty_lbl = None
        self._open_note_id = None
        # Set right before dispatching note.create, so update_status()
        # knows to jump straight into the editor for whichever note turns
        # out to be the new one.
        self._open_next_new_note = False

    # -------------------------------------------------------------------
    # LIST PAGE -- one preview row per note (title + first line), a "+ Add
    # Note" button at the bottom matching the todo widget's "+ Add Task".
    # -------------------------------------------------------------------
    def _build_list_page(self):
        self.list_page = QWidget()
        list_layout = QVBoxLayout(self.list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(s(8))

        top_layout = QHBoxLayout()
        self.title_lbl = QLabel("Quick Notes")
        self.title_lbl.setStyleSheet(Theme.get_style("TitleLabel"))
        top_layout.addWidget(self.title_lbl)
        top_layout.addStretch()
        list_layout.addLayout(top_layout)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        # A QScrollArea's own sizeHint doesn't grow with its scrollable
        # content -- it stays small no matter how many notes exist. Once
        # the stack was fixed to stop borrowing the editor page's (larger)
        # sizeHint as an accidental floor for the list too, the list had
        # nothing left keeping it comfortably sized -- give it an honest
        # minimum of its own instead of relying on that side effect.
        self.scroll.setMinimumHeight(s(160))
        # The global stylesheet's "QScrollArea { background: transparent }"
        # only styles the scroll area's own frame -- its internal viewport
        # is a separate child widget Qt creates itself and doesn't inherit
        # that rule, so it still paints its default (grey) palette
        # background underneath the note cards unless told otherwise here.
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self.notes_container = QWidget()
        # QScrollArea content widgets don't inherit a transparent look by
        # default -- without this, it falls back to Qt's default (grey)
        # palette background instead of the dashboard's dark theme (same
        # fix the todo widget's own tab pages already apply).
        self.notes_container.setStyleSheet("background: transparent;")
        self.notes_layout = QVBoxLayout(self.notes_container)
        self.notes_layout.setContentsMargins(0, 0, 0, 0)
        self.notes_layout.setSpacing(s(8))

        # Loading placeholder shown until first real data arrives
        self._loading_lbl = QLabel("⏳ Loading notes...")
        self._loading_lbl.setStyleSheet(Theme.get_style("DimLabel"))
        self.notes_layout.addWidget(self._loading_lbl)
        self.notes_layout.addStretch()

        self.scroll.setWidget(self.notes_container)
        list_layout.addWidget(self.scroll, stretch=1)

        # Same widget, position, and style as the todo widget's "+ Add Task".
        self.add_btn = QPushButton("+ Add Note")
        self.add_btn.setFixedHeight(32)
        self.add_btn.setStyleSheet(Theme.get_style("AddButton"))
        self.add_btn.clicked.connect(self.add_note)
        list_layout.addWidget(self.add_btn)

        self.stack.addWidget(self.list_page)

    # -------------------------------------------------------------------
    # EDITOR PAGE -- a single note's full text, filling the whole widget,
    # with a back button that saves and returns to the list.
    # -------------------------------------------------------------------
    def _build_editor_page(self):
        self.editor_page = QWidget()
        editor_layout = QVBoxLayout(self.editor_page)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(s(8))

        editor_top = QHBoxLayout()
        self.back_btn = QPushButton()
        self.back_btn.setFixedSize(30, 30)
        self.back_btn.setIcon(Theme.get_icon("chevron_left.svg", 16))
        self.back_btn.setIconSize(QSize(16, 16))
        self.back_btn.setStyleSheet(Theme.get_style("RefreshButton"))
        self.back_btn.setToolTip("Back to notes")
        self.back_btn.clicked.connect(self.show_list)
        editor_top.addWidget(self.back_btn)

        # A QLineEdit, not a QTextEdit -- a title is always a single line,
        # no word wrap needed or wanted.
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Title")
        self.title_input.setStyleSheet(Theme.get_style("SubtitleLabel"))
        editor_top.addWidget(self.title_input, 1)
        editor_layout.addLayout(editor_top)

        self.editor_text = QTextEdit()
        self.editor_text.setPlaceholderText("Type a note...")
        self.editor_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        editor_layout.addWidget(self.editor_text, stretch=1)

        # Both autosave on focus-out, same pattern as the todo widget's
        # task input -- also explicitly saved on the back button itself
        # (see show_list), rather than relying only on the button click
        # causing a focus change first.
        def _title_focus_out(event):
            self._save_open_note()
            QLineEdit.focusOutEvent(self.title_input, event)
        self.title_input.focusOutEvent = _title_focus_out

        def _text_focus_out(event):
            self._save_open_note()
            QTextEdit.focusOutEvent(self.editor_text, event)
        self.editor_text.focusOutEvent = _text_focus_out

        self.stack.addWidget(self.editor_page)

    def update_scaling(self):
        self.layout.setContentsMargins(s(15), s(8), s(15), s(10))
        if hasattr(self, 'add_btn'):
            self.add_btn.setFixedHeight(32)
        if hasattr(self, 'back_btn'):
            self.back_btn.setFixedSize(30, 30)
        if hasattr(self, 'notes_layout'):
            self.notes_layout.setSpacing(s(8))
        # No self.adjustSize() here -- DraggableWidget.update_scaling()
        # calls this method FIRST, before computing its own grow-only
        # resize off self.sizeHint(). Calling adjustSize() on this widget
        # (a layout-managed child, not a free-floating window) here would
        # shrink it to whichever page's sizeHint is currently active right
        # before the wrapper measures it, undoing _force_resize()'s fix and
        # reproducing the exact same "stretches, then snaps back" bug.

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self.router.dispatch("note.list")
        except Exception:
            pass

    def add_note(self):
        self._open_next_new_note = True
        try:
            self.router.dispatch("note.create", text="")
        except Exception as e:
            logging.error(f"Failed to create note: {e}")

    def open_note(self, note_id):
        note = self._notes_by_id.get(note_id)
        if note is None:
            return
        self._open_note_id = note_id
        self.title_input.blockSignals(True)
        self.title_input.setText(note.get("title", ""))
        self.title_input.blockSignals(False)
        self.editor_text.blockSignals(True)
        self.editor_text.setPlainText(note.get("text", ""))
        self.editor_text.blockSignals(False)
        self.stack.setCurrentWidget(self.editor_page)
        self.editor_text.setFocus()
        self._schedule_resize()

    def show_list(self):
        # Leaving a note completely blank (created via "+ Add Note" and
        # never typed into, title included) discards it instead of
        # persisting a permanent, invisible-until-you-scroll ghost entry --
        # only on this explicit back navigation, not on every focus-out,
        # so a transient alt-tab away from a freshly opened note can't
        # destroy it.
        if (
            self._open_note_id is not None
            and not self.title_input.text().strip()
            and not self.editor_text.toPlainText().strip()
        ):
            self._delete_note(self._open_note_id)
        else:
            self._save_open_note()
        self._open_note_id = None
        self.stack.setCurrentWidget(self.list_page)
        self._schedule_resize()

    def _save_open_note(self):
        if self._open_note_id is not None:
            # A note with real content but no title is saved as "Unnamed"
            # rather than left with an empty title -- the fully-blank case
            # (no title AND no text) is instead discarded, see show_list().
            title = self.title_input.text().strip() or "Unnamed"
            self._save_note(self._open_note_id, title, self.editor_text.toPlainText())

    def _save_note(self, note_id, title, text):
        try:
            self.router.dispatch("note.update", id=note_id, title=title, text=text)
        except Exception as e:
            logging.error(f"Failed to save note: {e}")

    def _delete_note(self, note_id):
        try:
            self.router.dispatch("note.delete", id=note_id)
        except Exception as e:
            logging.error(f"Failed to delete note: {e}")
        if note_id in self.note_rows:
            row_data = self.note_rows.pop(note_id)
            row_data["widget"].deleteLater()
        if self._open_note_id == note_id:
            self._open_note_id = None
            self.stack.setCurrentWidget(self.list_page)
        self._maybe_show_empty_label()

    def _maybe_show_empty_label(self):
        if not self.note_rows and self._empty_lbl is None:
            self._empty_lbl = QLabel("No notes yet.")
            self._empty_lbl.setStyleSheet(Theme.get_style("DimLabel"))
            self.notes_layout.insertWidget(0, self._empty_lbl)

    @staticmethod
    def _body_preview(text):
        """The row's dim subtitle is just the body's first line -- the
        title is now its own separate field, not derived from the text."""
        return text.split("\n", 1)[0] if text else ""

    def _create_note_row(self, note):
        note_id = note["id"]

        row = QFrame()
        row.setObjectName("NoteCard")
        row.setStyleSheet(Theme.get_style("NoteCard"))
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row_layout = QHBoxLayout(row)
        # Extra bottom padding specifically so the border doesn't hug the
        # preview line sitting right underneath the title.
        row_layout.setContentsMargins(s(12), s(10), s(12), s(12))
        row_layout.setSpacing(s(6))

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(s(2))

        title_lbl = QLabel()
        title_lbl.setWordWrap(True)
        subtitle_lbl = QLabel()
        subtitle_lbl.setStyleSheet(Theme.get_style("DimLabel"))
        subtitle_lbl.setWordWrap(True)

        text_col.addWidget(title_lbl)
        text_col.addWidget(subtitle_lbl)

        delete_btn = QPushButton()
        delete_btn.setFixedSize(26, 26)
        delete_btn.setIcon(Theme.get_icon("close.svg", 14))
        delete_btn.setIconSize(QSize(14, 14))
        delete_btn.setToolTip("Delete note")
        delete_btn.setStyleSheet(Theme.get_style("SmallDangerButton"))
        delete_btn.clicked.connect(lambda checked=False, nid=note_id: self._delete_note(nid))

        row_layout.addLayout(text_col, 1)
        row_layout.addWidget(delete_btn, 0, Qt.AlignmentFlag.AlignTop)

        # Clicking anywhere on the row (title/subtitle included, since
        # plain non-interactive QLabels don't consume the click) opens the
        # note -- except the delete button, a real child widget that
        # claims its own clicks before they ever reach this handler.
        row.mouseReleaseEvent = lambda e, nid=note_id: (
            self.open_note(nid) if e.button() == Qt.MouseButton.LeftButton else None
        )

        self.notes_layout.insertWidget(self.notes_layout.count() - 1, row)
        row_data = {"widget": row, "title_lbl": title_lbl, "subtitle_lbl": subtitle_lbl, "delete_btn": delete_btn}
        self.note_rows[note_id] = row_data
        self._update_row_preview(row_data, note)

    def _update_row_preview(self, row_data, note):
        title = note.get("title", "").strip()
        subtitle = self._body_preview(note.get("text", ""))
        # Bigger than SubtitleLabel's own default size -- this line is the
        # note's title, it should read as more prominent than the preview
        # line below it.
        if title:
            row_data["title_lbl"].setStyleSheet(Theme.get_style("SubtitleLabel") + f" font-size: {Theme.F_TITLE};")
            row_data["title_lbl"].setText(title)
        else:
            row_data["title_lbl"].setStyleSheet(Theme.get_style("DimLabel") + f" font-style: italic; font-size: {Theme.F_TITLE};")
            row_data["title_lbl"].setText("New note")
        row_data["subtitle_lbl"].setText(subtitle)
        row_data["subtitle_lbl"].setVisible(bool(subtitle))

    def update_status(self, data):
        notes = data.get("notes", [])
        self._notes_by_id = {n["id"]: n for n in notes}
        new_ids = set(self._notes_by_id.keys())

        if self._loading_lbl is not None:
            self._loading_lbl.deleteLater()
            self._loading_lbl = None

        existing_before = set(self.note_rows.keys())

        for old_id in list(self.note_rows.keys()):
            if old_id not in new_ids:
                self.note_rows[old_id]["widget"].deleteLater()
                del self.note_rows[old_id]

        newly_created_id = None
        for note_id, note in self._notes_by_id.items():
            if note_id in self.note_rows:
                self._update_row_preview(self.note_rows[note_id], note)
            else:
                self._create_note_row(note)
                if note_id not in existing_before:
                    newly_created_id = note_id

        if self._open_note_id is not None and self._open_note_id in self._notes_by_id:
            # Keep the open editor in sync if the note changed elsewhere,
            # but never clobber text the user is actively typing.
            note = self._notes_by_id[self._open_note_id]
            if not self.editor_text.hasFocus() and self.editor_text.toPlainText() != note.get("text", ""):
                self.editor_text.blockSignals(True)
                self.editor_text.setPlainText(note.get("text", ""))
                self.editor_text.blockSignals(False)
            if not self.title_input.hasFocus() and self.title_input.text() != note.get("title", ""):
                self.title_input.blockSignals(True)
                self.title_input.setText(note.get("title", ""))
                self.title_input.blockSignals(False)

        if self._open_next_new_note and newly_created_id is not None:
            self._open_next_new_note = False
            self.open_note(newly_created_id)

        if notes and self._empty_lbl is not None:
            self._empty_lbl.deleteLater()
            self._empty_lbl = None
        elif not notes:
            self._maybe_show_empty_label()

        self.notes_container.adjustSize()
        self._schedule_resize()

    def _schedule_resize(self):
        # Deferred resize ensures the parent wrapper picks up the new
        # layout geometry -- without this the widget only relaid out
        # correctly after the user manually resized it (see
        # clLightControlWidget). Needed not just after a data refresh but
        # also on every list<->editor page switch: QStackedWidget swapping
        # its current page doesn't itself trigger the wrapper to re-fit --
        # the newly shown page just gets squeezed into whatever size the
        # wrapper already happened to be.
        QTimer.singleShot(50, self._force_resize)

    def _force_resize(self):
        # No self.adjustSize() here -- this widget is a layout-managed
        # child of DraggableWidget, not a free-floating top-level window.
        # Calling adjustSize() on it snaps IT to whichever page's sizeHint
        # is currently active (e.g. the editor's, smaller than the list's),
        # fighting the wrapper's own (larger, grow-only) size and leaving a
        # visible gap between this widget's new, smaller edge and the
        # wrapper's actual bottom. DraggableWidget.update_scaling() is the
        # only thing that should ever resize anything here -- it grows the
        # wrapper to fit if needed but never shrinks it, so a user's manual
        # drag-resize (or a size just restored from ui_state.json) survives
        # every refresh and every list<->editor page switch, and Qt's own
        # layout system stretches this widget (and the stack, and whichever
        # page is current) to fill however big the wrapper actually is.
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, 'update_scaling'):
            parent.update_scaling()

    def get_standalone_min_size(self):
        return 320, 360
