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

import netguard

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
                     "forecast_solar", "openai_conversation", "wyoming", "mqtt_statestream", "uptime",
                     "rpi_power", "hacs"}
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


def fetch_devices(url: str, token: str, local_ok: bool = True) -> list[dict]:
    """local_ok: the admin's Home Assistant may be on the home network; anyone else's must be public."""
    if not url or not token:
        raise HAError("Add your Home Assistant address and token in Settings > Home Assistant")
    if "?" in url or "#" in url:
        raise HAError("The Home Assistant address can't contain ? or #")
    req = urllib.request.Request(url.strip().rstrip("/") + "/api/template",
                                 json.dumps({"template": DEVICE_TEMPLATE}).encode(),
                                 {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                  "User-Agent": "deal-hunter/0.1 (Home Assistant device import)"})
    try:
        with netguard.urlopen(req, 30, local_ok=local_ok, what="Home Assistant address") as resp:
            return json.loads(resp.read())
    except netguard.BlockedURL as e:
        raise HAError(str(e)) from e
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise HAError("Home Assistant rejected the token") from e
        if e.code == 403:
            # Seen live: a Cloudflare-fronted address answered 403 before Home Assistant saw the request.
            raise HAError("Access was refused (HTTP 403). If the address goes through Cloudflare or another proxy, "
                          "use Home Assistant's local address instead, e.g. http://192.168.1.10:8123") from e
        raise HAError(f"Home Assistant returned HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise HAError(f"Can't reach Home Assistant at {url} ({getattr(e, 'reason', e)})" if local_ok
                      else "Can't reach Home Assistant at that address") from e
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


def split_make_model(make: str, model: str) -> tuple[str, str]:
    """("Google Inc.", "Google Nest Hub") -> ("Google", "Nest Hub"); ("LGE", "OLED65B2AUA") -> ("LG", "OLED65B2AUA")."""
    full = full_model(make, model)
    tidy = full_model(make, "")
    if tidy and full.lower().startswith(tidy.lower() + " "):
        return tidy, full[len(tidy) + 1:]
    return tidy, full if not tidy else model


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
    return {"refs": [device.get("id")], "name": name or model, "make": make, "model": clean_model(model),
            "area": device.get("area") or "", "kind": kind, "checked": ticked and not already,
            "already": already, "parts": []}


# ---- computers ----------------------------------------------------------------------
# Home Assistant doesn't know computers as computers. It sees up to three entries per PC, all named after
# it: the network card the router tracks (maker = the card's vendor, e.g. Lite-On for a Wi-Fi card), a
# ping check, and a wake-on-LAN button. Those are grouped into one PC, with the card kept as a part; its
# CPU/RAM/GPU come later from "Get specs".

NIC_VENDORS = re.compile(r"micro-star|asustek|gigabyte|asrock|dell|lenovo|hewlett|\bhp\b|intel|realtek|lite-on|"
                         r"plugable|killer|rivet|raspberry pi|azurewave|liteon|qualcomm|mediatek|framework", re.I)
MAC_NAME = re.compile(r"^[0-9a-f]{2}([:-][0-9a-f]{2}){5}$", re.I)
GENERIC_WORDS = {"main", "pc", "box", "server", "computer", "gaming", "the", "desktop", "my"}


def computer_signal(device: dict) -> str | None:
    integrations = set(device.get("integrations") or [])
    domains = set(device.get("domains") or [])
    make, model = device.get("make") or "", device.get("model") or ""
    if "ping" in integrations:
        return "ping"
    if not make and not model and domains == {"button"}:
        return "wake"
    if make and not model and domains <= {"device_tracker"} and "mobile_app" not in integrations:
        return "nic"
    return None


