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
        
    logging.basicConfig(
        level=log_level,
        format=f"\r\033[K[%(asctime)s] [{module_name.upper()}] %(message)s",
        datefmt="%H:%M:%S",
        force=True
    )
    
    # Silence noisy third-party debug logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

def update_log_level(mode_str: str) -> None:
    level = get_log_level_for_mode(mode_str)
    logging.getLogger().setLevel(level)
