with open("src/clControl.py", "r") as f:
    text = f.read()

# 1. Annotate parsed_devices
old_discovery = """        self.last_discovered_devices = parsed_devices"""
new_discovery = """        for p_dev in parsed_devices:
            for saved_name, saved_dev in self.lights.items():
                if p_dev['mac'] == saved_dev['mac'] or p_dev['ip'] == saved_dev['ip']:
                    p_dev['saved_name'] = saved_name
                    break
        self.last_discovered_devices = parsed_devices"""
text = text.replace(old_discovery, new_discovery)

# 2. Update _handle_discovery_selection
old_selection = """        # 4. Success Execution - Request Naming
        name = f"LIGHT_{len(self.lights)+1}"
        self.lights[name] = {
            "ip": selected_device['ip'],
            "mac": selected_device['mac'],
            "type": selected_device['type'].lower()
        }
        self._save_devices()
        return {
            "status": "success",
            "action": "request_naming",
            "temp_name": name,
            "message": f"Successfully connected to {selected_device['type']} at {selected_device['ip']}."
        }"""
new_selection = """        # 4. Success Execution - Request Naming
        name = selected_device.get('saved_name')
        if not name:
            name = f"LIGHT_{len(self.lights)+1}"
            self.lights[name] = {
                "ip": selected_device['ip'],
                "mac": selected_device['mac'],
                "type": selected_device['type'].lower()
            }
            self._save_devices()
        
        return {
            "status": "success",
            "action": "request_naming",
            "temp_name": name,
            "message": f"Selected {name.replace('_', ' ')}." if selected_device.get('saved_name') else f"Successfully connected to {selected_device['type']} at {selected_device['ip']}."
        }"""
text = text.replace(old_selection, new_selection)

with open("src/clControl.py", "w") as f:
    f.write(text)


with open("src/clDaemon.py", "r") as f:
    text2 = f.read()

old_daemon = """                                    ui_options = [f"[{i}] {dev['type'].upper()} {dev['model']}" for i, dev in enumerate(devices)]"""
new_daemon = """                                    ui_options = []
                                    for i, dev in enumerate(devices):
                                        if dev.get('saved_name'):
                                            ui_options.append(f"[{i}] {dev['saved_name'].replace('_', ' ').title()}")
                                        else:
                                            ui_options.append(f"[{i}] {dev['type'].upper()} {dev['model']}")"""

text2 = text2.replace(old_daemon, new_daemon)

with open("src/clDaemon.py", "w") as f:
    f.write(text2)
