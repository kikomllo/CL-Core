import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils import clTuya

ENTRY = {"type": "tuya", "kind": "plug", "ip": "10.0.0.60", "device_id": "bf123", "version": "3.5"}


class FakeDevice:
    """Stands in for tinytuya.OutletDevice; `world` is shared between sessions so a test can count them."""
    def __init__(self, world):
        self.w = world
        world["opened"] += 1

    def status(self):
        self.w["active"] += 1
        self.w["peak"] = max(self.w["peak"], self.w["active"])
        try:
            time.sleep(self.w.get("delay", 0))
            if self.w["fail"]:
                self.w["fail"] -= 1
                return {"Error": "Check device key or version", "Err": "914"}
            return {"dps": dict(self.w["dps"])}
        finally:
            self.w["active"] -= 1

    def set_status(self, on, switch=1):
        if self.w["fail_set"]:
            self.w["fail_set"] -= 1
            return {"Error": "Network Error: Unable to Connect", "Err": "901"}
        self.w["dps"][str(switch)] = on
        self.w["sent"].append((on, switch))
        return {"dps": {str(switch): on}}

    def close(self):
        self.w["closed"] += 1


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(clTuya, "JITTER", (0, 0))
    return {"opened": 0, "closed": 0, "dps": {"1": False}, "fail": 0, "fail_set": 0, "sent": [], "active": 0, "peak": 0}


def factory(world):
    return lambda entry, key: FakeDevice(world)


class TestSwitching:
    @pytest.mark.asyncio
    async def test_on_and_off_send_the_switch_data_point_and_close_the_session(self, world):
        await clTuya.set_power(ENTRY, "k", True, factory(world))
        await clTuya.set_power(ENTRY, "k", False, factory(world))
        assert world["sent"] == [(True, 1), (False, 1)]
        assert world["opened"] == world["closed"] == 2

    @pytest.mark.asyncio
    async def test_get_power_reads_the_switch(self, world):
        world["dps"]["1"] = True
        assert await clTuya.get_power(ENTRY, "k", factory(world)) is True
        world["dps"]["1"] = False
        assert await clTuya.get_power(ENTRY, "k", factory(world)) is False
        assert world["opened"] == world["closed"] == 2

    @pytest.mark.asyncio
    async def test_toggle_flips_in_one_session_and_says_the_new_state(self, world):
        assert await clTuya.toggle_power(ENTRY, "k", factory(world)) is True
        assert await clTuya.toggle_power(ENTRY, "k", factory(world)) is False
        assert world["sent"] == [(True, 1), (False, 1)]
        assert world["opened"] == 2 and world["closed"] == 2          # one session per toggle, not read + write

    @pytest.mark.asyncio
    async def test_a_dps_override_in_the_entry_is_used(self, world):
        world["dps"] = {"20": False}
        await clTuya.set_power({**ENTRY, "dps": 20}, "k", True, factory(world))
        assert world["sent"] == [(True, 20)]
        assert await clTuya.get_power({**ENTRY, "dps": "20"}, "k", factory(world)) is True

    @pytest.mark.asyncio
    async def test_a_plug_that_does_not_report_the_switch_is_an_error_naming_dps(self, world):
        world["dps"] = {"9": 1}
        with pytest.raises(clTuya.TuyaError, match="dps"):
            await clTuya.get_power(ENTRY, "k", factory(world))


class TestFailures:
    @pytest.mark.asyncio
    async def test_no_key_is_refused_before_any_connection(self, world):
        with pytest.raises(clTuya.TuyaError, match="TUYA_KEY_"):
            await clTuya.set_power(ENTRY, "", True, factory(world))
        assert world["opened"] == 0

    @pytest.mark.asyncio
    async def test_a_refused_reply_names_the_key_and_the_session_is_still_closed(self, world):
        world["fail"] = 5
        with pytest.raises(clTuya.TuyaError, match="local key"):
            await clTuya.get_power(ENTRY, "k", factory(world))
        assert world["opened"] == world["closed"] == 2               # tried, then tried once more, both closed

    @pytest.mark.asyncio
    async def test_one_bad_answer_is_retried_once_and_then_works(self, world):
        world["fail_set"] = 1
        await clTuya.set_power(ENTRY, "k", True, factory(world))
        assert world["sent"] == [(True, 1)] and world["opened"] == 2

    @pytest.mark.asyncio
    async def test_a_toggle_is_never_repeated(self, world):
        world["fail"] = 1
        with pytest.raises(clTuya.TuyaError):
            await clTuya.toggle_power(ENTRY, "k", factory(world))
        assert world["opened"] == 1 and world["sent"] == []

    @pytest.mark.asyncio
    async def test_a_socket_error_becomes_a_tuya_error(self, world):
        def boom(entry, key):
            raise OSError("no route to host")
        with pytest.raises(clTuya.TuyaError, match="no route"):
            await clTuya.get_power(ENTRY, "k", boom)

    @pytest.mark.asyncio
    async def test_a_silent_plug_gives_up_in_time(self, world, monkeypatch):
        monkeypatch.setattr(clTuya, "SOCKET_TIMEOUT", 0.05)
        world["delay"] = 0.5
        start = time.monotonic()
        with pytest.raises(clTuya.TuyaError, match="did not answer"):
            await clTuya.get_power(ENTRY, "k", factory(world))
        assert time.monotonic() - start < 1.0


class TestOneSessionAtATime:
    @pytest.mark.asyncio
    async def test_calls_to_one_plug_never_overlap(self, world):
        world["delay"] = 0.05
        await asyncio.gather(*(clTuya.get_power(ENTRY, "k", factory(world)) for _ in range(4)))
        assert world["peak"] == 1

    @pytest.mark.asyncio
    async def test_the_default_opener_builds_the_tinytuya_device_from_the_entry(self, monkeypatch):
        made = {}

        class Dev:
            def __init__(self, dev_id, address, key, **kw):
                made.update(dev_id=dev_id, address=address, key=key, **kw)

            def set_socketTimeout(self, t):
                made["timeout"] = t

        monkeypatch.setattr(clTuya, "tinytuya", type("T", (), {"OutletDevice": Dev}))
        clTuya._open(ENTRY, "secret")
        assert (made["dev_id"], made["address"], made["key"], made["version"]) == ("bf123", "10.0.0.60", "secret", 3.5)
        assert made["timeout"] == clTuya.SOCKET_TIMEOUT

    def test_a_missing_tinytuya_package_says_so(self, monkeypatch):
        monkeypatch.setattr(clTuya, "tinytuya", None)
        with pytest.raises(clTuya.TuyaError, match="not installed"):
            clTuya._open(ENTRY, "k")
