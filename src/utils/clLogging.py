import logging
import json
import os

def get_log_level_for_mode(mode_str: str) -> int:
    m = str(mode_str).lower()
    if m == "debug":
        return logging.DEBUG
    elif m == "background":
        return logging.CRITICAL
    return logging.INFO

def setup_logging(module_name: str) -> None:
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(curr_dir, "..", "..", "config", "core.json")
    
    log_level = logging.INFO
    if os.getenv("JARVIS_ECOSYSTEM") == "1":
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                mode = cfg.get("ecosystem", {}).get("mode") or cfg.get("settings", {}).get("ecosystem_state", "STANDARD")
                log_level = get_log_level_for_mode(mode)
        except Exception:
            pass
        
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        fmt=f"\r\033[K[%(asctime)s] [{module_name.upper()}] %(message)s",
        datefmt="%H:%M:%S",
    ))

    # DIAGNOSTIC: the console format above overwrites its own line (\r\033[K),
    # so past output can't be retroactively read, and has no sub-second
    # precision -- this persistent, millisecond-precision file lets a log
    # line be correlated directly against an external process's own
    # time.time()-based timestamps (e.g. a native window-creation monitor).
    data_dir = os.path.join(curr_dir, "..", "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(data_dir, f"{module_name.lower()}_debug.log"), mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        fmt=f"[%(asctime)s.%(msecs)03d] [{module_name.upper()}] %(message)s",
        datefmt="%H:%M:%S",
    ))

    logging.basicConfig(level=log_level, handlers=[console_handler, file_handler], force=True)

    # Silence noisy third-party debug logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("comtypes").setLevel(logging.WARNING)  # logs "Release <POINTER(...)>" per COM object on __del__

def update_log_level(mode_str: str) -> None:
    level = get_log_level_for_mode(mode_str)
    logging.getLogger().setLevel(level)
