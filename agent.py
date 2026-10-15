"""Desk Control agent: holds the desk's Bluetooth connection on an always-on box, such as a Home Assistant server.

The Desk Control app (remote mode) and the Alexa skill both drive the desk through it; see remote.py for the
protocol. Everything it keeps is in the data folder:

- settings.json: the desk's address and name, height calibration and presets;
- agent.json: the pairing key the app needs, made on first start;
- alexa/: the Alexa device certificate and settings (from alexa/export.py), if you use Alexa.

It uses the machine's own Bluetooth adapter, or an ESPHome Bluetooth proxy near the desk (proxy.py) when given one
with --proxy, or with bluetooth_proxy in a Home Assistant add-on's options file (--options).

Usage: python agent.py [--data FOLDER] [--port 8574] [--listen ADDRESS] [--proxy HOST [--proxy-key KEY]]
"""
import argparse
import asyncio
import hmac
import json
import logging
import os
import secrets
import signal
import sys
from pathlib import Path

if sys.platform == "win32":
    from bleak.backends.winrt.util import allow_sta
    allow_sta()

import alexa_link
import remote
from proxy import Proxy
from desk import MAX_POSITION, LinakDesk, NotLinakError, scan_devices, verify_desk

DEFAULT_SETTINGS = {"address": None, "desk_name": None, "offset_mm": 620.0, "presets": []}

log = logging.getLogger("agent")


