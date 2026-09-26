"""
Beacon simulator: advertises the JARVIS beacon protocol (see clBeacon.py) from a Linux machine
with BlueZ, rotating the token so the monitor keeps seeing a valid one. Stands in for the future
JARVIS app -- run it on a *second* machine (a Raspberry Pi works well); an adapter can't hear its
own advertisements, so it can't be the same PC that runs the monitor.

  python src/utils/clBeaconSim.py <PAIRING CODE>              # advertise until Ctrl+C
  python src/utils/clBeaconSim.py <PAIRING CODE> --print-only # no Bluetooth: print the payload each
                                                              # rotation, to paste into a phone app
  options: --adapter hci1   --interval 20
"""
import argparse
import asyncio
import os
import sys
import time
from typing import Callable, Optional

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from utils import clBeacon

ADVERTISEMENT_PATH = "/org/jarvis/beacon/advert"
DEFAULT_INTERVAL_S = 20  # comfortably inside the monitor's +/- one 30 s step window


def _advertisement_class():
    """Built lazily so importing this module (and --print-only) never needs dbus-fast."""
    from dbus_fast import Variant
    from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method

    class Advertisement(ServiceInterface):
        """org.bluez.LEAdvertisement1 carrying only our service data. A legacy advertisement is
        31 bytes: flags (3) + 128-bit service data (2 + 16 + 9) = 30, so no name or UUID list."""

        def __init__(self, uuid: str, data: bytes):
            super().__init__("org.bluez.LEAdvertisement1")
            self._uuid, self._data = uuid, data
            self.released = False

        @dbus_property(access=PropertyAccess.READ)
        def Type(self) -> 's':
            return "peripheral"

        @dbus_property(access=PropertyAccess.READ)
        def ServiceData(self) -> 'a{sv}':
            return {self._uuid: Variant('ay', self._data)}

        @method()
        def Release(self):
            self.released = True

    return Advertisement


class Advertiser:
    """Registers one advertisement, and swaps it for a fresh-token one every `interval` seconds.
    `bus` is a connected dbus_fast MessageBus and `manager` the adapter's LEAdvertisingManager1
    interface -- both injectable so the rotation logic is testable without Bluetooth."""

    def __init__(self, bus, manager, secret: bytes, interval: float = DEFAULT_INTERVAL_S,
                 clock: Callable[[], float] = time.time, log: Callable[[str], None] = print):
        self.bus, self.manager, self.secret = bus, manager, secret
        self.interval, self.clock, self.log = interval, clock, log
        self._current = None
        self.rotations = 0

    async def _publish(self):
        payload = clBeacon.service_payload(self.secret, self.clock())
        advertisement = _advertisement_class()(clBeacon.BEACON_SERVICE_UUID, payload)
        self.bus.export(ADVERTISEMENT_PATH, advertisement)
        await self.manager.call_register_advertisement(ADVERTISEMENT_PATH, {})
        self._current = advertisement
        self.rotations += 1
        self.log(f"advertising {payload.hex()}")

    async def _withdraw(self):
        if self._current is None:
            return
        try:
            await self.manager.call_unregister_advertisement(ADVERTISEMENT_PATH)
        except Exception:
            pass  # already gone (BlueZ released it, or the adapter reset)
        self.bus.unexport(ADVERTISEMENT_PATH)
        self._current = None

    async def rotate_once(self):
        await self._withdraw()
        await self._publish()

    async def run(self, sleep: Callable = asyncio.sleep, stop: Optional[asyncio.Event] = None):
        try:
            await self._publish()
            while not (stop and stop.is_set()):
                await sleep(self.interval)
                if stop and stop.is_set():
                    break
                await self.rotate_once()
        finally:
            await self._withdraw()


async def _connect(adapter: str):
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    path = f"/org/bluez/{adapter}"
    introspection = await bus.introspect("org.bluez", path)
    proxy = bus.get_proxy_object("org.bluez", path, introspection)
    return bus, proxy.get_interface("org.bluez.LEAdvertisingManager1")


async def _print_only(secret: bytes, interval: float):
    print(f"service UUID: {clBeacon.BEACON_SERVICE_UUID}")
    while True:
        payload = clBeacon.service_payload(secret)
        print(f"{time.strftime('%H:%M:%S')}  service data: {payload.hex()}")
        await asyncio.sleep(interval)


async def _main(args) -> int:
    try:
        secret = clBeacon.decode_secret(args.code)
    except ValueError as e:
        print(e)
        return 2
    if args.print_only:
        await _print_only(secret, args.interval)
        return 0
    if not sys.platform.startswith("linux"):
        print("The BlueZ advertiser only works on Linux; use --print-only and a phone app instead.")
        return 1
    try:
        bus, manager = await _connect(args.adapter)
    except Exception as e:
        print(f"Can't reach the Bluetooth adapter '{args.adapter}': {e}\n"
              "Is Bluetooth on (bluetoothctl power on) and BlueZ running?")
        return 1
    print(f"Advertising the JARVIS beacon on {args.adapter} (Ctrl+C to stop).")
    advertiser = Advertiser(bus, manager, secret, args.interval)
    try:
        await advertiser.run()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Advertising failed: {e}")
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Advertise the JARVIS beacon protocol from BlueZ.")
    parser.add_argument("code", help="the pairing code shown by JARVIS (dashes and case don't matter)")
    parser.add_argument("--adapter", default="hci0")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S,
                        help="seconds between token refreshes (default %(default)s)")
    parser.add_argument("--print-only", action="store_true",
                        help="don't touch Bluetooth; just print the current service data on each refresh")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_main(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
