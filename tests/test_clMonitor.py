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
def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "BASELINE_PATH", str(tmp_path / "room_baseline.json"))
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

    def decay_tracker(self):
        return m.PresenceTracker(-70, -85, away_timeout_s=30, smoothing=1.0)   # decays 0.5 dB/s from -70

    def test_silence_after_a_weak_packet_leaves_sooner_than_after_a_strong_one(self):
        weak, strong = self.decay_tracker(), self.decay_tracker()
        for t in (weak, strong):
            t.observe(-60, 0)
            t.update(0)
        weak.observe(-80, 5)
        strong.observe(-60, 5)
        assert weak.update(14) is None and weak.update(16) == "left"     # -80 - 0.5 * age crosses -85 at 10 s
        assert strong.update(16) is None and strong.update(25) is None   # -60 stays far above -85

    def test_a_strong_signal_that_goes_quiet_still_leaves_at_the_hard_limit(self):
        t = self.decay_tracker()
        t.observe(-60, 0)
        t.update(0)
        assert t.update(29) is None
        assert t.update(31) == "left" and "limit 30s" in t.reason

    def test_a_new_packet_resets_the_estimate(self):
        t = self.decay_tracker()
        t.observe(-60, 0)
        t.update(0)
        t.observe(-80, 5)
        t.update(14)
        t.observe(-78, 14)          # heard again just before it would have crossed
        assert t.update(20) is None and t.estimated_rssi(20) == pytest.approx(-81)

    def test_the_decay_rate_is_a_fixed_dB_per_second_whatever_the_timeout(self):
        t = m.PresenceTracker(-70, -85, away_timeout_s=30, smoothing=1.0)
        assert t.decay_rate() == 0.5
        t.baseline, t.away_timeout_s = -60, 10
        assert t.decay_rate() == 0.5
        assert m.PresenceTracker(-70, -85, 30, 1.0, decay_db_per_s=2).decay_rate() == 2

    def test_arrival_needs_a_stronger_signal_than_leaving(self):
        t = m.PresenceTracker(-75, -88, 30, 1.0, margins=(15, 21))
        for baseline in (None, -40, -60, -71, -85):
            t.baseline = baseline
            enter, exit_ = t.thresholds()
            assert enter - exit_ >= 6

    def test_service_wires_the_decay_setting_into_the_tracker(self, tmp_path):
        s = m.MonitorService(settings=dict(m.DEFAULT_SETTINGS, decay_db_per_s=1.5),
                             store=m.PairingStore(str(tmp_path / "p.json")))
        assert s.tracker.decay_rate() == 1.5

    def test_a_missing_estimate_before_any_packet(self):
        assert self.decay_tracker().estimated_rssi(10) is None

    def test_events_record_why_they_fired(self):
        t = self.decay_tracker()
        t.observe(-60, 0)
        assert t.update(0) == "arrived" and "-60 dBm reached -70" in t.reason
        t.observe(-80, 5)
        t.update(5)
        assert t.update(16) == "left"
        assert "no beacon for 11s: last -80 dBm faded to -86, below -85" in t.reason
        t.observe(-60, 20)
        t.update(20)
        t.observe(-90, 21)
        assert t.update(21) == "left" and "signal -90 dBm fell below -85" in t.reason

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


class TestRelativeThresholds:
    def tracker(self, baseline):
        t = m.PresenceTracker(-75, -88, 30, 1.0, margins=(15, 28))
        t.baseline = baseline
        return t

    def test_fixed_values_are_used_until_calibrated(self):
        assert self.tracker(None).thresholds() == (-75, -88)

    def test_thresholds_follow_the_in_room_level(self):
        assert self.tracker(-60).thresholds() == (-75, -88)
        assert self.tracker(-50).thresholds() == (-65, -78)

    def test_a_weak_phone_still_gets_sane_thresholds(self):
        enter, exit_ = self.tracker(-85).thresholds()
        assert enter == -90 and exit_ == -98

    def test_a_very_strong_baseline_is_capped(self):
        enter, exit_ = self.tracker(-30).thresholds()
        assert enter == -50 and exit_ == -58

    def test_arrival_and_leaving_use_the_relative_values(self):
        t = self.tracker(-50)               # enter -65, exit -78
        t.observe(-70, 0)
        assert t.update(0) is None          # would have arrived under the fixed -75
        t.observe(-60, 1)
        assert t.update(1) == "arrived"
        t.observe(-80, 2)                   # would still be present under the fixed -88
        assert t.update(2) == "left"


