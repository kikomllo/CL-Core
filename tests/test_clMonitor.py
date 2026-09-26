"""Tests for the single-device presence monitor: tracker hysteresis, pairing, scanning lifecycle,
and the topics it publishes. No Bluetooth or MQTT broker involved."""
import asyncio
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
import clMonitor as m
from utils import clBeacon


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class FakeScanner:
    fail = None
    created = []

    def __init__(self, on_beacon):
        self.on_beacon = on_beacon
        self.running = False
        self.last_any_advertisement = 0.0
        self.starts = self.stops = 0
        FakeScanner.created.append(self)

    async def start(self):
        if FakeScanner.fail:
            raise m.ScannerUnavailable(FakeScanner.fail)
        self.running = True
        self.starts += 1
        self.last_any_advertisement = CLOCK.t

    async def stop(self):
        self.running = False
        self.stops += 1


CLOCK = Clock()


@pytest.fixture(autouse=True)
def _reset():
    FakeScanner.fail = None
    FakeScanner.created = []
    CLOCK.t = 1_000_000.0


@pytest.fixture
def store(tmp_path):
    return m.PairingStore(str(tmp_path / "monitor" / "paired_device.json"))


def make_service(store, **overrides):
    settings = dict(m.DEFAULT_SETTINGS, **overrides)
    return m.MonitorService(settings=settings, store=store, scanner_factory=FakeScanner, clock=CLOCK)


def topics(service):
    return [(t, p) for t, p, _ in service.drain()]


def find(out, topic):
    return [p for t, p in out if t == topic]


def beacon_for(secret, offset=0):
    return clBeacon.service_payload(secret, CLOCK.t + offset)


def pair(service):
    """Run a full pairing; returns the secret the 'device' was given."""
    service.pair_start(CLOCK.t)
    code = find(topics(service), m.PAIRING_TOPIC)[0]["code"]
    secret = clBeacon.decode_secret(code)
    service.on_beacon(beacon_for(secret), -60, CLOCK.t)
    service.drain()
    return secret


class TestSettings:
    def test_defaults_when_there_is_no_core_json_block(self, tmp_path):
        p = tmp_path / "core.json"
        p.write_text(json.dumps({"settings": {}}))
        assert m.load_settings(str(p)) == m.DEFAULT_SETTINGS

    def test_overrides_are_applied_and_bad_values_ignored(self, tmp_path):
        p = tmp_path / "core.json"
        p.write_text(json.dumps({"settings": {"monitor_settings": {
            "enter_rssi": -65, "away_timeout_s": "soon", "enabled": False, "smoothing": 5}}}))
        s = m.load_settings(str(p))
        assert s["enter_rssi"] == -65 and s["enabled"] is False
        assert s["away_timeout_s"] == m.DEFAULT_SETTINGS["away_timeout_s"]
        assert s["smoothing"] == 1.0  # clamped

    def test_missing_or_broken_file_falls_back_to_defaults(self, tmp_path):
        assert m.load_settings(str(tmp_path / "nope.json")) == m.DEFAULT_SETTINGS
        p = tmp_path / "core.json"
        p.write_text("{not json")
        assert m.load_settings(str(p)) == m.DEFAULT_SETTINGS


