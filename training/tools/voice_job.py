"""voice_job.py - pause / resume / stop the local voice-training jobs that run inside WSL.

Run from the repo root (any Python):

    python training/tools/voice_job.py status             what is running, paused or finished, and GPU/RAM use
    python training/tools/voice_job.py pause  [job]       freeze everything: CPU and disk stop immediately
    python training/tools/voice_job.py resume [job]       carry on exactly where it stopped
    python training/tools/voice_job.py stop   [job]       end a job (training later restarts from its last checkpoint)
    python training/tools/voice_job.py game               before gaming: stop the GPU jobs and shut WSL down (frees all its RAM)
    python training/tools/voice_job.py release            shut WSL down if no job is alive (frees its RAM)
    python training/tools/voice_job.py start <job> <command...>   run a command as a registered job

`pause` does NOT free GPU memory: a frozen CUDA process keeps its VRAM, which would starve a game. Use `game` (or
`stop`) before playing. WSL's memory ceiling lives in %USERPROFILE%\\.wslconfig; the original (2 GB) is kept as
.wslconfig.before-voice-training.
"""
import os
import subprocess
import sys

DISTRO = "Ubuntu"
HERE = os.path.dirname(os.path.abspath(__file__))
CTL_LOCAL = os.path.join(HERE, "voice_ctl.sh")
CTL_REMOTE = "~/voice/ctl.sh"


def wsl(*args, stdin=None):
    p = subprocess.run(["wsl", "-d", DISTRO, "--", *args], input=stdin, capture_output=True)
    out = (p.stdout + p.stderr).replace(b"\x00", b"").decode("utf-8", "replace")
    return p.returncode, "\n".join(l for l in out.splitlines() if "screen size is bogus" not in l)


def install_controller() -> None:
    """Keeps the WSL copy of voice_ctl.sh in step with the repo (LF endings, executable)."""
    script = open(CTL_LOCAL, "rb").read().replace(b"\r\n", b"\n")
    wsl("bash", "-c", f"mkdir -p ~/voice/jobs && cat > {CTL_REMOTE} && chmod +x {CTL_REMOTE}", stdin=script)


def ctl(*args) -> str:
    install_controller()
    _, out = wsl("bash", CTL_REMOTE, *args)
    return out


def any_job_alive() -> bool:
    return any("running" in l or "paused" in l for l in ctl("status").splitlines() if l.strip() and not l.startswith("---"))


def main(argv) -> int:
    cmd = argv[0] if argv else "status"
    rest = argv[1:]
    if cmd in ("status", "pause", "resume", "stop", "start"):
        print(ctl(cmd, *rest))
    elif cmd == "release":
        if any_job_alive():
            print("A job is still alive - stop it first (or use `game`).")
            return 1
        subprocess.run(["wsl", "--terminate", DISTRO])
        print("WSL shut down; its memory is released.")
    elif cmd == "game":
        print(ctl("stop"))
        subprocess.run(["wsl", "--shutdown"])
        print("WSL shut down: no training jobs, no GPU memory, no WSL RAM. Have fun.")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
