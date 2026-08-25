import os
import sys
import json
import logging
import subprocess
import paho.mqtt.client as mqtt

try:
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
    from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
    from PyQt6.QtCore import Qt
except ImportError as e:
    print(f"[TRAY] Missing dependencies (PyQt6). Cannot start. Error: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [TRAY] %(message)s', datefmt='%H:%M:%S')

def create_qt_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("transparent"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    color1 = QColor(0, 0, 0)
    color2 = QColor(0, 200, 255)
    
    painter.setBrush(color1)
    
    pen = painter.pen()
    pen.setColor(color2)
    pen.setWidth(4)
    painter.setPen(pen)
    painter.drawEllipse(4, 4, 56, 56)
    
    font = QFont("Arial", 28, QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(color2)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "J")
    
    painter.end()
    return QIcon(pixmap)

class TrayApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        
        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.mqtt_client.on_connect = self.on_connect
        
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(create_qt_icon())
        
        self.menu = QMenu()
        
        view_logs_action = self.menu.addAction("View Live Logs")
        view_logs_action.triggered.connect(self.on_view_logs)
        
        shutdown_action = self.menu.addAction("Shutdown Ecosystem")
        shutdown_action.triggered.connect(self.on_shutdown_ecosystem)
        
        self.menu.addSeparator()
        
        exit_action = self.menu.addAction("Exit Tray")
        exit_action.triggered.connect(self.on_exit)
        
        self.tray.setContextMenu(self.menu)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logging.info("Connected to MQTT Broker.")
        else:
            logging.warning(f"Failed to connect to MQTT Broker. Code: {reason_code}")

    def on_view_logs(self):
        logging.info("Requested View Logs.")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.abspath(os.path.join(base_dir, "..", "logs", "latest_run.log"))
        tail_script = os.path.abspath(os.path.join(base_dir, "tail_log.py"))
        
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", sys.executable, tail_script, log_path])
        else:
            import shutil
            terminals = [
                ("qterminal", ["qterminal", "-e", f"bash -c 'tail -f {log_path}'"]),
                ("gnome-terminal", ["gnome-terminal", "--", "bash", "-c", f"tail -f {log_path}"]),
                ("konsole", ["konsole", "-e", f"tail -f {log_path}"]),
                ("xfce4-terminal", ["xfce4-terminal", "-e", f"tail -f {log_path}"]),
                ("x-terminal-emulator", ["x-terminal-emulator", "-e", f"tail -f {log_path}"])
            ]
            for cmd, args in terminals:
                if shutil.which(cmd):
                    subprocess.Popen(args)
                    break
            else:
                logging.error("No supported terminal emulator found to show logs.")

    def on_shutdown_ecosystem(self):
        logging.info("Dispatching ecosystem shutdown command...")
        try:
            self.mqtt_client.publish("jarvis/sys/manager", json.dumps({"action": "shutdown"}))
        except Exception as e:
            logging.error(f"Failed to publish shutdown command: {e}")

    def on_exit(self):
        logging.info("Exiting tray application.")
        self.tray.hide()
        self.app.quit()

    def run(self):
        try:
            self.mqtt_client.connect("localhost", 1883, 60)
            self.mqtt_client.loop_start()
        except Exception as e:
            logging.error(f"Failed to connect to MQTT broker: {e}")

        logging.info("Starting System Tray Icon (PyQt6)...")
        self.tray.show()
        self.app.exec()
        
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()

if __name__ == "__main__":
    app = TrayApp()
    app.run()
