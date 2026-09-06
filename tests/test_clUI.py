import pytest
import sys
import os
import json
import subprocess
import re
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def fake_state_file(tmp_path):
    return str(tmp_path / "ui_state.json")


class TestUiStateRestoreOnStartup:
    """A full ecosystem reboot tears down and respawns clUI.py entirely
    (clJarvis.py's stop_native explicitly saves state via jarvis/sys/ui_control
    before killing it). Without restoring is_fullscreen at startup, every
    reboot silently dropped the dashboard back to the small overlay widget
    regardless of what the user had open before. But a genuine cold ecosystem
    start must always open in overlay regardless of what was saved from the
    previous session -- clJarvis.py's start_native() sets JARVIS_REBOOT=1 to
    distinguish a respawn-within-an-active-session (reboot, crash recovery,
    single-module restart) from a true cold start.

    MqttThread.start is mocked in every test here -- JarvisUI() otherwise
    spins up a real QThread that opens a real MQTT connection, which must
    never happen from an automated test (a real ecosystem, this machine's
    own, may already be running and using that same broker)."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_restores_fullscreen_on_reboot_when_that_was_the_saved_mode(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": True, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file), \
             patch.object(clUI.JarvisUI, "set_ui_mode") as mock_set_mode:
            clUI.JarvisUI()

        mock_set_mode.assert_called_once_with("set_fullscreen")

    def test_stays_in_overlay_on_reboot_when_that_was_the_saved_mode(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file), \
             patch.object(clUI.JarvisUI, "set_ui_mode") as mock_set_mode:
            clUI.JarvisUI()

        mock_set_mode.assert_not_called()

    def test_cold_start_always_opens_in_overlay_even_if_fullscreen_was_saved(self, qapp, fake_state_file, mocker):
        """The actual feature requested: a genuine cold ecosystem start
        (JARVIS_REBOOT absent/"0") must never restore fullscreen, even if
        the previous session ended in fullscreen mode."""
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": True, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file), \
             patch.object(clUI.JarvisUI, "set_ui_mode") as mock_set_mode:
            clUI.JarvisUI()

        mock_set_mode.assert_not_called()

    def test_cold_start_with_no_reboot_env_var_at_all_also_stays_in_overlay(self, qapp, fake_state_file, mocker):
        """Running clUI.py standalone (e.g. for debugging) never sets
        JARVIS_REBOOT at all -- must behave like a cold start, not a reboot."""
        import clUI
        mocker.patch.dict(os.environ, {}, clear=False)
        os.environ.pop("JARVIS_REBOOT", None)
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": True, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file), \
             patch.object(clUI.JarvisUI, "set_ui_mode") as mock_set_mode:
            clUI.JarvisUI()

        mock_set_mode.assert_not_called()

    def test_no_saved_state_file_does_not_crash_or_enter_fullscreen(self, qapp, tmp_path, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        missing_path = str(tmp_path / "does_not_exist.json")

        with patch.object(clUI, "STATE_FILE", missing_path), \
             patch.object(clUI.JarvisUI, "set_ui_mode") as mock_set_mode:
            clUI.JarvisUI()

        mock_set_mode.assert_not_called()


class TestOverlayIdleWidgetCleanup:
    """set_state('IDLE') closes floating widgets while in overlay mode --
    fires naturally any time nothing is actively speaking/listening/
    processing, which happens briefly on almost every turn. An options
    prompt (list_-prefixed) must survive that, or it gets closed moments
    after appearing, before the user can act on it."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_options_widget_survives_idle_state_in_overlay_mode(self, qapp, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        ui = clUI.JarvisUI()
        mocker.patch.object(ui, "save_ui_state")

        options_widget = MagicMock()
        other_widget = MagicMock()
        ui.active_widgets = {"list_choose_a_track": options_widget, "widget_media_controls": other_widget}

        ui.set_state("IDLE")

        options_widget.hide.assert_not_called()
        other_widget.hide.assert_called_once()


@pytest.mark.skip(reason="Interactive GUI test")
def test_expose():
    os.environ["QT_QPA_PLATFORM"] = "xcb" # same as Jarvis
    from PyQt6.QtWidgets import QApplication, QWidget, QLabel
    from PyQt6.QtCore import QTimer

    app = QApplication.instance() or QApplication(sys.argv)
    w = QWidget()
    w.setGeometry(100, 100, 400, 400)
    l = QLabel("Test Expose", w)
    w.show()

    def check_expose():
        wh = w.windowHandle()
        exposed = wh.isExposed() if wh else False
        print(f"Is exposed? {exposed}")
        sys.stdout.flush()

    t = QTimer()
    t.timeout.connect(check_expose)
    t.start(1000)

    QTimer.singleShot(5000, app.quit)
    sys.exit(app.exec())

