import sys
import ctypes

def get_set_window_long():
    user32 = ctypes.windll.user32
    if sys.maxsize > 2**32:
        SetWindowLong = user32.SetWindowLongPtrW
        SetWindowLong.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        SetWindowLong.restype = ctypes.c_void_p
        
        GetWindowLong = user32.GetWindowLongPtrW
        GetWindowLong.argtypes = [ctypes.c_void_p, ctypes.c_int]
        GetWindowLong.restype = ctypes.c_void_p
    else:
        SetWindowLong = user32.SetWindowLongW
        GetWindowLong = user32.GetWindowLongW
    return GetWindowLong, SetWindowLong

try:
    g, s = get_set_window_long()
    print("Functions exist:", g, s)
except Exception as e:
    print("Error:", e)
