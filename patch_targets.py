with open("src/clControl.py", "r") as f:
    text = f.read()

rename_old = """                        if action == "intent_rename_light":
                            target_str = payload.get("target_str", "").strip()"""
rename_new = """                        if action == "intent_rename_light":
                            target_str = payload.get("target_str", "").strip()
                            if not target_str:
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "action": "request_light_rename", "message": "Which light would you like to rename, and to what?"}))
                                continue"""

remove_old = """                        if action == "intent_remove_light":
                            target_str = payload.get("target_str", "").strip().upper().replace(" ", "_")"""
remove_new = """                        if action == "intent_remove_light":
                            target_str = payload.get("target_str", "").strip().upper().replace(" ", "_")
                            if not target_str:
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "action": "request_light_remove", "message": "Which light would you like to remove?"}))
                                continue"""

default_old = """                        if action == "intent_set_default_light":
                            target_str = payload.get("target_str", "").strip().upper().replace(" ", "_")"""
default_new = """                        if action == "intent_set_default_light":
                            target_str = payload.get("target_str", "").strip().upper().replace(" ", "_")
                            if not target_str:
                                await mqtt_client.publish("jarvis/feedback", json.dumps({"device": "smart_lights", "status": "success", "action": "request_light_default", "message": "Which light would you like to set as default?"}))
                                continue"""

text = text.replace(rename_old, rename_new).replace(remove_old, remove_new).replace(default_old, default_new)

with open("src/clControl.py", "w") as f:
    f.write(text)

with open("src/clDaemon.py", "r") as f:
    daemon_text = f.read()

daemon_old = """                                elif device == 'smart_lights' and fb.get('action') == 'request_light_action':
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))"""
daemon_new = """                                elif device == 'smart_lights' and fb.get('action') == 'request_light_action':
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'smart_lights' and fb.get('action') == 'request_light_remove':
                                    self.active_context = {"type": "light_remove_target", "expires_at": time.time() + 30.0}
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'smart_lights' and fb.get('action') == 'request_light_default':
                                    self.active_context = {"type": "light_default_target", "expires_at": time.time() + 30.0}
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'smart_lights' and fb.get('action') == 'request_light_rename':
                                    self.active_context = {"type": "light_rename_target", "expires_at": time.time() + 30.0}
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))"""
daemon_text = daemon_text.replace(daemon_old, daemon_new)

context_old = """        if self.active_context["type"] == "discovery_name":
            temp_name = self.active_context.get("temp_name", "unknown")
            self.active_context["type"] = None
            if clean_text.lower() == "skip":
                return []
            return [({"action": "intent_rename_light", "target_str": f"{temp_name} to {clean_text}"}, "home/room/all/set")]"""
context_new = """        if self.active_context["type"] == "discovery_name":
            temp_name = self.active_context.get("temp_name", "unknown")
            self.active_context["type"] = None
            if clean_text.lower() == "skip":
                return []
            return [({"action": "intent_rename_light", "target_str": f"{temp_name} to {clean_text}"}, "home/room/all/set")]

        if self.active_context["type"] == "light_remove_target":
            self.active_context["type"] = None
            if clean_text.lower() == "cancel": return []
            return [({"action": "intent_remove_light", "target_str": clean_text}, "home/room/all/set")]

        if self.active_context["type"] == "light_default_target":
            self.active_context["type"] = None
            if clean_text.lower() == "cancel": return []
            return [({"action": "intent_set_default_light", "target_str": clean_text}, "home/room/all/set")]

        if self.active_context["type"] == "light_rename_target":
            self.active_context["type"] = None
            if clean_text.lower() == "cancel": return []
            return [({"action": "intent_rename_light", "target_str": clean_text}, "home/room/all/set")]"""
daemon_text = daemon_text.replace(context_old, context_new)

with open("src/clDaemon.py", "w") as f:
    f.write(daemon_text)