class TestRoomBaseline:
    def test_needs_enough_samples_then_reports_the_lower_quartile(self, tmp_path):
        b = m.RoomBaseline(str(tmp_path / "b.json"))
        for i, rssi in enumerate([-50, -52, -54, -56, -58, -60, -62]):
            assert b.add(rssi, i * 10)
        assert b.level() is None
        b.add(-64, 100)
        assert b.level() == -60.0  # the weak quartile of the eight samples

    def test_samples_are_rate_limited(self, tmp_path):
        b = m.RoomBaseline(str(tmp_path / "b.json"))
        assert b.add(-60, 100) and not b.add(-61, 102) and b.add(-62, 106)

    def test_persists_and_clears(self, tmp_path):
        path = str(tmp_path / "b.json")
        b = m.RoomBaseline(path)
        b.add(-60, 100)
        assert m.RoomBaseline(path).samples == [-60]
        b.clear()
        assert m.RoomBaseline(path).samples == [] and not os.path.exists(path)

    def test_history_is_capped_and_junk_files_are_ignored(self, tmp_path):
        path = tmp_path / "b.json"
        path.write_text("not json")
        b = m.RoomBaseline(str(path))
        assert b.samples == []
        for i in range(m.RoomBaseline.MAX_SAMPLES + 10):
            b.add(-60, i * 10)
        assert len(b.samples) == m.RoomBaseline.MAX_SAMPLES


class TestActivityCalibration:
    def paired_here(self, store, rssi=-60):
        s = make_service(store)
        secret = pair(s)
        s.on_beacon(beacon_for(secret), rssi, CLOCK.t)
        s.tick(CLOCK.t)
        s.drain()
        return s, secret

    def feed(self, s, secret, rssi, n=1):
        for _ in range(n):
            CLOCK.advance(6)
            s.on_beacon(beacon_for(secret), rssi, CLOCK.t)
            s.on_activity(CLOCK.t)

    def test_activity_while_present_calibrates_after_enough_samples(self, store):
        s, secret = self.paired_here(store)
        self.feed(s, secret, -55, n=7)
        assert s.tracker.baseline is None
        self.feed(s, secret, -55)
        assert s.tracker.baseline == -55
        assert s.tracker.thresholds() == (-70, -76)
        assert s.presence_payload()["baseline"] == -55

    def test_activity_while_away_or_unpaired_is_ignored(self, store):
        s = make_service(store)
        s.on_activity(CLOCK.t)                       # unpaired
        secret = pair(s)
        s.on_activity(CLOCK.t)                       # paired but not present
        assert s.baseline.samples == []

    def test_a_stale_reading_is_not_used(self, store):
        s, secret = self.paired_here(store)
        CLOCK.advance(20)
        s.on_activity(CLOCK.t)
        assert s.baseline.samples == []

    def test_a_new_pairing_or_unpairing_forgets_the_baseline(self, store):
        s, secret = self.paired_here(store)
        self.feed(s, secret, -55, n=8)
        assert s.tracker.baseline is not None
        s.unpair(CLOCK.t)
        assert s.tracker.baseline is None and s.baseline.samples == []
        s, secret = self.paired_here(store)
        self.feed(s, secret, -55, n=8)
        pair(s)
        assert s.tracker.baseline is None and s.baseline.samples == []

    def test_a_saved_baseline_is_used_at_startup_only_when_paired(self, store, tmp_path):
        s, secret = self.paired_here(store)
        self.feed(s, secret, -55, n=8)
        assert make_service(store).tracker.baseline == -55
        store.clear()
        assert make_service(store).tracker.baseline is None


class TestReceptionRate:
    def test_counts_only_valid_beacon_packets_over_a_sliding_window(self, store):
        s = make_service(store)
        secret = pair(s)
        for i in range(20):
            s.on_beacon(beacon_for(secret), -60, CLOCK.t + i * 0.25)
        s.on_beacon(b"junk", -60, CLOCK.t + 5)
        assert s.packets_per_s(CLOCK.t + 5) == 2.0          # 20 packets in a 10 s window
        assert s.packets_per_s(CLOCK.t + 30) == 0.0          # all aged out

    def test_the_rate_is_in_the_presence_payload(self, store):
        s = make_service(store)
        secret = pair(s)
        for i in range(10):
            s.on_beacon(beacon_for(secret), -60, CLOCK.t)
        assert s.presence_payload()["packets_per_s"] == 1.0

    def test_reception_is_logged_periodically_while_scanning(self, store, caplog):
        import logging
        s = make_service(store)
        secret = pair(s)
        s.scanner = FakeScanner(s.on_beacon)
        s.scanner.running = True
        s.scanner.advertisements = 0
        for i in range(8):
            s.on_beacon(beacon_for(secret), -60, CLOCK.t + 25)
        with caplog.at_level(logging.INFO):
            s._log_reception(CLOCK.t)       # starts the first period
            s.scanner.advertisements = 300
            s._log_reception(CLOCK.t + 30)
            s._log_reception(CLOCK.t + 40)  # too soon for another line
        lines = [r.message for r in caplog.records if "Reception" in r.message]
        assert len(lines) == 1 and "0.8 beacon packets/s" in lines[0] and "10/s from all" in lines[0]


