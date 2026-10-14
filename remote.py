"""The desk agent's network protocol, and the app's client for it.

The agent (agent.py) holds the desk's Bluetooth connection on an always-on box; the app, in remote mode, drives the
desk through it instead. Newline-delimited JSON over TCP (port 8574):

1. agent → {"hello": "desk-control", "version": 1, "nonce": hex}
2. app   → {"auth": HMAC-SHA256(key, nonce) as hex}; the pairing key itself never crosses the network.
3. agent → {"ok": true}, or {"ok": false, "error": ...} and closes.

Then the app sends requests {"id": n, "cmd": ...} and gets replies {"id": n, "ok": ..., ...}, and the agent pushes
{"event": "state", ...} whenever the desk or its settings change. Requests:

- move {"position": p} or {"height_cm": x}, preset {"index": n}, stop
- hold {"direction": ±1}: resend every HOLD_REFRESH seconds while held; the desk stops if they stop coming
- scan → {"devices": [...]}; choose {"address", "name"} pairs with and checks the desk (error kind "not_linak")
- settings {"presets"?, "offset_mm"?}
"""
import asyncio
import hashlib
import hmac
import json
import logging
from typing import Callable, Optional

from desk import NotLinakError

PORT = 8574
VERSION = 1
HOLD_REFRESH = 0.5
# The agent stops a hold this long after the last refresh, e.g. if the app's network drops mid-press.
HOLD_TIMEOUT = 1.5
REQUEST_TIMEOUT = 10
RETRY_DELAYS = (2, 5, 10, 30)

log = logging.getLogger("remote")


def proof(key: str, nonce: str) -> str:
    return hmac.new(key.strip().encode(), bytes.fromhex(nonce), hashlib.sha256).hexdigest()


async def write(writer: asyncio.StreamWriter, message: dict) -> None:
    writer.write(json.dumps(message).encode() + b"\n")
    await writer.drain()


async def read(reader: asyncio.StreamReader) -> dict:
    line = await reader.readline()
    if not line:
        raise ConnectionError("Connection closed")
    return json.loads(line)


class AuthError(Exception):
    pass


async def check(host: str, key: str, port: int = PORT) -> dict:
    """Pair with the agent once and return its state; raises AuthError or the connection error."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 10)
    try:
        hello = await asyncio.wait_for(read(reader), 10)
        await write(writer, {"auth": proof(key, hello["nonce"])})
        reply = await asyncio.wait_for(read(reader), 10)
        if not reply.get("ok"):
            raise AuthError(reply.get("error", "The desk agent refused the pairing key"))
        return await asyncio.wait_for(read(reader), 10)
    finally:
        writer.close()


class RemoteDesk:
    """A desk reached through the agent, with the same interface the app uses for LinakDesk."""

    def __init__(self, host: str, key: str, port: int = PORT):
        self.host, self.key, self.port = host, key, port
        self.position: Optional[int] = None
        self.speed = 0
        self.state = "disconnected"
        self.detail = ""
        self.auth_failed = False
        self.settings: dict = {}
        self.on_position: Callable[[int, int], None] = lambda pos, speed: None
        self.on_state: Callable[[str, str], None] = lambda state, detail: None
        self.on_settings: Callable[[dict], None] = lambda settings: None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._hold: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and self.state == "connected"

    @property
    def agent_online(self) -> bool:
        return self._writer is not None

    def _set_state(self, state: str, detail: str = "") -> None:
        if (state, detail) != getattr(self, "_last_state", None):
            self._last_state = (state, detail)
            self.state, self.detail = state, detail
            self.on_state(state, detail)

    async def run(self) -> None:
        """Stay connected to the agent, reconnecting whenever the connection drops."""
        failures = 0
        while True:
            self._set_state("connecting", f"Connecting to the desk agent at {self.host}")
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 10)
                try:
                    hello = await asyncio.wait_for(read(reader), 10)
                    await write(writer, {"auth": proof(self.key, hello["nonce"])})
                    reply = await asyncio.wait_for(read(reader), 10)
                    if not reply.get("ok"):
                        raise AuthError(reply.get("error", "The desk agent refused the pairing key"))
                    self._writer = writer
                    self.auth_failed = False
                    failures = 0
                    while True:
                        self._receive(await read(reader))
                finally:
                    self._writer = None
                    for future in self._pending.values():
                        future.cancel()
                    self._pending.clear()
                    writer.close()
            except asyncio.CancelledError:
                raise
            except AuthError as e:
                self.auth_failed = True
                self._set_state("disconnected", str(e))
                failures = len(RETRY_DELAYS)
            except Exception as e:
                failures += 1
                self._set_state("disconnected", f"Can't reach the desk agent at {self.host}: {str(e) or type(e).__name__}")
            await asyncio.sleep(RETRY_DELAYS[min(failures, len(RETRY_DELAYS) - 1)])

    def _receive(self, message: dict) -> None:
        if "id" in message:
            future = self._pending.pop(message["id"], None)
            if future and not future.done():
                future.set_result(message)
            return
        if message.get("event") != "state":
            return
        settings = message["settings"]
        if settings != self.settings:
            self.settings = settings
            self.on_settings(settings)
        position, speed = message.get("position"), message.get("speed", 0)
        if position is not None and (position, speed) != (self.position, self.speed):
            self.position, self.speed = position, speed
            self.on_position(position, speed)
        self._set_state(message["state"], message.get("detail", ""))

    async def request(self, cmd: str, timeout: float = REQUEST_TIMEOUT, **args) -> dict:
        if self._writer is None:
            raise RuntimeError("Not connected to the desk agent")
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = future
        await write(self._writer, {"id": self._next_id, "cmd": cmd, **args})
        reply = await asyncio.wait_for(future, timeout)
        if not reply.get("ok"):
            if reply.get("kind") == "not_linak":
                raise NotLinakError(reply.get("error"))
            raise RuntimeError(reply.get("error") or "The desk agent refused the request")
        return reply

    # The LinakDesk interface.
    async def move_to(self, position: int) -> None:
        self._end_hold()
        await self.request("move", position=int(position))

    async def hold(self, direction: int) -> None:
        self._end_hold()
        self._hold = asyncio.ensure_future(self._keep_holding(direction))

    async def _keep_holding(self, direction: int) -> None:
        while True:
            await self.request("hold", direction=direction)
            await asyncio.sleep(HOLD_REFRESH)

    def _end_hold(self) -> None:
        if self._hold and not self._hold.done():
            self._hold.cancel()
        self._hold = None

    async def stop(self) -> None:
        self._end_hold()
        await self.request("stop")

    # Agent-only.
    async def scan(self) -> list[dict]:
        return (await self.request("scan", timeout=30))["devices"]

    async def choose(self, address: str, name: str) -> None:
        await self.request("choose", timeout=120, address=address, name=name)

    async def save_settings(self, **settings) -> None:
        await self.request("settings", **settings)
