import json

with open("config/core.json", "r") as f:
    core = json.load(f)

if "abort_keywords" in core.get("nlp_rules", {}):
    core["nlp_rules"]["abort_keywords"].extend(["no", "no thanks", "nope", "nothing", "nah"])
    # deduplicate just in case
    core["nlp_rules"]["abort_keywords"] = list(set(core["nlp_rules"]["abort_keywords"]))

with open("config/core.json", "w") as f:
    json.dump(core, f, indent=4)


with open("src/nlp/clIntentEngine.py", "r") as f:
    nlp_text = f.read()

old_abort = """    def is_abort_command(self, text: str) -> bool:
        \"\"\"Checks if the payload contains any abort keywords.\"\"\"
        return any(abort_word in text for abort_word in self.abort_keywords)"""
new_abort = """    def is_abort_command(self, text: str) -> bool:
        \"\"\"Checks if the payload is purely an abort command.\"\"\"
        return text in self.abort_keywords"""

nlp_text = nlp_text.replace(old_abort, new_abort)
with open("src/nlp/clIntentEngine.py", "w") as f:
    f.write(nlp_text)


with open("src/clDaemon.py", "r") as f:
    daemon_text = f.read()

daemon_text = daemon_text.replace('if clean_text.lower() == "skip":', 'if clean_text.lower() == "skip" or self.nlp.is_abort_command(clean_text):')
daemon_text = daemon_text.replace('if clean_text.lower() == "cancel": return []', 'if self.nlp.is_abort_command(clean_text): return []')

with open("src/clDaemon.py", "w") as f:
    f.write(daemon_text)
