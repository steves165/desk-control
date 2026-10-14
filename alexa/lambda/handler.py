"""The Alexa Smart Home skill for Desk Control.

Alexa sends directives here; this function relays them to the Desk Control app on the PC through AWS IoT Core. The app
keeps two retained MQTT messages up to date, which this function reads instead of talking to the PC directly:

- desk-control/<thing>/online: {"online": true}, replaced by its last will ({"online": false}) when it drops off;
- desk-control/<thing>/state: the desk's status, height, range and presets.

Commands go to desk-control/<thing>/command, and the app ignores any that arrive more than 30 seconds late.
"""
import json
import logging
import os
import re
import time
import uuid

import boto3

THING = os.environ.get("THING_NAME", "desk-control-desk")
ENDPOINT_ID = THING
INSTANCE = "Desk.Height"
DEFAULT_STEP_CM = 10
LOCALES = ("en-GB", "en-US")

log = logging.getLogger()
log.setLevel(logging.INFO)
_iot = None


def iot():
    global _iot
    if _iot is None:
        _iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}")
    return _iot


def topic(name: str) -> str:
    return f"desk-control/{THING}/{name}"


def retained(name: str) -> dict | None:
    try:
        return json.loads(iot().get_retained_message(topic=topic(name))["payload"])
    except iot().exceptions.ResourceNotFoundException:
        return None


def send(command: dict) -> None:
    iot().publish(topic=topic("command"), qos=1, payload=json.dumps({**command, "sent": time.time()}))


# --- the desk as Alexa sees it --------------------------------------------------------------------------------------

def desk_range(state: dict | None) -> tuple[int, int]:
    state = state or {}
    return round(state.get("min_cm", 62)), round(state.get("max_cm", 127))


def presets(state: dict | None) -> list[dict]:
    """The app's presets, named so Alexa accepts them (letters, digits and spaces), lowest first."""
    found = []
    for preset in (state or {}).get("presets", []):
        name = re.sub(r"[^\w ]+", " ", preset["name"]).strip()
        if name:
            found.append({"name": name, "height_cm": preset["height_cm"], "value": round(preset["height_cm"])})
    return sorted(found, key=lambda p: p["height_cm"])


def names(*words: str) -> list[dict]:
    return [{"@type": "text", "value": {"text": w, "locale": locale}} for w in words for locale in LOCALES]


def endpoint(state: dict | None) -> dict:
    low, high = desk_range(state)
    saved = [p for p in presets(state) if low <= p["value"] <= high]
    height = {
        "type": "AlexaInterface",
        "interface": "Alexa.RangeController",
        "instance": INSTANCE,
        "version": "3",
        "properties": {"supported": [{"name": "rangeValue"}], "proactivelyReported": False, "retrievable": True},
        "capabilityResources": {"friendlyNames": names("height", "position")},
        "configuration": {
            "supportedRange": {"minimumValue": low, "maximumValue": high, "precision": 1},
            "presets": [{"rangeValue": p["value"], "presetResources": {"friendlyNames": names(p["name"])}}
                        for p in saved],
        },
    }
    if len(saved) >= 2:
        # "Alexa, raise the desk" goes to the highest preset, "lower" to the lowest.
        height["semantics"] = {"actionMappings": [
            {"@type": "ActionsToDirective", "actions": ["Alexa.Actions.Raise"],
             "directive": {"name": "SetRangeValue", "payload": {"rangeValue": saved[-1]["value"]}}},
            {"@type": "ActionsToDirective", "actions": ["Alexa.Actions.Lower"],
             "directive": {"name": "SetRangeValue", "payload": {"rangeValue": saved[0]["value"]}}},
        ]}
    return {
        "endpointId": ENDPOINT_ID,
        "manufacturerName": "Desk Control",
        "friendlyName": "Desk",
        "description": "Standing desk, through Desk Control on your PC",
        "displayCategories": ["OTHER"],
        "capabilities": [
            {"type": "AlexaInterface", "interface": "Alexa", "version": "3"},
            height,
            {"type": "AlexaInterface", "interface": "Alexa.EndpointHealth", "version": "3",
             "properties": {"supported": [{"name": "connectivity"}], "proactivelyReported": False,
                            "retrievable": True}},
        ],
    }


# --- responses ------------------------------------------------------------------------------------------------------

