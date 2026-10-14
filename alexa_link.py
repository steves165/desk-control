"""Optional link to the Alexa skill: an MQTT connection from the app to AWS IoT Core (see alexa/README.md).

It only runs once alexa/export.py has written the device's certificate and settings to CONFIG_DIR. The PC only makes
an outgoing connection; the skill's Lambda function sends commands through IoT Core and reads two retained messages
the app keeps up to date: whether the app is online, and the desk's state.
"""
import asyncio
import json
import logging
import os
import ssl
import time
from pathlib import Path

import paho.mqtt.client as mqtt

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "DeskControl" / "alexa"
KEEPALIVE = 300
# Commands older than this are dropped, so a slow network can't move the desk long after someone asked.
MAX_AGE = 30
# The skill can move the desk and stop it, nothing else.
ALLOWED = {"move", "stop"}

log = logging.getLogger("alexa")


def configured(folder: Path = CONFIG_DIR) -> bool:
    return all((folder / name).exists() for name in ("alexa.json", "certificate.pem", "private.key", "ca.pem"))


class AlexaLink:
    def __init__(self, handler, folder: Path = CONFIG_DIR):
        self.handler = handler
        self.folder = folder
        self.config = json.loads((folder / "alexa.json").read_text())
        self.loop = asyncio.get_event_loop()
        self.client: mqtt.Client | None = None
        self.connected = False
        self._state: dict | None = None
        self._sent: dict | None = None

    def topic(self, name: str) -> str:
        return f"desk-control/{self.config['thing']}/{name}"

    def start(self) -> None:
        context = ssl.create_default_context(cafile=str(self.folder / "ca.pem"))
        context.load_cert_chain(self.folder / "certificate.pem", self.folder / "private.key")
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.config["thing"], clean_session=True)
        client.tls_set_context(context)
        client.will_set(self.topic("online"), json.dumps({"online": False}), qos=1, retain=True)
        client.reconnect_delay_set(2, 300)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.connect_async(self.config["endpoint"], 8883, keepalive=KEEPALIVE)
        client.loop_start()
        self.client = client
        log.info("alexa: connecting to %s", self.config["endpoint"])

    def stop(self) -> None:
        if not self.client:
            return
        if self.connected:
            self.client.publish(self.topic("online"), json.dumps({"online": False}), qos=1, retain=True).wait_for_publish(3)
        self.client.disconnect()
        self.client.loop_stop()
        self.client = None
        self.connected = False

    def report(self, state: dict) -> None:
        """Publish the desk's state for the skill, when it changes."""
        self._state = state
        if self.connected and state != self._sent:
            self._publish_state()

    # Called on paho's network thread.
    def _on_connect(self, client, userdata, flags, reason, properties) -> None:
        if reason.is_failure:
            log.warning("alexa: connection refused: %s", reason)
            return
        log.info("alexa: connected")
        self.connected = True
        client.subscribe(self.topic("command"), qos=1)
        client.publish(self.topic("online"), json.dumps({"online": True}), qos=1, retain=True)
        if self._state:
            self._publish_state()

    def _on_disconnect(self, client, userdata, flags, reason, properties) -> None:
        self.connected = False
        log.info("alexa: disconnected: %s", reason)

    def _on_message(self, client, userdata, message) -> None:
        try:
            request = json.loads(message.payload)
        except ValueError:
            log.warning("alexa: unreadable command")
            return
        age = time.time() - float(request.get("sent", 0))
        if request.get("cmd") not in ALLOWED or not -MAX_AGE < age < MAX_AGE:
            log.warning("alexa: ignored %s (%.0f s old)", request.get("cmd"), age)
            return
        log.info("alexa: %s", request)
        self.loop.call_soon_threadsafe(self.handler, request)

    def _publish_state(self) -> None:
        state = self._state
        self.client.publish(self.topic("state"), json.dumps({**state, "time": time.time()}), qos=1, retain=True)
        self._sent = state
