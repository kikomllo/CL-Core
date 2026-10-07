"""Controls the local Spotify desktop client -- media buttons, shuffle, repeat
and seek -- with no Spotify Web API request involved. Targets Spotify's own process rather
than the OS-wide media-key focus, so a browser tab or video player that
happens to own that focus never receives the press."""
import asyncio
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# command -> (APPCOMMAND_MEDIA_* id for WM_APPCOMMAND, MPRIS Player method)
COMMANDS = {
    "toggle": (14, "PlayPause"),
    "next": (11, "Next"),
    "prev": (12, "Previous"),
}

WM_APPCOMMAND = 0x0319
SMTO_ABORTIFHUNG = 0x0002
SPOTIFY_WINDOW_CLASS = "Chrome_WidgetWin_1"


def pick_main_window(windows: List[Tuple[int, str, str]]) -> Optional[int]:
    """windows: (hwnd, owning exe basename, window class). Spotify is
    Chromium-based and spreads many windows over several processes; only the
    main UI window (Chrome_WidgetWin_1) acts on WM_APPCOMMAND -- the hidden
    Chrome_WidgetWin_0 ones silently ignore it (verified live)."""
    for hwnd, exe, window_class in windows:
        if exe.lower() == "spotify.exe" and window_class == SPOTIFY_WINDOW_CLASS:
            return hwnd
    return None


def _enumerate_windows() -> List[Tuple[int, str, str]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    exe_by_pid = {}

    def exe_name(pid: int) -> str:
        if pid not in exe_by_pid:
            name = ""
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    name = os.path.basename(buf.value)
                kernel32.CloseHandle(handle)
            exe_by_pid[pid] = name
        return exe_by_pid[pid]

    found: List[Tuple[int, str, str]] = []

    def on_window(hwnd, _):
        window_class = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, window_class, 256)
        if window_class.value == SPOTIFY_WINDOW_CLASS:  # cheap filter before the process lookup
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((hwnd, exe_name(pid.value), window_class.value))
        return True

    user32.EnumWindows(enum_proc(on_window), 0)
    return found


def _send_app_command_windows(command: str) -> bool:
    import ctypes
    from ctypes import wintypes

    hwnd = pick_main_window(_enumerate_windows())
    if hwnd is None:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.DWORD)]
    user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    result = wintypes.DWORD()
    # lParam's high word carries the APPCOMMAND_* id; a hung window must not block us.
    return bool(user32.SendMessageTimeoutW(hwnd, WM_APPCOMMAND, hwnd, COMMANDS[command][0] << 16, SMTO_ABORTIFHUNG, 1000, ctypes.byref(result)))


def _smtc_spotify_call(operation, label: str) -> bool:
    """Runs `async operation(session) -> bool` on Spotify's own SMTC session.
    False when winsdk is missing, Spotify has no session, or it fails."""
    try:
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SMTC
    except ImportError:
        return False

    async def _run() -> bool:
        manager = await SMTC.request_async()
        for session in manager.get_sessions():
            if "spotify" in (session.source_app_user_model_id or "").lower():
                return bool(await operation(session))
        return False

    try:
        return asyncio.run(_run())
    except Exception as e:
        logging.error(f"SMTC {label} for Spotify failed: {e}")
        return False


def _send_smtc_command_windows(command: str) -> bool:
    """Fallback for when Spotify has no main window (closed to the tray while
    still playing): the same command straight to its SMTC session."""
    async def _send(session) -> bool:
        action = {
            "toggle": session.try_toggle_play_pause_async,
            "next": session.try_skip_next_async,
            "prev": session.try_skip_previous_async,
        }[command]
        return await action()

    return _smtc_spotify_call(_send, f"'{command}'")


# --- SHUFFLE / REPEAT / SEEK ---
# Repeat uses the Spotify API's repeat_state names; the numbers are SMTC's
# MediaPlaybackAutoRepeatMode and the words are playerctl's `loop` values.
REPEAT_MODES = {"off": (0, "None"), "track": (1, "Track"), "context": (2, "Playlist")}
SEEK_TOLERANCE_SECONDS = 3.0


