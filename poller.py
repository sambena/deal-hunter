"""Background loop: runs every watch against its sources and stores new matches."""

from __future__ import annotations

import datetime
import json
import re
import threading
import time
import traceback
import urllib.request
import zoneinfo

import db
import netguard
import matching
from sources import SOURCES, SourceError, USER_AGENT, family

state = {"running": False, "last_cycle": None, "next_cycle": None, "source_errors": {},
         "fetches": {"made": 0, "shared": 0}}  # the last full cycle: requests sent vs answered from another watch
_wake = threading.Event()
_run_lock = threading.Lock()


def notify_discord(settings: dict, watch: dict, listings: list[dict]) -> None:
    if not listings:
        return
    embeds = []
    for l in listings[:10]:  # Discord's per-message embed limit
        price = f"${l['total']:.2f}" if l["total"] is not None else "price not stated"
        label = matching.deal_label(l["deal_pct"])
        deal = f" · {round(l['deal_pct'] * 100)}% under typical ({label})" if label else ""
        embed = {
            "title": l["title"][:250],
            "url": l["url"],
            "description": f"**{price}**{deal}\n{l['source']} · {l['condition'] or ''} {l['location'] or ''}".strip(),
            "footer": {"text": f"Watch: {watch['name']}"},
        }
        if l.get("image"):
            embed["thumbnail"] = {"url": l["image"]}
        embeds.append(embed)
    send_discord(settings["discord_webhook"], {"content": f"{len(listings)} new match(es) for **{watch['name']}**",
                                               "embeds": embeds})


