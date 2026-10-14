"""Tests for the skill's Lambda function, with a fake IoT Core. Run: python -m pytest alexa/lambda"""
import json
import time

import pytest

import handler


class NotFound(Exception):
    pass


class FakeIot:
    class exceptions:
        ResourceNotFoundException = NotFound

    def __init__(self, retained: dict):
        self.retained = retained
        self.published = []

    def get_retained_message(self, topic):
        name = topic.rsplit("/", 1)[1]
        if name not in self.retained:
            raise NotFound(topic)
        return {"payload": json.dumps(self.retained[name]).encode()}

    def publish(self, topic, qos, payload):
        self.published.append((topic, json.loads(payload)))


STATE = {
    "desk": "connected", "height_cm": 76.5, "min_cm": 62.0, "max_cm": 127.0,
    "presets": [{"name": "Stand!", "height_cm": 115.8}, {"name": "Sit", "height_cm": 76.5}],
}


@pytest.fixture
def iot(monkeypatch):
    fake = FakeIot({"online": {"online": True}, "state": STATE})
    monkeypatch.setattr(handler, "_iot", fake)
    return fake


def directive(namespace, name, payload=None, instance=None):
    header = {"namespace": namespace, "name": name, "messageId": "m", "correlationToken": "c", "payloadVersion": "3"}
    if instance:
        header["instance"] = instance
    return {"directive": {
        "header": header,
        "endpoint": {"endpointId": handler.ENDPOINT_ID, "scope": {"type": "BearerToken", "token": "secret"}},
        "payload": payload or {},
    }}


def range_value(result):
    return next(p["value"] for p in result["context"]["properties"] if p["name"] == "rangeValue")


def test_discovery_lists_presets_lowest_first_with_raise_and_lower(iot):
    result = handler.lambda_handler(
        {"directive": {"header": {"namespace": "Alexa.Discovery", "name": "Discover", "messageId": "m",
                                  "payloadVersion": "3"},
                       "payload": {"scope": {"type": "BearerToken", "token": "secret"}}}}, None)
    [endpoint] = result["event"]["payload"]["endpoints"]
    height = next(c for c in endpoint["capabilities"] if c["interface"] == "Alexa.RangeController")
    config = height["configuration"]
    assert config["supportedRange"] == {"minimumValue": 62, "maximumValue": 127, "precision": 1}
    assert [p["rangeValue"] for p in config["presets"]] == [76, 116]
    assert config["presets"][1]["presetResources"]["friendlyNames"][0]["value"]["text"] == "Stand"
    raise_, lower = height["semantics"]["actionMappings"]
    assert raise_["directive"]["payload"] == {"rangeValue": 116}
    assert lower["directive"]["payload"] == {"rangeValue": 76}


def test_discovery_works_before_the_app_has_ever_connected(monkeypatch):
    monkeypatch.setattr(handler, "_iot", FakeIot({}))
    result = handler.handle(
        {"header": {"namespace": "Alexa.Discovery", "name": "Discover", "messageId": "m"}, "payload": {}})
    [endpoint] = result["event"]["payload"]["endpoints"]
    assert "semantics" not in endpoint["capabilities"][1]


def test_preset_value_moves_to_the_exact_preset_height(iot):
    result = handler.lambda_handler(
        directive("Alexa.RangeController", "SetRangeValue", {"rangeValue": 116}, handler.INSTANCE), None)
    assert result["event"]["header"]["name"] == "Response"
    assert result["event"]["header"]["correlationToken"] == "c"
    assert range_value(result) == 116
    [(topic, command)] = iot.published
    assert topic == "desk-control/desk-control-desk/command"
    assert command["cmd"] == "move" and command["height_cm"] == 115.8
    assert abs(command["sent"] - time.time()) < 5


def test_any_height_in_range(iot):
    handler.handle(directive("Alexa.RangeController", "SetRangeValue", {"rangeValue": 100}, handler.INSTANCE)["directive"])
    assert iot.published[0][1]["height_cm"] == 100


def test_out_of_range_is_refused(iot):
    result = handler.handle(
        directive("Alexa.RangeController", "SetRangeValue", {"rangeValue": 140}, handler.INSTANCE)["directive"])
    assert result["event"]["payload"]["type"] == "VALUE_OUT_OF_RANGE"
    assert result["event"]["payload"]["validRange"] == {"minimumValue": 62, "maximumValue": 127}
    assert iot.published == []


def test_adjust_by_default_step_and_by_amount(iot):
    handler.handle(directive("Alexa.RangeController", "AdjustRangeValue",
                             {"rangeValueDelta": 1, "rangeValueDeltaDefault": True}, handler.INSTANCE)["directive"])
    handler.handle(directive("Alexa.RangeController", "AdjustRangeValue",
                             {"rangeValueDelta": -3, "rangeValueDeltaDefault": False}, handler.INSTANCE)["directive"])
    handler.handle(directive("Alexa.RangeController", "AdjustRangeValue",
                             {"rangeValueDelta": -50, "rangeValueDeltaDefault": False}, handler.INSTANCE)["directive"])
    assert [c["height_cm"] for _, c in iot.published] == [86.5, 73.5, 62]


def test_report_state(iot):
    result = handler.handle(directive("Alexa", "ReportState")["directive"])
    assert result["event"]["header"]["name"] == "StateReport"
    assert range_value(result) == 76


@pytest.mark.parametrize("retained", [
    {"online": {"online": False}, "state": STATE},
    {"online": {"online": True}, "state": {**STATE, "desk": "connecting"}},
    {},
])
def test_unreachable_when_the_app_or_desk_is_offline(monkeypatch, retained):
    fake = FakeIot(retained)
    monkeypatch.setattr(handler, "_iot", fake)
    result = handler.handle(
        directive("Alexa.RangeController", "SetRangeValue", {"rangeValue": 100}, handler.INSTANCE)["directive"])
    assert result["event"]["payload"]["type"] == "ENDPOINT_UNREACHABLE"
    assert fake.published == []


def test_unknown_endpoint_and_directive(iot):
    d = directive("Alexa.RangeController", "SetRangeValue", {"rangeValue": 100}, handler.INSTANCE)["directive"]
    d["endpoint"]["endpointId"] = "other"
    assert handler.handle(d)["event"]["payload"]["type"] == "NO_SUCH_ENDPOINT"
    assert handler.handle(directive("Alexa.PowerController", "TurnOn")["directive"])["event"]["payload"]["type"] \
        == "INVALID_DIRECTIVE"


def test_token_is_not_logged(iot, caplog):
    caplog.set_level("INFO")
    handler.lambda_handler(
        {"directive": {"header": {"namespace": "Alexa.Discovery", "name": "Discover", "messageId": "m",
                                  "payloadVersion": "3"},
                       "payload": {"scope": {"type": "BearerToken", "token": "secret"}}}}, None)
    assert "secret" not in caplog.text