class TestPresenceTracker:
    def tracker(self):
        return m.PresenceTracker(enter_rssi=-70, exit_rssi=-85, away_timeout_s=30, smoothing=1.0)

    def test_arrives_when_the_signal_is_strong_enough(self):
        t = self.tracker()
        t.observe(-72, 0)
        assert t.update(0) is None  # seen, but too weak
        t.observe(-60, 1)
        assert t.update(1) == "arrived" and t.present

    def test_does_not_flap_between_the_thresholds(self):
        t = self.tracker()
        t.observe(-60, 0)
        t.update(0)
        t.observe(-78, 5)  # below enter but above exit: still present
        assert t.update(5) is None and t.present

    def test_leaves_on_a_very_weak_signal(self):
        t = self.tracker()
        t.observe(-60, 0)
        t.update(0)
        t.observe(-90, 5)
        assert t.update(5) == "left" and not t.present

    def test_leaves_when_the_beacon_goes_quiet(self):
        t = self.tracker()
        t.observe(-60, 0)
        t.update(0)
        assert t.update(29) is None
        assert t.update(31) == "left"

    def test_after_leaving_it_needs_the_enter_threshold_again(self):
        t = self.tracker()
        t.observe(-60, 0)
        t.update(0)
        t.observe(-90, 5)
        t.update(5)
        t.observe(-78, 6)
        assert t.update(6) is None
        t.observe(-65, 7)
        assert t.update(7) == "arrived"

    def test_smoothing_damps_a_single_spike(self):
        t = m.PresenceTracker(-70, -85, 30, smoothing=0.2)
        for i in range(5):
            t.observe(-90, i)
        t.observe(-40, 5)  # one strong reading among weak ones
        assert t.update(5) is None

    def test_reset_clears_everything(self):
        t = self.tracker()
        t.observe(-60, 0)
        t.update(0)
        t.reset()
        assert not t.present and t.rssi_avg is None and t.last_seen is None


class TestPairingStore:
    def test_round_trip_and_private_permissions(self, store):
        secret = clBeacon.generate_secret()
        store.save(secret)
        assert store.load() == secret
        if os.name == "posix":
            assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600

    def test_clear_and_missing_and_corrupt(self, store):
        assert store.load() is None
        store.save(clBeacon.generate_secret())
        store.clear()
        assert store.load() is None
        store.clear()  # clearing twice is fine
        os.makedirs(os.path.dirname(store.path), exist_ok=True)
        with open(store.path, "w") as f:
            f.write("garbage")
        assert store.load() is None


class TestPairing:
    def test_pair_start_publishes_a_code_and_uri_and_speaks(self, store):
        s = make_service(store)
        s.pair_start(CLOCK.t)
        out = topics(s)
        state = find(out, m.PAIRING_TOPIC)[0]
        assert state["state"] == "waiting"
        assert clBeacon.decode_secret(state["code"]) and state["uri"].startswith("jarvisbeacon://")
        assert state["expires_at"] == CLOCK.t + m.DEFAULT_SETTINGS["pairing_timeout_s"]
        assert "Pairing mode is on" in find(out, m.SPEAK_TOPIC)[0]["text"]

    def test_pair_start_again_keeps_the_same_code(self, store):
        s = make_service(store)
        s.pair_start(CLOCK.t)
        first = find(topics(s), m.PAIRING_TOPIC)[0]["code"]
        s.pair_start(CLOCK.t + 5)
        assert find(topics(s), m.PAIRING_TOPIC)[0]["code"] == first

    def test_a_valid_beacon_completes_pairing(self, store):
        s = make_service(store)
        s.pair_start(CLOCK.t)
        secret = clBeacon.decode_secret(find(topics(s), m.PAIRING_TOPIC)[0]["code"])

        s.on_beacon(beacon_for(secret), -60, CLOCK.t)

        out = topics(s)
        assert find(out, m.PAIRING_TOPIC)[0]["state"] == "paired"
        assert "Device paired" in find(out, m.SPEAK_TOPIC)[0]["text"]
        assert find(out, m.PRESENCE_TOPIC)[0]["paired"] is True
        assert store.load() == secret and s.pairing is None

    def test_a_wrong_beacon_does_not_pair(self, store):
        s = make_service(store)
        s.pair_start(CLOCK.t)
        s.drain()
        s.on_beacon(beacon_for(clBeacon.generate_secret()), -60, CLOCK.t)
        assert s.secret is None and store.load() is None and s.pairing is not None

    def test_pairing_expires(self, store):
        s = make_service(store, pairing_timeout_s=60)
        s.pair_start(CLOCK.t)
        secret = clBeacon.decode_secret(find(topics(s), m.PAIRING_TOPIC)[0]["code"])
        CLOCK.advance(61)

        s.on_beacon(beacon_for(secret), -60, CLOCK.t)  # too late
        assert s.secret is None
        s.tick(CLOCK.t)

        out = topics(s)
        assert find(out, m.PAIRING_TOPIC)[0]["state"] == "expired"
        assert "timed out" in find(out, m.SPEAK_TOPIC)[0]["text"]

    def test_cancel(self, store):
        s = make_service(store)
        s.pair_start(CLOCK.t)
        s.drain()
        s.pair_cancel()
        out = topics(s)
        assert find(out, m.PAIRING_TOPIC)[0]["state"] == "cancelled" and s.pairing is None

    def test_cancel_with_nothing_pending_is_silent(self, store):
        s = make_service(store)
        s.pair_cancel()
        assert topics(s) == []

    def test_disabled_monitor_refuses_to_pair(self, store):
        s = make_service(store, enabled=False)
        s.pair_start(CLOCK.t)
        out = topics(s)
        assert s.pairing is None and "turned off" in find(out, m.SPEAK_TOPIC)[0]["text"]

    def test_re_pairing_replaces_the_old_device(self, store):
        s = make_service(store)
        old = pair(s)
        new = pair(s)
        assert new != old and store.load() == new
        s.on_beacon(beacon_for(old), -50, CLOCK.t)  # the old device is no longer recognised
        assert s.tracker.last_seen is None


