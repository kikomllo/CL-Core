"""Local control of a Tuya smart plug (the LSC Smart Connect heater plug) over the LAN, through tinytuya.

A Tuya device accepts one local session at a time, and the phone app and both machines running clControl all want one, so every
call here is a short session: connect, do the one thing, close. Calls to one device never overlap inside this process (a lock per
device), a failed on/off/status is tried once more after a small random pause, and a toggle is never repeated (it may have worked
before the answer was lost, and a second one would flip it back). Nothing here touches the network at import time.
"""
import asyncio
import logging
import random
from typing import Any, Callable, Dict, Optional

try:
    import tinytuya
except ImportError:                                   # the module still imports; a call then says what is missing
    tinytuya = None

SOCKET_TIMEOUT = 3.0                                  # seconds for one connect / answer
JITTER = (0.2, 0.6)                                   # pause before the one retry, so two machines do not collide again
SWITCH_DP = "1"                                       # the data point of a plug's switch; a device entry can override it with "dps"
DEFAULT_VERSION = 3.3                                 # newer LSC plugs speak 3.5; the wizard prints the right one

_locks: Dict[str, asyncio.Lock] = {}


class TuyaError(Exception):
    """The plug could not be switched or read; the message says what to check."""


def _open(entry: Dict[str, Any], key: str):
    if tinytuya is None:
        raise TuyaError("The tinytuya package is not installed.")
    device = tinytuya.OutletDevice(
        entry["device_id"], entry["ip"], key, version=float(entry.get("version", DEFAULT_VERSION)),
        connection_timeout=SOCKET_TIMEOUT, connection_retry_limit=1, connection_retry_delay=0, persist=True)
    device.set_socketTimeout(SOCKET_TIMEOUT)
    return device


def _checked(reply, what: str):
    """tinytuya reports failures as {"Error": ..., "Err": ...} instead of raising."""
    if not isinstance(reply, dict):
        raise TuyaError(f"No answer when {what}: check the plug's IP and that it is on Wi-Fi.")
    if reply.get("Error"):
        raise TuyaError(f"{reply['Error']} (when {what}): check the local key, the version and the IP.")
    return reply


def _read(device, dp: str) -> bool:
    dps = (_checked(device.status(), "reading the plug").get("dps") or {})
    if dp not in dps:
        raise TuyaError(f"The plug did not report data point {dp}: set \"dps\" in its device entry.")
    return bool(dps[dp])


def _session(entry: Dict[str, Any], key: str, op: Callable, factory: Optional[Callable] = None):
    """One short session, in a worker thread: open, run `op(device, dp)`, always close."""
    device = (factory or _open)(entry, key)
    try:
        return op(device, str(entry.get("dps", SWITCH_DP)))
    finally:
        try:
            device.close()
        except Exception:
            pass


async def _run(entry, key, op, attempts: int, factory=None):
    if not key:
        raise TuyaError("There is no local key for this plug: set TUYA_KEY_<NAME> in .env.")
    lock = _locks.setdefault(str(entry.get("device_id", entry.get("ip"))), asyncio.Lock())
    async with lock:
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(asyncio.to_thread(_session, entry, key, op, factory), SOCKET_TIMEOUT * 3)
            except asyncio.TimeoutError:
                error = TuyaError("The plug did not answer in time: check the IP and that it is on Wi-Fi.")
            except TuyaError as e:
                error = e
            except Exception as e:                    # a socket error out of the library
                error = TuyaError(f"The plug could not be reached ({e}).")
            if attempt == attempts:
                raise error
            logging.debug(f"[TUYA] {error}; trying once more")
            await asyncio.sleep(random.uniform(*JITTER))


async def get_power(entry: Dict[str, Any], key: str, factory=None) -> bool:
    """Whether the plug is on."""
    return await _run(entry, key, _read, 2, factory)


async def set_power(entry: Dict[str, Any], key: str, on: bool, factory=None) -> None:
    def op(device, dp):
        _checked(device.set_status(bool(on), switch=int(dp)), "switching the plug")
    await _run(entry, key, op, 2, factory)


async def toggle_power(entry: Dict[str, Any], key: str, factory=None) -> bool:
    """Flips the plug in one session and returns the new state. Never retried."""
    def op(device, dp):
        new = not _read(device, dp)
        _checked(device.set_status(new, switch=int(dp)), "switching the plug")
        return new
    return await _run(entry, key, op, 1, factory)
