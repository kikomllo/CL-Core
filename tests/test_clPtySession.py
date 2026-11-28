"""The shell behind the dashboard's full terminal: a real pseudo-terminal streamed as it prints (src/utils/clPtySession.py)."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils import clPtySession as P


class FakePty:
    """What a backend gives the session: chunks to read, what was written and resized."""
    def __init__(self, chunks=()):
        self.chunks, self.written, self.sizes, self.closed = list(chunks), [], [], False
        self._alive = True
        self.code = 0

    def wait_readable(self, timeout):
        if self.chunks:
            return True
        time.sleep(min(timeout, 0.01))
        return False

    def read(self, n):
        if self.chunks:
            c = self.chunks.pop(0)
            if c is EOFError:
                self._alive = False
                raise EOFError
            return c
        return ""

    def write(self, data): self.written.append(data)
    def resize(self, cols, rows): self.sizes.append((cols, rows))
    def alive(self): return self._alive
    def exit_code(self): return self.code
    def close(self): self.closed = True; self._alive = False


def session(fake, **kw):
    out, ended = [], []
    s = P.PtySession(out.append, ended.append, argv=["sh"], spawn=lambda *a: fake, env={"PATH": "x"}, **kw)
    return s, out, ended


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class TestOutput:
    def test_what_the_shell_prints_comes_out_in_order(self):
        s, out, _ = session(FakePty(["hello ", "world"]))
        s.start()
        assert wait_for(lambda: "".join(out) == "hello world")
        s.close()

    def test_a_burst_of_small_reads_is_one_message(self):
        s, out, _ = session(FakePty(["a", "b", "c", "d", "e"]))
        s.start()
        assert wait_for(lambda: "".join(out) == "abcde")
        assert len(out) <= 2
        s.close()

    def test_a_page_that_opens_later_gets_a_replay(self):
        s, out, _ = session(FakePty(["one\n", "two\n"]))
        s.start()
        assert wait_for(lambda: "".join(out) == "one\ntwo\n")
        assert s.replay() == "one\ntwo\n"
        s.close()

    def test_the_replay_is_capped_and_starts_on_a_line(self, monkeypatch):
        monkeypatch.setattr(P, "SCROLLBACK_CHARS", 20)
        s, _, _ = session(FakePty())
        s._remember("aaaa\nbbbb\ncccc\ndddd\neeee\nffff\n")
        assert len(s.replay()) <= 20 and s.replay().endswith("ffff\n")
        assert s.replay().startswith(("c", "d", "e", "f"))


class TestInputAndSize:
    def test_keystrokes_reach_the_shell_as_typed(self):
        fake = FakePty()
        s, _, _ = session(fake)
        s.start()
        s.write("ls\r")
        s.write("\x03")
        assert fake.written == ["ls\r", "\x03"]
        s.close()

    def test_writing_before_the_shell_is_running_is_an_error(self):
        s, _, _ = session(FakePty())
        with pytest.raises(RuntimeError):
            s.write("x")

    def test_resize_reaches_the_shell_and_is_remembered_for_a_later_start(self):
        fake = FakePty()
        s, _, _ = session(fake, cols=100, rows=30)
        s.resize(80, 20)
        s.start()
        s.resize(90, 25)
        assert fake.sizes == [(90, 25)]
        assert (s._cols, s._rows) == (90, 25)
        s.close()


class TestEnding:
    def test_the_exit_is_reported_once_with_its_code(self):
        fake = FakePty(["bye\n", EOFError])
        fake.code = 3
        s, out, ended = session(fake)
        s.start()
        assert wait_for(lambda: ended == [3])
        assert "".join(out) == "bye\n" and s.alive is False

    def test_closing_it_ourselves_is_not_reported_as_an_exit(self):
        fake = FakePty()
        s, _, ended = session(fake)
        s.start()
        s.close()
        time.sleep(0.3)
        assert fake.closed and ended == []


class TestEnvironmentAndShell:
    def test_the_environment_drops_what_breaks_programs_started_from_it(self):
        env = P.clean_env({"PATH": "p", "ELECTRON_RUN_AS_NODE": "1", "CLAUDE_CODE_ENTRYPOINT": "x", "HOME": "h"})
        assert env["PATH"] == "p" and env["HOME"] == "h" and env["TERM"] == "xterm-256color"
        assert "ELECTRON_RUN_AS_NODE" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env

    def test_the_shell_can_be_chosen_in_the_environment(self, monkeypatch):
        monkeypatch.setattr(P.sys, "platform", "linux")
        assert P.default_shell({"JARVIS_CONSOLE_SHELL": "zsh -l"}) == ["zsh", "-l"]

    def test_windows_prefers_powershell_then_cmd(self, monkeypatch):
        monkeypatch.setattr(P.sys, "platform", "win32")
        monkeypatch.setattr(P.shutil, "which", lambda n: "C:/x/" + n if n == "powershell.exe" else None)
        assert P.default_shell({}) == ["powershell.exe", "-NoLogo"]
        monkeypatch.setattr(P.shutil, "which", lambda n: None)
        assert P.default_shell({"COMSPEC": "C:/cmd.exe"}) == ["C:/cmd.exe"]

    def test_linux_uses_the_login_shell(self, monkeypatch):
        monkeypatch.setattr(P.sys, "platform", "linux")
        assert P.default_shell({"SHELL": "/bin/zsh"}) == ["/bin/zsh"]
        assert P.default_shell({}) == ["/bin/bash"]


@pytest.mark.skipif(not (sys.platform == "win32" or os.path.exists("/bin/sh")), reason="no shell to start")
def test_a_real_shell_prints_what_it_is_told():
    """The real backend (ConPTY on Windows, pty elsewhere): type a command, read its output back."""
    out = []
    s = P.PtySession(out.append, lambda code: None, cwd=os.getcwd(), cols=100, rows=24)
    try:
        s.start()
    except Exception as e:
        pytest.skip(f"cannot start a pseudo-terminal here: {e}")
    try:
        time.sleep(1.5)
        s.write("echo smoke-test-123\r")
        assert wait_for(lambda: "smoke-test-123" in "".join(out), timeout=10)
    finally:
        s.close()