class TestStaticTestMode:
    """Opt-in pairing for a beacon that can only send one fixed value (e.g. a phone advertiser app)."""

    def start_static(self, s):
        s.pair_start(CLOCK.t, static=True)
        state = find(topics(s), m.PAIRING_TOPIC)[0]
        return clBeacon.decode_secret(state["code"]), state

    def test_the_pairing_state_says_it_is_test_mode(self, store):
        s = make_service(store)
        _, state = self.start_static(s)
        assert state["static"] is True

    def test_a_normal_pairing_rejects_the_static_payload(self, store):
        s = make_service(store)
        s.pair_start(CLOCK.t)
        secret = clBeacon.decode_secret(find(topics(s), m.PAIRING_TOPIC)[0]["code"])
        s.on_beacon(clBeacon.static_payload(secret), -60, CLOCK.t)
        assert s.secret is None

    def test_a_static_pairing_accepts_it_and_keeps_accepting_it_forever(self, store):
        s = make_service(store)
        secret, _ = self.start_static(s)
        s.on_beacon(clBeacon.static_payload(secret), -60, CLOCK.t)
        assert s.secret == secret and s.static is True
        s.drain()

        for _ in range(20):  # ten minutes of the same, never-rotating payload
            CLOCK.advance(30)
            s.on_beacon(clBeacon.static_payload(secret), -60, CLOCK.t)
            s.tick(CLOCK.t)
            assert s.tracker.present
        assert s.presence_payload()["test_mode"] is True

    def test_a_static_device_still_accepts_real_rotating_tokens(self, store):
        s = make_service(store)
        secret, _ = self.start_static(s)
        s.on_beacon(clBeacon.static_payload(secret), -60, CLOCK.t)
        s.on_beacon(beacon_for(secret), -55, CLOCK.t)
        assert s.tracker.last_seen == CLOCK.t

    def test_a_normally_paired_device_never_accepts_the_static_payload(self, store):
        s = make_service(store)
        secret = pair(s)
        s.on_beacon(clBeacon.static_payload(secret), -60, CLOCK.t)
        assert s.tracker.last_seen is None and s.presence_payload()["test_mode"] is False

    def test_test_mode_survives_a_restart_and_unpair_clears_it(self, store):
        s = make_service(store)
        secret, _ = self.start_static(s)
        s.on_beacon(clBeacon.static_payload(secret), -60, CLOCK.t)
        again = make_service(store)
        assert again.static is True
        again.unpair(CLOCK.t)
        assert again.static is False and make_service(store).static is False

    def test_asking_for_test_mode_replaces_an_open_normal_pairing(self, store):
        s = make_service(store)
        s.pair_start(CLOCK.t)
        normal = find(topics(s), m.PAIRING_TOPIC)[0]["code"]
        s.pair_start(CLOCK.t + 1, static=True)
        state = find(topics(s), m.PAIRING_TOPIC)[0]
        assert state["static"] is True and state["code"] != normal

    def test_control_message_and_cli_flag(self, store, capsys):
        s = make_service(store)
        s.handle_control({"action": "pair_start", "static": True}, CLOCK.t)
        assert s.pairing.static is True
        s.handle_control({"action": "pair_cancel"}, CLOCK.t)
        s.handle_control({"action": "pair_start", "static": "yes"}, CLOCK.t)  # only a real True counts
        assert s.pairing.static is False
        assert m._cli(["x", "--status", "--static"]) == 1
        assert "usage" in capsys.readouterr().out