def send_discord(webhook: str, payload: dict) -> None:
    netguard.check_discord(webhook)
    req = urllib.request.Request(webhook, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    urllib.request.urlopen(req, timeout=15).close()


# Everything a source's request depends on. Two watches (anyone's) with the same key get the same results,
# so during one pass the request is sent once and the answer reused: friends watching the same card don't
# multiply eBay calls (5000 a day) or KSL requests. Matching, excludes and alerts still run per watch.
FETCH_SETTINGS = ("zip_code", "local_radius_miles", "ebay_marketplace", "deal_max_age_days", "reddit_subs")
# Sources that send the watch's price/condition/auction choices with the request; the rest only send the query.
WATCH_FILTERS = {"ebay": ("min_price", "max_price", "condition", "include_auctions")}
WATCH_FILTERS["ebay_local"] = WATCH_FILTERS["ebay"]
WATCH_FILTERS["craigslist"] = WATCH_FILTERS["offerup"] = ("min_price", "max_price")


# Vehicle watches search sources' car and truck sections, with the year range and mileage.
VEHICLE_FILTERS = ("kind", "year_min", "year_max", "max_miles")
# The sources that can search for cars and trucks; vehicle watches skip the rest (Reddit, Best Buy...).
VEHICLE_SOURCES = {"ebay", "ebay_local", "craigslist", "offerup", "ksl_cars"}
VEHICLE_ONLY = {"ksl_cars"}  # and these only search for vehicles
# Clothes and shoes: where they're sold (no Reddit, Best Buy or car sites); Poshmark only sells clothes.
CLOTHING_SOURCES = {"ebay", "ebay_local", "poshmark", "craigslist", "offerup", "ksl", "slickdeals"}
CLOTHING_ONLY = {"poshmark"}
WATCH_FILTERS["poshmark"] = ("size", "department")


def fetch_key(name: str, watch: dict, settings: dict) -> str:
    vehicle = [watch.get(k) for k in VEHICLE_FILTERS] if watch.get("kind") == "vehicle" else None
    return json.dumps([name, watch.get("query"), [watch.get(k) for k in WATCH_FILTERS.get(name, ())], vehicle,
                       watch.get("fits"), [str(settings.get(k)) for k in FETCH_SETTINGS]])


def vehicle_fit(watch: dict) -> dict | None:
    """A watch linked to one of the owner's vehicles searches for parts that fit it (eBay's fitment check)."""
    if not watch.get("machine_id") or watch.get("kind") == "vehicle":
        return None
    m = db.get_machine(watch["machine_id"])
    if not m or m.get("kind") != "vehicle" or not (m.get("make") and m.get("model") and m.get("year")):
        return None
    return {"year": m["year"], "make": m["make"], "model": m["model"]}


def fetch(name: str, watch: dict, settings: dict, shared: dict | None = None) -> list[dict]:
    """Run one source for a watch, reusing an identical request made earlier in this pass (errors too)."""
    if shared is None:
        return SOURCES[name](watch, settings)
    key = fetch_key(name, watch, settings)
    if key in shared:
        shared["_shared"] = shared.get("_shared", 0) + 1
        hit = shared[key]
        if isinstance(hit, SourceError):
            raise hit
        return [dict(it) for it in hit]
    shared["_made"] = shared.get("_made", 0) + 1
    try:
        shared[key] = SOURCES[name](watch, settings)
    except SourceError as e:
        shared[key] = e
        raise
    return [dict(it) for it in shared[key]]


def drop_reason(why: str) -> str:
    """matching's reason for a rejected listing, grouped the way a person reads them."""
    for start, label in (("missing", "title missing a search word"), ("excluded", "excluded words"),
                         ("junk", "junk words (parts, broken...)"), ("over max price", "over the max price"),
                         ("under min price", "under the min price"), ("no model year", "no model year (parts, ads)"),
                         ("older", "older than wanted"), ("newer", "newer than wanted"),
                         ("too many miles", "too many miles")):
        if why.startswith(start):
            return label
    return why or "other"


def run_watch(watch: dict, settings: dict, shared: dict | None = None) -> dict:
    """Poll one watch. Returns {new: int, errors: [..]}. `shared` holds this pass's requests (see fetch)."""
    fits = vehicle_fit(watch)
    if fits:
        watch = {**watch, "fits": fits}
    vehicle = watch.get("kind") == "vehicle"
    # (price, model year) of everything found so far; vehicles compare with similar model years.
    history = [(r["total"], r["year"]) for r in db.query(
        "SELECT total, year FROM listings WHERE watch_id = ? AND total IS NOT NULL", (watch["id"],))]
    polled = set(watch["polled_sources"])
    enabled = settings["sources_enabled"]
    new_rows, errors = [], []
    # What each site returned this check and why listings were dropped, for "why does it find nothing?"
    diagnosis: dict = {}
    # With a "keep the cheapest N" limit, new finds are stored hidden and only the ones that make the cut
    # become 'new' (so nothing reading the database alerts on one that's about to be hidden).
    limited = bool(watch.get("keep_cheapest"))
    # Local searches go first so an item that is both local and national is stored as local.
    for name in sorted(watch["sources"], key=lambda n: n != "ebay_local"):
        clothing = watch.get("kind") == "clothing"
        if (not enabled.get(name) or name not in SOURCES or (vehicle and name not in VEHICLE_SOURCES)
                or (not vehicle and name in VEHICLE_ONLY)
                or (clothing and name not in CLOTHING_SOURCES) or (not clothing and name in CLOTHING_ONLY)):
            continue
        try:
            items = fetch(name, watch, settings, shared)
            state["source_errors"].pop(name, None)
        except SourceError as e:
            errors.append(f"{name}: {e}")
            state["source_errors"][name] = str(e)
            diagnosis[name] = {"error": str(e)[:300]}
            continue
        diag = diagnosis[name] = {"raw": len(items), "kept": 0, "new": 0, "dropped": {}}

        def drop(reason: str) -> None:
            diag["dropped"][reason] = diag["dropped"].get(reason, 0) + 1
        # A source's first successful check finds everything already listed;
        # store those but don't send them to Discord.
        first_from_source = name not in polled
        polled.add(name)
        for it in items:
            total = None
            if it["price"] is not None:
                total = round(it["price"] + (it["shipping"] or 0), 2)
            ok, why = matching.check(it.get("match_text") or it["title"], watch, settings["junk_terms"],
                                     total, it.get("text", ""))
            if not ok:
                drop(drop_reason(why))
                continue
            year = miles = status = size = None
            if watch.get("kind") == "clothing":
                size = it.get("size") or matching.title_size(it["title"])
                if not matching.check_size(size, watch)[0]:
                    drop("other sizes")
                    continue
            if vehicle:
                text = f"{it['title']} {it.get('text', '')}"
                year = it.get("year") or matching.vehicle_year(it["title"])
                miles = it.get("miles") if it.get("miles") is not None else matching.vehicle_miles(text)
                status = it.get("title_status") or matching.title_status(text)
                ok, why = matching.check_vehicle(year, miles, watch)
                if not ok:
                    drop(drop_reason(why))
                    continue
            fam = family(it["source"])  # e.g. an eBay item found by both the national and local search
            exists = db.query(f"""SELECT id FROM listings WHERE watch_id = ? AND source_id = ?
                                  AND source IN ({','.join('?' * len(fam))})""",
                              (watch["id"], it["source_id"], *fam))
            diag["kept"] += 1
            if exists:
                db.execute("UPDATE listings SET seen_at = ? WHERE id = ?", (time.time(), exists[0]["id"]))
                continue
            diag["new"] += 1
            compare = matching.vehicle_history(year, history) if vehicle else [t for t, _ in history]
            pct = matching.deal_pct(total, compare)
            lid = db.execute("""INSERT INTO listings (watch_id, source, source_id, title, price, shipping, total,
                currency, url, image, location, condition, buying, first_seen, status, deal_pct,
                year, miles, title_status, size, make)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (watch["id"], it["source"], it["source_id"], it["title"], it["price"], it["shipping"], total,
                 it["currency"], it["url"], it["image"], it["location"], it["condition"], it["buying"],
                 time.time(), "pruned" if limited else "new", pct, year, miles, status or None, size or None,
                 (it.get("make") or matching.vehicle_make(it["title"]) or None) if vehicle else None))
            if total is not None:
                history.append((total, year))
            new_rows.append({**it, "id": lid, "total": total, "deal_pct": pct, "backlog": first_from_source})
    db.execute("UPDATE watches SET last_polled = ?, last_error = ?, polled_sources = ?, last_check = ? WHERE id = ?",
               (time.time(), "; ".join(errors) or None, json.dumps(sorted(polled)), json.dumps(diagnosis), watch["id"]))
    if limited and new_rows:
        # Only the cheapest N stay; a new find that didn't make the cut stays hidden and never alerts.
        db.prune_cheapest(watch, {r["id"] for r in new_rows})
        kept = {r["id"] for r in db.query("SELECT id FROM listings WHERE watch_id = ? AND status = 'new'",
                                          (watch["id"],))}
        new_rows = [r for r in new_rows if r["id"] in kept]
    to_send = [r for r in new_rows if not r["backlog"]]
    if settings["discord_deals_only"]:
        to_send = [r for r in to_send if matching.deal_label(r["deal_pct"])]
    if to_send and settings["discord_enabled"] and settings["discord_webhook"]:
        try:
            notify_discord(settings, watch, to_send)
        except Exception as e:  # noqa: BLE001 - a notification failure must not stop polling
            errors.append(f"discord: {e}")
    return {"new": len(new_rows), "errors": errors}


# Best Buy's API terms allow keeping its content for at most 72 hours, so its finds are dropped once
# they haven't been seen in a check for that long (a find that's still listed keeps being refreshed).
RETENTION_SECONDS = {"bestbuy": 72 * 3600}


def purge_expired(now: float | None = None) -> int:
    now = now or time.time()
    removed = 0
    for source, seconds in RETENTION_SECONDS.items():
        removed += db.execute_count("DELETE FROM listings WHERE source = ? AND COALESCE(seen_at, first_seen) < ?",
                                    (source, now - seconds))
    return removed


def run_all() -> None:
    with _run_lock:
        state["running"] = True
        shared: dict = {}
        try:
            for w in db.list_watches(all_users=True):
                if w["enabled"]:
                    _run_as_owner(w, shared)
            purge_expired()
            state["fetches"] = {"made": shared.get("_made", 0), "shared": shared.get("_shared", 0)}
        finally:
            state["running"] = False
            state["last_cycle"] = time.time()


def _run_as_owner(w: dict, shared: dict | None = None) -> dict:
    """Each watch runs with its owner's settings (ZIP, junk words, Discord...)."""
    with db.as_user(w["user_id"]):
        try:
            return run_watch(w, db.get_settings(), shared)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            db.execute("UPDATE watches SET last_error = ? WHERE id = ?", ("internal error, see console", w["id"]))
            return {"new": 0, "errors": ["internal error"]}


def run_one(watch_id: int) -> dict:
    """Check one of the current user's watches now."""
    with _run_lock:
        w = db.get_watch(watch_id)
        if not w:
            return {"new": 0, "errors": ["watch not found"]}
        return run_watch(w, db.get_settings())


def poll_user(user_id: int) -> None:
    """Check one person's watches in the background (a member's "Check now" shouldn't check everyone's)."""
    def work():
        with _run_lock:
            with db.as_user(user_id):
                shared: dict = {}
                for w in db.list_watches():
                    if w["enabled"]:
                        _run_as_owner(w, shared)
    threading.Thread(target=work, name=f"poll-user-{user_id}", daemon=True).start()


def clean_times(times) -> list[str]:
    """["7:00", "18:00", "junk"] -> ["07:00", "18:00"]: valid HH:MM, sorted, no repeats."""
    out = set()
    for t in times if isinstance(times, list) else str(times or "").replace(";", ",").split(","):
        m = re.fullmatch(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*", str(t))
        if m:
            out.add(f"{int(m.group(1)):02d}:{m.group(2)}")
    return sorted(out)


def _zone(name: str | None):
    """The time zone to keep set times in: the chosen one, else Mountain, else this machine's own."""
    for key in (name, "America/Denver"):
        try:
            return zoneinfo.ZoneInfo(key)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError, TypeError):
            continue
    return datetime.datetime.now().astimezone().tzinfo


def next_check(settings: dict, now: float | None = None) -> float:
    """When the next full check is due: poll_minutes from now, or the next of the day's set times."""
    now = now or time.time()
    if settings.get("check_mode") == "times":
        times = clean_times(settings.get("check_times"))
        if times:
            tz = _zone(settings.get("check_timezone"))
            local = datetime.datetime.fromtimestamp(now, tz)
            for day in (0, 1):
                for t in times:
                    h, m = map(int, t.split(":"))
                    at = (local + datetime.timedelta(days=day)).replace(hour=h, minute=m, second=0, microsecond=0)
                    if at.timestamp() > now + 30:
                        return at.timestamp()
    return now + max(5, int(settings.get("poll_minutes") or 15)) * 60


def _loop() -> None:
    first = True
    while True:
        try:
            settings = db.get_settings()
            # On set times, a restart waits for the next one instead of checking straight away.
            if not (first and settings.get("check_mode") == "times"):
                run_all()
            due = next_check(db.get_settings())
        except Exception:  # noqa: BLE001 - one bad cycle must not stop the poller for good
            traceback.print_exc()
            due = time.time() + 15 * 60
        first = False
        state["next_cycle"] = due
        _wake.wait(max(1, due - time.time()))
        _wake.clear()


def poll_now() -> None:
    _wake.set()


def start() -> None:
    threading.Thread(target=_loop, name="poller", daemon=True).start()
