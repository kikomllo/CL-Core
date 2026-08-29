with open("src/clUI_new.py", "r") as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if line.startswith("class JarvisUI(QObject):"):
        # The next line is def __init__(self): but maybe it's not indented correctly
        print(f"Line {i}: {line}")
        print(f"Line {i+1}: {lines[i+1]}")
