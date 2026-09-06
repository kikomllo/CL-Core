import platform
import shutil
import subprocess

CURRENT_OS = platform.system().lower()

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
        if CURRENT_OS == "windows":
            if shutil.which("wmic"):
                ui_res = subprocess.run(
                    ["wmic", "process", "where", "commandline like '%src/clUI.py%'", "get", "processid"],
                    capture_output=True, text=True
                )
                ui_running = any(line.strip().isdigit() for line in ui_res.stdout.splitlines())
            else:
                # wmic is deprecated -- Get-CimInstance is its replacement.
                ps_cmd = (
                    "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
                    "| Where-Object { $_.CommandLine -like '*src/clUI.py*' }).ProcessId"
                )
                ui_res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
                ui_running = any(line.strip().isdigit() for line in ui_res.stdout.splitlines())
        else:
            ui_res = subprocess.run(["pgrep", "-f", "src/clUI.py"], capture_output=True, text=True)
            ui_running = ui_res.returncode == 0

        if ui_running:
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