class TestPresence:
    def test_the_paired_device_arrives_and_leaves(self, store):
        s = make_service(store)
        secret = pair(s)

        s.on_beacon(beacon_for(secret), -60, CLOCK.t)
        s.tick(CLOCK.t)
        out = topics(s)
        assert find(out, m.EVENT_TOPIC)[0]["event"] == "arrived"
        presence = find(out, m.PRESENCE_TOPIC)[0]
        assert presence["present"] is True and presence["rssi"] == -60

        CLOCK.advance(m.DEFAULT_SETTINGS["away_timeout_s"] + 1)
        s.tick(CLOCK.t)
        out = topics(s)
        assert find(out, m.EVENT_TOPIC)[0]["event"] == "left"
        assert find(out, m.PRESENCE_TOPIC)[0]["present"] is False

    def test_forged_and_replayed_beacons_are_ignored(self, store):
        s = make_service(store)
        secret = pair(s)
        s.on_beacon(beacon_for(clBeacon.generate_secret()), -40, CLOCK.t)  # someone else's secret
        old = beacon_for(secret)
        CLOCK.advance(10 * clBeacon.TOKEN_STEP_S)
        s.on_beacon(old, -40, CLOCK.t)  # a captured token, replayed later
        s.tick(CLOCK.t)
        assert not s.tracker.present and find(topics(s), m.EVENT_TOPIC) == []

    def test_an_unpaired_monitor_never_reports_presence(self, store):
        s = make_service(store)
        s.on_beacon(beacon_for(clBeacon.generate_secret()), -40, CLOCK.t)
        s.tick(CLOCK.t)
        assert not s.tracker.present

    def test_presence_is_retained_and_only_republished_on_change(self, store):
        s = make_service(store)
        s.tick(CLOCK.t)
        first = [x for x in s.drain() if x[0] == m.PRESENCE_TOPIC]
        assert first and first[0][2] is True  # retained
        CLOCK.advance(2)
        s.tick(CLOCK.t)
        assert [x for x in s.drain() if x[0] == m.PRESENCE_TOPIC] == []  # nothing changed

    def test_a_big_signal_change_republishes_and_a_present_device_heartbeats(self, store):
        s = make_service(store)
        secret = pair(s)
        s.on_beacon(beacon_for(secret), -60, CLOCK.t)
        s.tick(CLOCK.t)
        s.drain()
        for _ in range(8):
            s.on_beacon(beacon_for(secret), -80, CLOCK.t)
        CLOCK.advance(1)
        s.tick(CLOCK.t)
        assert find(topics(s), m.PRESENCE_TOPIC)  # rssi moved by more than a bucket
        CLOCK.advance(31)
        s.on_beacon(beacon_for(secret), -80, CLOCK.t)
        s.tick(CLOCK.t)
        assert find(topics(s), m.PRESENCE_TOPIC)  # heartbeat

    def test_unpair_while_present_emits_left_and_forgets_the_secret(self, store):
        s = make_service(store)
        secret = pair(s)
        s.on_beacon(beacon_for(secret), -60, CLOCK.t)
        s.tick(CLOCK.t)
        s.drain()

        s.unpair(CLOCK.t)

        out = topics(s)
        assert find(out, m.EVENT_TOPIC)[0]["event"] == "left"
        assert find(out, m.PRESENCE_TOPIC)[0]["paired"] is False
        assert store.load() is None and s.secret is None
        s.on_beacon(beacon_for(secret), -60, CLOCK.t)
        assert s.tracker.last_seen is None

    def test_unpair_with_nothing_paired_says_so(self, store):
        s = make_service(store)
        s.unpair(CLOCK.t)
        assert "No device is paired" in find(topics(s), m.SPEAK_TOPIC)[0]["text"]

    def test_a_restart_remembers_the_pairing(self, store):
        secret = pair(make_service(store))
        again = make_service(store)
        assert again.secret == secret
        again.on_beacon(beacon_for(secret), -60, CLOCK.t)
        again.tick(CLOCK.t)
        assert again.tracker.present