class Agent:
    def __init__(self, data: Path, proxy: Proxy | None = None):
        self.data = data
        self.proxy = proxy
        self.data.mkdir(parents=True, exist_ok=True)
        self.settings = {**DEFAULT_SETTINGS, **self._load("settings.json")}
        agent = self._load("agent.json")
        if not agent.get("key"):
            agent["key"] = "-".join(secrets.token_hex(2).upper() for _ in range(4))
            self._save("agent.json", agent)
        self.key = agent["key"]
        self.desk: LinakDesk | None = None
        self._run_task: asyncio.Task | None = None
        self._detail = ""
        self.clients: set[asyncio.StreamWriter] = set()
        self.alexa: alexa_link.AlexaLink | None = None
        self._hold_direction = 0
        self._hold_deadline = 0.0
        self._hold_owner: asyncio.StreamWriter | None = None
        self._hold_watch: asyncio.Task | None = None

    def _load(self, name: str) -> dict:
        try:
            return json.loads((self.data / name).read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, name: str, value: dict) -> None:
        path = self.data / name
        path.with_suffix(".tmp").write_text(json.dumps(value, indent=2))
        os.replace(path.with_suffix(".tmp"), path)

    # --- the desk --------------------------------------------------------------------------------------------------
    def start_desk(self) -> None:
        if not self.settings["address"]:
            self.on_state("nodesk", "")
            return
        self.desk = LinakDesk(self.settings["address"])
        self.desk.on_state = self.on_state
        self.desk.on_position = lambda position, speed: self.broadcast()
        self._run_task = asyncio.ensure_future(self.desk.run())

    async def stop_desk(self) -> None:
        if self._run_task:
            self._run_task.cancel()
            await asyncio.wait({self._run_task}, timeout=5)
        self._run_task = None
        self.desk = None

    def on_state(self, state: str, detail: str) -> None:
        log.info("desk: %s %s", state, detail)
        self._detail = detail
        self.broadcast()

    @property
    def state(self) -> str:
        return self.desk.state if self.desk else "nodesk"

    def connected(self) -> bool:
        return bool(self.desk and self.desk.connected)

    def to_position(self, height_cm: float) -> int:
        return round((height_cm * 10 - self.settings["offset_mm"]) * 10)

    def to_cm(self, position: int) -> float:
        return (self.settings["offset_mm"] + position / 10) / 10

    def snapshot(self) -> dict:
        position = self.desk.position if self.desk else None
        return {
            "event": "state",
            "state": self.state,
            "detail": self._detail,
            "position": position,
            "speed": self.desk.speed if self.desk else 0,
            "settings": {k: self.settings[k] for k in ("address", "desk_name", "offset_mm", "presets")},
        }

    def broadcast(self) -> None:
        message = (json.dumps(self.snapshot()) + "\n").encode()
        for writer in list(self.clients):
            writer.write(message)
        self.report_alexa()

    def report_alexa(self) -> None:
        if not self.alexa:
            return
        position = self.desk.position if self.desk else None
        self.alexa.report({
            "desk": self.state,
            "height_cm": None if position is None else round(self.to_cm(position), 1),
            "min_cm": round(self.to_cm(0), 1),
            "max_cm": round(self.to_cm(MAX_POSITION), 1),
            "presets": [{"name": p["name"], "height_cm": p["height_cm"]} for p in self.settings["presets"]],
        })

    # --- holding a button ------------------------------------------------------------------------------------------
    def hold(self, direction: int, owner) -> None:
        loop = asyncio.get_running_loop()
        self._hold_deadline = loop.time() + remote.HOLD_TIMEOUT
        self._hold_owner = owner
        # Refreshes only extend the deadline: changing the target mid-move would stop the desk dead.
        if direction != self._hold_direction:
            self._hold_direction = direction
            self.desk.hold(direction)
        if not self._hold_watch or self._hold_watch.done():
            self._hold_watch = asyncio.ensure_future(self._watch_hold())

    async def _watch_hold(self) -> None:
        loop = asyncio.get_running_loop()
        while self._hold_direction:
            if loop.time() > self._hold_deadline:
                log.info("hold not refreshed: stopping")
                await self.stop()
                return
            await asyncio.sleep(0.1)

    def end_hold(self) -> None:
        self._hold_direction = 0
        self._hold_owner = None

    async def stop(self) -> None:
        self.end_hold()
        if self.connected():
            await self.desk.stop()

    def move(self, position: int) -> None:
        self.end_hold()
        self.desk.move_to(position)

    # --- requests --------------------------------------------------------------------------------------------------
    async def handle(self, request: dict, owner=None) -> dict:
        """Requests from the app (owner = its connection) and the Alexa skill (owner None)."""
        cmd = request.get("cmd")
        if cmd == "state":
            return {"ok": True, **self.snapshot()}
        if cmd == "scan":
            return {"ok": True, "devices": await scan_devices()}
        if cmd == "choose":
            return await self.choose(request["address"], request.get("name") or request["address"])
        if cmd == "settings":
            for key in ("presets", "offset_mm"):
                if key in request:
                    self.settings[key] = request[key]
            self._save("settings.json", self.settings)
            self.broadcast()
            return {"ok": True}
        if not self.connected():
            return {"ok": False, "error": "The desk is not connected"}
        if cmd == "stop":
            await self.stop()
        elif cmd == "hold":
            self.hold(1 if request["direction"] > 0 else -1, owner)
        elif cmd == "move":
            if "position" in request:
                self.move(int(request["position"]))
            else:
                self.move(self.to_position(float(request["height_cm"])))
        elif cmd == "preset":
            self.move(self.to_position(self.settings["presets"][int(request["index"])]["height_cm"]))
        else:
            return {"ok": False, "error": f"Unknown command {cmd!r}"}
        return {"ok": True}

    async def choose(self, address: str, name: str) -> dict:
        # The desk accepts one connection at a time, so let go of it while pairing.
        await self.stop_desk()
        try:
            await verify_desk(address, name)
        except NotLinakError as e:
            self.start_desk()
            return {"ok": False, "kind": "not_linak", "error": str(e)}
        except Exception as e:
            self.start_desk()
            return {"ok": False, "error": str(e) or type(e).__name__}
        self.settings["address"], self.settings["desk_name"] = address, name
        self._save("settings.json", self.settings)
        self.start_desk()
        return {"ok": True}

    def on_alexa(self, request: dict) -> None:
        asyncio.ensure_future(self.handle(request))

    # --- the app's connections -------------------------------------------------------------------------------------
    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            nonce = secrets.token_hex(16)
            await remote.write(writer, {"hello": "desk-control", "version": remote.VERSION, "nonce": nonce})
            answer = await asyncio.wait_for(remote.read(reader), 10)
            if not hmac.compare_digest(str(answer.get("auth", "")), remote.proof(self.key, nonce)):
                log.warning("%s: wrong pairing key", peer)
                await remote.write(writer, {"ok": False, "error": "Wrong pairing key"})
                return
            await remote.write(writer, {"ok": True})
            log.info("%s: connected", peer)
            self.clients.add(writer)
            writer.write((json.dumps(self.snapshot()) + "\n").encode())
            while True:
                request = await remote.read(reader)
                asyncio.ensure_future(self._reply(writer, request))
        except Exception as e:
            log.info("%s: disconnected (%s)", peer, str(e) or type(e).__name__)
        finally:
            self.clients.discard(writer)
            if self._hold_owner is writer:
                await self.stop()
            writer.close()

    async def _reply(self, writer: asyncio.StreamWriter, request: dict) -> None:
        try:
            reply = await self.handle(request, writer)
        except Exception as e:
            log.exception("request %s failed", request.get("cmd"))
            reply = {"ok": False, "error": str(e) or type(e).__name__}
        if writer in self.clients:
            await remote.write(writer, {"id": request.get("id"), **reply})

    # --- running ---------------------------------------------------------------------------------------------------
    async def run(self, port: int, listen: str | None = None) -> None:
        server = await asyncio.start_server(self.serve, listen, port)
        log.info("listening on port %d; pairing key %s", port, self.key)
        if self.proxy:
            await self.proxy.start()
        alexa_folder = self.data / "alexa"
        if alexa_link.configured(alexa_folder):
            self.alexa = alexa_link.AlexaLink(self.on_alexa, alexa_folder)
            self.alexa.start()
        self.start_desk()
        stopping = asyncio.Event()
        if sys.platform != "win32":
            for sig in (signal.SIGTERM, signal.SIGINT):
                asyncio.get_running_loop().add_signal_handler(sig, stopping.set)
        try:
            await stopping.wait()
        finally:
            log.info("stopping")
            server.close()
            if self.alexa:
                self.alexa.stop()
            await self.stop_desk()
            if self.proxy:
                await self.proxy.stop()


