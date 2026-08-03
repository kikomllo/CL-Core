import subprocess, re
out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
match = re.search(r'#\s*(.*)', out)
if match:
    win_ids = [w.strip() for w in match.group(1).split(',')]
    for wid in win_ids:
        name = subprocess.getoutput(f"xprop -id {wid} _NET_WM_NAME")
        if "clUI" in name:
            print(f"Found clUI.py with id {wid}")
