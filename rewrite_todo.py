import re

with open("src/ui/clTodoWidget.py", "r") as f:
    code = f.read()

# Remove adding "+" tab in _submit_new_list
code = re.sub(r'(\s+)if plus_idx >= 0:\s+self\.tabs\.removeTab\(plus_idx\).*?self\.create_tab\(text\)\s+if plus_idx >= 0:\s+self\.tabs\.addTab\(QWidget\(\),\s*"\+"\)', 
              r'\1self.create_tab(text)', code, flags=re.DOTALL)

# Remove adding "+" tab in update_status
code = re.sub(r'\s+self\.tabs\.addTab\(QWidget\(\),\s*"\+"\)', '', code)

# Remove special "+" handling in on_tab_changed
code = re.sub(r'(\s+def on_tab_changed\(self, index\):.*?)(if index >= 0:.*?self\.tabs\.tabText\(index\) == "\+":.*?return\s+)(self\.last_valid_index = index)',
              r'\1if index >= 0:\n            \3', code, flags=re.DOTALL)

# Remove on_tab_clicked
code = re.sub(r'\s+def on_tab_clicked\(self, index\):.*?(?=\s+def showEvent)', '\n', code, flags=re.DOTALL)

# Remove tabBarClicked connection in __init__
code = re.sub(r'\s+self\.tabs\.tabBarClicked\.connect\(self\.on_tab_clicked\)', '', code)

# Add corner widget in __init__
corner_widget_code = """
        self.btn_add_list = QPushButton("+")
        self.btn_add_list.setFixedSize(24, 24)
        self.btn_add_list.setStyleSheet(\"\"\"
            QPushButton {
                background: transparent;
                color: #ffaa00;
                font-weight: bold;
                border: none;
                font-size: 14pt;
            }
            QPushButton:hover {
                color: #ffcc00;
            }
        \"\"\")
        self.btn_add_list.clicked.connect(self.prompt_new_list)
        self.tabs.setCornerWidget(self.btn_add_list)
"""
code = re.sub(r'(self\.tabs = QTabWidget\(\).*?self\.layout\.addWidget\(self\.tabs, stretch=1\))',
              r'\1' + corner_widget_code, code, flags=re.DOTALL)

with open("src/ui/clTodoWidget.py", "w") as f:
    f.write(code)

print("done")