def proxy_settings(host: str | None, key: str | None, options: Path | None) -> tuple[str | None, str | None]:
    """The proxy to use: an add-on's options win over the command line and environment."""
    if options and options.exists():
        values = json.loads(options.read_text())
        host = (values.get("bluetooth_proxy") or "").strip() or host
        key = (values.get("bluetooth_proxy_key") or "").strip() or key
    return host or None, key or None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("DESK_CONTROL_DATA", "data")))
    parser.add_argument("--port", type=int, default=remote.PORT)
    parser.add_argument("--listen", default=None, help="address to listen on (default: all)")
    parser.add_argument("--proxy", default=os.environ.get("DESK_CONTROL_PROXY"),
                        help="reach the desk through this ESPHome Bluetooth proxy (host name or address)")
    parser.add_argument("--proxy-key", default=os.environ.get("DESK_CONTROL_PROXY_KEY"),
                        help="the proxy's API encryption key, if it has one")
    parser.add_argument("--options", type=Path, help="a Home Assistant add-on's options file (bluetooth_proxy, "
                                                     "bluetooth_proxy_key)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    proxy_host, proxy_key = proxy_settings(args.proxy, args.proxy_key, args.options)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(message)s", stream=sys.stdout)
    try:
        asyncio.run(Agent(args.data, Proxy(proxy_host, proxy_key) if proxy_host else None).run(args.port, args.listen))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
