"""Optional: reach the desk through an ESPHome Bluetooth proxy, such as an Ethernet ESP32 near the desk.

The desk agent normally uses the machine's own Bluetooth adapter. When the machine is out of range (in a network
cabinet, say), an ESP32 running ESPHome's Bluetooth proxy (with active connections) can sit near the desk instead.
bleak-esphome registers the proxy with habluetooth's Bluetooth manager, which swaps bleak.BleakClient and
bleak.BleakScanner for versions that route through it: desk.py works unchanged, because it looks them up when used.

The proxy is reached on ESPHome's native API (TCP 6053), with its API encryption key if it has one.
"""
import asyncio
import logging
from typing import Optional

log = logging.getLogger("proxy")


class Proxy:
    def __init__(self, host: str, key: Optional[str] = None):
        self.host = host
        self.key = key or None
        self._connection = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Set up routing through the proxy and start connecting to it; returns without waiting for it."""
        import habluetooth
        from bleak_esphome import APIConnectionManager

        class Manager(habluetooth.BluetoothManager):
            def _discover_service_info(self, service_info) -> None:
                pass  # the desk code scans and connects itself; nothing to do with each advertisement

        # Only the proxy is used, so habluetooth's local adapter management doesn't apply: drop its complaint about
        # lacking the permissions for it, and its advice on connection retries (desk.py has its own).
        logging.getLogger("habluetooth.manager").addFilter(
            lambda record: not record.getMessage().startswith("Missing required permissions"))
        logging.getLogger("habluetooth.channels.bluez").setLevel(logging.ERROR)
        logging.getLogger("habluetooth.wrappers").setLevel(logging.ERROR)
        logging.getLogger("aioesphomeapi").setLevel(logging.WARNING)
        await Manager().async_setup()
        self._connection = APIConnectionManager({"address": self.host, "noise_psk": self.key})
        self._task = asyncio.ensure_future(self._connect())

    async def _connect(self) -> None:
        log.info("connecting to the Bluetooth proxy at %s", self.host)
        try:
            # Returns once connected; until then it keeps retrying in the background, and so does the desk.
            await self._connection.start()
            log.info("Bluetooth proxy connected")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("Bluetooth proxy: %s", str(e) or type(e).__name__)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._connection:
            await self._connection.stop()
