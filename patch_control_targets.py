with open("src/clControl.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if 'target_str = payload.get("target_str", "").strip()' in line and 'words = target_str.split(" to ")' in "".join(lines):
        # We are at intent_rename_light
        new_lines.append(line)
        new_lines.append('                            if not target_str:\n')
        new_lines.append('                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "action": "request_light_rename", "message": "Which light would you like to rename, and to what?"}))\n')
        new_lines.append('                                continue\n')
    elif 'target_str = payload.get("target_str", "").strip().upper().replace(" ", "_")' in line:
        new_lines.append(line)
        # Check if we are in remove or default
        # Actually it's both.
        # We can just add a check but wait, remove vs default have different messages. Let's do it manually instead of a script that might break.
        pass
    else:
        new_lines.append(line)
