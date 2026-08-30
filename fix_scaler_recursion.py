with open("src/clUIScaler.py", "r") as f:
    code = f.read()

code = code.replace("screens = self.get_stable_screens()\\n        reg_file", "screens = QApplication.screens()\\n        reg_file")

with open("src/clUIScaler.py", "w") as f:
    f.write(code)

print("done")
