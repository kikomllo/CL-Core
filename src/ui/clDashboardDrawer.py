from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QRadialGradient, QColor
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from clCalendarWidget import CalendarWidget
from clTheme import Theme
from clUIScaler import UIScaler
from ui.clDrawerControls import DragHandle, ElidedLabel, EventDot, MediaPill, RoundButton, letter_spaced
from ui.clLightControlWidget import LightControlWidget
from ui.clMediaWidget import MediaWidget
from ui.clReminderWidget import ReminderWidget
from ui.clTodoWidget import TodoWidget
from utils import clCalendarEvents as ev_util


def s(val):
    return UIScaler.get().scale(val)


class EventRow(QFrame):
    """One line in the day sheet: time, dot (solid = local, hollow = Google), title, source tag.
    Local events open the edit dialog on click; Google ones are read-only."""
    clicked = pyqtSignal(dict)

    def __init__(self, event, parent=None):
        super().__init__(parent)
        self.event = event
        self.editable = ev_util.is_editable(event)
        self.setObjectName("SheetRow")
        self.setStyleSheet(Theme.get_style("SheetRow" if self.editable else "SheetRowStatic"))
        self.setFixedHeight(42)
        if self.editable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(s(14))

        self.time_lbl = QLabel(ev_util.time_label(event, "%H:%M"))
        self.time_lbl.setFixedWidth(s(56))
        self.time_lbl.setStyleSheet(Theme.get_style("SheetTime"))
        self.dot = EventDot(hollow=ev_util.event_source(event) != ev_util.SOURCE_LOCAL)
        self.title_lbl = ElidedLabel(event.get("summary", ""))
        self.title_lbl.setStyleSheet(Theme.get_style("SheetEventTitle"))
        layout.addWidget(self.time_lbl)
        layout.addWidget(self.dot)
        layout.addWidget(self.title_lbl, 1)
        self.tag = None
        if ev_util.event_source(event) == ev_util.SOURCE_GOOGLE:
            self.tag = QLabel("GOOGLE")
            self.tag.setStyleSheet(Theme.get_style("GoogleTag"))
            letter_spaced(self.tag, 1)
            layout.addWidget(self.tag, 0, Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.editable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.event)


class NextDayRow(QFrame):
    """"Mon 12 · 3 events  ->": jumps the calendar to that day."""
    clicked = pyqtSignal(datetime)

    def __init__(self, date, count, parent=None):
        super().__init__(parent)
        self.date = date
        self.setObjectName("SheetRow")
        self.setStyleSheet(Theme.get_style("SheetRow"))
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(f"{date.strftime('%a')} {date.day} · {count} event{'' if count == 1 else 's'}")
        self.label.setStyleSheet(Theme.get_style("SheetDim"))
        arrow = QLabel("→")
        arrow.setStyleSheet(Theme.get_style("SheetDim"))
        layout.addWidget(self.label)
        layout.addStretch()
        layout.addWidget(arrow)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(datetime.combine(self.date, datetime.min.time()))


