"""Tests for the beacon simulator, including a full loop against the real presence monitor
(no Bluetooth: the BlueZ side is faked)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
import clMonitor as mon
from utils import clBeacon
from utils import clBeaconSim as sim

SECRET = bytes(range(20))


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeBus:
    def __init__(self):
        self.exported = {}

    def export(self, path, iface):
        assert path not in self.exported, "an advertisement was exported twice without unexporting"
        self.exported[path] = iface

    def unexport(self, path):
        self.exported.pop(path, None)


class FakeManager:
    def __init__(self, bus):
        self.bus = bus
        self.registered = []
        self.unregistered = 0
        self.fail_unregister = False

    async def call_register_advertisement(self, path, options):
        self.registered.append(self.bus.exported[path].ServiceData[clBeacon.BEACON_SERVICE_UUID].value)

    async def call_unregister_advertisement(self, path):
        self.unregistered += 1
        if self.fail_unregister:
            raise RuntimeError("already gone")


@pytest.fixture
def rig():
    pytest.importorskip("dbus_fast")
    clock, bus = Clock(), FakeBus()
    manager = FakeManager(bus)
    return clock, bus, manager, sim.Advertiser(bus, manager, SECRET, interval=20, clock=clock, log=lambda s: None)


class TestAdvertisement:
    def test_it_carries_only_our_service_data_as_a_peripheral_advertisement(self):
        pytest.importorskip("dbus_fast")
        adv = sim._advertisement_class()(clBeacon.BEACON_SERVICE_UUID, b"\x01" + bytes(8))
        assert adv.Type == "peripheral"
        assert list(adv.ServiceData) == [clBeacon.BEACON_SERVICE_UUID]
        assert bytes(adv.ServiceData[clBeacon.BEACON_SERVICE_UUID].value) == b"\x01" + bytes(8)

    def test_it_fits_in_a_legacy_31_byte_advertisement(self):
        # flags (3) + service-data AD structure: length + type + 16-byte UUID + payload
        payload = clBeacon.service_payload(SECRET, 5000)
        assert 3 + (1 + 1 + 16 + len(payload)) <= 31


class TestAdvertiser:
    def test_first_publish_registers_a_valid_token(self, rig):
        clock, bus, manager, advertiser = rig
        asyncio.run(advertiser._publish())
        assert clBeacon.verify_payload(SECRET, bytes(manager.registered[0]), clock.t)

    def test_rotation_replaces_the_advertisement_with_a_fresh_token(self, rig):
        clock, bus, manager, advertiser = rig

        async def go():
            await advertiser._publish()
            clock.t += clBeacon.TOKEN_STEP_S
            await advertiser.rotate_once()

        asyncio.run(go())
        assert manager.registered[0] != manager.registered[1]
        assert clBeacon.verify_payload(SECRET, bytes(manager.registered[1]), clock.t)
        assert manager.unregistered == 1 and len(bus.exported) == 1  # exactly one live advertisement

    def test_run_rotates_every_interval_and_withdraws_on_stop(self, rig):
        clock, bus, manager, advertiser = rig
        stop = asyncio.Event()
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            clock.t += seconds
            if len(sleeps) == 3:
                stop.set()

        asyncio.run(advertiser.run(sleep=fake_sleep, stop=stop))

        assert sleeps == [20, 20, 20]
        assert advertiser.rotations == 3  # the initial publish + two rotations before the stop
        assert bus.exported == {}  # withdrawn cleanly

    def test_a_vanished_advertisement_does_not_break_rotation(self, rig):
        clock, bus, manager, advertiser = rig
        manager.fail_unregister = True

        async def go():
            await advertiser._publish()
            await advertiser.rotate_once()

        asyncio.run(go())
        assert advertiser.rotations == 2


class TestWholeLoopWithTheRealMonitor:
    """The simulator's advertisements, fed to the real MonitorService, pair and keep presence."""

    def _service(self, tmp_path, clock):
        return mon.MonitorService(
            settings=dict(mon.DEFAULT_SETTINGS), store=mon.PairingStore(str(tmp_path / "p.json")),
            scanner_factory=lambda cb: None, clock=clock)

    def test_pairing_then_continuous_presence_across_many_rotations(self, tmp_path, rig):
        clock, bus, manager, advertiser = rig
        service = self._service(tmp_path, clock)
        service.pair_start(clock.t)
        code = [p for t, p, _ in service.drain() if t == mon.PAIRING_TOPIC][0]["code"]
        advertiser.secret = clBeacon.decode_secret(code)  # "type the code into the beacon"

        async def hear():
            await advertiser.rotate_once()
            service.on_beacon(bytes(manager.registered[-1]), -60, clock.t)

        asyncio.run(hear())
        assert service.secret == advertiser.secret  # paired by the simulator's first advertisement

        for _ in range(30):  # ten minutes of 20 s rotations
            clock.t += 20
            asyncio.run(hear())
            service.tick(clock.t)
            assert service.tracker.present, f"lost presence at +{clock.t}"

    def test_a_beacon_that_stops_rotating_is_eventually_ignored(self, tmp_path, rig):
        clock, bus, manager, advertiser = rig
        service = self._service(tmp_path, clock)
        service.pair_start(clock.t)
        code = [p for t, p, _ in service.drain() if t == mon.PAIRING_TOPIC][0]["code"]
        advertiser.secret = clBeacon.decode_secret(code)
        asyncio.run(advertiser.rotate_once())
        frozen = bytes(manager.registered[-1])
        service.on_beacon(frozen, -60, clock.t)  # pairs

        clock.t += 300
        service.on_beacon(frozen, -60, clock.t)  # a captured/static payload, five minutes later
        service.tick(clock.t)
        assert not service.tracker.present


class TestCli:
    def test_a_bad_code_is_rejected(self, capsys):
        assert sim.main(["nope"]) == 2
        assert "pairing code" in capsys.readouterr().out

    def test_print_only_prints_the_uuid_and_a_fresh_payload_each_refresh(self, capsys, monkeypatch):
        calls = []

        async def fake_sleep(seconds):
            calls.append(seconds)
            if len(calls) == 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(sim.asyncio, "sleep", fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(sim._print_only(SECRET, 20))
        out = capsys.readouterr().out
        assert clBeacon.BEACON_SERVICE_UUID in out and out.count("service data:") == 2

    def test_non_linux_is_told_to_use_print_only(self, capsys, monkeypatch):
        monkeypatch.setattr(sim.sys, "platform", "win32")
        assert sim.main([clBeacon.encode_secret(SECRET)]) == 1
        assert "--print-only" in capsys.readouterr().out

    def test_an_unreachable_adapter_gives_a_helpful_message(self, capsys, monkeypatch):
        async def boom(adapter):
            raise OSError("no bus")

        monkeypatch.setattr(sim, "_connect", boom)
        monkeypatch.setattr(sim.sys, "platform", "linux")
        assert sim.main([clBeacon.encode_secret(SECRET)]) == 1
        assert "bluetoothctl power on" in capsys.readouterr().out
