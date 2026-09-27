"""Tests for the raw HCI beacon scanner's parsing and command sequence. No Bluetooth hardware needed."""
import os
import socket
import struct
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils import clBeacon, clHciScan as h

WIRE = h.wire_uuid()


def ad_with_beacon(payload: bytes) -> bytes:
    flags = bytes([2, 0x01, 0x06])
    return flags + bytes([1 + 16 + len(payload), 0x21]) + WIRE + payload


def report(rssi: int, data: bytes) -> bytes:
    return (struct.pack("<HB", 0x0013, 0) + bytes(6) + bytes([1, 0, 0xFF, 0x7F]) + struct.pack("b", rssi)
            + struct.pack("<H", 0) + bytes([0]) + bytes(6) + bytes([len(data)]) + data)


def packet(*reports: bytes) -> bytes:
    body = bytes([h.SUBEVT_EXT_ADV_REPORT, len(reports)]) + b"".join(reports)
    return bytes([4, h.EVT_LE_META, len(body)]) + body


class TestWireUuid:
    def test_uuid_is_fully_reversed_not_mixed_endian(self):
        assert WIRE.hex() == "221a0f5e3d7c419a7d4b2e8c103a5b6f"


class TestServiceData:
    def test_finds_the_payload_after_the_uuid(self):
        payload = bytes.fromhex("015ed556066f75530a")
        assert h.service_data_in(ad_with_beacon(payload), WIRE) == payload

    def test_ignores_other_service_data_and_junk(self):
        other = bytes([1 + 16 + 2, 0x21]) + bytes(16) + b"\x01\x02"
        assert h.service_data_in(other, WIRE) is None
        assert h.service_data_in(b"", WIRE) is None
        assert h.service_data_in(bytes([200, 0x21, 1]), WIRE) is None       # length runs past the end
        assert h.service_data_in(bytes([0, 0, 0]), WIRE) is None            # zero-length padding stops the walk


class TestParseExtendedReports:
    def test_reads_rssi_and_data(self):
        data = ad_with_beacon(b"\x01" + bytes(8))
        assert h.parse_extended_reports(packet(report(-67, data))) == [(-67, data)]

    def test_reads_several_reports_in_one_event(self):
        a, b = ad_with_beacon(b"\x01" + bytes(8)), bytes([2, 1, 6])
        assert h.parse_extended_reports(packet(report(-60, a), report(-90, b))) == [(-60, a), (-90, b)]

    def test_other_events_and_truncated_packets_are_ignored(self):
        assert h.parse_extended_reports(b"") == []
        assert h.parse_extended_reports(bytes([4, 0x0E, 4, 1, 0x0C, 0x20, 0])) == []
        assert h.parse_extended_reports(bytes([4, 0x3E, 3, 0x02, 1])) == []   # legacy report subevent
        assert h.parse_extended_reports(packet(report(-60, bytes(10)))[:-5]) == []


class Emitted(list):
    def __call__(self, obj):
        self.append(obj)


class TestHciScanner:
    @pytest.fixture
    def pair(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        yield a, b
        a.close()
        b.close()

    @staticmethod
    def complete(ocf, status=0):
        opcode = (h.OGF_LE << 10) | ocf
        return bytes([4, 0x0E, 4, 1]) + struct.pack("<H", opcode) + bytes([status])

    def test_start_sends_disable_params_enable_with_duplicate_filtering_off(self, pair):
        a, b = pair
        for ocf in (h.OCF_EXT_SCAN_ENABLE, h.OCF_EXT_SCAN_PARAMS, h.OCF_EXT_SCAN_ENABLE):
            b.send(self.complete(ocf))
        h.HciScanner(a, Emitted()).start()
        sent = [b.recv(260) for _ in range(3)]
        enable = sent[2]
        assert struct.unpack("<H", enable[1:3])[0] == (h.OGF_LE << 10) | h.OCF_EXT_SCAN_ENABLE
        assert enable[4:] == struct.pack("<BBHH", 1, 0, 0, 0)     # enable, filter_duplicates = 0

    def test_a_refused_enable_is_an_error_with_the_reason(self, pair):
        a, b = pair
        b.send(self.complete(h.OCF_EXT_SCAN_ENABLE))
        b.send(self.complete(h.OCF_EXT_SCAN_PARAMS))
        for _ in range(3):
            b.send(self.complete(h.OCF_EXT_SCAN_ENABLE, 0x0C))
        with pytest.raises(h.HciError, match="command disallowed") as err:
            h.HciScanner(a, Emitted()).start()
        assert err.value.code == h.EXIT_UNSUPPORTED

    def test_refused_parameters_are_an_error(self, pair):
        a, b = pair
        b.send(self.complete(h.OCF_EXT_SCAN_ENABLE))
        b.send(self.complete(h.OCF_EXT_SCAN_PARAMS, 0x01))
        with pytest.raises(h.HciError, match="unknown command"):
            h.HciScanner(a, Emitted()).start()

    def test_no_permission_maps_to_its_own_exit_code(self):
        class Denied:
            def send(self, data):
                raise PermissionError

        with pytest.raises(h.HciError) as err:
            h.HciScanner(Denied(), Emitted()).start()
        assert err.value.code == h.EXIT_NO_PERMISSION

    def test_beacon_packets_are_emitted_and_other_devices_only_counted(self, pair):
        a, _ = pair
        out = Emitted()
        s = h.HciScanner(a, out)
        s.handle(packet(report(-67, ad_with_beacon(bytes.fromhex("015ed556066f75530a"))),
                        report(-80, bytes([2, 1, 6]))))
        assert out == [{"rssi": -67, "payload": "015ed556066f75530a"}]
        assert s.total == 2

    def test_a_heartbeat_with_the_running_total_is_emitted(self, pair):
        a, b = pair
        out = Emitted()
        now = [1000.0]
        s = h.HciScanner(a, out, clock=lambda: now[0])
        s.handle(packet(report(-80, bytes([2, 1, 6]))))
        now[0] += 2.5
        s.poll(0)
        assert {"adv": 1} in out

    def test_silence_re_issues_the_enable_but_permission_errors_still_raise(self, pair):
        a, b = pair
        now = [1000.0]
        s = h.HciScanner(a, Emitted(), clock=lambda: now[0])
        started = []
        s.start = lambda: started.append(1)
        now[0] += h.SILENCE_RESTART_S + 1
        s.poll(0)
        assert started == [1]


class TestSetup:
    def test_setup_commands_copy_the_interpreter_and_grant_only_that_copy(self):
        cmds = h.setup_commands(python="/usr/bin/python3", target="data/monitor/hci_python")
        assert any("cp --remove-destination" in c and "data/monitor/hci_python" in c for c in cmds)
        assert cmds[-1] == "sudo setcap cap_net_raw,cap_net_admin+eip data/monitor/hci_python"

    def test_check_and_setup_flags(self, capsys):
        assert h.main(["--setup"]) == 0
        assert "setcap" in capsys.readouterr().out
