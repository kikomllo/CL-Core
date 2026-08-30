with open("/home/kikomlo/.gemini/antigravity-ide/brain/bb0ac0b9-37a3-4bad-90fc-c097b059dae8/task.md", "r") as f:
    text = f.read()

text = text.replace("- `[ ]`", "- `[x]`")

with open("/home/kikomlo/.gemini/antigravity-ide/brain/bb0ac0b9-37a3-4bad-90fc-c097b059dae8/task.md", "w") as f:
    f.write(text)