def _words(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", name.lower())) - GENERIC_WORDS


def match_machine(name: str, machines: list[dict]) -> dict | None:
    """An existing PC/server this Home Assistant name refers to ("proxmox" -> "Proxmox server")."""
    words = _words(name)
    for m in machines:
        if m.get("kind", "pc") in ("pc", "server") and words and words & _words(m["name"]):
            return m
    return None


def _computers(devices: list[dict], machines: list[dict]) -> tuple[list[dict], set[str]]:
    groups: dict[str, list[tuple[str, dict]]] = {}
    for d in devices:
        sig = computer_signal(d)
        name = (d.get("name") or "").strip()
        if sig and name and not MAC_NAME.match(name):
            groups.setdefault(re.sub(r"\s+", " ", name.lower()), []).append((sig, d))
    # The Pi Home Assistant runs on also reports its board ("Raspberry Pi 4") as a device of its own.
    pi_board = next((d for d in devices if "raspberry_pi" in (d.get("integrations") or [])), None)
    out, used, by_match = [], set(), {}
    for entries in groups.values():
        sigs = {s for s, _ in entries}
        nics = [d for s, d in entries if s == "nic"]
        if sigs == {"nic"} and not all(NIC_VENDORS.search(d["make"]) for d in nics):
            continue  # a tracked network client that isn't obviously a computer
        names = [d["name"].strip() for _, d in entries]
        name = next((n for n in names if n[:1].isupper()), names[0])
        parts = [{"category": "network", "model": f"{tidy_vendor(d['make'])} network adapter"} for d in nics]
        refs = [d["id"] for _, d in entries]
        kind, model = "pc", ""
        if any(re.search(r"raspberry pi", d["make"], re.I) for d in nics):
            kind = "server"
            model = f"Raspberry Pi {pi_board['model']}" if pi_board and pi_board.get("model") else "Raspberry Pi"
            name = "Home Assistant (Raspberry Pi)" if name.lower() == "homeassistant" else name
            if pi_board:
                refs.append(pi_board["id"])
        used.update(refs)
        match = match_machine(name, machines)
        if match and match["id"] in by_match:  # "Bazzite" and "Main Bazzite" are the same box
            merged = by_match[match["id"]]
            merged["refs"] += refs
            merged["parts"] += [p for p in parts if p not in merged["parts"]]
            continue
        cand = {"refs": refs, "name": name, "make": "", "model": model, "area": "", "kind": kind,
                "parts": parts, "checked": True, "already": False, "computer": True,
                "match_id": match["id"] if match else None, "match_name": match["name"] if match else ""}
        if match:
            by_match[match["id"]] = cand
        out.append(cand)
    return out, used


def tidy_vendor(make: str) -> str:
    make = re.sub(r"\s+(network\s+)?communication.*$", "", make.strip(), flags=re.I)
    make = re.sub(r",?\s+(trading\s+)?(inc\.?|corporation|corp\.?|networks|technologies|co\.?,? ltd\.?|ltd\.?|"
                  r"limited|gmbh)$", "", make, flags=re.I)
    return {"micro-star intl": "MSI", "micro-star int'l": "MSI", "intel corporate": "Intel"}.get(make.lower(), make)


def candidates(url: str, token: str, machines: list[dict], local_ok: bool = True) -> list[dict]:
    known_refs = {r for m in machines for r in (m.get("source_ref") or "").split()}
    known_names = {m["name"].lower() for m in machines}
    devices = [d for d in fetch_devices(url, token, local_ok) if d.get("id") not in known_refs]
    out, used = _computers(devices, machines)
    seen = set()
    for d in devices:
        if d.get("id") in used:
            continue
        c = classify(d, known_names)
        if not c:
            continue
        key = (c["name"].lower(), c["model"].lower())
        if key in seen:  # e.g. the same gateway reported by two integrations
            continue
        seen.add(key)
        out.append(c)
    order = {k: i for i, k in enumerate(["pc", "tv", "phone", "tablet", "audio", "network", "console", "printer",
                                          "appliance", "vehicle", "smart home", "other"])}
    return sorted(out, key=lambda c: (not c["checked"], order.get(c["kind"], 99), c["name"].lower()))