class DayAgenda(QWidget):
    """The sheet's day section: "Fri 9 / 4 EVENTS / +", that day's events, then the next busy days."""
    add_event_signal = pyqtSignal()
    edit_event_signal = pyqtSignal(dict)
    day_requested = pyqtSignal(datetime)
    NEXT_DAYS = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_date = datetime.now()
        self.current_events_data = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, s(4))
        header.setSpacing(s(10))
        self.lbl_title = QLabel()
        self.lbl_title.setStyleSheet(Theme.get_style("SheetTitle"))
        self.lbl_count = QLabel()
        self.lbl_count.setStyleSheet(Theme.get_style("SheetCount"))
        letter_spaced(self.lbl_count, 2)
        self.btn_add = RoundButton(34, "add.svg", 16, kind="add")
        self.btn_add.setToolTip("Add event")
        self.btn_add.clicked.connect(self.add_event_signal.emit)
        header.addWidget(self.lbl_title, 0, Qt.AlignmentFlag.AlignBottom)
        header.addWidget(self.lbl_count, 0, Qt.AlignmentFlag.AlignBottom)
        header.addStretch()
        header.addWidget(self.btn_add)
        self._layout.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.events_layout = QVBoxLayout(self.scroll_content)
        self.events_layout.setContentsMargins(0, 0, 0, 0)
        self.events_layout.setSpacing(0)
        self.events_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        self.scroll.setMinimumHeight(s(84))
        self._layout.addWidget(self.scroll, 1)

        self.next_layout = QVBoxLayout()
        self.next_layout.setContentsMargins(0, 0, 0, 0)
        self.next_layout.setSpacing(0)
        self._layout.addLayout(self.next_layout)
        self.render_events()

    def update_scaling(self):
        self.render_events()

    def load_events(self, events_data):
        self.current_events_data = events_data
        self.render_events()

    def set_specific_day(self, date):
        self.selected_date = date
        self.render_events()

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                # Not setParent(None): that turns the row into a parentless
                # top-level window, and hiding/destroying one of those while the
                # main window is momentarily hidden makes Qt quit the whole app.
                w.hide()
                w.deleteLater()

    def _events(self):
        return [e for e in self.current_events_data.get('events', []) if ev_util.event_start(e) is not None]

    def render_events(self):
        self._clear(self.events_layout)
        self._clear(self.next_layout)
        day = self.selected_date.date()
        events = self._events()
        todays = sorted((e for e in events if ev_util.starts_on(e, day)), key=ev_util.sort_key)

        self.lbl_title.setText(f"{self.selected_date.strftime('%a')} {self.selected_date.day}")
        self.lbl_count.setText("NO EVENTS" if not todays else f"{len(todays)} EVENT{'' if len(todays) == 1 else 'S'}")

        for ev in todays:
            row = EventRow(ev)
            row.clicked.connect(self.edit_event_signal.emit)
            self.events_layout.addWidget(row)
        if not todays:
            empty = QLabel("Nothing planned")
            empty.setStyleSheet(Theme.get_style("SheetDim"))
            empty.setFixedHeight(s(42))
            self.events_layout.addWidget(empty)

        upcoming = {}
        for ev in events:
            d = ev_util.event_start(ev).date()
            if d > day:
                upcoming[d] = upcoming.get(d, 0) + 1
        for d in sorted(upcoming)[: self.NEXT_DAYS]:
            row = NextDayRow(d, upcoming[d])
            row.clicked.connect(self.day_requested.emit)
            self.next_layout.addWidget(row)


