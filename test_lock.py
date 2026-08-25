import socket, os, time, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("127.0.0.1", 64000))
os.set_inheritable(s.fileno(), True)
if len(sys.argv) == 1:
    print("Execing")
    os.execve(sys.executable, [sys.executable, "test_lock.py", "2"], os.environ)
else:
    print("Sleeping...")
    time.sleep(10)
