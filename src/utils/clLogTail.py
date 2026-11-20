"""Live tail of logs/latest_run.log, shared by the dashboard's Live Logs widget and the terminal window it can open
(`python src/utils/clLogTail.py [--raw]`). Pure Python, no Qt.

The log is the full record (every service's stdout, plus the supervisor's MQTT sniffer), so most of it is heartbeat noise and a few
lines are tens of KB. `condense` is a view over it, never a change to the file: it drops the noise, shortens what is left to one
screen line, and redacts bearer tokens."""
import os
import re
import subprocess
import sys
import shutil
import time

LOG_PATH = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs", "latest_run.log"))

LINE_CHARS = 200            # condensed: one screen line
RAW_LINE_CHARS = 600        # raw: still cut (a lyrics payload is one 40 KB line), but generously
PAYLOAD_CHARS = 140         # an MQTT payload
RESULTS_CHARS = 60          # a Spotify API response dump

_ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
_STAMPS = re.compile(r'^(?:\[\d\d:\d\d:\d\d\] )+')        # services stamp their lines and the supervisor stamps them again
_BEARER = re.compile(r'(Bearer )[A-Za-z0-9._~+/=-]+')
_MQTT = re.compile(r'^\[DEBUG-MQTT\] \[CH: ([^\]]+)\] ?(.*)$', re.S)
_HTTP_CALL = re.compile(r"^\[SPOTIFY\] Sending (GET|PUT|POST|DELETE) to https?://[^/\s]+(/[^\s?]*)(\?\S*)? with Params")

# Topics that fire every second or so (position ticks, VU meter, status echoes) or carry a whole payload (lyrics, queue).
NOISY_TOPICS = frozenset({
    "jarvis/sys/audio_vol", "jarvis/sys/spotify_local_status", "jarvis/sys/os_media_status", "jarvis/sys/media_status",
    "jarvis/sys/tts_ref", "jarvis/sys/user_activity", "jarvis/sys/spotify_lyrics", "jarvis/sys/spotify_queue",
    "jarvis/sys/todo/request", "jarvis/sys/todo/status",          # the dashboard polls the list every few seconds
})

# Lines (after the timestamps) that say nothing: protocol chatter of paho, urllib3 and the Tapo client, and the UI's own key/layout dumps.
_NOISE = re.compile(
    r"^\[\w+\] (?:Sending|Received) (?:CONNECT|CONNACK|PUBLISH|SUBSCRIBE|SUBACK|UNSUBSCRIBE|UNSUBACK|PUBACK|DISCONNECT|PINGREQ|PINGRESP)\b"
    r"|^\[TERMINAL\] AUMID lookup"
    r"|^\[UI\] \[DEBUG (?:KEY|LAYOUT)\]"
    r"|^\[SPOTIFY\] https?://"                       # urllib3's one line per request
    r"|^\[SPOTIFY\] Resetting dropped connection"
    r"|^\[CONTROL\] (?:Device url:|Component negotiation request|Performing handshake|Handshake\d OK|Testing the AES protocol)"
)


def _cut(text, limit):
    return text if len(text) <= limit else f"{text[:limit]} ... [+{len(text) - limit} chars]"


def condense(line, condensed=True):
    """The line as it should be shown, or None to hide it. condensed=False only cleans and cuts."""
    line = _ANSI.sub('', line).rstrip("\r").rsplit("\r", 1)[-1]
    line = _BEARER.sub(r'\1***', line)
    if not condensed:
        return _cut(line, RAW_LINE_CHARS)
    if not line.strip():
        return None
    m = _STAMPS.match(line)
    stamp = m.group(0).split("] ")[0] + "] " if m else ""
    rest = line[m.end():] if m else line

    mqtt = _MQTT.match(rest)
    if mqtt:
        topic, payload = mqtt.group(1), mqtt.group(2).strip()
        if topic in NOISY_TOPICS:
            return None
        return _cut(f"{stamp}[MQTT] {topic} {_cut(payload, PAYLOAD_CHARS)}", LINE_CHARS)
    if _NOISE.match(rest):
        return None
    call = _HTTP_CALL.match(rest)
    if call:                                          # the headers carry the token and say nothing
        query = re.sub(r'[?&]device_id=[^&\s]*', '', call.group(3) or '').replace('?&', '?')
        return f"{stamp}[SPOTIFY] {call.group(1)} {call.group(2)}{query if query != '?' else ''}"
    if rest.startswith("[SPOTIFY] RESULTS: "):         # a whole API response: its size is all that matters
        return _cut(stamp + rest, len(stamp) + 19 + RESULTS_CHARS)
    return _cut(stamp + rest, LINE_CHARS)


