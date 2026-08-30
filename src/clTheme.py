class Theme:
    # Colors
    C_PRIMARY = "#ffaa00"
    C_PRIMARY_HOVER = "#ffcc00"
    C_PRIMARY_DIM = "rgba(255, 170, 0, 150)"
    
    C_BG_PANEL = "rgba(20, 10, 0, 180)"
    C_BG_INPUT = "rgba(25, 12, 3, 255)"
    
    C_BORDER = "rgba(255, 150, 0, 80)"
    C_BORDER_FOCUS = "rgba(255, 180, 0, 150)"
    
    C_TEXT = "#ffe6cc"
    C_TEXT_DIM = "#aaaaaa"
    C_TEXT_TITLE = "#ff7700"
    
    C_SUCCESS = "#88ff88"
    C_SUCCESS_BG = "rgba(50, 150, 50, 60)"
    C_SUCCESS_BORDER = "rgba(50, 150, 50, 100)"
    
    # Fonts
    FONT_FAMILY = "'Courier New'"
    F_TINY = "11px"
    F_SMALL = "12px"
    F_NORMAL = "14px"
    F_LARGE = "16px"
    F_TITLE = "18px"
    F_HEADER = "20px"
    F_HUGE = "24px"
    
    @classmethod
    def get_global_stylesheet(cls):
        return f"""
            QWidget {{
                font-family: {cls.FONT_FAMILY};
                color: {cls.C_TEXT};
            }}
            
            QPushButton {{
                background-color: {cls.C_BG_PANEL};
                color: {cls.C_PRIMARY};
                border-radius: 6px;
                font-size: {cls.F_NORMAL};
                font-weight: bold;
                border: 1px solid {cls.C_BORDER};
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 120, 0, 80);
                color: #ffffff;
            }}
            
            QLineEdit, QTextEdit {{
                background-color: {cls.C_BG_INPUT};
                color: {cls.C_TEXT};
                border: 1px solid {cls.C_BORDER_FOCUS};
                border-radius: 6px;
                font-size: {cls.F_NORMAL};
                padding: 6px;
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border: 1px solid {cls.C_PRIMARY};
            }}
            
            QLabel {{
                color: {cls.C_TEXT};
                font-size: {cls.F_NORMAL};
                background: transparent;
            }}
            
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                width: 6px;
                background: rgba(0,0,0,40);
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255,170,0,120);
                border-radius: 3px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """
        
    @classmethod
    def get_style(cls, component):
        if component == "TitleLabel":
            return f"color: {cls.C_PRIMARY}; font-weight: 800; font-size: {cls.F_TITLE}; border: none; background: transparent;"
        elif component == "SubtitleLabel":
            return f"color: {cls.C_TEXT_TITLE}; font-size: {cls.F_LARGE}; border: none; background: transparent; font-weight: bold;"
        elif component == "DimLabel":
            return f"color: {cls.C_TEXT_DIM}; font-size: {cls.F_SMALL};"
        elif component == "SuccessButton":
            return f"""
                QPushButton {{ background: {cls.C_SUCCESS_BG}; color: {cls.C_SUCCESS}; border: 1px solid {cls.C_SUCCESS_BORDER}; border-radius: 3px; font-weight: bold; font-size: {cls.F_SMALL}; padding: 2px 5px; }}
                QPushButton:hover {{ background: rgba(70, 200, 70, 100); color: #ffffff; }}
            """
        elif component == "DangerButton":
            return f"""
                QPushButton {{ background-color: transparent; color: rgba(255, 150, 0, 180); font-weight: bold; border: 1px solid rgba(255, 150, 0, 50); border-radius: 6px; font-size: {cls.F_NORMAL}; }}
                QPushButton:hover {{ color: #ff5500; background-color: rgba(255, 150, 0, 20); border: 1px solid rgba(255, 150, 0, 100); }}
            """
        elif component == "SmallDangerButton":
            return f"""
                QPushButton {{ background-color: rgba(20, 10, 0, 180); color: {cls.C_PRIMARY}; border-radius: 4px; font-size: 8pt; font-weight: bold; border: 1px solid {cls.C_BORDER}; padding-bottom: 2px; }}
                QPushButton:hover {{ background-color: rgba(255, 120, 0, 80); color: #ffffff; border: 1px solid {cls.C_PRIMARY}; }}
            """
        elif component == "TransparentButton":
            return f"""
                QPushButton {{ background: transparent; color: {cls.C_PRIMARY}; font-weight: bold; border: none; font-size: {cls.F_TITLE}; }}
                QPushButton:hover {{ color: {cls.C_PRIMARY_HOVER}; }}
            """
        elif component == "CalendarButton":
            return f"""
                QPushButton {{ background-color: rgba(255, 150, 0, 40); color: {cls.C_PRIMARY}; border-radius: 5px; font-weight: bold; border: 1px solid rgba(255, 150, 0, 80); }}
                QPushButton:hover {{ background-color: rgba(255, 150, 0, 80); }}
            """
        elif component == "AddButton":
            return f"""
                QPushButton {{
                    text-align: center; padding-bottom: 4px;
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 150, 0, 0.25), stop:1 rgba(255, 100, 0, 0.25));
                    color: #ffbb33; border-radius: 8px; border: 1px solid rgba(255, 160, 0, 0.5);
                    font-weight: bold; font-size: {cls.F_NORMAL};
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 160, 0, 0.45), stop:1 rgba(255, 110, 0, 0.45));
                    color: #ffffff; border: 1px solid {cls.C_PRIMARY};
                }}
            """
        elif component == "SecondaryButton":
            return f"""
                QPushButton {{ background: rgba(255, 120, 0, 40); color: {cls.C_PRIMARY}; border: 1px solid {cls.C_BORDER}; border-radius: 4px; padding: 5px 10px; font-weight: bold; font-size: {cls.F_NORMAL}; }}
                QPushButton:hover {{ background: rgba(255, 150, 0, 80); color: #ffffff; }}
            """
        elif component == "BadgeLabel":
            return f"color: {cls.C_PRIMARY}; font-size: 10px; font-weight: 800; letter-spacing: 1px; border: none; background: transparent;"
        elif component == "MediaSmallBtn":
            return f"""
                QPushButton {{ text-align: center; padding-bottom: 4px; background-color: rgba(35, 18, 5, 0.8); color: {cls.C_PRIMARY}; border-radius: 16px; border: 1px solid rgba(255, 160, 0, 0.4); font-size: {cls.F_SMALL}; font-family: "Segoe UI Symbol", sans-serif; }}
                QPushButton:hover {{ background-color: rgba(255, 150, 0, 0.35); color: #ffffff; border: 1px solid {cls.C_PRIMARY}; }}
            """
        elif component == "MediaPlayBtn":
            return f"""
                QPushButton {{ text-align: center; padding-bottom: 4px; background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff8c00, stop:1 #e65c00); color: #ffffff; border-radius: 20px; border: 1px solid #ffcc66; font-size: {cls.F_LARGE}; font-weight: bold; font-family: "Segoe UI Symbol", sans-serif; }}
                QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffa01a, stop:1 #ff701a); }}
            """
        elif component == "RefreshButton":
            return f"""
                QPushButton {{ text-align: center; padding-bottom: 4px; background-color: rgba(255, 150, 0, 20); color: {cls.C_PRIMARY}; border-radius: 15px; border: 1px solid rgba(255,150,0,80); font-size: 14pt; }}
                QPushButton:hover {{ background-color: rgba(255, 150, 0, 60); color: #ffffff; }}
            """
        elif component == "LightDangerButton":
            return f"""
                QPushButton {{ text-align: center; padding-bottom: 4px; background-color: rgba(255, 50, 0, 40); color: {cls.C_PRIMARY}; border-radius: 5px; padding: 5px; border: 1px solid rgba(255,100,0,80); font-weight: bold; font-size: {cls.F_NORMAL}; }}
                QPushButton:hover {{ background-color: rgba(255, 50, 0, 80); color: #ffffff; }}
            """
        elif component == "LogViewer":
            return f"background: rgba(10, 5, 0, 200); color: {cls.C_PRIMARY}; font-size: {cls.F_TINY}; border: 1px solid {cls.C_BORDER}; border-radius: 4px;"
        elif component == "RebootButton":
            return f"""
                QPushButton {{ text-align: center; padding-bottom: 4px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(200, 30, 0, 0.4), stop:1 rgba(150, 10, 0, 0.4)); color: #ffcccc; border-radius: 6px; border: 1px solid rgba(255, 50, 0, 0.5); font-weight: bold; font-size: {cls.F_NORMAL}; margin: 5px 15px 10px 15px; }}
                QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(220, 50, 0, 0.6), stop:1 rgba(180, 20, 0, 0.6)); color: #ffffff; border: 1px solid #ff4400; }}
            """
        elif component == "SectionLabel":
            return f"color: rgba(255, 170, 0, 180); font-weight: bold; font-size: {cls.F_NORMAL}; border-bottom: 1px solid rgba(255, 150, 0, 50); padding-bottom: 2px;"
        elif component == "SettingsCheckbox":
            return f"""
                QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid rgba(255, 170, 0, 0.5); background: rgba(35, 18, 5, 0.8); }}
                QCheckBox::indicator:hover {{ border: 1px solid {cls.C_PRIMARY}; background: rgba(255, 150, 0, 0.3); }}
                QCheckBox::indicator:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff8c00, stop:1 #e65c00); border: 1px solid #ffbb33; }}
            """
        elif component == "SettingsDropdown":
            return f"""
                QComboBox {{ background-color: rgba(25, 12, 3, 240); color: #ffe6cc; border: 1px solid rgba(255, 180, 0, 100); border-radius: 4px; padding: 3px 6px; font-size: {cls.F_NORMAL}; }}
                QComboBox::drop-down {{ border: none; }}
                QComboBox QAbstractItemView {{ background-color: rgba(20, 10, 0, 240); color: #ffe6cc; selection-background-color: rgba(255, 150, 0, 100); }}
            """
        elif component == "SettingsLineEdit":
            return f"""
                QLineEdit {{ background-color: rgba(25, 12, 3, 240); color: #ffe6cc; border: 1px solid rgba(255, 180, 0, 80); border-radius: 4px; padding: 5px; font-size: {cls.F_NORMAL}; }}
                QLineEdit:focus {{ border: 1px solid {cls.C_PRIMARY}; background-color: rgba(35, 18, 5, 255); }}
            """
        elif component == "SettingsDashedButton":
            return f"""
                QPushButton {{ text-align: center; padding-bottom: 4px; background-color: rgba(40, 25, 10, 200); color: {cls.C_PRIMARY}; border: 1px dashed rgba(255, 170, 0, 120); border-radius: 4px; font-weight: bold; font-size: {cls.F_NORMAL}; }}
                QPushButton:hover {{ background-color: rgba(70, 45, 15, 255); border: 1px solid {cls.C_PRIMARY}; }}
            """
        elif component == "TodoTabs":
            return f"""
                QTabWidget::pane {{ border: 0px; background: transparent; }}
                QTabBar {{ draw-base: false; border: 0px; outline: none; }}
                QTabBar::tab {{ background: rgba(30, 15, 5, 200); color: {cls.C_TEXT_DIM}; padding: 6px 16px; border-top-left-radius: 6px; border-top-right-radius: 6px; font-weight: bold; font-size: {cls.F_NORMAL}; margin-right: 2px; border: none; outline: none; }}
                QTabBar::tab:selected {{ background: {cls.C_BG_INPUT}; color: {cls.C_PRIMARY}; border-bottom: 2px solid {cls.C_PRIMARY}; }}
                QTabBar QToolButton {{ background: rgba(30, 15, 5, 200); color: {cls.C_PRIMARY}; border: none; }}
                QTabBar QToolButton:hover {{ background: rgba(255, 120, 0, 60); }}
            """
        elif component == "DockButton":
            return f"QPushButton {{ color: #ffaa00; background-color: rgba(20, 10, 0, 150); border: 1px solid rgba(255, 150, 0, 80); border-radius: 6px; padding: 2px 6px; font-weight: bold; font-size: {cls.F_TINY}; }} QPushButton:hover {{ background-color: rgba(255, 120, 0, 60); color: #ffffff; border: 1px solid {cls.C_PRIMARY}; }}"
        elif component == "SettingsCollapseBtn":
            return f"""
                QPushButton {{ background-color: rgba(35, 18, 5, 220); color: #ffe6cc; border: 1px solid rgba(255, 180, 0, 80); border-radius: 4px; text-align: left; padding-left: 8px; font-weight: bold; font-size: {cls.F_NORMAL}; }}
                QPushButton:hover {{ background-color: rgba(55, 30, 10, 255); border: 1px solid {cls.C_PRIMARY}; }}
            """
        elif component == "SettingsCollapseBody":
            return f"QWidget {{ background-color: rgba(20, 10, 2, 180); border-left: 2px solid rgba(255, 170, 0, 120); border-bottom: 1px solid rgba(255, 170, 0, 40); border-right: 1px solid rgba(255, 170, 0, 40); border-bottom-left-radius: 4px; border-bottom-right-radius: 4px; }}"
        elif component == "SettingsActionBtn":
            return f"""
                QPushButton {{ text-align: center; padding-bottom: 4px; background-color: rgba(30, 20, 5, 180); color: #ffbb44; border: 1px solid rgba(255, 170, 0, 60); border-radius: 4px; font-size: {cls.F_SMALL}; }}
                QPushButton:hover {{ background-color: rgba(60, 40, 10, 220); color: #ffffff; border: 1px solid {cls.C_PRIMARY}; }}
            """
        elif component == "DrawerBackButton":
            return f"QPushButton {{ color: {cls.C_PRIMARY}; background: transparent; border: none; font-weight: bold; font-size: 10px; }} QPushButton:hover {{ color: #ffffff; }}"
        elif component == "EventCard":
            return f"QFrame {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(50, 25, 8, 0.85), stop:1 rgba(30, 14, 3, 0.85)); border-left: 4px solid #ff8c00; border-top: 1px solid rgba(255, 160, 0, 0.3); border-right: 1px solid rgba(255, 160, 0, 0.3); border-bottom: 1px solid rgba(255, 160, 0, 0.3); border-radius: 8px; }}"
        elif component == "EventTitleLabel":
            return f"color: #ffffff; font-weight: 800; font-size: 12px; border: none; background: transparent;"
        elif component == "CarouselNav":
            return f"QFrame {{ background: rgba(35, 17, 4, 0.85); border: 1px solid rgba(255, 160, 0, 0.35); border-radius: 15px; }}"
        elif component == "DashboardDrawer":
            return f"#DashboardDrawer {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #180a02, stop:0.5 #120601, stop:1 #0c0400); border-left: 2px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffaa00, stop:0.5 #ff7700, stop:1 #ffbb33); }}"
        elif component == "UpNextContainer":
            return f"QFrame#UpNextContainer {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(45, 22, 6, 0.75), stop:1 rgba(25, 11, 2, 0.75)); border: 1px solid rgba(255, 160, 0, 0.3); border-radius: 12px; }}"
        elif component == "DrawerDivider":
            return f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 transparent, stop:0.5 rgba(255, 170, 0, 0.4), stop:1 transparent); border: none;"
        elif component == "CalendarNavBtn":
            return f"QPushButton {{ text-align: center; padding-bottom: 4px; color: {cls.C_PRIMARY}; background: rgba(255, 150, 0, 0.12); border-radius: 6px; padding: 4px 8px; border: 1px solid rgba(255, 150, 0, 0.35); font-weight: bold; font-size: {cls.F_SMALL}; }} QPushButton:hover {{ background: rgba(255, 150, 0, 0.3); border: 1px solid {cls.C_PRIMARY}; color: #ffffff; }}"
        elif component == "CalendarTitle":
            return f"color: #ffbb33; font-size: {cls.F_LARGE}; font-weight: 800; border: none; background: transparent; letter-spacing: 0.5px;"
        elif component == "CalendarYearBtnActive":
            return f"background: rgba(255, 150, 0, 60); color: white; border-radius: 5px; font-weight: bold; border: 1px solid {cls.C_PRIMARY}; font-size: {cls.F_NORMAL};"
        elif component == "CalendarYearBtnInactive":
            return f"background: rgba(255, 150, 0, 10); color: {cls.C_PRIMARY}; border-radius: 5px; border: 1px solid rgba(255, 150, 0, 30); font-size: {cls.F_NORMAL};"
        elif component == "CalendarDayLabel":
            return f"color: #ff9900; font-weight: 700; font-size: {cls.F_TINY}; border: none; background: transparent;"
        elif component == "CalendarBtnSelected":
            return f"QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff8c00, stop:1 #e65c00); border-radius: 6px; border: 1px solid #ffcc66; }} QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffa01a, stop:1 #ff701a); }}"
        elif component == "CalendarBtnToday":
            return f"QPushButton {{ background: rgba(255, 150, 0, 0.2); border-radius: 6px; border: 1px solid rgba(255, 170, 0, 0.5); }} QPushButton:hover {{ background: rgba(255, 150, 0, 0.4); border: 1px solid {cls.C_PRIMARY}; }}"
        elif component == "CalendarBtnDefault":
            return f"QPushButton {{ background: rgba(35, 18, 5, 0.75); border-radius: 6px; border: 1px solid rgba(255, 170, 0, 0.25); }} QPushButton:hover {{ background: rgba(255, 150, 0, 0.4); border: 1px solid {cls.C_PRIMARY}; }}"
        elif component == "CalendarLblSelected":
            return f"background: transparent; border: none; color: #ffffff; font-size: {cls.F_NORMAL}; font-weight: 900;"
        elif component == "CalendarLblToday":
            return f"background: transparent; border: none; color: {cls.C_PRIMARY}; font-size: {cls.F_NORMAL}; font-weight: 900;"
        elif component == "CalendarLblDefault":
            return f"background: transparent; border: none; color: #ffffff; font-size: {cls.F_NORMAL}; font-weight: 700;"
        elif component == "CalendarWeekCol":
            return f"background: rgba(255, 150, 0, 5); border: 1px solid rgba(255, 150, 0, 20); border-radius: 5px;"
        elif component == "CalendarWeekLblToday":
            return f"background: rgba(255, 150, 0, 60); color: white; border-radius: 3px; padding: 2px; font-weight: bold; border: none; font-size: {cls.F_TINY};"
        elif component == "CalendarWeekLblDefault":
            return f"color: {cls.C_PRIMARY}; font-weight: bold; border: none; background: transparent; font-size: {cls.F_TINY};"
        elif component == "CalendarWeekEventBtn":
            return f"background: rgba(255, 150, 0, 40); color: white; border-left: 2px solid {cls.C_PRIMARY}; border-radius: 2px; font-size: {cls.F_TINY}; text-align: left; padding: 2px;"
        elif component == "EventDialogBase":
            return f"""
                #BgFrame {{ background-color: rgba(20, 10, 0, 240); border: 1px solid rgba(255, 120, 0, 100); border-radius: 12px; }}
                QLineEdit, QDateEdit, QTimeEdit {{ background-color: rgba(25, 12, 3, 255); color: #ffe6cc; border: 1px solid rgba(255, 180, 0, 150); border-radius: 6px; padding: 6px; font-size: {cls.F_NORMAL}; font-weight: 600; }}
                QLineEdit:focus, QDateEdit:focus, QTimeEdit:focus {{ border: 1px solid {cls.C_PRIMARY}; }}
                QDateEdit::up-button, QTimeEdit::up-button, QDateEdit::down-button, QTimeEdit::down-button {{ width: 0px; height: 0px; border: none; }}
                QLabel {{ color: {cls.C_PRIMARY}; font-weight: bold; font-size: {cls.F_NORMAL}; }}
                QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 150, 0, 0.25), stop:1 rgba(255, 100, 0, 0.25)); color: #ffbb33; border-radius: 6px; border: 1px solid rgba(255, 160, 0, 0.5); font-weight: bold; padding: 6px; font-size: {cls.F_NORMAL}; }}
                QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 160, 0, 0.45), stop:1 rgba(255, 110, 0, 0.45)); color: #ffffff; border: 1px solid {cls.C_PRIMARY}; }}
            """
        elif component == "NotificationTitleBar":
            return "#TitleBar { background-color: rgba(255, 120, 0, 60); border-top-left-radius: 12px; border-top-right-radius: 12px; border-bottom: 1px solid rgba(255, 150, 0, 80); }"
        elif component == "NotificationTitle":
            return f"color: rgba(255, 200, 0, 200); font-weight: bold; background: transparent; border: none; font-size: {cls.F_SMALL};"
        elif component == "NotificationCloseBtn":
            return f"QPushButton {{ color: rgba(255, 150, 0, 200); background: transparent; border: none; font-weight: bold; font-size: {cls.F_NORMAL}; }} QPushButton:hover {{ color: #ffffff; background-color: rgba(255, 50, 0, 150); border-top-right-radius: 12px; }}"
        elif component == "NotificationBody":
            return f"#PopupMain {{ background-color: rgba(15, 8, 2, 230); border: 1px solid rgba(255, 150, 0, 80); border-radius: 12px; }} QLabel {{ color: #ffe6cc; font-size: {cls.F_NORMAL}; }}"
        elif component == "HealthDanger":
            return f"color: rgba(255, 100, 100, 180); font-size: {cls.F_TINY}; background: transparent;"
        elif component == "HealthWarning":
            return f"color: rgba(255, 200, 0, 180); font-size: {cls.F_TINY}; background: transparent;"
        elif component == "Panel":
            return f"background-color: {cls.C_BG_PANEL}; border: 1px solid {cls.C_BORDER}; border-radius: 8px;"
        elif component == "ProgressBar":
            return f"""
                QProgressBar {{ border: 1px solid {cls.C_BORDER}; border-radius: 2px; text-align: center; font-size: {cls.F_SMALL}; }}
                QProgressBar::chunk {{ background-color: {cls.C_PRIMARY}; }}
            """
        elif component == "TodoTabs":
            return f"""
            QTabWidget::pane {{ border: none; background: transparent; }}
            QTabWidget::tab-bar {{ alignment: left; left: 0px; right: 0px; }}
            QTabBar {{ background-color: rgba(255, 120, 0, 10); border-bottom: 1px solid rgba(255, 150, 0, 60); min-height: 24px; max-height: 24px; }}
            QTabBar::tab {{ 
                background: transparent; color: rgba(255, 200, 0, 180); padding: 0px 12px; height: 24px; border: none;
                border-bottom-left-radius: 6px; border-bottom-right-radius: 6px; border-top-left-radius: 0px; border-top-right-radius: 0px;
                font-family: {cls.FONT_FAMILY}; font-weight: bold; font-size: {cls.F_SMALL};
            }}
            QTabBar::tab:hover {{ color: #ffffff; background-color: rgba(255, 150, 0, 40); }}
            QTabBar::tab:selected {{ background-color: rgba(255, 150, 0, 70); color: {cls.C_PRIMARY}; border-bottom: 2px solid {cls.C_PRIMARY}; }}
            QTabBar::scroller {{ width: 20px; height: 24px; }}
            QTabBar QToolButton {{ background: transparent; border: none; color: {cls.C_PRIMARY}; height: 24px; }}
            QTabBar::tab:last {{
                border-bottom-right-radius: 10px; border-bottom-left-radius: 10px; border-top-right-radius: 0px; border-top-left-radius: 0px;
                background-color: rgba(255, 120, 0, 15); min-width: 16px; padding: 0px 4px;
            }}
            QTabBar::tab:last:hover {{ background-color: rgba(255, 150, 0, 40); }}
            """
        elif component == "TodoCheckbox":
            return f"""
                QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid rgba(255, 170, 0, 0.5); background: rgba(35, 18, 5, 0.8); }}
                QCheckBox::indicator:hover {{ border: 1px solid {cls.C_PRIMARY}; background: rgba(255, 150, 0, 0.3); }}
                QCheckBox::indicator:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff8c00, stop:1 #e65c00); border: 1px solid #ffbb33; }}
            """
        return ""
