"""Bluetooth LE control of a Linak standing desk (DPG / "Desk XXXX" controllers)."""
import asyncio
import logging
import struct
from typing import Callable, Optional

import bleak

COMMAND = "99fa0002-338a-1024-8a49-009c0215f78a"
HEIGHT = "99fa0021-338a-1024-8a49-009c0215f78a"
REFERENCE_INPUT = "99fa0031-338a-1024-8a49-009c0215f78a"
SERVICES = [
    "99fa0001-338a-1024-8a49-009c0215f78a",  # control
    "99fa0020-338a-1024-8a49-009c0215f78a",  # reference output (height)
    "99fa0030-338a-1024-8a49-009c0215f78a",  # reference input (target)
]

log = logging.getLogger("desk")

# bleak's classes are looked up when used (bleak.BleakClient), not imported by name: a Bluetooth proxy (proxy.py)
# swaps in versions that route through it after this module has loaded.

WAKEUP = bytes([0xFE, 0x00])
STOP = bytes([0xFF, 0x00])

# Positions are in 0.1 mm above the desk's lowest point.
MAX_POSITION = 6500
MOVE_PERIOD = 0.4
# Holding a button aims at a fixed far target: the controller stops dead if the target changes mid-move.
HOLD_DISTANCE = 3000
# The controller can take ~35 s to accept a connection; rapid retries make it worse.
CONNECT_TIMEOUT = 45
RETRY_DELAYS = (3, 6, 12, 30, 60)


class NotLinakError(Exception):
    """The chosen device doesn't expose the Linak desk control services."""


async def scan_devices(timeout: float = 6) -> list[dict]:
    """Nearby named BLE devices, likely desks first."""
    found = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
    devices = []
    for device, adv in found.values():
        name = device.name or adv.local_name
        if not name:
            continue
        likely = name.startswith("Desk") or any(u.lower() in SERVICES for u in adv.service_uuids)
        devices.append({"address": device.address, "name": name, "rssi": adv.rssi, "likely": likely})
    devices.sort(key=lambda d: (not d["likely"], -d["rssi"]))
    return devices


async def verify_desk(address: str, name: str = "This device") -> None:
    """Pair with the device and check it's a Linak controller.

    Raises NotLinakError if it isn't, or the connection error if it can't be reached.
    """
    error: Exception | None = None
    # Windows often drops the first connection attempt, so allow a second.
    for _ in range(2):
        try:
            async with bleak.BleakClient(address, services=SERVICES, timeout=CONNECT_TIMEOUT, pair=True) as client:
                uuids = {service.uuid.lower() for service in client.services}
                if not set(SERVICES) <= uuids:
                    try:
                        await client.unpair()
                    except Exception:
                        pass
                    raise NotLinakError(f"{name} isn't a Linak desk controller.")
                await client.read_gatt_char(HEIGHT)
                return
        except NotLinakError:
            raise
        except Exception as e:
            error = e
            await asyncio.sleep(2)
    raise error


class LinakDesk:
    def __init__(self, address: str):
        self.address = address
        self.position: Optional[int] = None
        self.speed = 0
        self.state = "disconnected"
        self.on_position: Callable[[int, int], None] = lambda pos, speed: None
        self.on_state: Callable[[str, str], None] = lambda state, detail: None
        self._client: Optional[bleak.BleakClient] = None
        self._motion: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        self.on_state(state, detail)

    def _update(self, data: bytes) -> None:
        self.position, self.speed = struct.unpack("<Hh", bytes(data[:4]))
        log.debug("position %d speed %d", self.position, self.speed)
        self.on_position(self.position, self.speed)

    async def run(self) -> None:
        """Keep a connection open, reconnecting whenever it drops."""
        loop = asyncio.get_running_loop()
        failures = 0
        while True:
            dropped = asyncio.Event()
            self._set_state("connecting")
            try:
                async with bleak.BleakClient(
                    self.address,
                    disconnected_callback=lambda _: loop.call_soon_threadsafe(dropped.set),
                    services=SERVICES,
                    timeout=CONNECT_TIMEOUT,
                ) as client:
                    await client.start_notify(HEIGHT, lambda _, data: loop.call_soon_threadsafe(self._update, data))
                    self._update(await client.read_gatt_char(HEIGHT))
                    self._client = client
                    self._set_state("connected")
                    failures = 0
                    await dropped.wait()
                self._set_state("disconnected")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                failures += 1
                self._set_state("disconnected", str(e) or type(e).__name__)
            finally:
                self._client = None
                self._cancel_motion()
            await asyncio.sleep(RETRY_DELAYS[min(failures, len(RETRY_DELAYS) - 1)])

    def _require(self) -> bleak.BleakClient:
        if not self.connected:
            raise RuntimeError("Desk is not connected")
        return self._client

    async def _write_target(self, client: bleak.BleakClient, position: int) -> None:
        position = max(0, min(MAX_POSITION, int(position)))
        log.debug("target %d", position)
        await client.write_gatt_char(REFERENCE_INPUT, struct.pack("<H", position), response=True)

    async def _send_stop(self) -> None:
        log.debug("stop")
        if self.connected:
            await self._client.write_gatt_char(COMMAND, STOP, response=True)

    def _cancel_motion(self) -> None:
        if self._motion and not self._motion.done():
            self._motion.cancel()
        self._motion = None

    def _start_motion(self, coro) -> asyncio.Task:
        self._cancel_motion()
        self._motion = asyncio.ensure_future(coro)
        return self._motion

    async def stop(self) -> None:
        self._cancel_motion()
        await self._send_stop()

    def move_to(self, position: int) -> asyncio.Task:
        return self._start_motion(self._move_to(position))

    def hold(self, direction: int) -> asyncio.Task:
        """Move up (+1) or down (-1) until stop() is called."""
        log.debug("hold %+d from %s", direction, self.position)
        return self.move_to((self.position or 0) + direction * HOLD_DISTANCE)

    async def _move_to(self, position: int) -> None:
        log.debug("move_to %d from %s", position, self.position)
        client = self._require()
        await client.write_gatt_char(COMMAND, WAKEUP, response=True)
        await client.write_gatt_char(COMMAND, STOP, response=True)
        started, idle = False, 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 90
        try:
            # The controller only keeps moving while the target is refreshed.
            while loop.time() < deadline:
                await self._write_target(client, position)
                await asyncio.sleep(MOVE_PERIOD)
                self._update(await client.read_gatt_char(HEIGHT))
                if self.speed != 0:
                    started, idle = True, 0
                    continue
                idle += 1
                if started or idle >= 3:
                    break
        except asyncio.CancelledError:
            await self._send_stop()
            raise
