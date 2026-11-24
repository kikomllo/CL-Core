"""Which supervised service(s) a spoken "restart <target>" means (used by clJarvis.py; kept here so it can be tested)."""

# "the UI" is the dashboard as a whole: the web server AND the window that shows it (or the Qt dashboard).
UI_ALIASES = {"ui", "the ui", "gui", "user interface", "interface", "dashboard", "the dashboard"}


def clean_target(raw_target):
    return str(raw_target or "").lower().replace(" module", "").replace(" service", "").replace(" container", "").strip()


def _matches(target, desc, filename):
    desc, filename = desc.lower(), filename.lower()
    return bool(target in desc or desc in target or target in filename
                or ("ui" in target and ("ui" in filename or "shell" in filename))
                or ("update" in target and desc == "updater"))


def services_to_restart(raw_target, services, native_cfg):
    """[(desc, filename)] to restart, in start order. A generic UI target restarts every enabled UI service: before, only the
    first match (the Web UI server) restarted, which the open page just reconnected to, so nothing visibly happened while the
    window and the page it shows stayed as they were. Any other target restarts its first match, as it always did."""
    target = clean_target(raw_target)
    enabled = [(d, f) for d, f in services if native_cfg.get(d, True)]
    if target in UI_ALIASES:
        return [(d, f) for d, f in enabled if "ui" in f.lower() or "shell" in f.lower()]
    return [(d, f) for d, f in enabled if _matches(target, d, f)][:1]
