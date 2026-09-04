import os
import sys
import subprocess
import platform
import json
import re


def _detect_cuda_tag():
    """Best-effort NVIDIA GPU detection via nvidia-smi. Returns a
    llama-cpp-python wheel-index CUDA tag (e.g. "cu124") to try first, or
    None if no compatible GPU/driver was found. Never raises -- any
    detection failure just means no GPU tag is attempted, falling back to
    the CPU wheel like today."""
    try:
        result = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return None
        match = re.search(r"CUDA Version:\s*([\d.]+)", result.stdout)
        if not match:
            return None
        driver_cuda = tuple(int(p) for p in match.group(1).split(".")[:2])
    except Exception:
        return None

    # Known llama-cpp-python prebuilt CUDA wheel tags, newest first -- picks
    # the highest one the driver supports (CUDA runtimes are backward
    # compatible within a major version). If a chosen tag isn't actually
    # hosted, the install below just fails and falls through to the CPU
    # wheel, so this list doesn't need to be exhaustive or current.
    known_tags = [("cu125", (12, 5)), ("cu124", (12, 4)), ("cu122", (12, 2)), ("cu121", (12, 1))]
    for tag, min_version in known_tags:
        if driver_cuda >= min_version:
            return tag
    return None

def main():
    print("==================================================")
    print("JARVIS ECOSYSTEM BOOTLOADER")
    print("==================================================")
    
    # OS specifics
    is_windows = platform.system() == "Windows"
    venv_dir = ".venv"
    
    python_exe = os.path.join(venv_dir, "Scripts", "python.exe") if is_windows else os.path.join(venv_dir, "bin", "python")
    pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe") if is_windows else os.path.join(venv_dir, "bin", "pip")
    
    # 1. Ensure Virtual Environment Exists
    if not os.path.exists(python_exe):
        print(f"[BOOT] Creating Python virtual environment in '{venv_dir}'...")
        try:
            subprocess.run([sys.executable, "-m", "venv", "--clear", venv_dir], check=True)
            print("[BOOT] Virtual environment created successfully.")
        except subprocess.CalledProcessError as e:
            print(f"[BOOT] FATAL: Failed to create virtual environment: {e}")
            sys.exit(1)
            
    # 2. Check for dependencies update
    req_file = "requirements.txt"
    timestamp_file = os.path.join(venv_dir, ".req_timestamp")
    
    should_install = True
    if os.path.exists(req_file) and os.path.exists(timestamp_file):
        req_mtime = os.path.getmtime(req_file)
        with open(timestamp_file, "r") as f:
            try:
                last_install = float(f.read().strip())
                if req_mtime <= last_install:
                    should_install = False
                    print("[BOOT] Dependencies are up to date.")
            except ValueError:
                pass
            
    if should_install:
        print("[BOOT] Checking/Installing dependencies from requirements.txt (This may take a moment)...")
        log_file = os.path.abspath(os.path.join("logs", "pip_install.log"))
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        def stream_install(cmd, log_handle):
            """Runs a pip command, tees output to console + log, returns exit code."""
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log_handle.write(line)
            proc.wait()
            return proc.returncode

        def pip_install_and_verify(args, log_handle):
            """Installs then actually imports llama_cpp in a subprocess to
            confirm the build loads -- a GPU wheel can install successfully
            via pip yet still fail to import if it doesn't match the local
            driver, which would otherwise look like a silent success.
            --force-reinstall is required: without it, pip can see a
            same-version llama-cpp-python already satisfied (from a prior
            attempt at a *different* build/tag) and skip the install
            entirely, silently re-verifying whatever was already there
            instead of the build actually being requested here."""
            if stream_install([pip_exe, "install", "--force-reinstall"] + args, log_handle) != 0:
                return False
            # Mirrors nlp/clSLM.py's _register_nvidia_dll_dirs() -- a GPU wheel
            # needs pip-installed nvidia-*-cu12 packages' bundled DLLs
            # (e.g. cudart64_12.dll), which llama_cpp's own loader never looks
            # for on its own. Without this, the verify below would falsely
            # report a working GPU build as broken.
            verify_script = (
                "import os, sys\n"
                "sp = os.path.join(os.path.dirname(os.path.dirname(sys.executable)), 'Lib', 'site-packages')\n"
                "nv = os.path.join(sp, 'nvidia')\n"
                "if os.path.isdir(nv):\n"
                "    for p in os.listdir(nv):\n"
                "        b = os.path.join(nv, p, 'bin')\n"
                "        if os.path.isdir(b):\n"
                "            os.add_dll_directory(b)\n"
                "import llama_cpp\n"
            )
            verify = subprocess.run([python_exe, "-c", verify_script], capture_output=True, text=True)
            log_handle.write(verify.stdout + verify.stderr)
            return verify.returncode == 0

        def enable_gpu_in_config():
            """Flips gpu_layers from its 0 default to -1 (offload all layers)
            for both SLM engines, so a verified GPU install actually gets
            used instead of silently sitting unused. Only touches fields
            still at the untouched default -- never overwrites a value the
            user already customized."""
            core_path = os.path.join("config", "core.json")
            try:
                with open(core_path, "r", encoding="utf-8") as cf:
                    core = json.load(cf)
                settings = core.get("settings", {})
                changed = False
                for key in ("slm_settings", "reply_slm_settings"):
                    if key in settings and settings[key].get("gpu_layers", 0) == 0:
                        settings[key]["gpu_layers"] = -1
                        changed = True
                if changed:
                    with open(core_path, "w", encoding="utf-8") as cf:
                        json.dump(core, cf, indent=4)
                    print("[BOOT] GPU acceleration enabled in config/core.json (gpu_layers=-1 for both models).")
            except Exception as e:
                print(f"[BOOT] Could not update config/core.json for GPU settings: {e}")

        # llama-cpp-python has no plain PyPI wheel for most platforms -- pip's
        # default `install -r requirements.txt` falls back to compiling it from
        # source, which needs a working C++ toolchain (Visual Studio Build
        # Tools on Windows, build-essential/cmake on Linux) most users won't
        # have on a first install. Installed separately here, preferring the
        # maintainer's prebuilt-wheel index, so a fresh clone works out of the
        # box without the user ever needing to debug a compiler error.
        llama_line = None
        other_lines = []
        with open(req_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().lower().startswith("llama-cpp-python"):
                    llama_line = line.strip()
                else:
                    other_lines.append(line)

        try:
            with open(log_file, "w", encoding="utf-8") as f:
                other_req_file = os.path.join(venv_dir, "_requirements_minus_llama.txt")
                with open(other_req_file, "w", encoding="utf-8") as orf:
                    orf.writelines(other_lines)

                rc = stream_install([pip_exe, "install", "-r", other_req_file], f)
                if rc != 0:
                    raise subprocess.CalledProcessError(rc, "pip install (main requirements)")

                if llama_line:
                    installed_ok = False

                    cuda_tag = _detect_cuda_tag()
                    if cuda_tag:
                        print(f"[BOOT] NVIDIA GPU detected (driver supports CUDA {cuda_tag}+) -- trying GPU-accelerated build...")
                        f.write(f"\n--- Installing {llama_line} (GPU: {cuda_tag}) ---\n")
                        installed_ok = pip_install_and_verify(
                            ["--prefer-binary", "--extra-index-url",
                             f"https://abetlen.github.io/llama-cpp-python/whl/{cuda_tag}", llama_line],
                            f
                        )
                        if installed_ok:
                            print("[BOOT] GPU-accelerated llama-cpp-python installed and verified.")
                            enable_gpu_in_config()
                        else:
                            print("[BOOT] GPU build unavailable or failed to load; falling back to CPU build...")

                    if not installed_ok:
                        print(f"[BOOT] Installing {llama_line} (CPU build)...")
                        f.write(f"\n--- Installing {llama_line} (CPU) ---\n")
                        installed_ok = pip_install_and_verify(
                            ["--prefer-binary", "--extra-index-url",
                             "https://abetlen.github.io/llama-cpp-python/whl/cpu", llama_line],
                            f
                        )

                    if not installed_ok:
                        print("[BOOT] Prebuilt wheels unavailable; falling back to a source build...")
                        f.write("\n--- Prebuilt wheels failed, falling back to source build ---\n")
                        source_rc = stream_install([pip_exe, "install", llama_line], f)
                        if source_rc != 0:
                            raise subprocess.CalledProcessError(source_rc, "pip install (llama-cpp-python)")

            # Save timestamp
            with open(timestamp_file, "w") as f:
                f.write(str(os.path.getmtime(req_file)))
            print("[BOOT] Dependencies successfully updated.")

        except subprocess.CalledProcessError:
            print("\n" + "="*70)
            print("[BOOT] FATAL ERROR: Failed to install Python dependencies.")
            print(f"[BOOT] The full installation log has been saved to: {log_file}")
            print("\n[BOOT] This is likely because your system is missing C++ build tools required by the AI model.")

            if not is_windows:
                print("[BOOT] REQUIRED ACTION: Please run the following command in your terminal:")
                print("[BOOT]   sudo apt update && sudo apt install build-essential cmake")
            else:
                print("[BOOT] REQUIRED ACTION: Please install Visual Studio Build Tools (Desktop development with C++) and CMake.")

            print("="*70 + "\n")
            print("[BOOT] Halting boot process.")

            # Autonomously pop open the log file for the user to see the exact C++ error
            try:
                if is_windows:
                    os.startfile(log_file)
                elif sys.platform == "darwin":
                    subprocess.call(["open", log_file])
                else:
                    subprocess.call(["xdg-open", log_file])
            except Exception:
                pass

            sys.exit(1)
            
    # 3. Launch the supervisor
    print(f"[BOOT] Launching Ecosystem Supervisor ({'Windows' if is_windows else 'Linux'} Native)...")
    env = os.environ.copy()
    
    # 4. Single Instance Lock & Cleanup
    import socket
    import time
    
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    is_locked = False
    try:
        lock_socket.bind(("127.0.0.1", 64000))
    except socket.error:
        is_locked = True
        
    if is_locked:
        print("[BOOT] Existing Jarvis instance detected. Attempting graceful shutdown...")
        try:
            shutdown_script = "import paho.mqtt.publish as p; import json; p.single('jarvis/sys/manager', json.dumps({'action': 'shutdown'}), hostname='localhost')"
            subprocess.run([python_exe, "-c", shutdown_script], check=False)
            
            print("[BOOT] Waiting for existing instance to shutdown...")
            for _ in range(15):
                time.sleep(1)
                try:
                    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    lock_socket.bind(("127.0.0.1", 64000))
                    print("[BOOT] Old instance successfully shut down.")
                    is_locked = False
                    break
                except socket.error:
                    pass
        except Exception as e:
            print(f"[BOOT] Error during graceful shutdown attempt: {e}")

    # FORCE CLEANUP: Always kill any lingering processes to ensure a clean boot
    print("[BOOT] Sweeping system for any lingering ecosystem processes...")
    ecosystem_scripts = [
        "clJarvis.py", "clUI.py", "clKeybinds.py", "clUtilities.py", 
        "clUpdater.py", "clTrayIcon.py", "clWhisper.py", "clDaemon.py", 
        "clSpotify.py", "clTTS.py", "clControl.py", "clMic.py", "clTerminal.py"
    ]
    for script in ecosystem_scripts:
        if is_windows:
            subprocess.run(f'wmic process where "name=\'python.exe\' and commandline like \'%{script}%\'" call terminate', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(['pkill', '-f', f'python.*{script}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
    time.sleep(1)
            
    if is_locked:
        try:
            lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            lock_socket.bind(("127.0.0.1", 64000))
        except socket.error:
            print("[BOOT] FATAL: Port 64000 is still locked after forceful cleanup. Cannot start.")
            sys.exit(1)

    # Execute clJarvis replacing the current process (on Unix) or launching subprocess (on Windows)
    if is_windows:
        sys.exit(subprocess.run([python_exe, "clJarvis.py"], env=env).returncode)
    else:
        # Prevent the lock socket from closing during os.execve
        os.set_inheritable(lock_socket.fileno(), True)
        os.execve(python_exe, [python_exe, "clJarvis.py"], env)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass