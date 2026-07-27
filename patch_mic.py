with open("src/clMic.py", "r") as f:
    text = f.read()

old_mic = """            # Publish volume data to MQTT so the host supervisor can display it
            # (docker logs doesn't support carriage-return overwriting)
            self._publish("jarvis/sys/volume", {
                "rms": int(current_rms),
                "bar": meter,
                "status": status_tag
            })

            is_active_window = time.time() < self.active_window_end
            bypass_wakeword = self.attention_mode or self.awaiting_reply or is_active_window

            b_noise, a_thresh, s_thresh = self._calculate_thresholds()"""

new_mic = """            b_noise, a_thresh, s_thresh = self._calculate_thresholds()

            # Publish volume data to MQTT so the host supervisor can display it
            # (docker logs doesn't support carriage-return overwriting)
            self._publish("jarvis/sys/volume", {
                "rms": int(current_rms),
                "bar": meter,
                "status": status_tag,
                "b_noise": int(b_noise),
                "a_thresh": int(a_thresh),
                "s_thresh": int(s_thresh)
            })

            is_active_window = time.time() < self.active_window_end
            bypass_wakeword = self.attention_mode or self.awaiting_reply or is_active_window"""

text = text.replace(old_mic, new_mic)

with open("src/clMic.py", "w") as f:
    f.write(text)

with open("clJarvis.py", "r") as f:
    jarvis = f.read()

old_jarvis = """            vol = json.loads(payload_str)
            rms = vol.get("rms", 0)
            bar = vol.get("bar", "-" * 40)
            status = vol.get("status", "STANDARD")
            print(f"\\r\\033[K[{status}] Vol: {rms:5d} ||{bar}||", end='', flush=True)"""
new_jarvis = """            vol = json.loads(payload_str)
            rms = vol.get("rms", 0)
            bar = vol.get("bar", "-" * 40)
            status = vol.get("status", "STANDARD")
            b_noise = vol.get("b_noise")
            if b_noise is not None:
                a_thresh = vol.get("a_thresh", 0)
                s_thresh = vol.get("s_thresh", 0)
                print(f"\\r\\033[K[{status}] Vol: {rms:5d} ||{bar}|| ACT: {a_thresh} SIL: {s_thresh} AVG: {b_noise}", end='', flush=True)
            else:
                print(f"\\r\\033[K[{status}] Vol: {rms:5d} ||{bar}||", end='', flush=True)"""
jarvis = jarvis.replace(old_jarvis, new_jarvis)

with open("clJarvis.py", "w") as f:
    f.write(jarvis)

