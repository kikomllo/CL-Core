"""Watches a WSL training job until it finishes, dies or stalls, and keeps Windows awake meanwhile (a WSL job does not stop
the machine from sleeping, and a sleeping machine stops training). Exits when something needs a decision:

    0  the job finished (AUTO_DONE)
    2  the job is gone without finishing (crashed past the auto-resume's retries, or stopped)
    3  no new epoch for --stall-min minutes (hung, or the machine slept)

    python training/tools/voice_watch.py ultron_v2 150            # job name, total epochs
Progress is printed every --every seconds (flush), so redirecting to a file gives a running history."""
import argparse
import ctypes
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_job  # noqa: E402

ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("job")
    p.add_argument("epochs", type=int)
    p.add_argument("--every", type=int, default=180)
    p.add_argument("--stall-min", type=int, default=20)
    a = p.parse_args()

    if sys.platform == "win32":
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)   # lasts while this process lives
    last_epoch, last_change = -1, time.time()
    while True:
        text = voice_job.progress(a.job, a.epochs)
        print(time.strftime("%H:%M:%S"), text.replace("\n", " | "), flush=True)
        if "FINISHED" in text:
            return 0
        m = re.search(r"epoch (\d+)", text)
        epoch = int(m.group(1)) if m else -1
        if epoch != last_epoch:
            last_epoch, last_change = epoch, time.time()
        status = voice_job.ctl("status")
        alive = any(l.split()[0] == a.job and ("running" in l or "paused" in l) for l in status.splitlines() if l.strip())
        if not alive:
            print("job is not running and did not finish", flush=True)
            return 2
        if time.time() - last_change > a.stall_min * 60:
            print(f"no new epoch for {a.stall_min} minutes", flush=True)
            return 3
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
