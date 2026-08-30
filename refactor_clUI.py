import re

with open("src/clUI.py", "r") as f:
    content = f.read()

# I will write a script to insert `def refresh_layout(self):` after `JarvisUI.__init__`
