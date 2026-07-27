import logging
import json
import os

def setup_logging(module_name: str) -> None:
    # Handle paths robustly whether called from root or src/
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(curr_dir, "..", "..", "config", "core.json")
    
    log_level = logging.INFO
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            mode = cfg.get("ecosystem", {}).get("mode", "STANDARD").lower()
            if mode == "debug":
                log_level = logging.DEBUG
            elif mode == "background":
                log_level = logging.CRITICAL
    except Exception:
        pass
        
    logging.basicConfig(
        level=log_level,
        format=f"\r\033[K[%(asctime)s] [{module_name.upper()}] %(message)s",
        datefmt="%H:%M:%S",
        force=True
    )
