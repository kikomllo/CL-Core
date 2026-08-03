import subprocess
import re

def get_active_window_rect():
    try:
        active_win = subprocess.getoutput("xdotool getactivewindow").strip()
        if not active_win.isdigit(): return None
        geom_str = subprocess.getoutput(f"xdotool getwindowgeometry {active_win}")
        
        # Example output:
        # Window 104857609
        #   Position: 2750,154 (screen: 0)
        #   Geometry: 1080x1910
        pos_match = re.search(r"Position:\s*(-?\d+),(-?\d+)", geom_str)
        geom_match = re.search(r"Geometry:\s*(\d+)x(\d+)", geom_str)
        if pos_match and geom_match:
            x, y = int(pos_match.group(1)), int(pos_match.group(2))
            w, h = int(geom_match.group(1)), int(geom_match.group(2))
            return (x, y, w, h)
    except Exception as e:
        print(e)
    return None

print(get_active_window_rect())
