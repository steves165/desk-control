"""Tests for reaching the desk through an ESPHome Bluetooth proxy, with the proxy faked. Run: python -m pytest tests"""
import asyncio
import json
import sys
from pathlib import Path

import bleak
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent  # noqa: E402
import desk  # noqa: E402
import proxy  # noqa: E402


@pytest.fixture
def restore_bleak():
    client, scanner = bleak.BleakClient, bleak.BleakScanner
    yield
    bleak.BleakClient, bleak.BleakScanner = client, scanner


def test_desk_uses_whatever_bleak_client_is_installed(restore_bleak):
    """A proxy swaps bleak's classes after desk.py has loaded; desk.py must pick the new ones up."""
    used = []

    class Swapped:
        def __init__(self, address, **options):
            used.append((address, options.get("pair")))

        async def __aenter__(self):
            raise ConnectionError("through the proxy")

        async def __aexit__(self, *exc):
            return False

    bleak.BleakClient = Swapped
    with pytest.raises(ConnectionError, match="proxy"):
        asyncio.run(desk.verify_desk("AA:BB", "Desk 1"))
    assert used[0] == ("AA:BB", True)


def test_options_file_picks_the_proxy(tmp_path):
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"bluetooth_proxy": " desk-proxy.local ", "bluetooth_proxy_key": ""}))
    assert agent.proxy_settings(None, None, options) == ("desk-proxy.local", None)
    assert agent.proxy_settings("cli-host", "k", options) == ("desk-proxy.local", "k")
    options.write_text(json.dumps({"bluetooth_proxy": "", "bluetooth_proxy_key": ""}))
    assert agent.proxy_settings(None, None, options) == (None, None)
    assert agent.proxy_settings("cli-host", None, tmp_path / "missing.json") == ("cli-host", None)


def test_proxy_routes_bleak_and_keeps_trying_in_the_background(restore_bleak, monkeypatch):
    import bleak_esphome
    started, stopped = asyncio.Event(), []

    class FakeConnection:
        def __init__(self, config):
            self.config = config

        async def start(self):
            started.set()
            await asyncio.Event().wait()  # the proxy never answers

        async def stop(self):
            stopped.append(self.config)

    monkeypatch.setattr(bleak_esphome, "APIConnectionManager", FakeConnection)

    async def scenario():
        link = proxy.Proxy("desk-proxy.local", "")
        await asyncio.wait_for(link.start(), 30)  # returns without waiting for the proxy
        await asyncio.wait_for(started.wait(), 5)
        assert bleak.BleakClient.__name__ == "HaBleakClientWrapper"
        # Until the proxy has heard the desk, connecting fails, and the desk's own retry loop tries again.
        with pytest.raises(Exception, match="never seen|No backend"):
            async with bleak.BleakClient("AA:BB:CC:DD:EE:FF", timeout=2, services=desk.SERVICES):
                pass
        await link.stop()
        assert stopped == [{"address": "desk-proxy.local", "noise_psk": None}]
    asyncio.run(scenario())
