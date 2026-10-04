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


def check(title: str, watch: dict, junk_terms: list[str], total: float | None,
          extra_text: str = "") -> tuple[bool, str]:
    """Decide whether a listing belongs to a watch. Returns (ok, reason if rejected)."""
    groups, q_excludes = parse_query(watch["query"])
    hn, hc = normalize(title), compact(title)
    for group in groups:
        if not any(_contains(hn, hc, alt) for alt in group):
            return False, f"missing '{'|'.join(group)}'"
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


def deal_pct(total: float | None, history: list[float]) -> float | None:
    """How far below the typical asking price this is, as a fraction (0.2 = 20% under).

    Needs a few prior prices for the same watch; returns None until then.
    """
    if total is None:
        return None
    prices = sorted(p for p in history if p and p > 0)
    if len(prices) < 5:
        return None
    # Trim the extremes so a $1 auction or a $9999 troll listing doesn't skew it.
    cut = len(prices) // 10
    trimmed = prices[cut:len(prices) - cut] if cut else prices
    median = statistics.median(trimmed)
    return round((median - total) / median, 3) if median else None


def deal_label(pct: float | None) -> str:
    if pct is None:
        return ""
    if pct >= 0.25:
        return "great"
    if pct >= 0.10:
        return "good"
    return ""
