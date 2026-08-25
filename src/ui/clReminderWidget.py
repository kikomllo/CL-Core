import os
import logging
import json
from datetime import datetime
from utils.clActionRouter import ActionRouter
from PyQt6.QtWidgets import QWidget, QPushButton
from PyQt6.QtCore import QTimer, QFileSystemWatcher, Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

class ReminderWidget(QWidget):
    def __init__(self, parent=None, grid_mode=False):
        super().__init__(parent)
        self.router = ActionRouter()
        self.grid_mode = grid_mode
        self.reminders = []
        self.current_idx = 0
        self.current_page = 0
        
        if not self.grid_mode:
            self.setMinimumSize(220, 135)
            
        self.data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "reminders"))
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)
            
        self.watcher = QFileSystemWatcher([self.data_dir], self)
        self.watcher.directoryChanged.connect(self.reload_reminders)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        
        btn_arrow_style = """
            QPushButton {
                text-align: center;
                padding-bottom: 4px;
                background-color: transparent;
                color: #ffaa00;
                border: none;
                font-size: 14pt;
                font-weight: bold;
                font-family: 'Courier New';
            }
            QPushButton:hover {
                color: #ffffff;
            }
        """
        
        btn_delete_style = """
            QPushButton {
                text-align: center;
                padding-bottom: 4px;
                background-color: rgba(20, 10, 0, 180);
                color: #ffaa00;
                border-radius: 4px;
                font-size: 7.5pt;
                font-weight: bold;
                font-family: 'Courier New';
                border: 1px solid rgba(255, 150, 0, 80);
            }
            QPushButton:hover {
                background-color: rgba(255, 120, 0, 80);
            }
        """
        
        # Single mode controls
        self.btn_delete = QPushButton("DELETE", self)
        self.btn_delete.setFixedSize(64, 22)
        self.btn_delete.setStyleSheet(btn_delete_style)
        self.btn_delete.clicked.connect(self.cancel_reminder)
        self.btn_delete.move(78, 96)
        
        self.btn_prev = QPushButton("<", self)
        self.btn_prev.setFixedSize(24, 24)
        self.btn_prev.setStyleSheet(btn_arrow_style)
        self.btn_prev.clicked.connect(self.prev_reminder)
        self.btn_prev.move(48, 95)
        
        self.btn_next = QPushButton(">", self)
        self.btn_next.setFixedSize(24, 24)
        self.btn_next.setStyleSheet(btn_arrow_style)
        self.btn_next.clicked.connect(self.next_reminder)
        self.btn_next.move(148, 95)
        
        # Grid mode controls (4 corner slots + page navigation)
        self.grid_delete_btns = []
        for i in range(4):
            btn = QPushButton("DELETE", self)
            btn.setStyleSheet(btn_delete_style)
            btn.clicked.connect(lambda checked, idx=i: self.cancel_grid_reminder(idx))
            btn.hide()
            self.grid_delete_btns.append(btn)
            
        self.btn_grid_prev = QPushButton("<", self)
        self.btn_grid_prev.setStyleSheet(btn_arrow_style)
        self.btn_grid_prev.clicked.connect(self.prev_grid_page)
        self.btn_grid_prev.hide()
        
        self.btn_grid_next = QPushButton(">", self)
        self.btn_grid_next.setStyleSheet(btn_arrow_style)
        self.btn_grid_next.clicked.connect(self.next_grid_page)
        self.btn_grid_next.hide()
        
        self.reload_reminders()

    def reload_reminders(self):
        self.reminders = []
        for f in os.listdir(self.data_dir):
            if f.endswith(".json"):
                try:
                    fpath = os.path.join(self.data_dir, f)
                    with open(fpath, "r") as file:
                        data = json.load(file)
                        target_dt = datetime.fromisoformat(data["time"])
                        
                        created_dt_str = data.get("time_created")
                        if created_dt_str:
                            created_dt = datetime.fromisoformat(created_dt_str)
                        else:
                            ctime = os.path.getctime(fpath)
                            created_dt = datetime.fromtimestamp(ctime)
                            
                        if target_dt > datetime.now():
                            data["target_dt"] = target_dt
                            data["created_dt"] = created_dt
                            data["id"] = f.replace(".json", "")
                            self.reminders.append(data)
                except Exception as e:
                    logging.error(f"Error loading reminder {f}: {e}")
                    
        self.reminders.sort(key=lambda x: x["target_dt"])
        
        if self.grid_mode:
            max_page = max(0, (len(self.reminders) - 1) // 4)
            if self.current_page > max_page:
                self.current_page = max_page
        else:
            if self.current_idx >= len(self.reminders):
                self.current_idx = 0
                
        self.update_buttons()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_buttons()

    def update_buttons(self):
        if not self.grid_mode:
            for b in self.grid_delete_btns: b.hide()
            self.btn_grid_prev.hide()
            self.btn_grid_next.hide()
            
            if not self.reminders:
                self.btn_delete.hide()
                self.btn_prev.hide()
                self.btn_next.hide()
                return
                
            self.btn_delete.show()
            if len(self.reminders) > 1:
                self.btn_prev.show()
                self.btn_next.show()
            else:
                self.btn_prev.hide()
                self.btn_next.hide()
        else:
            self.btn_delete.hide()
            self.btn_prev.hide()
            self.btn_next.hide()
            
            total_items = len(self.reminders)
            if total_items == 0:
                for b in self.grid_delete_btns: b.hide()
                self.btn_grid_prev.hide()
                self.btn_grid_next.hide()
                return
                
            total_pages = max(1, (total_items + 3) // 4)
            if self.current_page >= total_pages:
                self.current_page = total_pages - 1
                
            has_nav = (total_items > 4)
            nav_h = 24 if has_nav else 0
            W = self.width()
            H = self.height()
            grid_h = max(1, H - nav_h)
            cw = W / 2.0
            ch = grid_h / 2.0
            
            start_i = self.current_page * 4
            end_i = min(total_items, start_i + 4)
            page_count = end_i - start_i
            
            for k in range(4):
                if k < page_count:
                    btn = self.grid_delete_btns[k]
                    r = k // 2
                    c = k % 2
                    cx = c * cw
                    cy = r * ch
                    
                    arc_size = min(cw * 0.62, ch * 0.62)
                    bw = max(42, min(56, int(cw * 0.45)))
                    bh = max(16, min(20, int(ch * 0.2)))
                    gap = 6
                    total_h = (arc_size / 2.0) + gap + bh
                    
                    top_padding = max(4, (ch - total_h) / 2.0)
                    arc_y = cy + top_padding
                    arc_baseline = arc_y + arc_size / 2.0
                    
                    bx = int(cx + (cw - bw) / 2.0)
                    by = int(arc_baseline + gap)
                    btn.setGeometry(bx, by, bw, bh)
                    btn.show()
                else:
                    self.grid_delete_btns[k].hide()
                    
            if has_nav:
                bw = 24
                bh = 20
                by = int(H - bh - 2)
                self.btn_grid_prev.setGeometry(int(W / 2.0 - 40), by, bw, bh)
                self.btn_grid_next.setGeometry(int(W / 2.0 + 16), by, bw, bh)
                self.btn_grid_prev.show()
                self.btn_grid_next.show()
            else:
                self.btn_grid_prev.hide()
                self.btn_grid_next.hide()

    def tick(self):
        if self.reminders:
            self.update()

    def next_reminder(self):
        if self.reminders:
            self.current_idx = (self.current_idx + 1) % len(self.reminders)
            self.update()

    def prev_reminder(self):
        if self.reminders:
            self.current_idx = (self.current_idx - 1) % len(self.reminders)
            self.update()

    def next_grid_page(self):
        if self.reminders:
            total_pages = (len(self.reminders) + 3) // 4
            self.current_page = (self.current_page + 1) % total_pages
            self.update_buttons()
            self.update()

    def prev_grid_page(self):
        if self.reminders:
            total_pages = (len(self.reminders) + 3) // 4
            self.current_page = (self.current_page - 1) % total_pages
            self.update_buttons()
            self.update()

    def cancel_reminder(self):
        if not self.reminders: return
        rem_id = self.reminders[self.current_idx]["id"]
        self.router.dispatch("reminder.delete", id=rem_id)

    def cancel_grid_reminder(self, slot_idx):
        idx = self.current_page * 4 + slot_idx
        if 0 <= idx < len(self.reminders):
            rem_id = self.reminders[idx]["id"]
            self.router.dispatch("reminder.delete", id=rem_id)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        if not self.reminders:
            painter.setPen(QColor(255, 170, 0, 150))
            font = painter.font()
            font.setFamily("Courier New")
            font.setPointSize(10)
            font.setItalic(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No active reminders")
            return
            
        if not self.grid_mode:
            rem = self.reminders[self.current_idx]
            now = datetime.now()
            target = rem["target_dt"]
            created = rem["created_dt"]
            
            total_secs = (target - created).total_seconds()
            remaining_secs = (target - now).total_seconds()
            
            if remaining_secs < 0: remaining_secs = 0
            if total_secs > 0:
                progress = max(0.0, min(1.0, 1.0 - (remaining_secs / total_secs)))
            else:
                progress = 1.0
            
            arc_rect = QRectF(35, 30, 150, 150)
            
            painter.setPen(QPen(QColor(40, 20, 0, 200), 18))
            painter.drawArc(arc_rect, 180 * 16, -180 * 16)
            
            painter.setPen(QPen(QColor(255, 170, 0, 255), 18, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
            span_angle = int(-progress * 180 * 16)
            painter.drawArc(arc_rect, 180 * 16, span_angle)
            
            mins, secs = divmod(int(remaining_secs), 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                time_str = f"{hours:02d}:{mins:02d}:{secs:02d}"
            else:
                time_str = f"{mins:02d}:{secs:02d}"
                
            painter.setPen(QColor(255, 170, 0, 255))
            font = painter.font()
            font.setFamily("Courier New")
            font.setPointSize(15)
            font.setBold(True)
            painter.setFont(font)
            
            text_rect = QRectF(35, 54, 150, 32)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, time_str)
        else:
            W = self.width()
            H = self.height()
            has_nav = (len(self.reminders) > 4)
            nav_h = 24 if has_nav else 0
            grid_h = max(1, H - nav_h)
            cw = W / 2.0
            ch = grid_h / 2.0
            
            start_i = self.current_page * 4
            end_i = min(len(self.reminders), start_i + 4)
            now = datetime.now()
            
            for k in range(end_i - start_i):
                rem_idx = start_i + k
                rem = self.reminders[rem_idx]
                r = k // 2
                c = k % 2
                cx = c * cw
                cy = r * ch
                
                target = rem["target_dt"]
                created = rem["created_dt"]
                
                total_secs = (target - created).total_seconds()
                remaining_secs = (target - now).total_seconds()
                if remaining_secs < 0: remaining_secs = 0
                
                progress = max(0.0, min(1.0, 1.0 - (remaining_secs / total_secs))) if total_secs > 0 else 1.0
                
                arc_size = min(cw * 0.62, ch * 0.62)
                bw = max(42, min(56, int(cw * 0.45)))
                bh = max(16, min(20, int(ch * 0.2)))
                gap = 6
                total_h = (arc_size / 2.0) + gap + bh
                
                top_padding = max(4, (ch - total_h) / 2.0)
                arc_x = cx + (cw - arc_size) / 2.0
                arc_y = cy + top_padding
                arc_rect = QRectF(arc_x, arc_y, arc_size, arc_size)
                
                pen_width = max(3.0, min(8.0, arc_size * 0.12))
                
                # Background arc
                painter.setPen(QPen(QColor(40, 20, 0, 200), pen_width))
                painter.drawArc(arc_rect, 180 * 16, -180 * 16)
                
                # Active progress arc
                painter.setPen(QPen(QColor(255, 170, 0, 255), pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
                span_angle = int(-progress * 180 * 16)
                painter.drawArc(arc_rect, 180 * 16, span_angle)
                
                mins, secs = divmod(int(remaining_secs), 60)
                hours, mins = divmod(mins, 60)
                if hours > 0:
                    time_str = f"{hours:02d}:{mins:02d}:{secs:02d}"
                else:
                    time_str = f"{mins:02d}:{secs:02d}"
                    
                painter.setPen(QColor(255, 170, 0, 255))
                font = painter.font()
                font.setFamily("Courier New")
                font_pt = max(7, min(11, int(arc_size * 0.17)))
                font.setPointSize(font_pt)
                font.setBold(True)
                painter.setFont(font)
                
                text_rect = QRectF(cx, arc_y + arc_size * 0.20, cw, arc_size * 0.35)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, time_str)
