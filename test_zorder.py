import subprocess
import re

def get_obscuring_windows(target_rect):
    try:
        # Get all windows in stacking order (bottom to top)
        out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
        match = re.search(r'#\s*(.*)', out)
        if not match: return []
        
        # list of window IDs from bottom to top
        win_ids = [w.strip() for w in match.group(1).split(',')]
        
        obscuring = []
        for wid in win_ids:
            # Check if window is mapped/visible
            state = subprocess.getoutput(f"xprop -id {wid} _NET_WM_STATE")
            if "HIDDEN" in state:
                continue
                
            geom_str = subprocess.getoutput(f"xdotool getwindowgeometry {wid}")
            pos_match = re.search(r"Position:\s*(-?\d+),(-?\d+)", geom_str)
            geom_match = re.search(r"Geometry:\s*(\d+)x(\d+)", geom_str)
            if pos_match and geom_match:
                x, y = int(pos_match.group(1)), int(pos_match.group(2))
                w, h = int(geom_match.group(1)), int(geom_match.group(2))
                
                # Simple intersection check
                if not (x + w <= target_rect[0] or x >= target_rect[0] + target_rect[2] or 
                        y + h <= target_rect[1] or y >= target_rect[1] + target_rect[3]):
                    obscuring.append(wid)
        return obscuring
    except Exception as e:
        print(e)
        return []

print(get_obscuring_windows((0,0, 1920,1080)))