async def _wait_until(condition, timeout: float = 1.0, step: float = 0.1) -> bool:
    """`try_*` only says the request was accepted; the session's own state
    is what proves the app actually obeyed (and stops a silent no-op from
    passing as success, so the caller can fall back to the API)."""
    waited = 0.0
    while True:
        if condition():
            return True
        if waited >= timeout:
            return False
        await asyncio.sleep(step)
        waited += step


def _smtc_position_seconds(session) -> float:
    timeline = session.get_timeline_properties()
    position = timeline.position.total_seconds()
    if int(session.get_playback_info().playback_status) == 4:  # Playing: the snapshot ages
        elapsed = (datetime.now(timezone.utc) - timeline.last_updated_time).total_seconds()
        position += max(0.0, elapsed)
    return position


def _set_shuffle_windows(enabled: bool) -> bool:
    async def _set(session) -> bool:
        if not await session.try_change_shuffle_active_async(enabled):
            return False
        return await _wait_until(lambda: session.get_playback_info().is_shuffle_active == enabled)

    return _smtc_spotify_call(_set, "shuffle")


def _set_repeat_windows(mode: str) -> bool:
    from winsdk.windows.media import MediaPlaybackAutoRepeatMode
    wanted = REPEAT_MODES[mode][0]

    async def _set(session) -> bool:
        if not await session.try_change_auto_repeat_mode_async(MediaPlaybackAutoRepeatMode(wanted)):
            return False
        return await _wait_until(lambda: int(session.get_playback_info().auto_repeat_mode) == wanted)

    return _smtc_spotify_call(_set, "repeat")


def _seek_windows(position_ms: int) -> bool:
    target = position_ms / 1000.0

    async def _seek(session) -> bool:
        # SMTC positions are TimeSpan ticks (100 ns), not milliseconds.
        if not await session.try_change_playback_position_async(int(position_ms) * 10_000):
            return False
        return await _wait_until(lambda: abs(_smtc_position_seconds(session) - target) <= SEEK_TOLERANCE_SECONDS)

    return _smtc_spotify_call(_seek, "seek")


def _playerctl_spotify(*args: str) -> bool:
    if not shutil.which("playerctl"):
        return False
    try:
        result = subprocess.run(["playerctl", "--player=spotify", *args], capture_output=True, timeout=3)
        return result.returncode == 0
    except Exception as e:
        logging.error(f"playerctl {' '.join(args)} for Spotify failed: {e}")
        return False


def _local_call(windows_fn, linux_args, label: str) -> bool:
    try:
        if sys.platform == "win32":
            return windows_fn()
        if sys.platform.startswith("linux"):
            return _playerctl_spotify(*linux_args)
    except Exception as e:
        logging.error(f"Local Spotify {label} failed: {e}")
    return False


def set_spotify_shuffle(enabled: bool) -> bool:
    return _local_call(lambda: _set_shuffle_windows(enabled), ("shuffle", "On" if enabled else "Off"), "shuffle")


def set_spotify_repeat(mode: str) -> bool:
    if mode not in REPEAT_MODES:
        return False
    return _local_call(lambda: _set_repeat_windows(mode), ("loop", REPEAT_MODES[mode][1]), "repeat")


def seek_spotify(position_ms: int) -> bool:
    position_ms = max(0, int(position_ms))
    return _local_call(lambda: _seek_windows(position_ms), ("position", f"{position_ms / 1000.0:.3f}"), "seek")


def _send_mpris_command_linux(command: str) -> bool:
    """Spotify's own MPRIS name, same D-Bus target clSpotify's wake-up uses."""
    try:
        result = subprocess.run(
            ["dbus-send", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify",
             "/org/mpris/MediaPlayer2", f"org.mpris.MediaPlayer2.Player.{COMMANDS[command][1]}"],
            capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except Exception as e:
        logging.error(f"MPRIS '{command}' for Spotify failed: {e}")
        return False


def send_spotify_command(command: str) -> bool:
    """command: 'toggle' | 'next' | 'prev'. True if it was delivered to a local
    Spotify client; False means there's no local client to command (not
    running, or unsupported OS) and the caller should fall back."""
    if command not in COMMANDS:
        return False
    try:
        if sys.platform == "win32":
            return _send_app_command_windows(command) or _send_smtc_command_windows(command)
        if sys.platform.startswith("linux"):
            return _send_mpris_command_linux(command)
    except Exception as e:
        logging.error(f"Local Spotify '{command}' failed: {e}")
    return False
