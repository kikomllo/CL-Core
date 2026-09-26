"""Tests for the rotating-token beacon protocol."""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils import clBeacon as b

SECRET = bytes(range(20))


class TestSecretEncoding:
    def test_round_trip(self):
        secret = b.generate_secret()
        assert b.decode_secret(b.encode_secret(secret)) == secret

    def test_encoded_form_is_grouped_and_typeable(self):
        code = b.encode_secret(SECRET)
        assert all(len(g) == 4 for g in code.split("-"))
        assert code == code.upper()

    def test_decoding_ignores_case_spaces_and_dashes(self):
        code = b.encode_secret(SECRET)
        assert b.decode_secret(code.lower().replace("-", " ")) == SECRET

    def test_wrong_length_and_garbage_are_rejected(self):
        with pytest.raises(ValueError):
            b.decode_secret("ABCD-EFGH")
        with pytest.raises(ValueError):
            b.decode_secret("!!!!")

    def test_secrets_are_random(self):
        assert b.generate_secret() != b.generate_secret()


class TestTokens:
    def test_token_is_stable_within_a_step_and_changes_after_it(self):
        t0 = 1_000_000 * b.TOKEN_STEP_S
        assert b.service_payload(SECRET, t0) == b.service_payload(SECRET, t0 + b.TOKEN_STEP_S - 1)
        assert b.service_payload(SECRET, t0) != b.service_payload(SECRET, t0 + b.TOKEN_STEP_S)

    def test_a_different_secret_gives_a_different_token(self):
        assert b.service_payload(SECRET, 5000) != b.service_payload(bytes(20), 5000)

    def test_payload_layout_is_version_then_token(self):
        payload = b.service_payload(SECRET, 5000)
        assert len(payload) == 1 + b.TOKEN_BYTES and payload[0] == b.PROTOCOL_VERSION


class TestVerification:
    NOW = 1_000_000 * b.TOKEN_STEP_S + 10

    def test_current_token_verifies(self):
        assert b.verify_payload(SECRET, b.service_payload(SECRET, self.NOW), self.NOW)

    def test_adjacent_steps_are_accepted_for_clock_skew(self):
        for skew in (-b.TOKEN_STEP_S, b.TOKEN_STEP_S):
            assert b.verify_payload(SECRET, b.service_payload(SECRET, self.NOW + skew), self.NOW)

    def test_old_and_future_tokens_are_rejected(self):
        for skew in (-3 * b.TOKEN_STEP_S, 3 * b.TOKEN_STEP_S):
            assert not b.verify_payload(SECRET, b.service_payload(SECRET, self.NOW + skew), self.NOW)

    def test_wrong_secret_is_rejected(self):
        assert not b.verify_payload(bytes(20), b.service_payload(SECRET, self.NOW), self.NOW)

    def test_malformed_payloads_are_rejected(self):
        good = b.service_payload(SECRET, self.NOW)
        assert not b.verify_payload(SECRET, good[:-1], self.NOW)
        assert not b.verify_payload(SECRET, good + b"\x00", self.NOW)
        assert not b.verify_payload(SECRET, bytes([99]) + good[1:], self.NOW)
        assert not b.verify_payload(SECRET, b"", self.NOW)


class TestAdvertisementParsing:
    def test_picks_our_service_data_regardless_of_uuid_case(self):
        data = {"0000180f-0000-1000-8000-00805f9b34fb": b"\x50", b.BEACON_SERVICE_UUID.upper(): b"\x01abc"}
        assert b.beacon_payload(data) == b"\x01abc"

    def test_other_advertisements_are_ignored(self):
        assert b.beacon_payload({"0000180f-0000-1000-8000-00805f9b34fb": b"\x50"}) is None
        assert b.beacon_payload({}) is None


class TestPairingUri:
    def test_uri_carries_the_secret_uuid_and_step(self):
        uri = b.pairing_uri(SECRET)
        assert uri.startswith("jarvisbeacon://pair?")
        assert b.encode_secret(SECRET).replace("-", "") in uri
        assert b.BEACON_SERVICE_UUID in uri and f"step={b.TOKEN_STEP_S}" in uri


class TestCli:
    def test_payload_command_prints_the_current_service_data(self, capsys):
        assert b._main(["x", "payload", b.encode_secret(SECRET)]) == 0
        out = capsys.readouterr().out
        assert b.BEACON_SERVICE_UUID in out and "service data" in out

    def test_bad_code_is_reported(self, capsys):
        assert b._main(["x", "payload", "nope"]) == 2


class TestStaticTestMode:
    def test_static_payload_never_changes_and_is_not_a_rotating_token(self):
        assert b.static_payload(SECRET) == b.static_payload(SECRET)
        assert b.static_payload(SECRET) == bytes([b.PROTOCOL_VERSION]) + b.token_for(SECRET, 0)
        assert b.static_payload(SECRET) != b.service_payload(SECRET, 5_000_000)

    def test_verify_static_accepts_only_that_exact_payload(self):
        good = b.static_payload(SECRET)
        assert b.verify_static(SECRET, good)
        assert not b.verify_static(bytes(20), good)
        assert not b.verify_static(SECRET, good[:-1])
        assert not b.verify_static(SECRET, b.service_payload(SECRET, 5_000_000))

    def test_the_normal_verifier_rejects_the_static_payload_long_after_counter_zero(self):
        assert not b.verify_payload(SECRET, b.static_payload(SECRET), 5_000_000)

    def test_cli_prints_the_static_payload(self, capsys):
        assert b._main(["x", "payload", b.encode_secret(SECRET), "--static"]) == 0
        out = capsys.readouterr().out
        assert b.static_payload(SECRET).hex() in out and "never rotates" in out
