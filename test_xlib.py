from Xlib import display, X, Xatom

d = display.Display()
root = d.screen().root
NET_CLIENT_LIST_STACKING = d.intern_atom('_NET_CLIENT_LIST_STACKING')

reply = root.get_full_property(NET_CLIENT_LIST_STACKING, X.AnyPropertyType)
if reply:
    win_ids = reply.value
    print(f"Found {len(win_ids)} windows")
    for wid in win_ids[-5:]: # Check top 5 windows
        try:
            w = d.create_resource_object('window', wid)
            geom = w.get_geometry()
            coords = w.translate_coords(root, 0, 0)
            print(f"Window {wid}: geom ({geom.x}, {geom.y}, {geom.width}, {geom.height}), abs coords ({coords.x}, {coords.y})")
        except Exception as e:
            print(f"Error on {wid}: {e}")
