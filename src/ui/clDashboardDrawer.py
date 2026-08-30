from datetime import datetime, timedelta
from clTheme import Theme
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QFrame, QScrollArea, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal

from clCalendarWidget import CalendarWidget
from ui.clTodoWidget import TodoWidget
from ui.clReminderWidget import ReminderWidget
from ui.clMediaWidget import MediaWidget
from ui.clLightControlWidget import LightControlWidget

class UpNextWidget(QFrame):
    add_event_signal = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UpNextContainer")
        self.setStyleSheet(Theme.get_style("UpNextContainer"))
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 10, 12, 10)
        self.layout.setSpacing(6)
        
        self.header_layout = QHBoxLayout()
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        
        self.btn_back = QPushButton("< Back")
        self.btn_back.setStyleSheet(Theme.get_style("DrawerBackButton"))
        self.btn_back.clicked.connect(self.reset_to_upcoming)
        self.btn_back.hide()
        
        self.lbl_title = QLabel("UP NEXT")
        self.lbl_title.setStyleSheet(Theme.get_style("BadgeLabel"))
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.btn_add_event = QPushButton("+ Event")
        self.btn_add_event.setStyleSheet(Theme.get_style("SettingsActionBtn"))
        self.btn_add_event.clicked.connect(self.add_event_signal.emit)
        self.btn_add_event.hide()
        
        self.header_layout.addWidget(self.btn_back)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.lbl_title)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.btn_add_event)
        
        self.layout.addLayout(self.header_layout)
        
        self.mode = "upcoming"
        self.selected_date = None
        self.current_events_data = {}

        # Scroll Area for events
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.events_layout = QVBoxLayout(self.scroll_content)
        self.events_layout.setContentsMargins(0, 0, 0, 0)
        self.events_layout.setSpacing(6)
        self.events_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.scroll.setWidget(self.scroll_content)
        self.layout.addWidget(self.scroll)
        
    def load_events(self, events_data):
        self.current_events_data = events_data
        self.render_events()
        
    def set_specific_day(self, date):
        self.mode = "day"
        self.selected_date = date
        self.lbl_title.setText(date.strftime("EVENTS FOR %b %d").upper())
        self.btn_back.show()
        self.btn_add_event.show()
        self.render_events()
        
    def reset_to_upcoming(self):
        self.mode = "upcoming"
        self.selected_date = None
        self.lbl_title.setText("UP NEXT")
        self.btn_back.hide()
        self.btn_add_event.hide()
        self.render_events()

    def render_events(self):
        # Clear existing event cards
        while self.events_layout.count():
            item = self.events_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
                
        events = self.current_events_data.get('events', [])
        now = datetime.now()
        display_events = []
        for ev in events:
            dt = datetime.fromisoformat(ev['start']['dateTime'])
            dt = dt.replace(tzinfo=None)
            
            if self.mode == "upcoming":
                if dt > now:
                    display_events.append((ev, dt))
            elif self.mode == "day":
                if dt.date() == self.selected_date.date():
                    display_events.append((ev, dt))
                
        if not display_events:
            lbl = QLabel("No events" if self.mode == "day" else "No upcoming events")
            lbl.setStyleSheet(Theme.get_style("DimLabel"))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.events_layout.addWidget(lbl)
            return
            
        display_events.sort(key=lambda x: x[1])
        
        for ev, dt in display_events:
            time_str = dt.strftime('%I:%M %p')
            if dt.date() == now.date():
                day_str = "Today"
            elif dt.date() == (now + timedelta(days=1)).date():
                day_str = "Tomorrow"
            else:
                day_str = dt.strftime('%b %d')
                
            # Create a styled event card rectangle
            card = QFrame()
            card.setStyleSheet(Theme.get_style("EventCard"))
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 7, 10, 7)
            card_layout.setSpacing(2)
            
            lbl_ev_title = QLabel(ev['summary'])
            lbl_ev_title.setStyleSheet(Theme.get_style("EventTitleLabel"))
            lbl_ev_title.setWordWrap(True)
            
            lbl_ev_time = QLabel(f"{day_str} at {time_str}")
            lbl_ev_time.setStyleSheet(Theme.get_style("SubtitleLabel"))
            
            card_layout.addWidget(lbl_ev_title)
            card_layout.addWidget(lbl_ev_time)
            
            self.events_layout.addWidget(card)