class TestStatusSpeech:
    def say(self, s):
        s.status(CLOCK.t)
        return find(topics(s), m.SPEAK_TOPIC)[0]["text"]

    def test_each_state_has_its_own_answer(self, store):
        s = make_service(store)
        assert "No device is paired" in self.say(s)
        secret = pair(s)
        assert "can't see" in self.say(s)
        s.on_beacon(beacon_for(secret), -60, CLOCK.t)
        s.tick(CLOCK.t)
        assert "I can see" in self.say(s)
        s.available, s.reason = False, "Bluetooth is off"
        assert "Bluetooth is off" in self.say(s)


class TestScanning:
    def run(self, coro):
        return asyncio.run(coro)

    def test_an_unpaired_monitor_never_starts_a_scan(self, store):
        s = make_service(store)
        self.run(s.ensure_scanner(CLOCK.t))
        assert FakeScanner.created == [] and s.presence_payload()["scanning"] is False

    def test_pairing_starts_the_scan_and_cancelling_stops_it(self, store):
        s = make_service(store)
        s.pair_start(CLOCK.t)
        self.run(s.ensure_scanner(CLOCK.t))
        assert FakeScanner.created[0].running and s.available is True
        s.pair_cancel()
        self.run(s.ensure_scanner(CLOCK.t))
        assert not FakeScanner.created[0].running

    def test_a_paired_monitor_keeps_scanning(self, store):
        s = make_service(store)
        pair(s)
        self.run(s.ensure_scanner(CLOCK.t))
        self.run(s.ensure_scanner(CLOCK.t + 1))
        assert FakeScanner.created[0].starts == 1 and s.presence_payload()["scanning"] is True

    def test_bluetooth_unavailable_fails_pairing_clearly(self, store):
        FakeScanner.fail = "No powered Bluetooth adapters found"
        s = make_service(store)
        s.pair_start(CLOCK.t)
        s.drain()

        self.run(s.ensure_scanner(CLOCK.t))

        out = topics(s)
        assert s.available is False and s.pairing is None
        failed = find(out, m.PAIRING_TOPIC)[0]
        assert failed["state"] == "failed" and "No powered Bluetooth" in failed["reason"]
        assert "can't reach Bluetooth" in find(out, m.SPEAK_TOPIC)[0]["text"]
        assert find(out, m.PRESENCE_TOPIC)[0]["available"] is False

    def test_a_paired_monitor_retries_after_the_adapter_comes_back(self, store):
        s = make_service(store, unavailable_retry_s=30)
        pair(s)
        FakeScanner.fail = "adapter off"
        self.run(s.ensure_scanner(CLOCK.t))
        assert s.available is False
        FakeScanner.fail = None
        self.run(s.ensure_scanner(CLOCK.t + 10))  # too soon
        assert s.available is False
        self.run(s.ensure_scanner(CLOCK.t + 31))
        assert s.available is True and s.reason is None

    def test_a_scan_that_hears_nothing_at_all_is_restarted(self, store):
        s = make_service(store, scanner_stall_s=90)
        pair(s)
        self.run(s.ensure_scanner(CLOCK.t))
        scanner = FakeScanner.created[0]
        self.run(s.ensure_scanner(CLOCK.t + 91))
        assert scanner.stops == 1 and scanner.starts == 2

    def test_missing_bleak_is_reported_as_unavailable(self, store, monkeypatch):
        monkeypatch.setitem(sys.modules, "bleak", None)  # makes "from bleak import ..." raise ImportError
        scanner = m.BeaconScanner(lambda *_: None)
        with pytest.raises(m.ScannerUnavailable, match="bleak"):
            asyncio.run(scanner.start())


