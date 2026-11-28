"""A shell in a real pseudo-terminal, streamed as it prints (used by src/clConsole.py, drawn by xterm.js in the dashboard's Claude window).

Unlike the screen-snapshot bridges (clPlainTerminalBridge / clWinTerminalBridge: an emulator on this side, whole screens sent over), nothing is
interpreted here: the shell's output goes out as the bytes it wrote and keystrokes go in as the bytes typed, so the page's terminal emulator
does everything a terminal does (colours, cursor, full-screen programs, paste, scrollback) at the speed of the shell.

Windows: ConPTY through pywinpty (the same backend the Claude bridge uses). Linux/macOS: the standard library's pty."""
import codecs
import logging
import os
import select
import shlex
import shutil
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

SCROLLBACK_CHARS = 200_000        # what a page that opens later gets replayed
MAX_CHUNK = 32_768                # one output message is at most about this
COALESCE_S = 0.004                # a burst of small reads goes out as one message


def default_shell(env: Optional[Dict[str, str]] = None) -> List[str]:
    """The command that starts the shell. JARVIS_CONSOLE_SHELL overrides it (a path, with arguments if wanted)."""
    env = os.environ if env is None else env
    forced = env.get("JARVIS_CONSOLE_SHELL")
    if forced:
        return shlex.split(forced, posix=(sys.platform != "win32"))
    if sys.platform == "win32":
        for name in ("pwsh.exe", "powershell.exe"):
            if shutil.which(name):
                return [name, "-NoLogo"]
        return [env.get("COMSPEC") or "cmd.exe"]
    return [env.get("SHELL") or "/bin/bash"]


def clean_env(env: Dict[str, str]) -> Dict[str, str]:
    """The environment for the shell: what was inherited, minus what would break a program started from it (VS Code's ELECTRON_RUN_AS_NODE makes
    Electron run as plain Node; CLAUDE_CODE_* makes a `claude` started here think it is inside another session), plus a terminal type."""
    out = {k: v for k, v in env.items() if k != "ELECTRON_RUN_AS_NODE" and not k.startswith("CLAUDE_CODE_")}
    out["TERM"] = "xterm-256color"
    out["COLORTERM"] = "truecolor"
    return out


class _WinPty:
    def __init__(self, argv, cwd, env, cols, rows):
        import winpty
        # backend=1 is ConPTY: the default silently hangs on reads on this stack (see clWinTerminalBridge)
        self._p = winpty.PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols), backend=1)

    def wait_readable(self, timeout: float) -> bool:
        ready, _, _ = select.select([self._p.fileobj], [], [], timeout)
        return bool(ready)

    def read(self, n: int) -> str:
        return self._p.read(n)

    def write(self, data: str) -> None:
        self._p.write(data)

    def resize(self, cols: int, rows: int) -> None:
        self._p.setwinsize(rows, cols)

    def alive(self) -> bool:
        return bool(self._p.isalive())

    def exit_code(self) -> Optional[int]:
        return getattr(self._p, "exitstatus", None)

    def close(self) -> None:
        try:
            self._p.terminate(force=True)
        except Exception:
            pass


