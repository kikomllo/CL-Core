import ast
import astor

with open("src/clUI.py", "r") as f:
    source = f.read()

tree = ast.parse(source)

jarvis_ui_class = None
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "JarvisUI":
        jarvis_ui_class = node
        break

fullscreen_methods = ["__init__", "resizeEvent", "paintEvent", "_generate_honeycomb"]
manager_methods = [m.name for m in jarvis_ui_class.body if isinstance(m, ast.FunctionDef) and m.name not in fullscreen_methods]

print(f"Fullscreen methods: {fullscreen_methods}")
print(f"Manager methods: {len(manager_methods)}")