def condense_text(text, condensed=True):
    """text is whole lines (it ends with a newline); returns what is left of them, again newline-terminated."""
    out = []
    for line in text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"):
        shown = condense(line, condensed)
        if shown is not None:
            out.append(shown + "\n")
    return "".join(out)


class LogTailer:
    """Reads the log incrementally and in bounded steps, whole lines only. The first read starts at the tail."""

    def __init__(self, path=LOG_PATH, initial_bytes=256 * 1024, max_read=256 * 1024):
        self.path = path
        self.initial_bytes = initial_bytes
        self.max_read = max_read
        self.pos = None

    def reset(self):
        self.pos = None

    def read(self):
        """(text, note, restarted): the whole lines written since the last read; note says part of the log was skipped; restarted
        says the log was rewritten (a new run), so a viewer should start over."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return "", "", False
        note = ""
        restarted = self.pos is not None and size < self.pos
        if restarted:
            self.pos = 0
        start = self.pos
        if start is None:
            start = max(0, size - self.initial_bytes)
            skipped = start
        elif size - start > self.max_read:
            skipped = size - self.max_read - start
            start = size - self.max_read
            note = f"... {skipped // 1024} KB of log skipped ...\n"
        else:
            skipped = 0
        if size == start:
            self.pos = start
            return "", note, restarted

        with open(self.path, 'rb') as f:
            f.seek(start)
            data = f.read(size - start)
        if skipped:                                                    # started mid-line: drop the partial first line
            cut = data.find(b"\n")
            if cut != -1:
                data = data[cut + 1:]
                start += cut + 1
        end = data.rfind(b"\n")
        if end == -1:                                                  # a line still being written: wait for its end
            self.pos = start
            return "", note, restarted
        self.pos = start + end + 1
        return data[:end + 1].decode('utf-8', errors='replace'), note, restarted


def _console_python():
    """A console needs python.exe, not pythonw.exe."""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(console):
            return console
    return exe


def terminal_command(raw=False):
    cmd = [_console_python(), os.path.abspath(__file__)]
    return cmd + ["--raw"] if raw else cmd


_LINUX_TERMINALS = (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"), ("konsole", "-e"), ("xfce4-terminal", "-x"), ("xterm", "-e"))


def open_terminal(raw=False):
    """Opens a separate terminal window following the log. False if the machine has no terminal to open."""
    cmd = terminal_command(raw)
    try:
        if sys.platform == "win32":
            subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
            return True
        for term, flag in _LINUX_TERMINALS:
            if shutil.which(term):
                subprocess.Popen([term, flag] + cmd, start_new_session=True)
                return True
    except OSError:
        pass
    return False


def main(argv):
    raw = "--raw" in argv
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    title = "JARVIS Live Logs (raw)" if raw else "JARVIS Live Logs"
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
    else:
        sys.stdout.write(f"\033]0;{title}\007")
    print(f"{title}: {LOG_PATH}  (Ctrl+C to close)\n", flush=True)

    tailer = LogTailer(initial_bytes=64 * 1024)
    try:
        while True:
            text, note, restarted = tailer.read()
            if restarted:
                print("---- new run ----", flush=True)
            shown = note + condense_text(text, not raw)
            if shown:
                sys.stdout.write(shown)
                sys.stdout.flush()
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