class _PosixPty:
    def __init__(self, argv, cwd, env, cols, rows):
        import pty
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._status: Optional[int] = None
        self._pid, self._fd = pty.fork()
        if self._pid == 0:                                      # the child: becomes the shell
            try:
                os.chdir(cwd or os.getcwd())
                os.execvpe(argv[0], argv, env)
            finally:
                os._exit(127)
        self.resize(cols, rows)

    def wait_readable(self, timeout: float) -> bool:
        ready, _, _ = select.select([self._fd], [], [], timeout)
        return bool(ready)

    def read(self, n: int) -> str:
        try:
            data = os.read(self._fd, n)
        except OSError:
            raise EOFError                                      # the shell closed its side
        if not data:
            raise EOFError
        return self._decoder.decode(data)

    def write(self, data: str) -> None:
        os.write(self._fd, data.encode("utf-8"))

    def resize(self, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios
        fcntl.ioctl(self._fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def alive(self) -> bool:
        if self._status is not None:
            return False
        try:
            pid, status = os.waitpid(self._pid, os.WNOHANG)
        except ChildProcessError:
            self._status = 0
            return False
        if pid == 0:
            return True
        self._status = os.waitstatus_to_exitcode(status)
        return False

    def exit_code(self) -> Optional[int]:
        return self._status

    def close(self) -> None:
        import signal
        try:
            os.kill(self._pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass


def _default_spawn(argv, cwd, env, cols, rows):
    return (_WinPty if sys.platform == "win32" else _PosixPty)(argv, cwd, env, cols, rows)


class PtySession:
    """One shell. `on_output(text)` gets what it prints, in order, in chunks; `on_exit(code)` is called once when it ends. `spawn` is the backend
    factory (tests give a fake)."""

    def __init__(self, on_output: Callable[[str], None], on_exit: Callable[[Optional[int]], None], argv: Optional[List[str]] = None,
                 cwd: Optional[str] = None, cols: int = 120, rows: int = 30, spawn=None, env: Optional[Dict[str, str]] = None):
        self._on_output, self._on_exit = on_output, on_exit
        self._argv = argv or default_shell()
        self._cwd, self._cols, self._rows = cwd, int(cols), int(rows)
        self._spawn = spawn or _default_spawn
        self._env = clean_env(os.environ if env is None else env)
        self._backend = None
        self._lock = threading.Lock()
        self._scroll = ""
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ---- lifecycle ----
    def start(self) -> None:
        with self._lock:
            if self._backend is not None:
                return
            self._backend = self._spawn(self._argv, self._cwd, self._env, self._cols, self._rows)
            self._running = True
            self._scroll = ""
        self._thread = threading.Thread(target=self._read_loop, name="pty-reader", daemon=True)
        self._thread.start()
        logging.info(f"[CONSOLE] shell started: {self._argv[0]} ({self._cols}x{self._rows})")

    @property
    def alive(self) -> bool:
        b = self._backend
        return bool(b is not None and self._running and b.alive())

    def close(self) -> None:
        self._running = False
        b, self._backend = self._backend, None
        if b is not None:
            b.close()

    # ---- input / size ----
    def write(self, data: str) -> None:
        if not data:
            return
        with self._lock:
            b = self._backend
            if b is None:
                raise RuntimeError("the shell is not running")
            b.write(data)

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(2, int(cols)), max(2, int(rows))
        self._cols, self._rows = cols, rows
        with self._lock:
            b = self._backend
            if b is not None:
                b.resize(cols, rows)

    # ---- output ----
    def replay(self) -> str:
        """What the shell has printed recently, for a page that opens later."""
        return self._scroll

    def _remember(self, data: str) -> None:
        scroll = self._scroll + data
        if len(scroll) > SCROLLBACK_CHARS:
            scroll = scroll[-SCROLLBACK_CHARS:]
            cut = scroll.find("\n")
            if 0 <= cut < 2000:
                scroll = scroll[cut + 1:]                        # start on a line, not in the middle of an escape sequence
        self._scroll = scroll

    def _read_loop(self) -> None:
        b = self._backend
        code: Optional[int] = None
        try:
            while self._running:
                try:
                    if not b.wait_readable(0.1):
                        if not b.alive():
                            break
                        continue
                    data = b.read(65536)
                except EOFError:
                    break
                except (OSError, ValueError):
                    break
                if not data:
                    if not b.alive():
                        break
                    continue
                deadline = time.time() + COALESCE_S
                while len(data) < MAX_CHUNK and time.time() < deadline:
                    try:
                        if not b.wait_readable(0):
                            break
                        more = b.read(65536)
                    except (EOFError, OSError, ValueError):
                        break
                    if not more:
                        break
                    data += more
                self._remember(data)
                self._on_output(data)
        finally:
            try:
                code = b.exit_code()
            except Exception:
                code = None
            was_running, self._running = self._running, False
            with self._lock:
                if self._backend is b:
                    self._backend = None
            if was_running:
                logging.info(f"[CONSOLE] shell ended (exit {code}).")
                self._on_exit(code)
