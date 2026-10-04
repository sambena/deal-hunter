"""Import devices from Home Assistant into My hardware.

Home Assistant's REST API has no device list, but its template endpoint can build one: every device
behind an entity, with maker, model, area and which integrations/entity types it has. A rough guess
at each device's kind is made here; the user picks what to import.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

DEVICE_TEMPLATE = """
{%- set ns = namespace(ids=[]) -%}
{%- for s in states -%}{%- set d = device_id(s.entity_id) -%}
{%- if d and d not in ns.ids -%}{%- set ns.ids = ns.ids + [d] -%}{%- endif -%}{%- endfor -%}
[{%- for d in ns.ids -%}{{ {"id": d, "name": device_attr(d, "name_by_user") or device_attr(d, "name"),
  "make": device_attr(d, "manufacturer"), "model": device_attr(d, "model"), "area": area_name(d),
  "integrations": device_attr(d, "identifiers") | map("first") | unique | list,
  "domains": device_entities(d) | map("regex_replace", "[.].*", "") | unique | list,
  "entry_type": device_attr(d, "entry_type")} | tojson }}{{ "," if not loop.last }}{%- endfor -%}]
"""

# Software and virtual "devices" that aren't things you own.
SKIP_INTEGRATIONS = {"sun", "met", "backup", "hassio", "google_translate", "google_assistant", "ping",
                     "proxmoxve", "frigate", "template", "group", "shopping_list", "radio_browser",
                     "forecast_solar", "openai_conversation", "wyoming", "mqtt_statestream", "uptime"}
SKIP_MODELS = re.compile(r"^unifi (wlan|network)$|tts|assistant", re.I)
SKIP_MAKERS = re.compile(r"proxmox server solutions|intel corporate", re.I)  # virtual machines, bare network cards

# (pattern over "make model name", kind, ticked by default)
KIND_RULES = [
    (r"\bipad\b|\btablet\b|galaxy tab|fire hd|kindle", "tablet", True),
    (r"oled\d{2}|qled|smart tv|chromecast|\broku\b|\bshield\b|bravia|vizio|\btcl\b|\btv\b|television", "tv", True),
    (r"nest hub|home mini|nest mini|google home|sonos|echo|homepod|onkyo|denon|yamaha|receiver|soundbar|speaker",
     "audio", True),
    (r"ubiquiti|unifi|netgear|eero|tp-link deco|asus rt|switch.*poe|poe switch|router|access point", "network", True),
    (r"xbox|playstation|ps5|nintendo|steam deck", "console", True),
    (r"printer|envy|laserjet|officejet|epson|brother|canon pixma", "printer", True),
    (r"device_(washer|dryer|refrigerator|dish_washer|oven)|washer|dryer|refrigerator|dishwasher|\boven\b|microwave",
     "appliance", True),
    (r"tesla|model [sy3x]\b|rivian|ford|toyota", "vehicle", False),
    (r"tp-link|leviton|sonoff|kasa|shelly|bhyve|hobeian|aqara|ratgdo|espressif|zigbee|third reality|ecobee"
     r"|smart plug|switch|dimmer|thermostat", "smart home", False),
]


class HAError(Exception):
    pass


def fetch_devices(url: str, token: str) -> list[dict]:
    if not url or not token:
        raise HAError("Add your Home Assistant address and token in Settings > Home Assistant")
    req = urllib.request.Request(url.rstrip("/") + "/api/template",
                                 json.dumps({"template": DEVICE_TEMPLATE}).encode(),
                                 {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise HAError("Home Assistant rejected the token") from e
        raise HAError(f"Home Assistant returned HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise HAError(f"Can't reach Home Assistant at {url} ({e.reason})") from e
    except json.JSONDecodeError as e:
        raise HAError("Home Assistant's answer wasn't a device list") from e


MAKER_NAMES = {"lge": "LG"}


def full_model(make: str, model: str) -> str:
    """"Google Inc." + "Google Nest Hub" -> "Google Nest Hub"; "LGE" + "OLED65B2AUA" -> "LG OLED65B2AUA"."""
    make = re.sub(r",?\s+(inc\.?|corporation|corp\.?|networks|technologies|co\.?,? ltd\.?|ltd\.?|gmbh)$", "",
                  make.strip(), flags=re.I)
    make = MAKER_NAMES.get(make.lower(), make)
    if not model:
        return make
    if make and make.split()[0].lower() in model.lower():
        return model
    return f"{make} {model}".strip()


def clean_model(model: str) -> str:
    # LG ThinQ appends the device type: "T1789EFH_F (DEVICE_WASHER)".
    return re.sub(r"\s*\(DEVICE_[A-Z_]+\)$", "", model or "").strip()


def classify(device: dict, known_names: set[str]) -> dict | None:
    """A candidate for import, or None for software/virtual devices."""
    integrations = set(device.get("integrations") or [])
    make, model, name = device.get("make") or "", device.get("model") or "", (device.get("name") or "").strip()
    if (device.get("entry_type") == "service" or integrations & SKIP_INTEGRATIONS or SKIP_MODELS.search(model)
            or SKIP_MAKERS.search(make)):
        return None
    if not (make or model) and "mobile_app" not in integrations and not re.search(r"ipad|iphone", name, re.I):
        return None  # bare MAC addresses and the like
    text = f"{make} {model} {name}".lower()
    kind, ticked = "other", False
    if "mobile_app" in integrations or re.search(r"\b(iphone|pixel|galaxy s\d)", text):
        kind, ticked = ("tablet" if re.search(r"ipad|tab", text) else "phone"), True
    else:
        # Maker and model first: a smart plug named "Microwave" is still a smart plug.
        for haystack in (f"{make} {model}".lower(), name.lower()):
            match = next(((k, t) for pattern, k, t in KIND_RULES if re.search(pattern, haystack)), None)
            if match:
                kind, ticked = match
                break
    already = name.lower() in known_names
    return {"ref": device.get("id"), "name": name or model, "make": make, "model": clean_model(model),
            "area": device.get("area") or "", "kind": kind, "checked": ticked and not already,
            "already": already}


def candidates(url: str, token: str, known_names: set[str], known_refs: set[str]) -> list[dict]:
    out, seen = [], set()
    for d in fetch_devices(url, token):
        c = classify(d, known_names)
        if not c or c["ref"] in known_refs:
            continue
        key = (c["name"].lower(), c["model"].lower())
        if key in seen:  # e.g. the same gateway reported by two integrations
            continue
        seen.add(key)
        out.append(c)
    order = {k: i for i, k in enumerate(["tv", "phone", "tablet", "audio", "network", "console", "printer",
                                          "appliance", "vehicle", "smart home", "other"])}
    return sorted(out, key=lambda c: (not c["checked"], order.get(c["kind"], 99), c["name"].lower()))
