"""What the supervisor tells the dashboard about the system starting (jarvis/sys/boot_state, retained): which modules have reported in and when.
The dashboard's startup cover (web/js/boot.js) is drawn from it, so a window that opens late reads the current state on connect."""
from typing import Dict, Iterable, List


def module_key(name: str) -> str:
    """The name a module reports itself by ("web_ui") and the supervisor lists it by ("Web UI") compared the same way."""
    return str(name or "").lower().replace("_", " ").strip()


def build_boot_state(expected: Iterable[str], ready_at: Dict[str, float], online: bool, started: float) -> Dict:
    """`expected`: the display names of the modules this boot waits for, in start order. `ready_at`: module key -> seconds after the boot began.
    `started` identifies the boot (a new one is a new cover), `online` is the supervisor's own "all systems go"."""
    modules: List[Dict] = []
    for name in expected:
        at = ready_at.get(module_key(name))
        modules.append({"name": str(name).lower(), "ready": at is not None, "at": round(at, 1) if at is not None else None})
    total = len(modules)
    ready = total if online else sum(1 for m in modules if m["ready"])
    return {"ready": ready, "total": total, "online": bool(online), "started": int(started), "modules": modules}
