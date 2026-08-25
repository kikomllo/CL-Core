import paho.mqtt.client as mqtt
import json
import subprocess
import threading
import os
import time
import sys

MQTT_HOST = "localhost"
MQTT_PORT = 1883
IS_UPDATING = False

def load_settings():
    config_path = os.path.join("config", "core.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return cfg.get("settings", {})
    except Exception:
        return {}

def publish_status(client, status, details=None):
    payload = {"status": status}
    if details:
        payload["details"] = details
    client.publish("jarvis/sys/updates/status", json.dumps(payload), qos=1)

def publish_log(client, text):
    client.publish("jarvis/sys/updates/status", json.dumps({"action": "log", "text": text}), qos=1)

def run_winget_check():
    try:
        print("[UPDATER] [DEBUG] Executing Winget upgrade check...")
        # Use winget upgrade to list packages
        result = subprocess.run(["winget", "upgrade", "--accept-source-agreements"], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        print(f"[UPDATER] [DEBUG] Winget raw stdout length: {len(result.stdout)}")
        lines = result.stdout.split('\n')
        updates = []
        parsing = False
        for line in lines:
            if "Name" in line and "Id" in line and "Version" in line:
                parsing = True
                continue
            if parsing:
                if line.startswith("-"):
                    continue
                if line.strip() == "":
                    break
                
                import re
                parts = re.split(r'\s{2,}', line.strip())
                if len(parts) >= 3:
                    name = parts[0].strip()
                    app_id = parts[1].strip()
                    if name and app_id:
                        updates.append({"type": "app", "name": name, "id": f"APP:{app_id}"})
        return updates
    except Exception as e:
        print(f"[UPDATER] Winget check failed: {e}")
        return []

def run_pswindowsupdate_check():
    query = "IsInstalled=0 and Type='Software' and IsHidden=0"
    print(f"[UPDATER] [DEBUG] Executing Windows Update search query: \"{query}\"")
    script = f"""
    $UpdateSession = New-Object -ComObject Microsoft.Update.Session
    $UpdateSearcher = $UpdateSession.CreateUpdateSearcher()
    $SearchResult = $UpdateSearcher.Search("{query}")
    $updates = @()
    foreach ($update in $SearchResult.Updates) {{
        $updates += [PSCustomObject]@{{
            Title = $update.Title
            IsDownloaded = $update.IsDownloaded
        }}
    }}
    $updates | ConvertTo-Json
    """
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        print(f"[UPDATER] [DEBUG] Windows Update raw stdout output:\n{result.stdout.strip()}")
        if result.stderr.strip():
            print(f"[UPDATER] [DEBUG] Windows Update raw stderr output:\n{result.stderr.strip()}")
            
        if result.stdout.strip():
            updates_json = json.loads(result.stdout)
            if isinstance(updates_json, dict):
                updates_json = [updates_json]
            updates = []
            for u in updates_json:
                updates.append({"type": "driver", "name": u.get("Title", "Unknown Update"), "id": f"DRV:{u.get('Title')}"})
            return updates
        return []
    except Exception as e:
        print(f"[UPDATER] Windows Update check failed: {e}")
        return []

def run_apt_check():
    try:
        print("[UPDATER] [DEBUG] Executing Apt upgrade check...")
        result = subprocess.run(["apt", "list", "--upgradable"], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        updates = []
        for line in lines:
            if "upgradable from" in line and "/" in line:
                name = line.split("/")[0].strip()
                if name:
                    updates.append({"type": "app", "name": name, "id": f"APP:{name}"})
        return updates
    except Exception as e:
        print(f"[UPDATER] Apt check failed: {e}")
        return []

def install_apt_update(client, app_id):
    print(f"[UPDATER] Installing apt update: {app_id}")
    publish_log(client, f"--- Starting Apt Update: {app_id} ---")
    
    if app_id == "ALL":
        publish_log(client, "ERROR: Bulk updates are strictly prohibited by security policy.")
        return False
        
    cmd = ["pkexec", "apt", "install", "--only-upgrade", "-y", app_id]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in iter(proc.stdout.readline, ''):
            clean_line = line.strip()
            if clean_line:
                publish_log(client, clean_line)
        proc.wait()
        publish_log(client, f"--- Apt Update Finished (Code: {proc.returncode}) ---")
        return proc.returncode == 0
    except Exception as e:
        publish_log(client, f"Apt Update Error: {e}")
        return False

CACHE_TIMESTAMP = 0
CACHED_UPDATES = []

def check_updates_task(client, force_check=False):
    global IS_UPDATING, CACHE_TIMESTAMP, CACHED_UPDATES
    if IS_UPDATING:
        publish_status(client, "updating")
        return
        
    if not force_check and (time.time() - CACHE_TIMESTAMP < 3600) and CACHED_UPDATES:
        publish_status(client, "ready", CACHED_UPDATES)
        return
        
        publish_status(client, "updating")
        return
        
    publish_status(client, "checking")
    print("[UPDATER] Checking for updates...")
    if sys.platform == "win32":
        apps = run_winget_check()
        drivers = run_pswindowsupdate_check()
    else:
        apps = run_apt_check()
        drivers = []
    CACHED_UPDATES = apps + drivers
    CACHE_TIMESTAMP = time.time()
    publish_status(client, "ready", CACHED_UPDATES)
    print(f"[UPDATER] Found {len(apps)} app updates and {len(drivers)} system updates.")

def install_app_update(client, app_id):
    print(f"[UPDATER] Installing app update: {app_id}")
    publish_log(client, f"--- Starting App Update: {app_id} ---")
    
    cmd = ["winget", "upgrade", "--silent", "--accept-source-agreements", "--accept-package-agreements", "--uninstall-previous", "--force"]
    if app_id == "ALL":
        publish_log(client, "ERROR: Bulk updates are strictly prohibited by security policy.")
        return False
    else:
        cmd.extend(["--id", app_id])
        
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
        needs_fallback = False
        for line in iter(proc.stdout.readline, ''):
            clean_line = line.strip()
            if clean_line:
                publish_log(client, clean_line)
                if "install technology is different" in clean_line.lower():
                    needs_fallback = True
        proc.wait()
        
        if needs_fallback and app_id != "ALL":
            publish_log(client, f"--- Falling back to manual reinstall for {app_id} ---")
            uninstall_cmd = ["winget", "uninstall", "--id", app_id, "--silent", "--accept-source-agreements"]
            install_cmd = ["winget", "install", "--id", app_id, "--silent", "--accept-source-agreements", "--accept-package-agreements"]
            
            publish_log(client, "Uninstalling previous version...")
            u_proc = subprocess.Popen(uninstall_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            for line in iter(u_proc.stdout.readline, ''):
                if line.strip(): publish_log(client, line.strip())
            u_proc.wait()
            
            if u_proc.returncode != 0:
                publish_log(client, f"Uninstall failed with exit code: {u_proc.returncode}. The application might be running or requires manual removal.")
                publish_log(client, f"--- App Reinstall Failed ---")
                return False
            
            publish_log(client, "Installing new version...")
            i_proc = subprocess.Popen(install_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            for line in iter(i_proc.stdout.readline, ''):
                if line.strip(): publish_log(client, line.strip())
            i_proc.wait()
            
            publish_log(client, f"--- App Reinstall Finished (Code: {i_proc.returncode}) ---")
            return i_proc.returncode == 0
        else:
            publish_log(client, f"--- App Update Finished (Code: {proc.returncode}) ---")
            return proc.returncode == 0
    except Exception as e:
        publish_log(client, f"App Update Error: {e}")
        return False

def install_driver_update(client, title):
    print(f"[UPDATER] Installing system update: {title}")
    publish_log(client, f"--- Starting System Update: {title} ---")
    if title == "ALL":
        script = """
        $UpdateSession = New-Object -ComObject Microsoft.Update.Session
        $UpdateSearcher = $UpdateSession.CreateUpdateSearcher()
        $SearchResult = $UpdateSearcher.Search("IsInstalled=0 and Type='Software' and IsHidden=0")
        if ($SearchResult.Updates.Count -gt 0) {
            Write-Output "Found $($SearchResult.Updates.Count) updates to install."
            $Downloader = $UpdateSession.CreateUpdateDownloader()
            $Downloader.Updates = $SearchResult.Updates
            Write-Output "Downloading updates..."
            $Downloader.Download()
            $Installer = $UpdateSession.CreateUpdateInstaller()
            $Installer.Updates = $SearchResult.Updates
            Write-Output "Installing updates..."
            $Installer.Install()
            Write-Output "Installation complete."
        } else {
            Write-Output "No system updates found."
        }
        """
    else:
        script = f"""
        $title = '{title}'
        $UpdateSession = New-Object -ComObject Microsoft.Update.Session
        $UpdateSearcher = $UpdateSession.CreateUpdateSearcher()
        $SearchResult = $UpdateSearcher.Search("IsInstalled=0 and Type='Software' and IsHidden=0")
        $UpdatesToDownload = New-Object -ComObject Microsoft.Update.UpdateColl
        foreach ($u in $SearchResult.Updates) {{
            if ($u.Title -eq $title) {{
                $UpdatesToDownload.Add($u)
            }}
        }}
        if ($UpdatesToDownload.Count -gt 0) {{
            Write-Output "Downloading '$title'..."
            $Downloader = $UpdateSession.CreateUpdateDownloader()
            $Downloader.Updates = $UpdatesToDownload
            $Downloader.Download()
            
            Write-Output "Installing '$title'..."
            $Installer = $UpdateSession.CreateUpdateInstaller()
            $Installer.Updates = $UpdatesToDownload
            $Installer.Install()
            Write-Output "Installation complete."
        }} else {{
            Write-Output "Update not found."
        }}
        """
        
    try:
        proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
        for line in iter(proc.stdout.readline, ''):
            clean_line = line.strip()
            if clean_line:
                publish_log(client, clean_line)
        proc.wait()
        publish_log(client, f"--- System Update Finished (Code: {proc.returncode}) ---")
        return proc.returncode == 0
    except Exception as e:
        publish_log(client, f"System Update Error: {e}")
        return False

def update_task(client, target, target_id=None):
    global IS_UPDATING
    if IS_UPDATING:
        return
        
    if target != "individual" or not target_id:
        publish_log(client, "ERROR: Security Policy Enforced. Only explicit individual updates are authorized.")
        check_updates_task(client)
        return

    IS_UPDATING = True
    publish_status(client, "updating")
    
    try:
        global CACHED_UPDATES
        if target == "individual" and target_id:
            if target_id.startswith("APP:"):
                clean_id = target_id.replace("APP:", "")
                if sys.platform == "win32":
                    success = install_app_update(client, clean_id)
                else:
                    success = install_apt_update(client, clean_id)
                if success:
                    CACHED_UPDATES = [u for u in CACHED_UPDATES if u["id"] != target_id]
            elif target_id.startswith("DRV:"):
                success = install_driver_update(client, target_id.replace("DRV:", ""))
                if success:
                    CACHED_UPDATES = [u for u in CACHED_UPDATES if u["id"] != target_id]
    finally:
        IS_UPDATING = False
        check_updates_task(client, force_check=False)

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"[UPDATER] Connected to Supervisor (Code: {reason_code})")
    client.subscribe("jarvis/sys/updates")
    client.publish("jarvis/sys/module_ready", json.dumps({"module": "updater"}), qos=1)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        action = payload.get("action")
        
        if action == "check":
            threading.Thread(target=check_updates_task, args=(client, True), daemon=True).start()
        elif action == "update_individual":
            target_id = payload.get("id")
            threading.Thread(target=update_task, args=(client, "individual", target_id), daemon=True).start()
        else:
            print(f"[UPDATER] Ignored unauthorized bulk action: {action}")
            
    except Exception as e:
        print(f"[UPDATER] Error handling message: {e}")

if __name__ == "__main__":
    time.sleep(1) # Let broker start
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        print("[UPDATER] Module initialized.")
        client.loop_forever()
    except Exception as e:
        print(f"[UPDATER] Failed to connect: {e}")
