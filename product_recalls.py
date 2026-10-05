"""Safety recalls for things in My Stuff that aren't cars: the US Consumer Product Safety Commission's free
recall database (saferproducts.gov). No key. CPSC can't search by model number, so this asks for the
brand's recalls and keeps the ones that name the model, or failing that the same kind of product.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from vehicles import LookupFailed, USER_AGENT

CPSC_URL = "https://www.saferproducts.gov/RestWebServices/Recall"
CACHE_SECONDS = 12 * 3600
LOOKBACK_YEARS = 20
_cache: dict = {}

# Words for the kind of product, so "Samsung fridge" finds "Samsung Recalls Refrigerators".
PRODUCT_WORDS = {
    "fridge": ["refrigerator", "fridge", "freezer"], "refrigerator": ["refrigerator", "fridge"],
    "freezer": ["freezer"], "stove": ["range", "stove", "oven", "cooktop"], "range": ["range", "stove", "oven"],
    "oven": ["oven", "range", "stove"], "cooktop": ["cooktop", "range"], "microwave": ["microwave"],
    "dishwasher": ["dishwasher"], "washer": ["washer", "washing machine"], "dryer": ["dryer"],
    "blender": ["blender"], "toaster": ["toaster"], "air fryer": ["air fryer", "fryer"],
    "coffee": ["coffee", "espresso"], "kettle": ["kettle"], "mixer": ["mixer"], "pressure cooker": ["pressure cooker"],
    "instant pot": ["pressure cooker", "multi-cooker"], "vacuum": ["vacuum"], "heater": ["heater"],
    "dehumidifier": ["dehumidifier"], "humidifier": ["humidifier"], "tv": ["television", "tv"],
    "phone": ["phone"], "laptop": ["laptop", "notebook computer"], "tablet": ["tablet"], "charger": ["charger"],
    "battery": ["battery", "batteries", "power bank"], "speaker": ["speaker"], "router": ["router"],
}
KIND_WORDS = {"tv": ["television", "tv"], "phone": ["phone"], "tablet": ["tablet"], "audio": ["speaker", "soundbar"],
              "network": ["router"], "printer": ["printer"], "monitor": ["monitor"]}


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def product_words(device: dict) -> list[str]:
    text = " ".join(str(device.get(k) or "") for k in ("name", "model", "notes")).lower()
    words = [w for key, ws in PRODUCT_WORDS.items() if re.search(rf"\b{re.escape(key)}s?\b", text) for w in ws]
    return list(dict.fromkeys(words or KIND_WORDS.get(device.get("kind") or "", [])))


def _fetch(make: str, now: float) -> list[dict]:
    key = make.lower()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    start = time.strftime("%Y-%m-%d", time.gmtime(now - LOOKBACK_YEARS * 365 * 86400))
    q = urllib.parse.urlencode({"format": "json", "RecallTitle": make, "RecallDateStart": start})
    req = urllib.request.Request(f"{CPSC_URL}?{q}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LookupFailed(f"CPSC answered HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise LookupFailed("couldn't reach CPSC; try again later") from e
    _cache[key] = (now, data if isinstance(data, list) else [])
    return _cache[key][1]


def recalls(device: dict, now: float | None = None) -> list[dict]:
    """CPSC recalls for this device: 'model' ones name its model number, 'type' ones are the same brand and
    kind of product (worth checking the model on the recall page). Newest first."""
    make = (device.get("make") or "").strip()
    if not make:
        raise LookupFailed("needs the make (brand), e.g. Samsung, GE, Ninja")
    model = _compact(device.get("model") or "")
    words = product_words(device)
    out = []
    for r in _fetch(make, now or time.time()):
        products = r.get("Products") or []
        text = " ".join([r.get("Title") or "", r.get("Description") or ""]
                        + [f"{p.get('Name', '')} {p.get('Model', '')} {p.get('Description', '')}" for p in products])
        listed = [_compact(p.get("Model", "")) for p in products]
        if len(model) >= 4 and (model in _compact(text)
                                or any(len(m) >= 5 and (m in model or model in m) for m in listed)):
            match = "model"  # also "WA49B5205AW/US" for a recall listing WA49B5205AW
        # The kind of product only counts in the title and product names: descriptions say things like
        # "a range of models".
        elif words and any(re.search(rf"\b{re.escape(w)}", " ".join([r.get("Title") or ""]
                                     + [p.get("Name", "") for p in products]), re.I) for w in words):
            match = "type"
        else:
            continue
        out.append({
            "campaign": r.get("RecallNumber", ""),
            "date": (r.get("RecallDate") or "")[:10],
            "component": r.get("Title", ""),
            "summary": ((r.get("Products") or [{}])[0].get("Name") or ""),
            "consequence": "; ".join(h.get("Name", "") for h in r.get("Hazards") or []),
            "remedy": "; ".join(x.get("Name", "") for x in r.get("Remedies") or []),
            "park_it": bool(re.search(r"stop using|unplug", " ".join(x.get("Name", "") for x in r.get("Remedies") or []),
                                      re.I)),
            "url": r.get("URL") or "https://www.cpsc.gov/Recalls",
            "match": match,
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    out.sort(key=lambda x: x["match"] != "model")  # stable: model matches first, each newest first
    return out
