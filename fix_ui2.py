import re

with open('src/clUI.py', 'r') as f:
    content = f.read()

# 1. Remove _is_ui_obscured entirely
content = re.sub(r'    def _is_ui_obscured\(self\):.*?    def set_state\(self, state\):', '    def set_state(self, state):', content, flags=re.DOTALL)

# 2. Fix set_state to not call _is_ui_obscured and publish overlay
content = re.sub(
    r'    def set_state\(self, state\):\n        self\.state = state\n        \n        if state in \["LISTENING", "RECORDING", "ATTENTION"\]:\n            if getattr\(self, \'is_fullscreen\', False\) and self\._is_ui_obscured\(\):\n                import paho\.mqtt\.publish as publish\n                import json\n                try:\n                    publish\.single\("jarvis/sys/ui_control", json\.dumps\(\{"action": "set_overlay"\}\), hostname="localhost", qos=0\)\n                except Exception as e:\n                    print\(f"Failed to switch to overlay mode: \{e\}"\)\n        \n        self\.visualizer\.set_state\(state, self\.is_fullscreen\)',
    '    def set_state(self, state):\n        self.state = state\n        \n        self.visualizer.set_state(state, self.is_fullscreen)',
    content
)


# 3. Add toggle_widget_signal to MqttThread
content = content.replace(
    'ui_mode_signal = pyqtSignal(str)',
    'ui_mode_signal = pyqtSignal(str)\n    toggle_widget_signal = pyqtSignal(str)'
)

# 4. Connect toggle_widget_signal in JarvisUI
content = content.replace(
    'self.mqtt_thread.ui_mode_signal.connect(self.set_ui_mode)',
    'self.mqtt_thread.ui_mode_signal.connect(self.set_ui_mode)\n        self.mqtt_thread.toggle_widget_signal.connect(self._handle_toggle_widget)'
)

# 5. Fix _handle_ui_control
content = re.sub(
    r'    def _handle_ui_control.*?def _handle_state_change',
    '''    def _handle_ui_control(self, payload):
        if isinstance(payload, dict) and "action" in payload:
            action = payload["action"]
            if action in ["set_fullscreen", "set_overlay"]:
                self.ui_mode_signal.emit(action)
            elif action.startswith("toggle_"):
                self.toggle_widget_signal.emit(action)

    def _handle_state_change''',
    content, flags=re.DOTALL
)

# 6. Add _handle_toggle_widget to JarvisUI
# I will append it before set_ui_mode
content = content.replace(
    '    def set_ui_mode(self, mode):',
    '''    def _handle_toggle_widget(self, action):
        if action == "toggle_todos":
            self._toggle_todos()
        elif action == "toggle_reminders":
            self._toggle_reminders()
        elif action == "toggle_media":
            self._toggle_media()
        elif action == "toggle_lights":
            self._toggle_lights()
        elif action == "toggle_calendar":
            self._toggle_calendar()

    def set_ui_mode(self, mode):'''
)

# 7. Remove is_fullscreen guards from toggle methods
def remove_guard(match):
    body = match.group(2)
    # Dedent the body by 4 spaces
    lines = body.split('\n')
    new_lines = []
    for line in lines:
        if line.startswith('    '):
            new_lines.append(line[4:])
        elif not line.strip():
            new_lines.append('')
        else:
            new_lines.append(line)
    return match.group(1) + '\n'.join(new_lines)

# _toggle_media
content = re.sub(r'(    def _toggle_media\(self\):\n)\s*if getattr\(self, \'is_fullscreen\', False\):\n(.*?)(\n    def _toggle_lights)', remove_guard, content, flags=re.DOTALL)
# _toggle_lights
content = re.sub(r'(    def _toggle_lights\(self\):\n)\s*if getattr\(self, \'is_fullscreen\', False\):\n(.*?)(\n    def _toggle_reminders)', remove_guard, content, flags=re.DOTALL)
# _toggle_todos
content = re.sub(r'(    def _toggle_todos\(self\):\n)\s*if getattr\(self, \'is_fullscreen\', False\):\n(.*?)(\n    def _toggle_calendar)', remove_guard, content, flags=re.DOTALL)

with open('src/clUI.py', 'w') as f:
    f.write(content)

