import os, subprocess, re
from PyQt6.QtWidgets import QApplication

def check_obs():
    my_pid = os.getpid()
    
    out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
    match = re.search(r'#\s*(.*)', out)
    if not match: 
        return "No stack match"
        
    win_ids = [w.strip() for w in match.group(1).split(',')]
    
    # Try to find jarvis (we know its name is clUI.py in our test output)
    jarvis_idx = -1
    for i, wid_str in enumerate(win_ids):
        name = subprocess.getoutput(f"xprop -id {wid_str} _NET_WM_NAME")
        if "clUI.py" in name:
            jarvis_idx = i
            break
            
    if jarvis_idx == -1:
        return "Jarvis not found in stack"
        
    print(f"Jarvis is at index {jarvis_idx} of {len(win_ids)-1}")
    
    tx, ty, tw, th = (0, 0, 1920, 1080) # mockup geometry
    
    for wid_str in win_ids[jarvis_idx+1:]:
        props = subprocess.getoutput(f"xprop -id {wid_str} _NET_WM_STATE _NET_WM_PID _NET_WM_NAME _NET_WM_WINDOW_TYPE")
        if "HIDDEN" in props:
            print(f"Ignoring {wid_str}: HIDDEN")
            continue
            
        pid_match = re.search(r'_NET_WM_PID.*?=\s*(\d+)', props)
        if pid_match:
            pid = int(pid_match.group(1))
            # we can't reliably check my_pid here because this is a new test script
            # let's just print it
            print(f"Window {wid_str} has PID {pid}")
            
        geom_str = subprocess.getoutput(f"xdotool getwindowgeometry {wid_str}")
        pos_match = re.search(r"Position:\s*(-?\d+),(-?\d+)", geom_str)
        geom_match = re.search(r"Geometry:\s*(\d+)x(\d+)", geom_str)
        
        name_match = re.search(r'_NET_WM_NAME.*?=\s*"(.*?)"', props)
        name = name_match.group(1) if name_match else "Unknown"
        
        if pos_match and geom_match:
            wx, wy = int(pos_match.group(1)), int(pos_match.group(2))
            ww, wh = int(geom_match.group(1)), int(geom_match.group(2))
            print(f"Checking window {wid_str} ({name}): pos=({wx},{wy}), geom=({ww}x{wh})")
            if ww <= 1 or wh <= 1:
                print(f"Ignoring {wid_str}: too small")
                continue
                
            if not (wx + ww <= tx or wx >= tx + tw or wy + wh <= ty or wy >= ty + th):
                print(f"!!! OBSCURED BY {wid_str} ({name}) !!!")
                return True
                
    return False

check_obs()
