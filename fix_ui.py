import re

with open('src/clUI.py', 'r') as f:
    content = f.read()

# Fix _handle_ui_control
content = re.sub(
    r'    def _handle_ui_control.*?def _handle_state_change',
    '''    def _handle_ui_control(self, payload):
        if isinstance(payload, dict) and "action" in payload:
            action = payload["action"]
            if action in ["set_fullscreen", "set_overlay"]:
                self.ui_mode_signal.emit(action)
            elif action == "toggle_todos":
                self.toggle_widget_signal.emit("toggle_todos")
            elif action == "toggle_reminders":
                self.toggle_widget_signal.emit("toggle_reminders")
            elif action == "toggle_media":
                self.toggle_widget_signal.emit("toggle_media")
            elif action == "toggle_lights":
                self.toggle_widget_signal.emit("toggle_lights")
            elif action == "toggle_calendar":
                self.toggle_widget_signal.emit("toggle_calendar")

    def _handle_state_change''',
    content, flags=re.DOTALL
)

with open('src/clUI.py', 'w') as f:
    f.write(content)

