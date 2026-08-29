import re

with open("src/clUI.py", "r") as f:
    code = f.read()

# We want to extract the body of __init__, resizeEvent, paintEvent, and _generate_honeycomb from JarvisUI.
# Actually, the simplest way is to rename JarvisUI to FullscreenUI, and then remove everything else from FullscreenUI!
# Then create OverlayUI.
# Then create JarvisUIManager that instantiates both.