def test_obs():
    def check_obs():
        my_pid = os.getpid()
        
        out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
        match = re.search(r'#\s*(.*)', out)
        if not match: 
            return "No stack match"
            
        win_ids = [w.strip() for w in match.group(1).split(',')]
        
        jarvis_idx = -1
        for i, wid_str in enumerate(win_ids):
            name = subprocess.getoutput(f"xprop -id {wid_str} _NET_WM_NAME")
            if "clUI.py" in name:
                jarvis_idx = i
                break
                
        if jarvis_idx == -1:
            return "Jarvis not found in stack"
            
        print(f"Jarvis is at index {jarvis_idx} of {len(win_ids)-1}")
        
        tx, ty, tw, th = (0, 0, 1920, 1080)
        
        for wid_str in win_ids[jarvis_idx+1:]:
            props = subprocess.getoutput(f"xprop -id {wid_str} _NET_WM_STATE _NET_WM_PID _NET_WM_NAME _NET_WM_WINDOW_TYPE")
            if "HIDDEN" in props:
                print(f"Ignoring {wid_str}: HIDDEN")
                continue
                
            pid_match = re.search(r'_NET_WM_PID.*?=\s*(\d+)', props)
            if pid_match:
                pid = int(pid_match.group(1))
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

@pytest.mark.skip(reason="Interactive GUI test")
def test_vis():
    from PyQt6.QtWidgets import QApplication, QWidget, QLabel
    from PyQt6.QtCore import QTimer
    
    app = QApplication.instance() or QApplication(sys.argv)
    w = QWidget()
    w.setGeometry(100, 100, 400, 400)
    l = QLabel("Test Window", w)
    w.show()

    def check_vis():
        print(f"Is active: {w.isActiveWindow()}, visibleRegion empty: {w.visibleRegion().isEmpty()}")
        sys.stdout.flush()

    t = QTimer()
    t.timeout.connect(check_vis)
    t.start(2000)

    QTimer.singleShot(6000, app.quit)
    sys.exit(app.exec())

def test_xdotool():
    def get_active_window_rect():
        try:
            active_win = subprocess.getoutput("xdotool getactivewindow").strip()
            if not active_win.isdigit(): return None
            geom_str = subprocess.getoutput(f"xdotool getwindowgeometry {active_win}")
            
            pos_match = re.search(r"Position:\s*(-?\d+),(-?\d+)", geom_str)
            geom_match = re.search(r"Geometry:\s*(\d+)x(\d+)", geom_str)
            if pos_match and geom_match:
                x, y = int(pos_match.group(1)), int(pos_match.group(2))
                w, h = int(geom_match.group(1)), int(geom_match.group(2))
                return (x, y, w, h)
        except Exception as e:
            print(e)
        return None
    print(get_active_window_rect())

def test_xlib():
    try:
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
    except ImportError:
        pytest.skip("Xlib not installed")
    except Exception as e:
        pytest.skip(f"Xlib display error: {e}")

def test_zorder():
    def get_obscuring_windows(target_rect):
        try:
            out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
            match = re.search(r'#\s*(.*)', out)
            if not match: return []
            
            win_ids = [w.strip() for w in match.group(1).split(',')]
            
            obscuring = []
            for wid in win_ids:
                state = subprocess.getoutput(f"xprop -id {wid} _NET_WM_STATE")
                if "HIDDEN" in state:
                    continue
                    
                geom_str = subprocess.getoutput(f"xdotool getwindowgeometry {wid}")
                pos_match = re.search(r"Position:\s*(-?\d+),(-?\d+)", geom_str)
                geom_match = re.search(r"Geometry:\s*(\d+)x(\d+)", geom_str)
                if pos_match and geom_match:
                    x, y = int(pos_match.group(1)), int(pos_match.group(2))
                    w, h = int(geom_match.group(1)), int(geom_match.group(2))
                    
                    if not (x + w <= target_rect[0] or x >= target_rect[0] + target_rect[2] or 
                            y + h <= target_rect[1] or y >= target_rect[1] + target_rect[3]):
                        obscuring.append(wid)
            return obscuring
        except Exception as e:
            print(e)
            return []
    print(get_obscuring_windows((0,0, 1920,1080)))

def test_types():
    out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
    match = re.search(r'#\s*(.*)', out)
    if match:
        win_ids = [w.strip() for w in match.group(1).split(',')]
        for wid in win_ids:
            props = subprocess.getoutput(f"xprop -id {wid} _NET_WM_WINDOW_TYPE _NET_WM_NAME")
            print(f"{wid}: {props}")

def test_early():
    out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
    match = re.search(r'#\s*(.*)', out)
    if match:
        win_ids = [w.strip() for w in match.group(1).split(',')]
        for wid in win_ids:
            name = subprocess.getoutput(f"xprop -id {wid} _NET_WM_NAME")
            if "clUI" in name:
                print(f"Found clUI.py with id {wid}")