def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.00Z", time.gmtime())


def header(directive: dict, namespace: str, name: str) -> dict:
    h = {"namespace": namespace, "name": name, "messageId": str(uuid.uuid4()), "payloadVersion": "3"}
    if "correlationToken" in directive["header"]:
        h["correlationToken"] = directive["header"]["correlationToken"]
    return h


def properties(height: float) -> list[dict]:
    return [
        {"namespace": "Alexa.RangeController", "instance": INSTANCE, "name": "rangeValue", "value": round(height),
         "timeOfSample": now(), "uncertaintyInMilliseconds": 1000},
        {"namespace": "Alexa.EndpointHealth", "name": "connectivity", "value": {"value": "OK"},
         "timeOfSample": now(), "uncertaintyInMilliseconds": 1000},
    ]


def response(directive: dict, name: str, height: float) -> dict:
    return {"event": {"header": header(directive, "Alexa", name), "endpoint": directive["endpoint"], "payload": {}},
            "context": {"properties": properties(height)}}


def error(directive: dict, kind: str, message: str, **payload) -> dict:
    event = {"header": header(directive, "Alexa", "ErrorResponse"),
             "payload": {"type": kind, "message": message, **payload}}
    if "endpoint" in directive:
        event["endpoint"] = directive["endpoint"]
    return {"event": event}


# --- directives -----------------------------------------------------------------------------------------------------

def reachable_state() -> dict | None:
    """The desk's state if the app is online and connected to the desk, else None."""
    online = retained("online")
    state = retained("state")
    if online and online.get("online") and state and state.get("desk") == "connected":
        return state
    return None


def move(directive: dict, state: dict, target: float) -> dict:
    low, high = desk_range(state)
    if not low <= round(target) <= high:
        return error(directive, "VALUE_OUT_OF_RANGE", f"The desk goes from {low} to {high} cm",
                     validRange={"minimumValue": low, "maximumValue": high})
    # Presets are offered to Alexa in whole centimetres; go to the preset's exact height.
    exact = next((p["height_cm"] for p in presets(state) if p["value"] == round(target)), target)
    send({"cmd": "move", "height_cm": exact})
    return response(directive, "Response", exact)


def handle(directive: dict) -> dict:
    namespace, name = directive["header"]["namespace"], directive["header"]["name"]
    if namespace == "Alexa.Discovery" and name == "Discover":
        return {"event": {"header": header(directive, "Alexa.Discovery", "Discover.Response"),
                          "payload": {"endpoints": [endpoint(retained("state"))]}}}
    if namespace == "Alexa.Authorization" and name == "AcceptGrant":
        return {"event": {"header": header(directive, "Alexa.Authorization", "AcceptGrant.Response"), "payload": {}}}

    if directive.get("endpoint", {}).get("endpointId") != ENDPOINT_ID:
        return error(directive, "NO_SUCH_ENDPOINT", "Unknown device")
    state = reachable_state()
    if state is None:
        return error(directive, "ENDPOINT_UNREACHABLE", "Desk Control isn't running or isn't connected to the desk")
    if namespace == "Alexa" and name == "ReportState":
        return response(directive, "StateReport", state["height_cm"])
    if namespace == "Alexa.RangeController" and directive["header"].get("instance") == INSTANCE:
        payload = directive["payload"]
        if name == "SetRangeValue":
            return move(directive, state, payload["rangeValue"])
        if name == "AdjustRangeValue":
            delta = payload["rangeValueDelta"]
            if payload.get("rangeValueDeltaDefault"):
                delta = DEFAULT_STEP_CM if delta > 0 else -DEFAULT_STEP_CM
            low, high = desk_range(state)
            return move(directive, state, min(max(state["height_cm"] + delta, low), high))
    return error(directive, "INVALID_DIRECTIVE", f"{namespace}.{name} isn't supported")


def lambda_handler(event: dict, context) -> dict:
    directive = event["directive"]
    # The payload's scope holds the user's access token: keep it out of the logs.
    payload = {k: v for k, v in directive.get("payload", {}).items() if k != "scope"}
    log.info("directive %s.%s %s", directive["header"]["namespace"], directive["header"]["name"], json.dumps(payload))
    result = handle(directive)
    log.info("result %s", result["event"]["header"]["name"])
    return result
