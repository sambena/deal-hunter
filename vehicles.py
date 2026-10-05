"""Cars and trucks someone owns: VIN decoding and safety recalls, from NHTSA's free public APIs.

vPIC (vpic.nhtsa.dot.gov) turns a VIN into make/model/year/trim/engine; the recalls API lists open
campaigns for a make/model/year. Neither needs a key. Only these two fixed hosts are ever called.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "deal-hunter/0.1 (personal vehicle lookup)"
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # 17 characters, never I, O or Q
RECALL_CACHE_SECONDS = 12 * 3600
_recall_cache: dict = {}


class LookupFailed(Exception):
    pass


def clean_vin(vin: str) -> str:
    v = re.sub(r"[\s-]", "", str(vin or "")).upper()
    if not VIN_RE.match(v):
        raise LookupFailed("A VIN is 17 letters and digits (no I, O or Q)")
    return v


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LookupFailed(f"NHTSA answered HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise LookupFailed("couldn't reach NHTSA; try again later") from e


def decode_vin(vin: str) -> dict:
    """Make, model, year, trim and a short spec line for a VIN."""
    v = clean_vin(vin)
    data = _get(f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{v}?format=json")
    r = (data.get("Results") or [{}])[0]
    if not r.get("Make"):
        raise LookupFailed("NHTSA doesn't know this VIN; check it for typos")
    engine = " ".join(x for x in (
        f"{float(r['DisplacementL']):.1f}L" if (r.get("DisplacementL") or "").replace(".", "", 1).isdigit() else "",
        f"V{r['EngineCylinders']}" if r.get("EngineCylinders") else "",
        r.get("FuelTypePrimary") if r.get("FuelTypePrimary") not in (None, "", "Gasoline") else "") if x)
    year = r.get("ModelYear")
    return {
        "vin": v,
        "make": (r.get("Make") or "").title(),
        "model": r.get("Model") or "",
        "year": int(year) if str(year or "").isdigit() else None,
        "trim": r.get("Trim") or "",
        "body": r.get("BodyClass") or "",
        "drive": r.get("DriveType") or "",
        "engine": engine,
        # vPIC returns "0 - VIN decoded clean..." or a warning such as a bad check digit.
        "warning": "" if str(r.get("ErrorCode", "0")).split(",")[0] == "0" else (r.get("ErrorText") or ""),
    }


def recalls(make: str, model: str, year: int | None, now: float | None = None) -> list[dict]:
    """Safety recalls NHTSA lists for this make/model/year (cached for half a day)."""
    if not (make and model and year):
        raise LookupFailed("needs the make, model and year (look up the VIN, or fill them in)")
    now = now or time.time()
    key = (make.lower(), model.lower(), int(year))
    hit = _recall_cache.get(key)
    if hit and now - hit[0] < RECALL_CACHE_SECONDS:
        return hit[1]
    def ask(m: str) -> list:
        q = urllib.parse.urlencode({"make": make, "model": m, "modelYear": int(year)})
        return _get(f"https://api.nhtsa.gov/recalls/recallsByVehicle?{q}").get("results") or []
    try:
        found = ask(model)
    except LookupFailed:
        found = None  # NHTSA rejects models it doesn't know, like "Tacoma TRD"
    if not found and " " in model.strip():
        found = ask(model.split()[0])  # the base model: "Tacoma TRD Off-Road" -> "Tacoma"
    if found is None:
        raise LookupFailed(f"NHTSA doesn't know the model {model!r}")
    out = [{
        "campaign": r.get("NHTSACampaignNumber", ""),
        "date": r.get("ReportReceivedDate", ""),
        "component": r.get("Component", ""),
        "summary": r.get("Summary", ""),
        "consequence": r.get("Consequence", ""),
        "remedy": r.get("Remedy", ""),
        "park_it": str(r.get("parkIt", "")).lower() == "true",  # NHTSA: don't drive it until fixed
        "url": f"https://www.nhtsa.gov/recalls?nhtsaId={urllib.parse.quote(r.get('NHTSACampaignNumber', ''))}",
    } for r in found]
    _recall_cache[key] = (now, out)
    return out