class WidgetCarousel(QWidget):
    """Round icon dock (To-do, Reminders, Media, Lights) over the stack of full widgets."""
    PAGES = [("To-do", "todos.svg", "TO-DO"), ("Reminders", "reminders.svg", "REMINDERS"),
             ("Media", "music.svg", "MEDIA"), ("Lights", "lights.svg", "LIGHTS")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(s(10))

        self.stack = QStackedWidget()
        self.todo_widget = TodoWidget()
        self.reminder_widget = ReminderWidget(grid_mode=True)
        self.media_widget = MediaWidget()
        self.lights_widget = LightControlWidget()
        for w in (self.todo_widget, self.reminder_widget, self.media_widget, self.lights_widget):
            self.stack.addWidget(w)

        dock = QHBoxLayout()
        dock.setContentsMargins(0, 0, 0, 0)
        dock.setSpacing(s(4))
        self.dock_buttons = []
        for i, (name, icon, _) in enumerate(self.PAGES):
            btn = RoundButton(44, icon, 20, kind="dock")
            btn.setToolTip(name)
            btn.clicked.connect(lambda _=False, idx=i: self.stack.setCurrentIndex(idx))
            dock.addWidget(btn)
            self.dock_buttons.append(btn)
        dock.addStretch()
        self.lbl_indicator = QLabel(self.PAGES[0][2])
        self.lbl_indicator.setStyleSheet(Theme.get_style("DockLabel"))
        letter_spaced(self.lbl_indicator, 2)
        dock.addWidget(self.lbl_indicator, 0, Qt.AlignmentFlag.AlignVCenter)

        self._layout.addLayout(dock)
        self._layout.addWidget(self.stack, 1)
        self.stack.currentChanged.connect(self.on_slide_changed)
        self.todo_widget.open_count_changed.connect(self.dock_buttons[0].set_badge)
        self.update_indicator()

    def on_slide_changed(self, idx):
        self.update_indicator()
        p = self.parent()
        while p is not None:
            if hasattr(p, 'save_ui_state'):
                p.save_ui_state()
                break
            p = p.parent()

    def prev_slide(self):
        self.stack.setCurrentIndex((self.stack.currentIndex() - 1) % self.stack.count())

    def next_slide(self):
        self.stack.setCurrentIndex((self.stack.currentIndex() + 1) % self.stack.count())

    def update_indicator(self):
        idx = self.stack.currentIndex()
        for i, btn in enumerate(self.dock_buttons):
            btn.set_selected(i == idx)
        if 0 <= idx < len(self.PAGES):
            self.lbl_indicator.setText(self.PAGES[idx][2])


class DashboardDrawer(QWidget):
    """The side panel: calendar on top; below it a rounded sheet holding the selected day, the
    widget dock and stack, and the media pill."""
    EDGE = 2   # the drawer's left border

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DashboardDrawer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(Theme.get_style("DashboardDrawer"))

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(s(self.EDGE), s(22), 0, 0)
        self._layout.setSpacing(0)

        self.calendar = CalendarWidget(self)
        self.sheet = QFrame(self)
        self.sheet.setObjectName("DaySheet")
        self.sheet.setStyleSheet(Theme.get_style("DaySheet"))
        self.sheet_layout = QVBoxLayout(self.sheet)
        self.sheet_layout.setContentsMargins(s(22), s(10), s(22), s(14))
        self.sheet_layout.setSpacing(0)

        self.handle = DragHandle()
        self.up_next = DayAgenda()
        self.divider = QFrame()
        self.divider.setFixedHeight(1)
        self.divider.setStyleSheet(Theme.get_style("SheetDivider"))
        self.carousel = WidgetCarousel(self)
        self.media_pill = MediaPill()

        self.sheet_layout.addWidget(self.handle, 0, Qt.AlignmentFlag.AlignHCenter)
        self.sheet_layout.addSpacing(s(12))
        self.sheet_layout.addWidget(self.up_next, 1)
        self.sheet_layout.addSpacing(s(6))
        self.sheet_layout.addWidget(self.divider)
        self.sheet_layout.addSpacing(s(16))
        self.sheet_layout.addWidget(self.carousel)
        self.sheet_layout.addSpacing(s(12))
        self.sheet_layout.addWidget(self.media_pill)

        self._layout.addWidget(self.calendar)
        self._layout.addWidget(self.sheet, 1)

        self.calendar.day_selected_signal.connect(self.up_next.set_specific_day)
        self.up_next.add_event_signal.connect(lambda: self.calendar.open_event_input(None))
        self.up_next.edit_event_signal.connect(self.calendar.open_event_input)
        self.up_next.day_requested.connect(self.calendar.select_date)
        self.up_next.set_specific_day(self.calendar.current_date)

    def paintEvent(self, event):
        super().paintEvent(event)
        # Soft orange glow from the top-right, behind the header (radial-gradient(circle at 92% 0%)).
        w, h = self.width(), self.height()
        center_x = 0.92 * w
        radius = 0.46 * ((center_x ** 2 + h ** 2) ** 0.5)
        glow = QRadialGradient(center_x, 0, radius)
        glow.setColorAt(0, QColor(255, 140, 0, 41))
        glow.setColorAt(1, QColor(255, 140, 0, 0))
        p = QPainter(self)
        p.fillRect(self.rect(), glow)
        p.end()

    def update_scaling(self):
        self._layout.setContentsMargins(s(self.EDGE), s(22), 0, 0)
        self.sheet_layout.setContentsMargins(s(22), s(10), s(22), s(14))
        self.calendar.update_scaling()
        self.up_next.update_scaling()
