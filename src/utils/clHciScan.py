"""
Raw HCI beacon scanner (Linux). BlueZ hides repeated advertisements, so through it the presence
monitor hears a beacon only about once every ten seconds. This talks to the Bluetooth adapter
directly with duplicate filtering off and prints one JSON line per beacon packet:

  {"rssi": -67, "payload": "01ab..."}     a packet carrying the beacon's service data
  {"adv": 1234}                           every 2 s: advertisements heard so far, from any device

Sending scan commands needs CAP_NET_RAW, so it is meant to run under a private copy of the Python
binary that alone has that capability (see --setup). It uses only the standard library.

  python src/utils/clHciScan.py            # scan until stopped
  python src/utils/clHciScan.py --check    # start and stop once; exit 0 if raw scanning works
  python src/utils/clHciScan.py --setup    # print the one-time commands that grant the capability

Exit codes: 3 = no permission, 4 = the adapter refused or isn't there.
"""
import argparse
import json
import os
import select
import signal
import socket
import struct
import sys
import time
import uuid
from typing import Callable, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import clBeacon

EXIT_NO_PERMISSION = 3
EXIT_UNSUPPORTED = 4

OGF_LE = 0x08
OCF_EXT_SCAN_PARAMS = 0x0041
OCF_EXT_SCAN_ENABLE = 0x0042
EVT_LE_META = 0x3E
SUBEVT_EXT_ADV_REPORT = 0x0D
AD_SERVICE_DATA_128 = 0x21

SCAN_INTERVAL = 0x0060   # 60 ms, in 0.625 ms units
SCAN_WINDOW = 0x0050     # 50 ms: listens about 83% of the time, leaving air time for a headset
SILENCE_RESTART_S = 10   # no reports at all for this long: re-issue the enable (something turned scanning off)
STATUS_NAMES = {0x01: "unknown command", 0x0C: "command disallowed", 0x12: "invalid parameters"}


class HciError(Exception):
    def __init__(self, message: str, code: int = EXIT_UNSUPPORTED):
        super().__init__(message)
        self.code = code


def wire_uuid(text: str = clBeacon.BEACON_SERVICE_UUID) -> bytes:
    """A 128-bit UUID as Bluetooth sends it: all sixteen bytes reversed."""
    return uuid.UUID(text).bytes[::-1]


def service_data_in(ad: bytes, wire: bytes) -> Optional[bytes]:
    """The payload after our UUID in a 128-bit service-data AD structure, if `ad` has one."""
    i = 0
    while i < len(ad):
        length = ad[i]
        if length == 0 or i + 1 + length > len(ad):
            break
        if ad[i + 1] == AD_SERVICE_DATA_128 and ad[i + 2:i + 18] == wire:
            return bytes(ad[i + 18:i + 1 + length])
        i += 1 + length
    return None


def parse_extended_reports(packet: bytes) -> List[Tuple[int, bytes]]:
    """(rssi, advertising data) for each report in an LE Extended Advertising Report event."""
    if len(packet) < 5 or packet[0] != 4 or packet[1] != EVT_LE_META or packet[3] != SUBEVT_EXT_ADV_REPORT:
        return []
    reports, pos = [], 5
    for _ in range(packet[4]):
        if pos + 24 > len(packet):
            break
        rssi = struct.unpack("b", packet[pos + 13:pos + 14])[0]
        length = packet[pos + 23]
        data = packet[pos + 24:pos + 24 + length]
        if len(data) < length:
            break
        reports.append((rssi, bytes(data)))
        pos += 24 + length
    return reports