class WidgetCarousel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(6)
        
        self.stack = QStackedWidget()
        
        # Initialize widget slides
        self.todo_widget = TodoWidget()
        self.reminder_widget = ReminderWidget(grid_mode=True)
        self.media_widget = MediaWidget()
        self.lights_widget = LightControlWidget()
        
        self.stack.addWidget(self.todo_widget)
        self.stack.addWidget(self.reminder_widget)
        self.stack.addWidget(self.media_widget)
        self.stack.addWidget(self.lights_widget)
        
        # Navigation bar frame (Floating Glass Pill)
        self.nav_frame = QFrame()
        self.nav_frame.setStyleSheet(Theme.get_style("CarouselNav"))
        nav_layout = QHBoxLayout(self.nav_frame)
        nav_layout.setContentsMargins(8, 3, 8, 3)
        
        self.btn_prev = QPushButton("❮")
        self.btn_prev.setStyleSheet(Theme.get_style("TransparentButton"))
        self.btn_prev.setFixedSize(25, 25)
        self.btn_prev.clicked.connect(self.prev_slide)
        
        self.btn_next = QPushButton("❯")
        self.btn_next.setStyleSheet(Theme.get_style("TransparentButton"))
        self.btn_next.setFixedSize(25, 25)
        self.btn_next.clicked.connect(self.next_slide)
        
        self.lbl_indicator = QLabel("To-Do List")
        self.lbl_indicator.setStyleSheet(Theme.get_style("BadgeLabel"))
        self.lbl_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        nav_layout.addWidget(self.btn_prev)
        nav_layout.addWidget(self.lbl_indicator)
        nav_layout.addWidget(self.btn_next)
        
        self.layout.addWidget(self.stack)
        self.layout.addWidget(self.nav_frame)
        self.stack.currentChanged.connect(self.on_slide_changed)
        
    def on_slide_changed(self, idx):
        self.update_indicator()
        p = self.parent()
        while p is not None:
            if hasattr(p, 'save_ui_state'):
                p.save_ui_state()
                break
            p = p.parent()
            
    def prev_slide(self):
        idx = self.stack.currentIndex() - 1
        if idx < 0: idx = self.stack.count() - 1
        self.stack.setCurrentIndex(idx)
        
    def next_slide(self):
        idx = (self.stack.currentIndex() + 1) % self.stack.count()
        self.stack.setCurrentIndex(idx)
        
    def update_indicator(self):
        titles = ["To-Do List", "Reminders", "Spotify Media", "Smart Lights"]
        idx = self.stack.currentIndex()
        if 0 <= idx < len(titles):
            self.lbl_indicator.setText(titles[idx])

class DashboardDrawer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DashboardDrawer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(Theme.get_style("DashboardDrawer"))
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.layout.setSpacing(10)
        
        # Calendar (Top Half, 50%)
        self.calendar = CalendarWidget(self)
        self.calendar.setStyleSheet("background-color: transparent; border: none;")
        
        # Up Next (Middle Quarter, 25%)
        self.up_next = UpNextWidget(self)
        
        # Divider line
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(Theme.get_style("DrawerDivider"))
        
        # Carousel (Bottom Quarter, 25%)
        self.carousel = WidgetCarousel(self)
        
        # Layout allocation
        self.layout.addWidget(self.calendar, stretch=2)
        self.layout.addWidget(self.up_next, stretch=1)
        self.layout.addWidget(divider)
        self.layout.addWidget(self.carousel, stretch=1)
        
        # Connect Signals
        self.calendar.day_selected_signal.connect(self.up_next.set_specific_day)
        self.up_next.add_event_signal.connect(self.calendar.open_event_input)
