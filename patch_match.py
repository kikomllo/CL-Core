with open("src/clControl.py", "r") as f:
    text = f.read()

old_match = """                        def _match_light(target, lights_dict):
                            keys = list(lights_dict.keys())
                            if target.isdigit():"""

new_match = """                        def _match_light(target, lights_dict):
                            keys = list(lights_dict.keys())
                            # Try stripping common conversational prefixes for indices
                            clean_target = target.replace("NUMBER_", "").replace("INDEX_", "").replace("OPTION_", "")
                            if clean_target.isdigit():
                                target = clean_target # For fallthrough
                                idx = int(clean_target)
                                if 0 <= idx < len(keys):
                                    return keys[idx]
                            elif target.isdigit():"""

text = text.replace(old_match, new_match)
with open("src/clControl.py", "w") as f:
    f.write(text)
