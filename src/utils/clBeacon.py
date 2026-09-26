"""
JARVIS beacon protocol -- how a paired device proves it is nearby without a fixed address.

The beacon (a phone app, later; a test advertiser today) broadcasts BLE service data under
BEACON_SERVICE_UUID: one version byte followed by a short token. The token is
HMAC-SHA256(secret, time_step_counter) truncated, so it changes every TOKEN_STEP_S seconds and
can't be replayed later or forged without the secret. The secret is shared once at pairing (like
TOTP enrolment): JARVIS shows it, the device is given it, and the first valid token seen while
pairing is open completes the pairing. Nothing here depends on the device's MAC address, which
phones randomise.

  python src/utils/clBeacon.py payload <SECRET>           # current service-data bytes, for a manual advertiser
  python src/utils/clBeacon.py payload <SECRET> --static  # the fixed test-mode payload (pair with --static)
"""
import base64
import hashlib
import hmac
import struct
import sys
import time
from typing import Mapping, Optional

BEACON_SERVICE_UUID = "6f5b3a10-8c2e-4b7d-9a41-7c3d5e0f1a22"
PROTOCOL_VERSION = 1
TOKEN_STEP_S = 30
TOKEN_BYTES = 8
SECRET_BYTES = 20
# Tokens from the previous/next step are accepted too, so up to ~TOKEN_STEP_S of clock skew
# between the two devices doesn't break detection.
ACCEPT_WINDOW = 1


def generate_secret() -> bytes:
    import secrets
    return secrets.token_bytes(SECRET_BYTES)


def encode_secret(secret: bytes) -> str:
    """Base32 in groups of four (ABCD-EFGH-...), easy to read out or type."""
    text = base64.b32encode(secret).decode("ascii").rstrip("=")
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def decode_secret(text: str) -> bytes:
    cleaned = "".join(ch for ch in text.upper() if ch.isalnum())
    cleaned += "=" * (-len(cleaned) % 8)
    try:
        secret = base64.b32decode(cleaned)
    except Exception as e:
        raise ValueError(f"not a valid pairing code: {e}") from None
    if len(secret) != SECRET_BYTES:
        raise ValueError("pairing code has the wrong length")
    return secret


def counter_for(now: float, step: int = TOKEN_STEP_S) -> int:
    return int(now // step)


def token_for(secret: bytes, counter: int) -> bytes:
    return hmac.new(secret, struct.pack(">Q", counter), hashlib.sha256).digest()[:TOKEN_BYTES]


def service_payload(secret: bytes, now: Optional[float] = None) -> bytes:
    """The exact service-data bytes a beacon should be advertising right now."""
    now = time.time() if now is None else now
    return bytes([PROTOCOL_VERSION]) + token_for(secret, counter_for(now))


def static_payload(secret: bytes) -> bytes:
    """Test-mode payload: the token for counter 0, which never changes. It lets a phone advertiser
    app (which can only broadcast one fixed value) act as the beacon. It is replayable by anyone
    who hears it, so the monitor only accepts it for a pairing that explicitly asked for test mode."""
    return bytes([PROTOCOL_VERSION]) + token_for(secret, 0)


def verify_static(secret: bytes, payload: bytes) -> bool:
    return len(payload) == 1 + TOKEN_BYTES and hmac.compare_digest(payload, static_payload(secret))


def verify_payload(secret: bytes, payload: bytes, now: Optional[float] = None) -> bool:
    if len(payload) != 1 + TOKEN_BYTES or payload[0] != PROTOCOL_VERSION:
        return False
    now = time.time() if now is None else now
    counter = counter_for(now)
    received = payload[1:]
    return any(
        hmac.compare_digest(received, token_for(secret, counter + offset))
        for offset in range(-ACCEPT_WINDOW, ACCEPT_WINDOW + 1)
    )


def beacon_payload(service_data: Mapping[str, bytes]) -> Optional[bytes]:
    """Pick our service's data out of an advertisement's service_data mapping (UUID keys may
    come back in any case)."""
    for uuid, data in service_data.items():
        if uuid.lower() == BEACON_SERVICE_UUID:
            return bytes(data)
    return None


def pairing_uri(secret: bytes) -> str:
    return (f"jarvisbeacon://pair?v={PROTOCOL_VERSION}&secret={encode_secret(secret).replace('-', '')}"
            f"&uuid={BEACON_SERVICE_UUID}&step={TOKEN_STEP_S}")


def _main(argv) -> int:
    if len(argv) == 4 and argv[1] == "payload" and argv[3] == "--static":
        try:
            secret = decode_secret(argv[2])
        except ValueError as e:
            print(e)
            return 2
        print(f"service UUID : {BEACON_SERVICE_UUID}")
        print(f"service data : {static_payload(secret).hex()}  (fixed -- test mode only, never rotates)")
        return 0
    if len(argv) == 3 and argv[1] == "payload":
        try:
            secret = decode_secret(argv[2])
        except ValueError as e:
            print(e)
            return 2
        now = time.time()
        payload = service_payload(secret, now)
        left = TOKEN_STEP_S - int(now % TOKEN_STEP_S)
        print(f"service UUID : {BEACON_SERVICE_UUID}")
        print(f"service data : {payload.hex()}  (valid for another ~{left}s, then it rotates)")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
