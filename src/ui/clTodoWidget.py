import json
from clTheme import Theme
from utils.clActionRouter import ActionRouter
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame, 
    QLineEdit, QPushButton, QCheckBox, QTabWidget, QInputDialog, QStackedWidget, QSizePolicy
)
from PyQt6.QtCore import Qt, QPoint

class TodoWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        self.setMinimumSize(350, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 10)
        self.layout.setSpacing(0)
        
        # Tabs for lists
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setStyleSheet(Theme.get_style("TodoTabs"))
        self.layout.addWidget(self.tabs, stretch=1)
        self.btn_add_list = QPushButton("+")
        self.btn_add_list.setFixedSize(24, 24)
        self.btn_add_list.setStyleSheet(Theme.get_style("TransparentButton"))
        self.btn_add_list.clicked.connect(self.prompt_new_list)
        self.tabs.setCornerWidget(self.btn_add_list)

        
        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.last_valid_index = 0
        
        self.bottom_stack = QStackedWidget()
        
        # Add task input
        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("New task...")
        self.task_input.setFixedHeight(32)
        self.task_input.setFixedHeight(32)
        self.task_input.returnPressed.connect(self.submit_task)
        
        def input_focus_out(event):
            self.bottom_stack.setCurrentWidget(self.add_btn)
            QLineEdit.focusOutEvent(self.task_input, event)
        self.task_input.focusOutEvent = input_focus_out
        
        # Add Task Button
        self.add_btn = QPushButton("+ Add Task")
        self.add_btn.setFixedHeight(32)
        self.add_btn.setStyleSheet(Theme.get_style("AddButton"))
        self.add_btn.clicked.connect(self.open_task_input)
        
        self.bottom_stack.addWidget(self.add_btn)
        self.bottom_stack.addWidget(self.task_input)
        
        self.bottom_container = QWidget()
        self.bottom_layout = QHBoxLayout(self.bottom_container)
        self.bottom_layout.setContentsMargins(15, 5, 15, 0)
        self.bottom_layout.setSpacing(10)
        self.bottom_layout.addWidget(self.bottom_stack, stretch=1)
        
        self.btn_delete_list = QPushButton("X")
        self.btn_delete_list.setFixedSize(32, 32)
        self.btn_delete_list.setToolTip("Delete List")
        self.btn_delete_list.setStyleSheet(Theme.get_style("DangerButton"))
        
        self.btn_delete_list.clicked.connect(self.delete_current_list)
        self.bottom_layout.addWidget(self.btn_delete_list)
        
        self.layout.addWidget(self.bottom_container)
        
        self.current_list_name = "My To-Do List"
        
    def prompt_new_list(self):
        if not hasattr(self, 'list_dialog') or self.list_dialog is None:
            from PyQt6.QtWidgets import QDialog, QLineEdit, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
            self.list_dialog = QDialog(self)
            self.list_dialog.setWindowTitle("New List")
            self.list_dialog.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
            self.list_dialog.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self.list_dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.list_dialog.setStyleSheet("""
                #BgFrame { background-color: rgba(20, 10, 0, 240); border: 1px solid rgba(255, 120, 0, 100); border-radius: 12px; }
            """)
            
            main_layout = QVBoxLayout(self.list_dialog)
            main_layout.setContentsMargins(0, 0, 0, 0)
            bg_frame = QFrame()
            bg_frame.setObjectName("BgFrame")
            main_layout.addWidget(bg_frame)

            dlg_layout = QVBoxLayout(bg_frame)
            dlg_layout.setContentsMargins(14, 14, 14, 14)
            dlg_layout.setSpacing(10)
            
            title_lbl = QLabel("NEW TO-DO LIST")
            title_lbl.setStyleSheet(Theme.get_style("TitleLabel"))
            dlg_layout.addWidget(title_lbl)
            
            lbl_name = QLabel("List Name:")
            lbl_name.setStyleSheet(Theme.get_style("DimLabel"))
            dlg_layout.addWidget(lbl_name)
            
            self.input_list_name = QLineEdit()
            self.input_list_name.setPlaceholderText("e.g. Groceries...")
            self.input_list_name.returnPressed.connect(self._submit_new_list)
            dlg_layout.addWidget(self.input_list_name)
            
            btn_layout = QHBoxLayout()
            btn_layout.setSpacing(6)
            
            btn_cancel = QPushButton("Cancel")
            btn_cancel.setStyleSheet(Theme.get_style("DangerButton"))
            btn_cancel.clicked.connect(self.list_dialog.reject)
            
            btn_save = QPushButton("Create")
            btn_save.setStyleSheet(Theme.get_style("AddButton"))
            btn_save.clicked.connect(self._submit_new_list)
            
            btn_layout.addWidget(btn_cancel)
            btn_layout.addWidget(btn_save)
            dlg_layout.addLayout(btn_layout)
            
        self.input_list_name.clear()
        
        # Center dialog
        dlg_width = 250
        dlg_height = 150
        self.list_dialog.resize(dlg_width, dlg_height)
        
        # Map to global properly for popup
        if self.parentWidget():
            global_pos = self.parentWidget().mapToGlobal(self.pos())
            cx = global_pos.x() + (self.width() - dlg_width) // 2
            cy = global_pos.y() + (self.height() - dlg_height) // 2
            self.list_dialog.move(cx, cy)
            
        if self.list_dialog.exec() == 0:
            # User canceled, restore the previous tab if it exists
            if hasattr(self, 'last_valid_index') and self.last_valid_index < self.tabs.count() and self.tabs.tabText(self.last_valid_index) != "+":
                self.tabs.setCurrentIndex(self.last_valid_index)
            else:
                # If there are no valid tabs, we default to index 0, which might be '+' but we prevent an infinite loop
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(0)
                self.tabs.blockSignals(False)

    def _submit_new_list(self):
        text = self.input_list_name.text().strip()
        if text:
            plus_idx = -1
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == "+":
                    plus_idx = i
                    break
            
            self.create_tab(text)
                
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == text:
                    self.tabs.setCurrentIndex(i)
                    self.current_list_name = text
                    self.last_valid_index = i
                    break
        else:
            if hasattr(self, 'last_valid_index') and self.last_valid_index < self.tabs.count():
                self.tabs.setCurrentIndex(self.last_valid_index)
        self.list_dialog.accept()

    def delete_current_list(self):
        idx = self.tabs.currentIndex()
        if idx < 0 or self.tabs.tabText(idx) == "+":
            return
            
        list_name = self.tabs.tabText(idx)
        
        # Dispatch delete for all tasks in this list
        if hasattr(self, 'grouped_todos') and list_name in self.grouped_todos:
            for t in self.grouped_todos[list_name]:
                try:
                    self.router.dispatch("todo.delete", id=t["id"])
                except Exception as e:
                    pass
                    
        self.tabs.blockSignals(True)
        self.tabs.removeTab(idx)
        
        # Select another tab if available
        if self.tabs.count() > 1:
            self.tabs.setCurrentIndex(0)
            self.current_list_name = self.tabs.tabText(0)
            self.last_valid_index = 0
        else:
            self.tabs.setCurrentIndex(0) # defaults to '+'
            self.current_list_name = "+"
            self.last_valid_index = 0
            
        self.tabs.blockSignals(False)
        
        # Trigger popup naturally if we fell back to '+'
        if self.tabs.tabText(self.tabs.currentIndex()) == "+":
            self.prompt_new_list()
            
    def create_tab(self, list_name):
        # Check if exists
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == list_name:
                return self.tabs.widget(i)
                
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(15, 15, 15, 30)
        scroll_layout.setSpacing(12)
        scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        scroll.setWidget(scroll_content)
        page_layout.addWidget(scroll)
        
        page.scroll_layout = scroll_layout  # attach layout to page for easy access
        self.tabs.addTab(page, list_name)
        return page

    def on_tab_changed(self, index):
        if index >= 0:
            self.last_valid_index = index
            self.current_list_name = self.tabs.tabText(index)

        
    def showEvent(self, event):
        super().showEvent(event)
        try:
            self.router.dispatch("todo.list")
        except:
            pass

    def hideEvent(self, event):
        super().hideEvent(event)
        self.bottom_stack.setCurrentWidget(self.add_btn)

    def on_drag_start(self):
        self.bottom_stack.setCurrentWidget(self.add_btn)

    def open_task_input(self):
        self.bottom_stack.setCurrentWidget(self.task_input)
        self.task_input.setFocus()

    def submit_task(self):
        task_text = self.task_input.text().strip()
        if task_text:
            self.router.dispatch("todo.create", task=task_text, list_name=self.current_list_name)
            self.task_input.clear()
        self.bottom_stack.setCurrentWidget(self.add_btn)

    def update_status(self, data):
        todos = data.get("todos", [])
        
        # Group todos by list_name
        grouped_todos = {}
        for t in todos:
            lname = t.get("list_name", "My To-Do List")
            if lname not in grouped_todos:
                grouped_todos[lname] = []
            grouped_todos[lname].append(t)
            
        self.grouped_todos = grouped_todos
        
        # Clear existing tabs completely
        self.tabs.clear()
        
        for list_name, tasks in grouped_todos.items():
            scroll = self.create_tab(list_name)
            layout = scroll.scroll_layout
            
            if not tasks:
                lbl = QLabel("No pending tasks.")
                lbl.setStyleSheet(Theme.get_style("DimLabel"))
                layout.addWidget(lbl)
                continue
                
            for t in tasks:
                task_widget = QWidget()
                task_layout = QHBoxLayout(task_widget)
                task_layout.setContentsMargins(0, 0, 0, 0)
                task_layout.setSpacing(8)
                
                chk = QCheckBox()
                chk.setStyleSheet(Theme.get_style("TodoCheckbox"))
                
                lbl = QLabel(t["task"])
                lbl.setWordWrap(True)
                lbl.setStyleSheet("padding: 2px 0px;")
                
                is_completed = t.get("completed", False)
                chk.setChecked(is_completed)
                if is_completed:
                    lbl.setStyleSheet(lbl.styleSheet() + " color: " + Theme.C_TEXT_DIM + "; text-decoration: line-through;")
                
                task_layout.addWidget(chk)
                task_layout.addWidget(lbl, 1)
                
                # Make the entire row toggle the checkbox
                task_widget.mouseReleaseEvent = lambda e, c=chk: c.setChecked(not c.isChecked()) if e.button() == Qt.MouseButton.LeftButton else None
                chk.stateChanged.connect(lambda state, tid=t["id"]: self.toggle_task(tid, state))
                layout.addWidget(task_widget)
        
        # Restore the previously selected tab if possible
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == self.current_list_name:
                self.tabs.setCurrentIndex(i)
                break
                
    def toggle_task(self, todo_id, state):
        is_checked = (state == 2)
        try:
            if is_checked:
                self.router.dispatch("todo.complete", id=todo_id)
            else:
                self.router.dispatch("todo.delete", id=todo_id)
        except:
            pass
