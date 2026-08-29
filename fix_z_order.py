import re

with open("src/clUI.py", "r") as f:
    text = f.read()

enforce_z_order_code = """
    def _enforce_z_order(self):
        if getattr(self, 'is_fullscreen', False):
            if hasattr(self, 'calendar_drawer'):
                self.calendar_drawer.raise_()
            if getattr(self, 'text_input', None) is not None:
                self.text_input.raise_()
"""

# Inject before _toggle_media
text = text.replace("    def _toggle_media(self):", enforce_z_order_code + "\n    def _toggle_media(self):")

# Fix DraggableWidget mousePressEvent
draggable_search = """                if hasattr(self.content_widget, "on_drag_start"):
                    self.content_widget.on_drag_start()
            self.raise_()"""

draggable_replace = """                if hasattr(self.content_widget, "on_drag_start"):
                    self.content_widget.on_drag_start()
            self.raise_()
            if hasattr(self.parent(), "_enforce_z_order"):
                self.parent()._enforce_z_order()"""

text = text.replace(draggable_search, draggable_replace)

# Replace all raise_() inside JarvisUI with raise_() + _enforce_z_order() where appropriate
# Actually, the easiest way is to intercept where widgets are raised in JarvisUI toggles
# The toggles look like:
# w.raise_() or wrapper.raise_()
# We can just blindly replace .raise_() with .raise_(); self._enforce_z_order() in the JarvisUI class body
# But we must be careful not to replace it in DraggableWidget again, or on calendar_drawer itself!

lines = text.split('\n')
out = []
in_jarvis = False
for line in lines:
    if line.startswith("class JarvisUI("):
        in_jarvis = True
    
    if in_jarvis and "raise_()" in line:
        if "self.calendar_drawer.raise_()" in line or "self.text_input.raise_()" in line or "self.raise_()" in line:
            # Don't enforce if we are already raising the drawer or text input
            pass
        else:
            line = line.replace("raise_()", "raise_(); self._enforce_z_order()")
            
    out.append(line)

with open("src/clUI.py", "w") as f:
    f.write('\n'.join(out))
    
