with open("src/clControl.py", "r") as f:
    text = f.read()

# 1. Update discovery name to uppercase
text = text.replace('name = f"Light_{len(self.lights)+1}"', 'name = f"LIGHT_{len(self.lights)+1}"')

# 2. Update list_saved formatting to add index and capitalize properly
old_list = 'devices = [k.replace("_", " ") for k in manager.lights.keys()]'
new_list = 'devices = [f"[{i}] {k.replace(\'_\', \' \').title()}" for i, k in enumerate(manager.lights.keys())]'
text = text.replace(old_list, new_list)

# 3. Update matching to use uppercase
old_match = """                            for k in keys:
                                if target in k.lower() or k.lower() in target:"""
new_match = """                            for k in keys:
                                if target in k.upper() or k.upper() in target:"""
text = text.replace(old_match, new_match)

# 4. Update intents to use uppercase for strings instead of lowercase
text = text.replace('old_name = words[0].strip().lower().replace(" ", "_")', 'old_name = words[0].strip().upper().replace(" ", "_")')
text = text.replace('new_name = words[-1].strip().lower().replace(" ", "_")', 'new_name = words[-1].strip().upper().replace(" ", "_")')
text = text.replace('target_str = payload.get("target_str", "").strip().lower().replace(" ", "_")', 'target_str = payload.get("target_str", "").strip().upper().replace(" ", "_")')

with open("src/clControl.py", "w") as f:
    f.write(text)