class TestReasonText:
    def test_takes_the_message_from_a_bleak_style_exception(self):
        assert m._reason(Exception("No powered Bluetooth adapters found.", 3)) == "No powered Bluetooth adapters found."

    def test_falls_back_for_bare_exceptions(self):
        assert m._reason(RuntimeError()) == "RuntimeError"
        assert m._reason(RuntimeError("boom")) == "boom"


class TestBeaconScannerCallback:
    class Adv:
        def __init__(self, service_data, rssi):
            self.service_data, self.rssi = service_data, rssi

    def test_only_our_service_data_is_forwarded(self):
        got = []
        sc = m.BeaconScanner(lambda payload, rssi: got.append((payload, rssi)))
        sc._callback(None, self.Adv({"0000180f-0000-1000-8000-00805f9b34fb": b"\x50"}, -50))
        sc._callback(None, self.Adv({clBeacon.BEACON_SERVICE_UUID: b"\x01abcdefgh"}, -61))
        sc._callback(None, self.Adv(None, -70))
        assert got == [(b"\x01abcdefgh", -61)]


class TestControlAndRuntime:
    def test_control_actions_are_routed(self, store):
        s = make_service(store)
        s.handle_control({"action": "pair_start"}, CLOCK.t)
        assert s.pairing is not None
        s.handle_control({"action": "pair_cancel"}, CLOCK.t)
        assert s.pairing is None
        s.handle_control({"action": "nonsense"}, CLOCK.t)  # ignored
        s.handle_control({}, CLOCK.t)

    @pytest.mark.asyncio
    async def test_run_announces_ready_and_answers_control_messages(self, store, mock_mqtt, message_stream, mocker):
        mocker.patch("asyncio.sleep", new=lambda *_a, **_k: _never())
        s = make_service(store)
        mock_mqtt.messages = message_stream([
            (m.CONTROL_TOPIC, json.dumps({"action": "pair_start"})),
        ])

        await s.run()

        published = {c.args[0]: c for c in mock_mqtt.publish.call_args_list}
        assert json.loads(published["jarvis/sys/module_ready"].args[1]) == {"module": "monitor"}
        pairing = json.loads(published[m.PAIRING_TOPIC].args[1])
        assert pairing["state"] == "waiting" and pairing["code"]
        presence_calls = [c for c in mock_mqtt.publish.call_args_list if c.args[0] == m.PRESENCE_TOPIC]
        assert presence_calls and presence_calls[0].kwargs.get("retain") is True
        mock_mqtt.subscribe.assert_any_call(m.CONTROL_TOPIC)

    def test_cli_rejects_unknown_arguments(self, capsys):
        assert m._cli(["x", "--bogus"]) == 1
        assert "usage" in capsys.readouterr().out


async def _never():
    future = asyncio.get_running_loop().create_future()
    await future


class TestEcosystemWiring:
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def _json(self, name):
        with open(os.path.join(self.ROOT, "config", name), encoding="utf-8") as f:
            return json.load(f)

    def test_the_supervisor_starts_it_and_counts_it_as_an_expected_module(self):
        with open(os.path.join(self.ROOT, "clJarvis.py"), encoding="utf-8") as f:
            assert '("Monitor", "src/clMonitor.py")' in f.read()
        assert self._json("modules.json")["native"]["Monitor"] is True

    def test_actions_target_the_control_topic(self):
        actions = self._json("actions.json")["monitor"]
        assert {a["payload"]["action"] for a in actions.values()} == {
            "pair_start", "pair_cancel", "unpair", "status"}
        assert all(a["topic"] == m.CONTROL_TOPIC for a in actions.values())

    def test_voice_intents_map_to_registered_actions(self):
        intents = self._json("intents.json")
        actions = self._json("actions.json")["monitor"]
        ids = {i["action_id"] for i in intents.values() if i["action_id"].startswith("monitor.")}
        assert ids == {"monitor.pair", "monitor.unpair", "monitor.status"}
        assert all(i.split(".")[1] in actions for i in ids)

    def test_bleak_is_a_declared_dependency(self):
        with open(os.path.join(self.ROOT, "requirements.txt"), encoding="utf-8") as f:
            assert any(line.lower().startswith("bleak") for line in f)
