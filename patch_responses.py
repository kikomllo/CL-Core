import json

with open("config/responses.json", "r") as f:
    data = json.load(f)

data["mqtt"]["jarvis/sys/ui_control"] = {
    "set_fullscreen": ["Entering fullscreen mode, sir.", "Maximizing the graphical interface."],
    "set_overlay": ["Switching to overlay mode.", "Minimizing the interface, sir."]
}

with open("config/responses.json", "w") as f:
    json.dump(data, f, indent=2)