class TestDisabled:
    @pytest.mark.asyncio
    async def test_a_disabled_monitor_never_scans_even_when_paired(self, store):
        s = make_service(store)
        pair(s)
        s.settings["enabled"] = False
        s.scanner = None
        await s.ensure_scanner(CLOCK.t)
        assert s.scanner is None and not s.scanning_needed


FAKE_HELPER = (
    "import json,sys,time\n"
    "print(json.dumps({'adv': 7}), flush=True)\n"
    "print(json.dumps({'rssi': -66, 'payload': '015ed556066f75530a'}), flush=True)\n"
    "print('not json', flush=True)\n"
    "time.sleep(30)\n")


class TestHciBeaconScanner:
    @pytest.mark.asyncio
    async def test_output_lines_become_beacon_callbacks_and_counts(self):
        import asyncio
        got = []
        sc = m.HciBeaconScanner(lambda p, r: got.append((p.hex(), r)), command=[sys.executable, "-c", FAKE_HELPER])
        await sc.start()
        try:
            for _ in range(50):
                if got:
                    break
                await asyncio.sleep(0.05)
            assert sc.running and sc.high_rate
            assert got == [("015ed556066f75530a", -66)] and sc.advertisements == 7
        finally:
            await sc.stop()
        assert not sc.running

    @pytest.mark.asyncio
    async def test_a_helper_that_exits_at_once_is_reported_with_its_message(self):
        sc = m.HciBeaconScanner(lambda p, r: None, command=[
            sys.executable, "-c", "import sys; print('raw Bluetooth access needs CAP_NET_RAW', file=sys.stderr); sys.exit(3)"])
        with pytest.raises(m.HciUnavailable, match="CAP_NET_RAW"):
            await sc.start()
        assert not sc.running

    @pytest.mark.asyncio
    async def test_not_set_up_or_unlaunchable_is_unavailable(self):
        with pytest.raises(m.HciUnavailable, match="isn't set up"):
            await m.HciBeaconScanner(lambda p, r: None, command=[]).start()
        with pytest.raises(m.HciUnavailable):
            await m.HciBeaconScanner(lambda p, r: None, command=["/nonexistent/python"]).start()

    def test_default_command_needs_the_privileged_copy(self, monkeypatch, tmp_path):
        monkeypatch.setattr(m, "HCI_PYTHON", str(tmp_path / "missing"))
        assert m.HciBeaconScanner.default_command() is None
        fake = tmp_path / "hci_python"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setattr(m, "HCI_PYTHON", str(fake))
        assert m.HciBeaconScanner.default_command() == [str(fake), m.HCI_HELPER]


class FakeBackend:
    def __init__(self, on_beacon, fail=None, fast=False):
        self.fail, self.high_rate = fail, fast
        self.running = False
        self.last_any_advertisement = 5.0
        self.advertisements = 3

    async def start(self):
        if self.fail:
            raise self.fail
        self.running = True

    async def stop(self):
        self.running = False


class TestAutoScanner:
    @pytest.mark.asyncio
    async def test_prefers_the_fast_scanner(self):
        s = m.AutoScanner(lambda p, r: None, hci_factory=lambda cb: FakeBackend(cb, fast=True),
                          bleak_factory=lambda cb: FakeBackend(cb))
        await s.start()
        assert s.running and s.high_rate and s.advertisements == 3
        await s.stop()
        assert not s.running

    @pytest.mark.asyncio
    async def test_falls_back_to_bluez_once_and_stays_there(self):
        made = []

        def hci(cb):
            made.append("hci")
            return FakeBackend(cb, fail=m.HciUnavailable("not set up"), fast=True)
        s = m.AutoScanner(lambda p, r: None, hci_factory=hci, bleak_factory=lambda cb: FakeBackend(cb))
        await s.start()
        assert s.running and not s.high_rate
        await s.stop()
        await s.start()                       # a restart doesn't retry the raw scanner
        assert s.running and not s.high_rate

    @pytest.mark.asyncio
    async def test_bluez_failure_still_surfaces_as_scanner_unavailable(self):
        s = m.AutoScanner(lambda p, r: None, hci_factory=lambda cb: FakeBackend(cb, fail=m.HciUnavailable("x")),
                          bleak_factory=lambda cb: FakeBackend(cb, fail=m.ScannerUnavailable("Bluetooth is off")))
        with pytest.raises(m.ScannerUnavailable, match="Bluetooth is off"):
            await s.start()


class TestFastTimeouts:
    def test_the_fast_scanner_shortens_the_silence_limit(self, store):
        s = make_service(store)
        s.scanner = FakeBackend(None, fast=True)
        s.scanner.running = True
        s.tick(CLOCK.t)
        assert s.tracker.away_timeout_s == 10
        s.scanner.high_rate = False
        s.tick(CLOCK.t)
        assert s.tracker.away_timeout_s == 30

    def test_a_stopped_scanner_uses_the_normal_limit(self, store):
        s = make_service(store)
        s.scanner = FakeBackend(None, fast=True)   # not running
        s.tick(CLOCK.t)
        assert s.tracker.away_timeout_s == 30