class HciScanner:
    """Drives one adapter through a raw HCI socket (anything with send/recv/fileno)."""

    def __init__(self, sock, emit: Callable[[dict], None], clock: Callable[[], float] = time.time):
        self.sock, self.emit, self.clock = sock, emit, clock
        self.wire = wire_uuid()
        self.total = 0
        self._last_report = clock()
        self._last_emit_total = clock()

    def _status(self, ocf: int, timeout: float = 1.0) -> Optional[int]:
        end = time.time() + timeout
        opcode = (OGF_LE << 10) | ocf
        while time.time() < end:
            if select.select([self.sock], [], [], 0.05)[0]:
                packet = self.sock.recv(260)
                if len(packet) >= 7 and packet[0] == 4 and packet[1] == 0x0E \
                        and struct.unpack("<H", packet[4:6])[0] == opcode:
                    return packet[6]
        return None

    def _command(self, ocf: int, params: bytes) -> Optional[int]:
        try:
            self.sock.send(struct.pack("<BHB", 1, (OGF_LE << 10) | ocf, len(params)) + params)
        except PermissionError:
            raise HciError("raw Bluetooth access needs CAP_NET_RAW", EXIT_NO_PERMISSION) from None
        return self._status(ocf)

    def start(self):
        """Extended scanning (the adapter refuses the legacy commands once it's a Bluetooth 5 device)
        with duplicate filtering off."""
        self._command(OCF_EXT_SCAN_ENABLE, struct.pack("<BBHH", 0, 0, 0, 0))
        status = self._command(OCF_EXT_SCAN_PARAMS,
                               struct.pack("<BBB", 0, 0, 0x01) + struct.pack("<BHH", 0, SCAN_INTERVAL, SCAN_WINDOW))
        if status != 0:
            raise HciError(f"scan parameters refused ({STATUS_NAMES.get(status, status)})")
        for _ in range(3):
            status = self._command(OCF_EXT_SCAN_ENABLE, struct.pack("<BBHH", 1, 0, 0, 0))
            if status == 0:
                self._last_report = self.clock()
                return
            time.sleep(0.2)
        raise HciError(f"scan enable refused ({STATUS_NAMES.get(status, status)})")

    def stop(self):
        try:
            self.sock.send(struct.pack("<BHB", 1, (OGF_LE << 10) | OCF_EXT_SCAN_ENABLE, 6)
                           + struct.pack("<BBHH", 0, 0, 0, 0))
        except OSError:
            pass

    def handle(self, packet: bytes):
        for rssi, data in parse_extended_reports(packet):
            self.total += 1
            self._last_report = self.clock()
            payload = service_data_in(data, self.wire)
            if payload is not None:
                self.emit({"rssi": rssi, "payload": payload.hex()})

    def poll(self, timeout: float = 0.5):
        if select.select([self.sock], [], [], timeout)[0]:
            self.handle(self.sock.recv(260))
        now = self.clock()
        if now - self._last_emit_total >= 2:
            self._last_emit_total = now
            self.emit({"adv": self.total})
        if now - self._last_report >= SILENCE_RESTART_S:
            self._last_report = now
            try:
                self.start()  # an idle adapter usually means something else switched scanning off
            except HciError as e:
                if e.code == EXIT_NO_PERMISSION:
                    raise


def open_socket(adapter: int = 0):
    try:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
        sock.bind((adapter,))
    except PermissionError:
        raise HciError("raw Bluetooth access needs CAP_NET_RAW", EXIT_NO_PERMISSION) from None
    except (OSError, AttributeError) as e:
        raise HciError(f"can't open the Bluetooth adapter: {e}") from None
    # Deliver every event type; the LE meta events are the ones parsed.
    sock.setsockopt(0, 2, struct.pack("<I2IHxx", 1 << 4, 0xFFFFFFFF, 0xFFFFFFFF, 0))
    return sock


def _print_line(obj: dict):
    print(json.dumps(obj), flush=True)


def run(adapter: int = 0, check: bool = False) -> int:
    try:
        sock = open_socket(adapter)
        scanner = HciScanner(sock, _print_line)
        scanner.start()
    except HciError as e:
        print(e, file=sys.stderr, flush=True)
        return e.code
    if check:
        scanner.stop()
        return 0
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    try:
        while not stopping:
            scanner.poll()
    except HciError as e:
        print(e, file=sys.stderr, flush=True)
        return e.code
    except KeyboardInterrupt:
        pass
    finally:
        scanner.stop()
    return 0


def setup_commands(python: str = sys.executable, target: str = "data/monitor/hci_python") -> List[str]:
    real = os.path.realpath(python)
    return [f'mkdir -p "$(dirname {target})"',
            f'cp --remove-destination "{real}" {target}',
            f"sudo setcap cap_net_raw,cap_net_admin+eip {target}"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Raw HCI scanner for the JARVIS beacon (Linux).")
    parser.add_argument("--adapter", type=int, default=0, help="hci index (default 0)")
    parser.add_argument("--check", action="store_true", help="start and stop once; exit 0 if it works")
    parser.add_argument("--setup", action="store_true", help="print the one-time permission setup commands")
    args = parser.parse_args(argv)
    if args.setup:
        print("Run these from the repo root (a private copy of Python gets the raw-Bluetooth capability,\n"
              "so no other Python program is affected):\n")
        print("\n".join(setup_commands()))
        return 0
    if not sys.platform.startswith("linux"):
        print("raw HCI scanning is Linux only", file=sys.stderr)
        return EXIT_UNSUPPORTED
    return run(args.adapter, args.check)


if __name__ == "__main__":
    sys.exit(main())
