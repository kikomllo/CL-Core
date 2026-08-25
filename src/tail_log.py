import sys
import time

def tail(filename):
    try:
        with open(filename, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(0, 2)
            if f.tell() > 2000:
                f.seek(f.tell() - 2000)
                f.readline() # align to next newline
            
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                sys.stdout.write(line)
                sys.stdout.flush()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"Error tailing log: {e}")
        time.sleep(5)

if __name__ == '__main__':
    if len(sys.argv) > 1:
        tail(sys.argv[1])
    else:
        print("No file specified.")
