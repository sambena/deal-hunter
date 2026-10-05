"""Rule-based listing filter and deal scoring (the "dummy mode" brain).

Query syntax, typed into a watch:
    10980xe                 every plain term must appear
    9980xe|10980xe          any one of the alternatives
    "ddr4 32gb"             quoted phrase
    -ecc                    must NOT appear (same as adding it to Exclude)

Matching ignores case, punctuation and spacing, so "10980xe" matches
"i9-10980 XE" and "4x32gb" matches "4 x 32GB".
"""

from __future__ import annotations

import re
import shlex
import statistics


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def parse_query(query: str) -> tuple[list[list[str]], list[str]]:
    """Return (include groups, excludes). Each group is a list of alternatives."""
    try:
        tokens = shlex.split(query)
    except ValueError:  # unbalanced quote
        tokens = query.replace('"', " ").split()
    groups, excludes = [], []
    for tok in tokens:
        if tok.startswith("-") and len(tok) > 1:
            excludes.append(tok[1:])
        else:
            alts = [a for a in tok.split("|") if a]
            if alts:
                groups.append(alts)
    return groups, excludes


def _contains(haystack_norm: str, haystack_compact: str, term: str) -> bool:
    tn = normalize(term)
    if not tn:
        return True
    if re.search(rf"(?<![a-z0-9]){re.escape(tn)}(?![a-z0-9])", haystack_norm):
        return True
    # Fall back to spacing-insensitive match only for terms with digits (model numbers),
    # so "rtx" doesn't match inside "vertx" but "10980xe" matches "10980 xe".
    tc = compact(term)
    if not (any(c.isdigit() for c in tc) and len(tc) >= 4 and tc in haystack_compact):
        return False
    # The joined-up term must start and end on word edges: "10980 xe" is "10980xe",
    # but "RX 5700 XT" is not "5700x".
    words = haystack_norm.split()
    for i in range(len(words)):
        joined = ""
        for w in words[i:]:
            joined += w
            if joined == tc:
                return True
            if not tc.startswith(joined):
                break
    return False


def search_terms(query: str) -> str:
    """Plain keywords for sites' own search boxes (first alternative of each group)."""
    groups, _ = parse_query(query)
    return " ".join(g[0] for g in groups)


# ---- vehicles -------------------------------------------------------------------

# For car and truck watches the hardware junk list doesn't fit ("as is" is how most dealers sell); these
# are what make a vehicle listing not a vehicle for sale. Salvage/rebuilt titles are shown, not hidden.
VEHICLE_JUNK = ["parts only", "parting out", "part out", "for parts", "not running", "doesn't run",
                "does not run", "non running", "wtb", "want to buy", "looking for", "iso"]
YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")
MILES_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k)?\s*(?:miles|mi\b|odometer)", re.I)
TITLE_STATUS = (("salvage", "salvage"), ("rebuilt", "rebuilt"), ("rebuild title", "rebuilt"),
                ("branded title", "branded"), ("lemon", "branded"), ("flood", "salvage"))


# Body styles aren't in listing titles ("2005 Ford F-150 XLT"), so for a vehicle watch a body-style word
# matches the common models of that style, and sources that can filter by body style do so themselves.
BODY_STYLES = {"pickup": "pickup", "pickups": "pickup", "truck": "pickup", "trucks": "pickup", "suv": "suv",
               "suvs": "suv", "van": "van", "vans": "van", "minivan": "minivan", "sedan": "sedan",
               "coupe": "coupe", "convertible": "convertible", "hatchback": "hatchback", "wagon": "wagon"}
