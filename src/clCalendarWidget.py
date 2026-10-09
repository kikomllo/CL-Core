import json
from calendar import monthrange
from datetime import datetime, timedelta

from PyQt6.QtCore import QDate, QTime, Qt, pyqtSignal
from PyQt6.QtWidgets import (QDateEdit, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QSizePolicy, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget)

from clTheme import Theme
from clUIScaler import UIScaler
from ui.clDrawerControls import DayBars, ElidedLabel, RoundButton, SegmentedControl
from utils import clCalendarEvents as ev_util

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DOWS = ["M", "T", "W", "T", "F", "S", "S"]


def s(val):
    return UIScaler.get().scale(val)


def _exact_height(widget, px):
    """Pin a widget to `px` physical pixels. setFixedHeight is wrapped by the UI scaler (it would
    scale `px` a second time); min/max height are not."""
    widget.setMinimumHeight(px)
    widget.setMaximumHeight(px)


class DayCell(QPushButton):
    """One square day: number plus up to three event bars."""

    def __init__(self, date, events, selected, today, parent=None):
        super().__init__(parent)
        self.date = date
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(s(24))
        style, num_style = ("DayCellSelected", "DayNumSelected") if selected else (
            ("DayCellToday", "DayNumToday") if today else ("DayCell", "DayNum"))
        self.setStyleSheet(Theme.get_style(style))
        self.setAccessibleName(f"{date.strftime('%B')} {date.day}, {len(events)} events")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(s(5))
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.number = QLabel(str(date.day))
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.number.setStyleSheet(Theme.get_style(num_style))
        self.number.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.number, 0, Qt.AlignmentFlag.AlignHCenter)
        self.bars = DayBars()
        self.bars.set_bars([ev_util.event_source(e) != ev_util.SOURCE_LOCAL for e in events], selected=selected)
        layout.addWidget(self.bars, 0, Qt.AlignmentFlag.AlignHCenter)


