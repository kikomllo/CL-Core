with open("src/clUI_new.py", "r") as f:
    lines = f.readlines()

out_lines = []
in_old_set_ui_mode = False
for i, line in enumerate(lines):
    if line.startswith("    def set_ui_mode(self, mode):") and i < 1500:
        in_old_set_ui_mode = True
        continue
    if in_old_set_ui_mode and line.startswith("    def save_ui_state(self):"):
        in_old_set_ui_mode = False
        
    if not in_old_set_ui_mode:
        if "sys.exit(app.exec())" in line:
            # Fix the appended junk at EOF
            line = line.replace("sys.exit(app.exec())    def set_ui_mode(self, mode):", "sys.exit(app.exec())\n")
            # The rest of the line is garbage, we just write sys.exit and break
            out_lines.append("    sys.exit(app.exec())\n")
            break
        out_lines.append(line)

with open("src/clUI_new.py", "w") as f:
    f.writelines(out_lines)
