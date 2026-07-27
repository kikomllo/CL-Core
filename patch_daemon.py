with open("src/clDaemon.py", "r") as f:
    text = f.read()

target = """                                elif device == 'smart_lights' and fb.get('action') == 'request_naming':
                                    self.active_context = {"type": "discovery_name", "expires_at": time.time() + 30.0, "temp_name": fb.get('temp_name')}
                                    msg = fb.get('message', '') + " What would you like to call this light?"
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                else:"""
                                
replacement = """                                elif device == 'smart_lights' and fb.get('action') == 'request_naming':
                                    self.active_context = {"type": "discovery_name", "expires_at": time.time() + 30.0, "temp_name": fb.get('temp_name')}
                                    msg = fb.get('message', '') + " What would you like to call this light?"
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                elif device == 'smart_lights' and fb.get('action') == 'request_light_action':
                                    msg = fb.get('message', '')
                                    await client.publish("jarvis/sys/speak", json.dumps({"text": msg, "request_reply": True}))

                                else:"""

text = text.replace(target, replacement)
with open("src/clDaemon.py", "w") as f:
    f.write(text)
