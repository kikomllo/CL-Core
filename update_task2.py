with open("/home/kikomlo/.gemini/antigravity-ide/brain/bb0ac0b9-37a3-4bad-90fc-c097b059dae8/task.md", "r") as f:
    text = f.read()

text += "\n- `[x]` Dynamically sync UIScaler with actual Wayland physical window location using `self.screen()`\n- `[x]` Anchor layout coordinates to internal window dimensions (`self.width()`) instead of target monitor geometry to mitigate compositor cropping/scaling."

with open("/home/kikomlo/.gemini/antigravity-ide/brain/bb0ac0b9-37a3-4bad-90fc-c097b059dae8/task.md", "w") as f:
    f.write(text)
