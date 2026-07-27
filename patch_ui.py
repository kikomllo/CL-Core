with open("src/clUI.py", "r") as f:
    text = f.read()

# Fix MediaWidget buttons
old_buttons = """        self.prev_btn.clicked.connect(lambda: self.send_cmd("media_prev"))
        
        self.play_btn = QPushButton("⏯")
        self.play_btn.setFixedSize(30, 30)
        self.play_btn.setStyleSheet(btn_style)
        self.play_btn.clicked.connect(lambda: self.send_cmd("media_play")) # Acts as toggle in playerctl usually
        
        self.next_btn = QPushButton("⏭")
        self.next_btn.setFixedSize(30, 30)
        self.next_btn.setStyleSheet(btn_style)
        self.next_btn.clicked.connect(lambda: self.send_cmd("media_next"))"""
new_buttons = """        self.prev_btn.clicked.connect(lambda: self.send_cmd("prev"))
        
        self.play_btn = QPushButton("⏯")
        self.play_btn.setFixedSize(30, 30)
        self.play_btn.setStyleSheet(btn_style)
        self.play_btn.clicked.connect(lambda: self.send_cmd("toggle"))
        
        self.next_btn = QPushButton("⏭")
        self.next_btn.setFixedSize(30, 30)
        self.next_btn.setStyleSheet(btn_style)
        self.next_btn.clicked.connect(lambda: self.send_cmd("next"))"""

text = text.replace(old_buttons, new_buttons)

old_send = """    def send_cmd(self, action):
        import paho.mqtt.publish as publish
        try:
            publish.single("pc/system/control", json.dumps({"action": action}), hostname="localhost", qos=0)
        except Exception as e:
            print(f"Failed to publish media control: {e}")"""
new_send = """    def send_cmd(self, action):
        import json
        import paho.mqtt.publish as publish
        try:
            publish.single("pc/spotify/control", json.dumps({"action": action}), hostname="localhost", qos=0)
        except Exception as e:
            print(f"Failed to publish media control: {e}")"""

text = text.replace(old_send, new_send)

# Fix adjustSize for lights and media
old_handle_light = """        wrapper = self.active_widgets[widget_id]
        if isinstance(wrapper.content_widget, LightControlWidget):
            wrapper.content_widget.update_status(data)"""
new_handle_light = """        wrapper = self.active_widgets[widget_id]
        if isinstance(wrapper.content_widget, LightControlWidget):
            wrapper.content_widget.update_status(data)
            wrapper.adjustSize()"""
text = text.replace(old_handle_light, new_handle_light)

old_handle_media = """        wrapper = self.active_widgets[widget_id]
        if isinstance(wrapper.content_widget, MediaWidget):
            wrapper.content_widget.update_status(data)"""
new_handle_media = """        wrapper = self.active_widgets[widget_id]
        if isinstance(wrapper.content_widget, MediaWidget):
            wrapper.content_widget.update_status(data)
            wrapper.adjustSize()"""
text = text.replace(old_handle_media, new_handle_media)

with open("src/clUI.py", "w") as f:
    f.write(text)
