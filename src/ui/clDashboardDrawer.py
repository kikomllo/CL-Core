from datetime import datetime, timedelta
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QFrame, QScrollArea
from PyQt6.QtCore import Qt

from clCalendarWidget import CalendarWidget
from ui.clTodoWidget import TodoWidget
from ui.clReminderWidget import ReminderWidget
from ui.clMediaWidget import MediaWidget
from ui.clLightControlWidget import LightControlWidget

class UpNextWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UpNextContainer")
        self.setStyleSheet("""
            QFrame#UpNextContainer {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(45, 22, 6, 0.75), stop:1 rgba(25, 11, 2, 0.75));
                border: 1px solid rgba(255, 160, 0, 0.3);
                border-radius: 12px;
            }
        """)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 10, 12, 10)
        self.layout.setSpacing(6)
        
        self.lbl_title = QLabel("UP NEXT")
        self.lbl_title.setStyleSheet("color: #ffaa00; font-size: 10px; font-weight: 800; letter-spacing: 1px; border: none; background: transparent;")
        self.layout.addWidget(self.lbl_title)
        
        # Scroll Area for events
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { width: 5px; background: rgba(0,0,0,30); border-radius: 2px; }
            QScrollBar::handle:vertical { background: rgba(255,170,0,100); border-radius: 2px; }
        """)
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.events_layout = QVBoxLayout(self.scroll_content)
        self.events_layout.setContentsMargins(0, 0, 0, 0)
        self.events_layout.setSpacing(6)
        self.events_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.scroll.setWidget(self.scroll_content)
        self.layout.addWidget(self.scroll)
        
    def load_events(self, events_data):
        # Clear existing event cards
        while self.events_layout.count():
            item = self.events_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
                
        events = events_data.get('events', [])
        now = datetime.now()
        future_events = []
        for ev in events:
            dt = datetime.fromisoformat(ev['start']['dateTime'])
            dt = dt.replace(tzinfo=None)
            if dt > now:
                future_events.append((ev, dt))
                
        if not future_events:
            lbl = QLabel("No upcoming events")
            lbl.setStyleSheet("color: rgba(255, 200, 150, 0.6); font-style: italic; font-size: 11px; border: none; background: transparent;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.events_layout.addWidget(lbl)
            return
            
        future_events.sort(key=lambda x: x[1])
        
        for ev, dt in future_events:
            time_str = dt.strftime('%I:%M %p')
            if dt.date() == now.date():
                day_str = "Today"
            elif dt.date() == (now + timedelta(days=1)).date():
                day_str = "Tomorrow"
            else:
                day_str = dt.strftime('%b %d')
                
            # Create a styled event card rectangle
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(50, 25, 8, 0.85), stop:1 rgba(30, 14, 3, 0.85));
                    border-left: 4px solid #ff8c00;
                    border-top: 1px solid rgba(255, 160, 0, 0.3);
                    border-right: 1px solid rgba(255, 160, 0, 0.3);
                    border-bottom: 1px solid rgba(255, 160, 0, 0.3);
                    border-radius: 8px;
                }
            """)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 7, 10, 7)
            card_layout.setSpacing(2)
            
            lbl_ev_title = QLabel(ev['summary'])
            lbl_ev_title.setStyleSheet("color: #ffffff; font-weight: 800; font-size: 12px; border: none; background: transparent;")
            lbl_ev_title.setWordWrap(True)
            
            lbl_ev_time = QLabel(f"{day_str} at {time_str}")
            lbl_ev_time.setStyleSheet("color: #ffaa00; font-size: 10px; font-weight: 600; border: none; background: transparent;")
            
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
        self.nav_frame.setStyleSheet("""
            QFrame {
                background: rgba(35, 17, 4, 0.85);
                border: 1px solid rgba(255, 160, 0, 0.35);
                border-radius: 15px;
            }
        """)
        nav_layout = QHBoxLayout(self.nav_frame)
        nav_layout.setContentsMargins(8, 3, 8, 3)
        
        btn_style = """
            QPushButton {
                background: transparent;
                color: #ffaa00;
                font-weight: bold;
                font-size: 14px;
                border: none;
            }
            QPushButton:hover { color: #ffffff; }
        """
        
        self.btn_prev = QPushButton("❮")
        self.btn_prev.setStyleSheet(btn_style)
        self.btn_prev.setFixedSize(25, 25)
        self.btn_prev.clicked.connect(self.prev_slide)
        
        self.btn_next = QPushButton("❯")
        self.btn_next.setStyleSheet(btn_style)
        self.btn_next.setFixedSize(25, 25)
        self.btn_next.clicked.connect(self.next_slide)
        
        self.lbl_indicator = QLabel("To-Do List")
        self.lbl_indicator.setStyleSheet("color: #ffcc66; font-weight: 800; font-size: 12px; letter-spacing: 0.5px; border: none; background: transparent;")
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
        self.setStyleSheet("""
            #DashboardDrawer {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #180a02, stop:0.5 #120601, stop:1 #0c0400);
                border-left: 2px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffaa00, stop:0.5 #ff7700, stop:1 #ffbb33);
            }
        """)
        
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
        divider.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 transparent, stop:0.5 rgba(255, 170, 0, 0.4), stop:1 transparent);
            height: 1px;
            border: none;
        """)
        
        # Carousel (Bottom Quarter, 25%)
        self.carousel = WidgetCarousel(self)
        
        # Layout allocation
        self.layout.addWidget(self.calendar, stretch=2)
        self.layout.addWidget(self.up_next, stretch=1)
        self.layout.addWidget(divider)
        self.layout.addWidget(self.carousel, stretch=1)
