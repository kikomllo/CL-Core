import subprocess

def check_ecosystem():
    print("--- JARVIS ECOSYSTEM HEALTH CHECK ---")
    try:
        result = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"], capture_output=True, text=True, check=True)
    except Exception as e:
        print(f"Failed to run docker ps: {e}")
        return

    crashed = []
    
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        name, status = line.split('|', 1)
        if 'jarvis' in name or 'mqtt' in name:
            if status.startswith('Up'):
                print(f"[\033[92mOK\033[0m] {name} ({status})")
            else:
                print(f"[\033[91mCRASHED\033[0m] {name} ({status})")
                crashed.append(name)
                
    print("\n--- NATIVE UI STATUS ---")
    try:
        ui_res = subprocess.run(["pgrep", "-f", "src/clUI.py"], capture_output=True, text=True)
        if ui_res.returncode == 0:
            print("[\033[92mOK\033[0m] Native UI is running.")
        else:
            print("[\033[91mCRASHED\033[0m] Native UI is NOT running.")
    except Exception:
        pass

    print("\n--- CRASH REPORTS ---")
    if not crashed:
        print("All Docker modules are running perfectly.")
    else:
        for name in crashed:
            print(f"\n--- LOGS FOR {name} ---")
            try:
                logs = subprocess.run(["docker", "logs", "--tail", "20", name], capture_output=True, text=True)
                out = logs.stdout + logs.stderr
                print(out if out.strip() else "<No Logs>")
            except Exception as e:
                print(f"Could not read logs: {e}")

if __name__ == '__main__':
    check_ecosystem()