BODY_MODELS = {
    "pickup": ["pickup", "truck", "f-150", "f150", "f-250", "f250", "f-350", "f350", "silverado", "sierra", "ram",
               "1500", "2500", "3500", "tacoma", "tundra", "ranger", "colorado", "canyon", "frontier", "titan",
               "ridgeline", "gladiator", "s-10", "s10", "dakota", "maverick", "santa cruz", "avalanche", "f-100",
               "c10", "k10", "cybertruck", "lightning", "rivian"],
    "suv": ["suv", "4runner", "tahoe", "suburban", "explorer", "expedition", "highlander", "pilot", "cr-v", "crv",
            "rav4", "equinox", "traverse", "durango", "grand cherokee", "cherokee", "wrangler", "bronco", "forester",
            "outback", "pathfinder", "rogue", "cx-5", "cx-9", "tucson", "santa fe", "sorento", "telluride", "yukon",
            "escalade", "land cruiser", "sequoia", "edge", "escape", "acadia", "x5", "q5", "model y", "model x"],
    "van": ["van", "sprinter", "transit", "express", "savana", "promaster", "econoline", "e-150", "e-250"],
    "minivan": ["minivan", "odyssey", "sienna", "pacifica", "caravan", "grand caravan", "carnival", "sedona", "quest"],
    "sedan": ["sedan"], "coupe": ["coupe"], "convertible": ["convertible"], "hatchback": ["hatchback"],
    "wagon": ["wagon"],
}


def body_style(query: str) -> tuple[str | None, str]:
    """('pickup', 'ford') for "ford pickup": the body style asked for, and the rest of the search words."""
    groups, _ = parse_query(query)
    body, rest = None, []
    for g in groups:
        styles = {BODY_STYLES.get(a.lower()) for a in g} - {None}
        if styles and body is None:
            body = styles.pop()
        else:
            rest.append(g[0] if len(g) == 1 else "|".join(g))
    return body, " ".join(rest)


def vehicle_year(title: str) -> int | None:
    """The model year, which listings put first: "2018 Toyota Tacoma TRD"."""
    m = YEAR_RE.search(title or "")
    return int(m.group(1)) if m else None


def vehicle_miles(text: str) -> int | None:
    m = MILES_RE.search(text or "")
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    miles = int(n * 1000) if m.group(2) or n < 1000 and "." in m.group(1) else int(n)
    return miles if miles >= 100 else None  # "5 miles from campus" is a distance, not an odometer


def title_status(text: str) -> str:
    low = (text or "").lower()
    return next((label for word, label in TITLE_STATUS if word in low), "")


def check_vehicle(year: int | None, miles: int | None, watch: dict) -> tuple[bool, str]:
    """A vehicle watch's year range and mileage. A vehicle for sale names its model year, so a listing
    without one (floor mats, a wanted ad) isn't one; unknown miles pass (not every seller states them)."""
    if year is None:
        return False, "no model year"
    if year is not None:
        if watch.get("year_min") and year < watch["year_min"]:
            return False, "older than wanted"
        if watch.get("year_max") and year > watch["year_max"]:
            return False, "newer than wanted"
    if miles is not None and watch.get("max_miles") and miles > watch["max_miles"]:
        return False, "too many miles"
    return True, ""


# ---- clothes and shoes ------------------------------------------------------------

LETTER_SIZES = {"xxs": "XXS", "xs": "XS", "extra small": "XS", "s": "S", "small": "S", "m": "M", "medium": "M",
                "l": "L", "large": "L", "xl": "XL", "x-large": "XL", "extra large": "XL", "xxl": "XXL", "2xl": "XXL",
                "xx-large": "XXL", "xxxl": "XXXL", "3xl": "XXXL"}
# "Size 10.5", "sz 10 1/2", "US 10.5", "10.5M", "W32 L30", "32x30", "size M"
_SIZE_RE = re.compile(
    r"\b(?:size|sz|us|uk|eu)\s*[:#]?\s*(\d{1,2}(?:\.5| 1/2|½)?|xxs|xs|s|m|l|xl|xxl|xxxl|[23]xl)\b"
    r"|\b(\d{1,2}(?:\.5)?)\s*(?:m|w|men'?s|women'?s)\b"
    r"|\bw\s?(\d{2})\s*l\s?(\d{2})\b|\b(\d{2})\s*x\s*(\d{2})\b", re.I)


def norm_size(size: str | None) -> str:
    """One spelling per size: "10 1/2" -> "10.5", "medium" -> "M", "W32 L30" -> "32x30"."""
    s = " ".join(str(size or "").lower().replace("½", ".5").split())
    if not s:
        return ""
    s = re.sub(r"(\d+) 1/2", r"\1.5", s)
    m = re.fullmatch(r"w?\s?(\d{2})\s*(?:x|l|/)\s*l?\s?(\d{2})", s)
    if m:
        return f"{m.group(1)}x{m.group(2)}"
    if s in LETTER_SIZES:
        return LETTER_SIZES[s]
    m = re.fullmatch(r"(\d{1,2}(?:\.5)?)(?:\.0)?\s*(?:m|w|us)?", s)
    return m.group(1) if m else s.upper()