class CalendarWidget(QWidget):
    """Header, Day/Week/Month pill and the day grid. Its height follows its width so the day
    cells are squares; give it no stretch in the parent layout.

    Modes: "month" (grid), "week" (one row), "day" (grid hidden, the sheet below is the day) and
    "year" (month picker, reached from the title). `current_date` is the selected day.
    """
    day_selected_signal = pyqtSignal(datetime)

    HEADER_H = 40
    SEG_TOP, SEG_H, SEG_BOTTOM = 16, 36, 14
    BOTTOM = 16
    SIDE = 14          # inset of the whole widget (the drawer's padding)
    GRID_SIDE = 6      # extra inset of the grid
    GAP = 4
    DOW_H = 20
    YEAR_ROWS = 5      # the year picker is as tall as a five-week month

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_date = datetime.now()
        self.current_mode = "month"
        self.events = []
        self._cells = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(s(self.SIDE), 0, s(self.SIDE), s(self.BOTTOM))
        outer.setSpacing(0)

        # --- header: month title + year, then sync / chevrons / Today ---
        header = QWidget()
        _exact_height(header, s(self.HEADER_H))
        hl = QHBoxLayout(header)
        hl.setContentsMargins(s(8), 0, s(8), 0)
        hl.setSpacing(s(10))
        self.btn_title = QPushButton()
        self.btn_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_title.setStyleSheet(Theme.get_style("DrawerMonthTitle"))
        self.btn_title.setToolTip("Pick a month")
        self.btn_title.clicked.connect(self.toggle_year)
        self.lbl_year = ElidedLabel()
        self.lbl_year.setStyleSheet(Theme.get_style("DrawerYearLabel"))
        hl.addWidget(self.btn_title, 0, Qt.AlignmentFlag.AlignBottom)
        hl.addWidget(self.lbl_year, 0, Qt.AlignmentFlag.AlignBottom)
        hl.addStretch()
        self.lbl_sync = QLabel()
        self.lbl_sync.setStyleSheet(Theme.get_style("DrawerSyncLabel"))
        self.lbl_sync.hide()
        self.btn_prev = RoundButton(24, "chevron_left.svg", 14, kind="ghost", ink=Theme.C_TEXT_DIM)
        self.btn_next = RoundButton(24, "chevron_right.svg", 14, kind="ghost", ink=Theme.C_TEXT_DIM)
        self.btn_prev.setToolTip("Previous")
        self.btn_next.setToolTip("Next")
        self.btn_prev.clicked.connect(lambda: self.shift(-1))
        self.btn_next.clicked.connect(lambda: self.shift(1))
        self.btn_today = QPushButton("Today")
        self.btn_today.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_today.setFixedHeight(28)
        self.btn_today.setStyleSheet(Theme.get_style("DrawerPillButton"))
        self.btn_today.clicked.connect(self.go_to_today)
        for w in (self.lbl_sync, self.btn_prev, self.btn_next, self.btn_today):
            hl.addWidget(w, 0, Qt.AlignmentFlag.AlignBottom)
        hl.setSpacing(s(6))
        outer.addWidget(header)

        # --- Day / Week / Month ---
        seg_wrap = QWidget()
        _exact_height(seg_wrap, s(self.SEG_TOP + self.SEG_H + self.SEG_BOTTOM))
        sl = QVBoxLayout(seg_wrap)
        sl.setContentsMargins(0, s(self.SEG_TOP), 0, s(self.SEG_BOTTOM))
        self.segments = SegmentedControl([("day", "DAY"), ("week", "WEEK"), ("month", "MONTH")])
        self.segments.set_value("month")
        self.segments.changed.connect(self.set_mode)
        sl.addWidget(self.segments)
        outer.addWidget(seg_wrap)

        # --- grid area: weekday row + cells, or the year picker ---
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(s(self.GRID_SIDE), 0, s(self.GRID_SIDE), 0)
        self.cells_page = QWidget()
        cl = QVBoxLayout(self.cells_page)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(s(self.GAP))
        self.dow_row = QWidget()
        _exact_height(self.dow_row, s(self.DOW_H))
        dl = QGridLayout(self.dow_row)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(s(self.GAP))
        for col, d in enumerate(DOWS):
            lbl = QLabel(d)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(Theme.get_style("DowLabel"))
            dl.addWidget(lbl, 0, col)
        cl.addWidget(self.dow_row)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(s(self.GAP))
        cl.addWidget(self.grid_host)
        cl.addStretch()
        self.year_page = QWidget()
        self.year_grid = QGridLayout(self.year_page)
        self.year_grid.setContentsMargins(0, s(self.DOW_H + self.GAP), 0, 0)
        self.year_grid.setSpacing(s(self.GAP))
        self.stack.addWidget(self.cells_page)
        self.stack.addWidget(self.year_page)
        outer.addWidget(self.stack)

        self.update_ui()

    # --- state -------------------------------------------------------------------------------
    def set_mode(self, mode):
        if mode in ("day", "week", "month", "year"):
            self.current_mode = mode
            self.update_ui()

    def toggle_year(self):
        self.set_mode("month" if self.current_mode == "year" else "year")

    def set_sync_status(self, text):
        """Google sync indicator ("2 min"); None hides it. Unused until the Google integration lands."""
        self.lbl_sync.setText(f"● {text}" if text else "")
        self.lbl_sync.setVisible(bool(text))

    def select_date(self, dt):
        self.current_date = dt
        self.update_ui()
        self.day_selected_signal.emit(self.current_date)

    def go_to_today(self):
        now = datetime.now()
        self.current_date = now
        if self.current_mode == "year":
            self.current_mode = "month"
        self.update_ui()
        self.day_selected_signal.emit(self.current_date)

    def go_to_day(self, day):
        self.select_date(self.current_date.replace(day=day))

    def go_to_month(self, month):
        day = min(self.current_date.day, monthrange(self.current_date.year, month)[1])
        self.current_mode = "month"
        self.select_date(self.current_date.replace(month=month, day=day))

    def shift(self, step):
        """Previous / next: a year, month, week or day depending on the mode."""
        d = self.current_date
        if self.current_mode == "year":
            day = min(d.day, monthrange(d.year + step, d.month)[1])
            new = d.replace(year=d.year + step, day=day)
        elif self.current_mode == "month":
            index = d.year * 12 + d.month - 1 + step
            year, month = divmod(index, 12)
            new = d.replace(year=year, month=month + 1, day=min(d.day, monthrange(year, month + 1)[1]))
        elif self.current_mode == "week":
            new = d + timedelta(days=7 * step)
        else:
            new = d + timedelta(days=step)
        self.select_date(new)

    def events_on(self, date):
        return sorted((e for e in self.events if ev_util.starts_on(e, date)), key=ev_util.sort_key)

    def load_events(self, events_data):
        self.events = events_data.get('events', [])
        self.update_ui()

    # --- sizing ------------------------------------------------------------------------------
    def _week_rows(self):
        first_weekday, days_in_month = monthrange(self.current_date.year, self.current_date.month)
        return -(-(first_weekday + days_in_month) // 7)

    def _cell_side(self, width):
        inner = width - 2 * s(self.SIDE) - 2 * s(self.GRID_SIDE)
        return max(s(24), (inner - 6 * s(self.GAP)) // 7)

    def _grid_rows(self):
        return {"week": 1, "month": self._week_rows(), "year": self.YEAR_ROWS}.get(self.current_mode, 0)

    def fitted_height(self, width):
        fixed = s(self.HEADER_H) + s(self.SEG_TOP + self.SEG_H + self.SEG_BOTTOM) + s(self.BOTTOM)
        rows = self._grid_rows()
        if not rows:
            return fixed
        grid = s(self.DOW_H) + s(self.GAP) + rows * self._cell_side(width) + (rows - 1) * s(self.GAP)
        return fixed + grid

    def _fit(self):
        width = self.width()
        side = self._cell_side(width)
        for cell in self._cells:
            _exact_height(cell, side)
        rows = self.YEAR_ROWS
        year_h = (rows * side + (rows - 1) * s(self.GAP) - 3 * s(self.GAP)) // 4
        for i in range(self.year_grid.count()):
            w = self.year_grid.itemAt(i).widget()
            if w:
                _exact_height(w, year_h)
        _exact_height(self, self.fitted_height(width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    # --- rendering ---------------------------------------------------------------------------
    def update_ui(self):
        d, mode = self.current_date, self.current_mode
        if mode == "year":
            title, sub = str(d.year), ""
        elif mode == "month":
            title, sub = MONTHS[d.month - 1], str(d.year)
        elif mode == "week":
            start = d - timedelta(days=d.weekday())
            end = start + timedelta(days=6)
            title = (f"{start.strftime('%b')} {start.day} – {end.day}" if start.month == end.month
                     else f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}")
            sub = f"W{d.isocalendar()[1]}"
        else:
            title, sub = d.strftime("%A"), f"{d.strftime('%b')} {d.day}, {d.year}"
        self.btn_title.setText(title)
        # Long titles (a week range, "Wednesday") use a smaller size so the header never outgrows the drawer.
        self.btn_title.setStyleSheet(Theme.get_style("DrawerMonthTitle" if mode in ("month", "year") else "DrawerMonthTitleSmall"))
        self.lbl_year.setText(sub)
        self.segments.set_value(None if mode == "year" else mode)

        self.stack.setVisible(mode != "day")
        self.stack.setCurrentWidget(self.year_page if mode == "year" else self.cells_page)
        if mode == "year":
            self._render_year()
        elif mode != "day":
            self._render_cells()
        self._fit()

    def _clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                # Not setParent(None): see DayAgenda._clear.
                w.hide()
                w.deleteLater()

    def _render_cells(self):
        self._clear(self.grid)
        self._cells = []
        today = datetime.now().date()
        sel = self.current_date.date()
        if self.current_mode == "week":
            start = self.current_date - timedelta(days=self.current_date.weekday())
            dates = [(start + timedelta(days=i)).date() for i in range(7)]
            positions = [(0, i) for i in range(7)]
        else:
            first_weekday, days_in_month = monthrange(self.current_date.year, self.current_date.month)
            dates = [self.current_date.replace(day=n).date() for n in range(1, days_in_month + 1)]
            positions = [divmod(first_weekday + i, 7) for i in range(days_in_month)]
        for date, (row, col) in zip(dates, positions):
            cell = DayCell(date, self.events_on(date), date == sel, date == today)
            cell.clicked.connect(lambda _=False, dt=date: self.select_date(datetime.combine(dt, self.current_date.time())))
            self.grid.addWidget(cell, row, col)
            self._cells.append(cell)
        for col in range(7):
            self.grid.setColumnStretch(col, 1)

    def _render_year(self):
        self._clear(self.year_grid)
        self._cells = []
        now = datetime.now()
        for i, name in enumerate(MONTHS):
            btn = QPushButton(name[:3])
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            current = (i + 1 == now.month and self.current_date.year == now.year)
            chosen = (i + 1 == self.current_date.month)
            btn.setStyleSheet(Theme.get_style("DayCellSelected" if chosen else "DayCellToday" if current else "DayCell")
                              + " QPushButton { color: #ffe6cc; font-size: 14px; font-weight: 700; }")
            btn.clicked.connect(lambda _=False, m=i + 1: self.go_to_month(m))
            row, col = divmod(i, 3)
            self.year_grid.addWidget(btn, row, col)
        for col in range(3):
            self.year_grid.setColumnStretch(col, 1)

    def update_scaling(self):
        self._fit()

    # --- add / edit dialog -------------------------------------------------------------------
    def open_event_input(self, event_data=None):
        if not hasattr(self, 'event_dialog') or self.event_dialog is None:
            from PyQt6.QtWidgets import QDialog
            self.event_dialog = QDialog(self)
            self.event_dialog.setWindowTitle("Create Event")
            self.event_dialog.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
            self.event_dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.event_dialog.setStyleSheet(Theme.get_style("EventDialogBase"))

            main_layout = QVBoxLayout(self.event_dialog)
            main_layout.setContentsMargins(0, 0, 0, 0)
            bg_frame = QFrame()
            bg_frame.setObjectName("BgFrame")
            main_layout.addWidget(bg_frame)

            dlg_layout = QVBoxLayout(bg_frame)
            dlg_layout.setContentsMargins(s(14), s(14), s(14), s(14))
            dlg_layout.setSpacing(s(10))

            self.dialog_title_lbl = QLabel("NEW CALENDAR EVENT")
            self.dialog_title_lbl.setStyleSheet(Theme.get_style("BadgeLabel"))
            dlg_layout.addWidget(self.dialog_title_lbl)

            lbl_name = QLabel("Event Title:")
            lbl_name.setStyleSheet("background: transparent; border: none;")
            dlg_layout.addWidget(lbl_name)

            self.input_title = QLineEdit()
            self.input_title.setPlaceholderText("e.g. Doctor Appointment...")
            dlg_layout.addWidget(self.input_title)

            lbl_dt = QLabel("Date & Time:")
            lbl_dt.setStyleSheet("background: transparent; border: none;")
            dlg_layout.addWidget(lbl_dt)

            dt_layout = QHBoxLayout()
            dt_layout.setSpacing(s(8))

            self.input_date = QDateEdit(QDate.currentDate())
            self.input_date.setDisplayFormat("yyyy-MM-dd")
            self.input_date.setCalendarPopup(False)
            self.input_date.setButtonSymbols(QDateEdit.ButtonSymbols.NoButtons)
            self.input_date.setAlignment(Qt.AlignmentFlag.AlignCenter)

            self.input_time = QTimeEdit(QTime.currentTime())
            self.input_time.setDisplayFormat("HH:mm")
            self.input_time.setButtonSymbols(QTimeEdit.ButtonSymbols.NoButtons)
            self.input_time.setAlignment(Qt.AlignmentFlag.AlignCenter)

            dt_layout.addWidget(self.input_date, stretch=1)
            dt_layout.addWidget(self.input_time, stretch=1)
            dlg_layout.addLayout(dt_layout)

            btn_layout = QHBoxLayout()
            btn_layout.setSpacing(s(6))

            self.btn_dialog_delete = QPushButton("Delete")
            self.btn_dialog_delete.setStyleSheet(Theme.get_style("LightDangerButton"))
            self.btn_dialog_delete.clicked.connect(self.submit_delete)

            btn_cancel = QPushButton("Cancel")
            btn_cancel.setStyleSheet(Theme.get_style("MediaSmallBtn"))
            btn_cancel.clicked.connect(self.event_dialog.hide)

            btn_save = QPushButton("Save Event")
            btn_save.clicked.connect(self.submit_event)

            btn_layout.addWidget(self.btn_dialog_delete, stretch=1)
            btn_layout.addWidget(btn_cancel, stretch=1)
            btn_layout.addWidget(btn_save, stretch=1)
            dlg_layout.addLayout(btn_layout)

            self.input_title.returnPressed.connect(self.submit_event)

        # Center dialog on the widget
        dlg_width = 270
        dlg_height = 220
        self.event_dialog.resize(dlg_width, dlg_height)

        # Map to global properly for popup
        anchor = self.parentWidget() or self
        global_pos = anchor.mapToGlobal(self.pos() if self.parentWidget() else self.rect().topLeft())
        cx = global_pos.x() + (self.width() - dlg_width) // 2
        cy = global_pos.y() + (self.height() - dlg_height) // 2
        self.event_dialog.move(cx, cy)
        self.input_title.clear()

        if event_data:
            self.editing_event_id = event_data.get('id')
            self.dialog_title_lbl.setText("EDIT CALENDAR EVENT")
            self.input_title.setText(event_data.get('summary', ''))
            self.btn_dialog_delete.show()
            dt = ev_util.event_start(event_data)
            if dt:
                self.input_date.setDate(QDate(dt.year, dt.month, dt.day))
                self.input_time.setTime(QTime(dt.hour, dt.minute))
            else:
                self.input_date.setDate(QDate.currentDate())
                self.input_time.setTime(QTime.currentTime())
        else:
            self.editing_event_id = None
            self.dialog_title_lbl.setText("NEW CALENDAR EVENT")
            self.btn_dialog_delete.hide()
            d = self.current_date
            self.input_date.setDate(QDate(d.year, d.month, d.day))   # a new event lands on the selected day
            self.input_time.setTime(QTime.currentTime())

        self.event_dialog.show()
        self.event_dialog.raise_()
        self.input_title.setFocus()

    def submit_delete(self):
        if getattr(self, 'editing_event_id', None):
            import paho.mqtt.publish as publish
            publish.single("jarvis/sys/calendar/control", json.dumps({"action": "delete", "id": self.editing_event_id}), hostname="localhost", qos=0)
            self.editing_event_id = None
            self.event_dialog.hide()

    def submit_event(self):
        if hasattr(self, 'input_title') and hasattr(self, 'input_date') and hasattr(self, 'input_time'):
            title = self.input_title.text().strip()
            d = self.input_date.date().toString("yyyy-MM-dd")
            t = self.input_time.time().toString("HH:mm")
            time_str = f"{d} {t}"
            if title:
                import paho.mqtt.publish as publish
                payload = {"event": title, "time_str": time_str}
                if getattr(self, 'editing_event_id', None):
                    payload["id"] = self.editing_event_id
                publish.single("jarvis/sys/calendar/create", json.dumps(payload), hostname="localhost", qos=0)
                self.editing_event_id = None
                self.event_dialog.hide()

    def get_standalone_min_size(self):
        return 320, 380
