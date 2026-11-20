"""voice_job.py - pause / resume / stop the local voice-training jobs that run inside WSL.

Run from the repo root (any Python):

    python training/tools/voice_job.py status             what is running, paused or finished, and GPU/RAM use
    python training/tools/voice_job.py progress [job]     training progress: epoch x of y, % done, time left, loss, snapshots, restarts
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


def progress(job: str, total: int = 0) -> str:
    """Reads the job's log (~/voice/<job>.log) and says how far training is: epoch, share done, time left, validation loss."""
    import re
    _, raw = wsl("bash", "-c", f"tr '\\r' '\\n' < ~/voice/{job}.log 2>/dev/null | grep -a . | tail -n 4000")
    lines = raw.splitlines()
    epochs = [(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5)) for l in lines
              if (m := re.search(r"Epoch (\d+):\s+\d+%\|[^|]*\|\s*(\d+)/(\d+) \[([\d:]+)<([\d:?]+)", l))]
    if not epochs:
        return f"{job}: no epoch lines in ~/voice/{job}.log yet (still loading, or the job name is wrong)."
    ep, step, steps, spent, left = epochs[-1]
    secs = lambda s: sum(int(x) * 60 ** i for i, x in enumerate(reversed(s.split(":")))) if s and "?" not in s else 0
    epoch_s = secs(spent) + secs(left) if step > 3 else 0
    attempts = [l for l in lines if "=== ATTEMPT" in l]
    done = any("AUTO_DONE" in l for l in lines)
    target = total                                   # else read it off the running job's command line
    _, cmd = wsl("bash", "-c", f"ps -eo args | grep -a 'voice_train_auto.sh' | grep -a '{job}' | grep -v grep | head -1")
    m = re.search(r"voice_train_auto\.sh\s+(\d+)\s+" + re.escape(job), cmd)
    if m:
        target = int(m.group(1))
    vals = [float(v) for l in lines for v in re.findall(r"val_vocoder_mel=([\d.]+)", l)]
    out = []
    frac = (ep + step / max(steps, 1)) / target if target else 0
    out.append(f"{job}: epoch {ep + 1}" + (f" of {target}  ({100 * frac:.0f}% done)" if target else " (total unknown: pass it as `progress <job> <epochs>`)"))
    if epoch_s and target:
        remaining = (target - ep - step / max(steps, 1)) * epoch_s
        out.append(f"  speed: {epoch_s:.0f} s per epoch, about {int(remaining // 3600)} h {int(remaining % 3600 // 60)} min left")
    if vals:
        best = min(vals)
        out.append(f"  validation loss (lower is better): now {vals[-1]:.3f}, best so far {best:.3f}, first {vals[0]:.3f}")
    _, snaps = wsl("bash", "-c", f"ls ~/voice/runs/{job}/lightning_logs/*/checkpoints/ 2>/dev/null | grep -c snap")
    out.append(f"  snapshots saved: {snaps.strip() or 0}  (one every 25 epochs)")
    out.append(f"  restarts after a crash: {max(0, len(attempts) - 1)}" + ("   FINISHED" if done else ""))
    return "\n".join(out)


def main(argv) -> int:
    cmd = argv[0] if argv else "status"
    rest = argv[1:]
    if cmd in ("status", "pause", "resume", "stop", "start"):
        print(ctl(cmd, *rest))
    elif cmd == "progress":
        print(progress(rest[0] if rest else "ultron_v2", int(rest[1]) if len(rest) > 1 else 0))
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