def title_size(text: str) -> str:
    """The size a listing title states, if any ("" when it doesn't say)."""
    m = _SIZE_RE.search(text or "")
    if not m:
        return ""
    if m.group(1):
        return norm_size(m.group(1))
    if m.group(2):
        return norm_size(m.group(2))
    a, b = (m.group(3), m.group(4)) if m.group(3) else (m.group(5), m.group(6))
    return f"{a}x{b}"


def check_size(listing_size: str | None, watch: dict) -> tuple[bool, str]:
    """A clothing watch's size: a listing in another size is out; one that doesn't say stays."""
    want = norm_size(watch.get("size"))
    have = norm_size(listing_size)
    if want and have and want != have:
        return False, f"size {have}, not {want}"
    return True, ""


def check(title: str, watch: dict, junk_terms: list[str], total: float | None,
          extra_text: str = "") -> tuple[bool, str]:
    """Decide whether a listing belongs to a watch. Returns (ok, reason if rejected)."""
    if watch.get("kind") == "vehicle":
        junk_terms = VEHICLE_JUNK
    groups, q_excludes = parse_query(watch["query"])
    if watch.get("kind") == "vehicle":  # "pickup" also matches an F-150 or a Silverado
        groups = [g + BODY_MODELS[BODY_STYLES[a.lower()]] if any(a.lower() in BODY_STYLES for a in g) else g
                  for g in groups for a in g[:1]]
    hn, hc = normalize(title), compact(title)
    for group in groups:
        if not any(_contains(hn, hc, alt) for alt in group):
            return False, f"missing '{'|'.join(group[:3])}'"
    full = f"{title} {extra_text}"
    fn, fc = normalize(full), compact(full)
    for term in [*q_excludes, *watch.get("exclude", [])]:
        if _contains(hn, hc, term):
            return False, f"excluded '{term}'"
    for term in junk_terms:
        # Junk phrases are checked against title + description.
        if _contains(fn, fc, term):
            return False, f"junk '{term}'"
    if total is not None:
        if watch.get("max_price") is not None and total > watch["max_price"]:
            return False, "over max price"
        if watch.get("min_price") is not None and total < watch["min_price"]:
            return False, "under min price"
    return True, ""


def still_wanted(title: str, total: float | None, watch: dict) -> bool:
    """For finds already stored when a watch is edited: do its excludes and price limits still allow it?
    (The query itself isn't re-checked: some sources match on text that isn't stored.)"""
    _, q_excludes = parse_query(watch["query"])
    hn, hc = normalize(title), compact(title)
    if any(_contains(hn, hc, t) for t in [*q_excludes, *watch.get("exclude", [])]):
        return False
    if total is not None:
        if watch.get("max_price") is not None and total > watch["max_price"]:
            return False
        if watch.get("min_price") is not None and total < watch["min_price"]:
            return False
    return True


def vehicle_history(year: int | None, history: list[tuple[float, int | None]]) -> list[float]:
    """Prices to compare a vehicle with: the watch's finds within two model years, when there are enough."""
    near = [t for t, y in history if year is not None and y is not None and abs(y - year) <= 2]
    return near if len(near) >= 5 else [t for t, _ in history]


def typical_price(history: list[float]) -> float | None:
    """The usual asking price: the median of a watch's prices with the extremes trimmed (needs 5+)."""
    prices = sorted(p for p in history if p and p > 0)
    if len(prices) < 5:
        return None
    # Trim the extremes so a $1 auction or a $9999 troll listing doesn't skew it.
    cut = len(prices) // 10
    return statistics.median(prices[cut:len(prices) - cut] if cut else prices)


def deal_pct(total: float | None, history: list[float]) -> float | None:
    """How far below the typical asking price this is, as a fraction (0.2 = 20% under).

    Needs a few prior prices for the same watch; returns None until then.
    """
    if total is None:
        return None
    median = typical_price(history)
    return round((median - total) / median, 3) if median else None


def deal_label(pct: float | None) -> str:
    if pct is None:
        return ""
    if pct >= 0.25:
        return "great"
    if pct >= 0.10:
        return "good"
    return ""
