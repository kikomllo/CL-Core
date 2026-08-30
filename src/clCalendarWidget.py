import json
import logging
from clTheme import Theme
from datetime import datetime, timedelta
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QGridLayout, QScrollArea, QFrame, QSizePolicy, QLineEdit, QDateEdit, QTimeEdit
from PyQt6.QtCore import Qt, pyqtSignal, QDate, QTime

class CalendarWidget(QWidget):
    day_selected_signal = pyqtSignal(datetime)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(Theme.get_style("Panel"))
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        
        # --- Top Navigation ---
        self.nav_layout = QHBoxLayout()
        
        self.btn_left = QPushButton("< Year")
        self.btn_left.setStyleSheet(Theme.get_style("CalendarNavBtn"))
        self.btn_left.setFixedSize(65, 28)
        self.btn_left.clicked.connect(self.navigate_left)
        
        self.lbl_title = QLabel("Calendar")
        self.lbl_title.setStyleSheet(Theme.get_style("CalendarTitle"))
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.btn_right = QPushButton("Week >")
        self.btn_right.setStyleSheet(Theme.get_style("CalendarNavBtn"))
        self.btn_right.setFixedSize(65, 28)
        self.btn_right.clicked.connect(self.navigate_right)
        
        self.nav_layout.addWidget(self.btn_left)
        self.nav_layout.addWidget(self.lbl_title)
        self.nav_layout.addWidget(self.btn_right)
        
        self.layout.addLayout(self.nav_layout)
        
        # --- Stacked Widget for Views ---
        self.stack = QStackedWidget()
        
        self.year_view = QWidget()
        self.month_view = QWidget()
        self.week_view = QWidget()
        self.day_view = QWidget()
        
        self.init_year_view()
        self.init_month_view()
        self.init_week_view()
        self.init_day_view()
        
        self.stack.addWidget(self.year_view)
        self.stack.addWidget(self.month_view)
        self.stack.addWidget(self.week_view)
        self.stack.addWidget(self.day_view)
        
        self.layout.addWidget(self.stack)
                
        self.current_date = datetime.now()
        self.current_mode = "month"
        self.events = []
        
        self.update_ui()
        
    def navigate_left(self):
        if self.current_mode == "day": self.current_mode = "week"
        elif self.current_mode == "week": self.current_mode = "month"
        elif self.current_mode == "month": self.current_mode = "year"
        self.update_ui()

    def navigate_right(self):
        if self.current_mode == "year": self.current_mode = "month"
        elif self.current_mode == "month": self.current_mode = "week"
        elif self.current_mode == "week": self.current_mode = "day"
        self.update_ui()

    def update_ui(self):
        if self.current_mode == "year":
            self.lbl_title.setText(self.current_date.strftime("%Y"))
            self.btn_left.hide()
            self.btn_right.setText("Month >")
            self.btn_right.show()
            self.stack.setCurrentWidget(self.year_view)
            self.render_year()
        elif self.current_mode == "month":
            self.lbl_title.setText(self.current_date.strftime("%B %Y"))
            self.btn_left.setText("< Year")
            self.btn_left.show()
            self.btn_right.setText("Week >")
            self.btn_right.show()
            self.stack.setCurrentWidget(self.month_view)
            self.render_month()
        elif self.current_mode == "week":
            # Start of week (Monday)
            start_week = self.current_date - timedelta(days=self.current_date.weekday())
            end_week = start_week + timedelta(days=6)
            self.lbl_title.setText(f"{start_week.strftime('%b %d')} - {end_week.strftime('%b %d, %Y')}")
            self.btn_left.setText("< Month")
            self.btn_left.show()
            self.btn_right.setText("Day >")
            self.btn_right.show()
            self.stack.setCurrentWidget(self.week_view)
            self.render_week()
        elif self.current_mode == "day":
            self.lbl_title.setText(self.current_date.strftime("%A, %B %d, %Y"))
            self.btn_left.setText("< Week")
            self.btn_left.show()
            self.btn_right.hide()
            self.stack.setCurrentWidget(self.day_view)
            self.render_day()

    def init_year_view(self):
        self.year_layout = QGridLayout(self.year_view)
        self.year_layout.setSpacing(5)
        
    def render_year(self):
        # Clear layout
        for i in reversed(range(self.year_layout.count())): 
            widget = self.year_layout.itemAt(i).widget()
            if widget: widget.deleteLater()
            
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        for i, month in enumerate(months):
            btn = QPushButton(month)
            if i + 1 == datetime.now().month and self.current_date.year == datetime.now().year:
                btn.setStyleSheet(Theme.get_style("CalendarYearBtnActive"))
            else:
                btn.setStyleSheet(Theme.get_style("CalendarYearBtnInactive"))
            btn.setMinimumHeight(60)
            btn.clicked.connect(lambda checked, m=i+1: self.go_to_month(m))
            row, col = divmod(i, 3)
            self.year_layout.addWidget(btn, row, col)
            
    def go_to_month(self, month):
        self.current_date = self.current_date.replace(month=month, day=1)
        self.current_mode = "month"
        self.update_ui()

    def init_month_view(self):
        self.month_layout = QGridLayout(self.month_view)
        self.month_layout.setSpacing(2)
        
    def render_month(self):
        for i in reversed(range(self.month_layout.count())): 
            widget = self.month_layout.itemAt(i).widget()
            if widget: widget.deleteLater()
            
        days = ["M", "T", "W", "T", "F", "S", "S"]
        for col, day in enumerate(days):
            lbl = QLabel(day)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(Theme.get_style("CalendarDayLabel"))
            lbl.setFixedHeight(26)
            self.month_layout.addWidget(lbl, 0, col)
            
        # Very naive calendar rendering
        first_day = self.current_date.replace(day=1)
        start_day = first_day.weekday()
        
        row, col = 1, start_day
        for day in range(1, 32):
            try:
                current = self.current_date.replace(day=day)
            except ValueError:
                break # Reached end of month
                
            btn = QPushButton()
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            
            btn_layout = QVBoxLayout(btn)
            btn_layout.setContentsMargins(2, 2, 2, 2)
            btn_layout.setSpacing(0)
            
            lbl_day = QLabel(str(day))
            lbl_day.setAlignment(Qt.AlignmentFlag.AlignCenter)
            btn_layout.addWidget(lbl_day)
            
            events_today = sum(1 for ev in self.events if datetime.fromisoformat(ev['start']['dateTime']).date() == current.date())
            if events_today > 0:
                dots_layout = QHBoxLayout()
                dots_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                dots_layout.setSpacing(2)
                for _ in range(min(events_today, 3)):
                    dot = QFrame()
                    dot.setFixedSize(4, 4)
                    dot.setStyleSheet("background-color: #ffcc00; border-radius: 2px; border: none;")
                    dots_layout.addWidget(dot)
                btn_layout.addLayout(dots_layout)
                
            is_today = (day == datetime.now().day and self.current_date.month == datetime.now().month and self.current_date.year == datetime.now().year)
            is_selected = (day == self.current_date.day)

            if is_selected:
                lbl_day.setStyleSheet(Theme.get_style("CalendarLblSelected"))
                btn.setStyleSheet(Theme.get_style("CalendarBtnSelected"))
            elif is_today:
                lbl_day.setStyleSheet(Theme.get_style("CalendarLblToday"))
                btn.setStyleSheet(Theme.get_style("CalendarBtnToday"))
            else:
                lbl_day.setStyleSheet(Theme.get_style("CalendarLblDefault"))
                btn.setStyleSheet(Theme.get_style("CalendarBtnDefault"))
            btn.setMinimumSize(36, 36)
            btn.clicked.connect(lambda checked, d=day: self.go_to_day(d))
            self.month_layout.addWidget(btn, row, col)
            
            col += 1
            if col > 6:
                col = 0
                row += 1

    def go_to_day(self, day):
        self.current_date = self.current_date.replace(day=day)
        self.update_ui() # Re-render to show selected day (highlight)
        self.day_selected_signal.emit(self.current_date)

    def init_week_view(self):
        self.week_layout = QHBoxLayout(self.week_view)
        self.week_layout.setSpacing(2)
        
    def render_week(self):
        for i in reversed(range(self.week_layout.count())): 
            widget = self.week_layout.itemAt(i).widget()
            if widget: widget.deleteLater()
            
        start_week = self.current_date - timedelta(days=self.current_date.weekday())
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        
        for i, day_name in enumerate(days):
            col_widget = QWidget()
            col_widget.setStyleSheet(Theme.get_style("CalendarWeekCol"))
            col_layout = QVBoxLayout(col_widget)
            col_layout.setContentsMargins(2, 4, 2, 4)
            col_layout.setSpacing(4)
            
            day_date = start_week + timedelta(days=i)
            
            lbl_day = QLabel(f"{day_name}\n{day_date.day}")
            lbl_day.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if day_date.date() == datetime.now().date():
                lbl_day.setStyleSheet(Theme.get_style("CalendarWeekLblToday"))
            else:
                lbl_day.setStyleSheet(Theme.get_style("CalendarWeekLblDefault"))
                
            col_layout.addWidget(lbl_day)
            
            day_events = []
            for ev in self.events:
                dt = datetime.fromisoformat(ev['start']['dateTime'])
                if dt.date() == day_date.date():
                    day_events.append(ev)
                    
            if day_events:
                for ev in day_events:
                    dt = datetime.fromisoformat(ev['start']['dateTime'])
                    time_str = dt.strftime('%H:%M')
                    btn_ev = QPushButton(f"{time_str}\n{ev['summary']}")
                    btn_ev.setStyleSheet(Theme.get_style("CalendarWeekEventBtn"))
                    btn_ev.clicked.connect(lambda checked, d=day_date.day: self.go_to_day(d))
                    col_layout.addWidget(btn_ev)
            
            col_layout.addStretch()
            self.week_layout.addWidget(col_widget)

    def init_day_view(self):
        self.day_layout = QVBoxLayout(self.day_view)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        
        self.day_content = QWidget()
        self.day_content_layout = QVBoxLayout(self.day_content)
        self.day_content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.day_content)
        
        self.day_layout.addWidget(self.scroll)
        
    def render_day(self):
        for i in reversed(range(self.day_content_layout.count())): 
            widget = self.day_content_layout.itemAt(i).widget()
            if widget: widget.deleteLater()
            
        today_events = []
        for ev in self.events:
            dt = datetime.fromisoformat(ev['start']['dateTime'])
            if dt.date() == self.current_date.date():
                today_events.append(ev)
                
        if not today_events:
            lbl = QLabel("No events for today.")
            lbl.setStyleSheet(Theme.get_style("DimLabel"))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.day_content_layout.addWidget(lbl)
        else:
            for ev in today_events:
                dt = datetime.fromisoformat(ev['start']['dateTime'])
                time_str = dt.strftime('%I:%M %p')
                
                frame = QFrame()
                frame.setStyleSheet(Theme.get_style("EventCard"))
                
                main_l = QHBoxLayout(frame)
                main_l.setContentsMargins(10, 8, 10, 8)
                main_l.setSpacing(8)
                
                info_layout = QVBoxLayout()
                info_layout.setSpacing(2)
                
                lbl_title = QLabel(ev['summary'])
                lbl_title.setStyleSheet(Theme.get_style("EventTitleLabel"))
                lbl_title.setWordWrap(True)
                
                lbl_time = QLabel(time_str)
                lbl_time.setStyleSheet(Theme.get_style("SubtitleLabel"))
                
                info_layout.addWidget(lbl_title)
                info_layout.addWidget(lbl_time)
                
                main_l.addLayout(info_layout, stretch=1)
                
                # Orange Edit button
                btn_edit = QPushButton("✎ Edit")
                btn_edit.setFixedSize(55, 26)
                btn_edit.setStyleSheet(Theme.get_style("SettingsActionBtn"))
                btn_edit.clicked.connect(lambda checked, event_data=ev: self.open_event_input(event_data))
                
                main_l.addWidget(btn_edit)
                
                self.day_content_layout.addWidget(frame)

    def open_event_input(self, event_data=None):
        if not hasattr(self, 'event_dialog') or self.event_dialog is None:
            from PyQt6.QtWidgets import QDialog
            self.event_dialog = QDialog(self)
            self.event_dialog.setWindowTitle("Create Event")
            self.event_dialog.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
            self.event_dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.event_dialog.setStyleSheet(Theme.get_style("EventDialogBase"))

            from PyQt6.QtWidgets import QFrame
            main_layout = QVBoxLayout(self.event_dialog)
            main_layout.setContentsMargins(0, 0, 0, 0)
            bg_frame = QFrame()
            bg_frame.setObjectName("BgFrame")
            main_layout.addWidget(bg_frame)

            dlg_layout = QVBoxLayout(bg_frame)
            dlg_layout.setContentsMargins(14, 14, 14, 14)
            dlg_layout.setSpacing(10)
            
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
            dt_layout.setSpacing(8)
            
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
            btn_layout.setSpacing(6)
            
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
        if self.parentWidget():
            global_pos = self.parentWidget().mapToGlobal(self.pos())
            cx = global_pos.x() + (self.width() - dlg_width) // 2
            cy = global_pos.y() + (self.height() - dlg_height) // 2
            self.event_dialog.move(cx, cy)
        else:
            global_pos = self.mapToGlobal(self.pos())
            cx = global_pos.x() + (self.width() - dlg_width) // 2
            cy = global_pos.y() + (self.height() - dlg_height) // 2
            self.event_dialog.move(cx, cy)
        self.input_title.clear()
        
        if event_data:
            self.editing_event_id = event_data.get('id')
            self.dialog_title_lbl.setText("EDIT CALENDAR EVENT")
            self.input_title.setText(event_data.get('summary', ''))
            self.btn_dialog_delete.show()
            try:
                dt = datetime.fromisoformat(event_data['start']['dateTime'])
                self.input_date.setDate(QDate(dt.year, dt.month, dt.day))
                self.input_time.setTime(QTime(dt.hour, dt.minute))
            except Exception:
                self.input_date.setDate(QDate.currentDate())
                self.input_time.setTime(QTime.currentTime())
        else:
            self.editing_event_id = None
            self.dialog_title_lbl.setText("NEW CALENDAR EVENT")
            self.btn_dialog_delete.hide()
            self.input_date.setDate(QDate.currentDate())
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

    def load_events(self, events_data):
        self.events = events_data.get('events', [])
        self.update_ui()
