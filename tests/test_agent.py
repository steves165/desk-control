"""Tests for the desk agent and the app's remote client, with a fake desk. Run: python -m pytest tests"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent as agent_module  # noqa: E402
import remote  # noqa: E402
from desk import NotLinakError  # noqa: E402


class FakeDesk:
    def __init__(self, address):
        self.address = address
        self.position, self.speed, self.state = 1450, 0, "disconnected"
        self.connected = False
        self.on_state = lambda state, detail: None
        self.on_position = lambda position, speed: None
        self.moves: list = []

    async def run(self):
        self.connected, self.state = True, "connected"
        self.on_state("connected", "")
        await asyncio.Event().wait()

    def move_to(self, position):
        self.moves.append(("move", position))

    def hold(self, direction):
        self.moves.append(("hold", direction))

    async def stop(self):
        self.moves.append(("stop",))


@pytest.fixture
def make_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "LinakDesk", FakeDesk)
    monkeypatch.setattr(remote, "RETRY_DELAYS", (0.05,))

    def make(**settings):
        (tmp_path / "settings.json").write_text(json.dumps({
            "address": "AA:BB", "desk_name": "Desk 1", "offset_mm": 620.0,
            "presets": [{"name": "Sit", "height_cm": 76.5}, {"name": "Stand", "height_cm": 115.8}], **settings}))
        return agent_module.Agent(tmp_path)
    return make


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


async def started(agent):
    server = await asyncio.start_server(agent.serve, "127.0.0.1", 0)
    agent.start_desk()
    await asyncio.sleep(0)
    return server, server.sockets[0].getsockname()[1]


async def connect(port, key):
    desk = remote.RemoteDesk("127.0.0.1", key, port)
    states = []
    desk.on_state = lambda state, detail: states.append((state, detail))
    task = asyncio.ensure_future(desk.run())
    for _ in range(100):
        if desk.connected or any("key" in d.lower() for _, d in states):
            break
        await asyncio.sleep(0.02)
    return desk, task, states


def test_key_is_made_once_and_kept(make_agent, tmp_path):
    first = make_agent().key
    assert len(first) == 19 and first.count("-") == 3
    assert make_agent().key == first


def test_app_pairs_gets_state_and_moves(make_agent):
    async def scenario():
        agent = make_agent()
        server, port = await started(agent)
        desk, task, _ = await connect(port, agent.key)
        assert desk.connected and desk.position == 1450
        assert desk.settings["presets"][1]["name"] == "Stand"
        await desk.move_to(3000)
        await desk.request("preset", index=1)
        await desk.request("move", height_cm=100)
        assert agent.desk.moves == [("move", 3000), ("move", 5380), ("move", 3800)]
        task.cancel()
        server.close()
    run(scenario())


def test_wrong_key_is_refused(make_agent):
    async def scenario():
        agent = make_agent()
        server, port = await started(agent)
        desk, task, states = await connect(port, "WRONG")
        assert not desk.connected
        assert states[-1] == ("disconnected", "Wrong pairing key")
        task.cancel()
        server.close()
    run(scenario())


def test_hold_refreshes_keep_one_target_and_stop_when_they_end(make_agent, monkeypatch):
    monkeypatch.setattr(remote, "HOLD_REFRESH", 0.05)
    monkeypatch.setattr(remote, "HOLD_TIMEOUT", 0.2)

    async def scenario():
        agent = make_agent()
        server, port = await started(agent)
        desk, task, _ = await connect(port, agent.key)
        await desk.hold(1)
        await asyncio.sleep(0.4)
        # Many refreshes, one hold: changing the target would stop the desk.
        assert agent.desk.moves == [("hold", 1)]
        desk._end_hold()  # the app stops refreshing without saying stop, as if its network dropped
        await asyncio.sleep(0.5)
        assert agent.desk.moves == [("hold", 1), ("stop",)]
        task.cancel()
        server.close()
    run(scenario())


def test_hold_stops_when_the_app_disconnects(make_agent):
    async def scenario():
        agent = make_agent()
        server, port = await started(agent)
        desk, task, _ = await connect(port, agent.key)
        await desk.hold(-1)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.2)
        assert agent.desk.moves == [("hold", -1), ("stop",)]
        server.close()
    run(scenario())


def test_settings_are_saved_and_pushed(make_agent, tmp_path):
    async def scenario():
        agent = make_agent()
        server, port = await started(agent)
        desk, task, _ = await connect(port, agent.key)
        pushed = []
        desk.on_settings = pushed.append
        await desk.save_settings(presets=[{"name": "Perch", "height_cm": 95.0}], offset_mm=625.0)
        await asyncio.sleep(0.05)
        assert pushed[-1]["presets"] == [{"name": "Perch", "height_cm": 95.0}]
        assert json.loads((tmp_path / "settings.json").read_text())["offset_mm"] == 625.0
        task.cancel()
        server.close()
    run(scenario())


def test_choose_reports_a_device_that_isnt_a_desk(make_agent, monkeypatch):
    async def not_linak(address, name):
        raise NotLinakError(f"{name} isn't a Linak desk controller.")
    monkeypatch.setattr(agent_module, "verify_desk", not_linak)

    async def scenario():
        agent = make_agent()
        server, port = await started(agent)
        desk, task, _ = await connect(port, agent.key)
        with pytest.raises(NotLinakError, match="Speaker"):
            await desk.choose("CC:DD", "Speaker")
        assert agent.settings["address"] == "AA:BB"
        task.cancel()
        server.close()
    run(scenario())


def test_alexa_moves_go_through_and_nothing_moves_without_a_desk(make_agent):
    async def scenario():
        agent = make_agent(address=None)
        agent.start_desk()
        assert (await agent.handle({"cmd": "move", "height_cm": 100}))["ok"] is False
        agent = make_agent()
        agent.start_desk()
        await asyncio.sleep(0)
        assert (await agent.handle({"cmd": "move", "height_cm": 100}))["ok"]
        assert agent.desk.moves == [("move", 3800)]
    run(scenario())
