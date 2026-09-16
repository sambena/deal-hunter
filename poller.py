"""Background loop: runs every watch against its sources and stores new matches."""

from __future__ import annotations

import json
import threading
import time
import traceback
import urllib.request

import db
import matching
from sources import SOURCES, SourceError, USER_AGENT

state = {"running": False, "last_cycle": None, "next_cycle": None, "source_errors": {}}
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
    req = urllib.request.Request(webhook, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    urllib.request.urlopen(req, timeout=15).close()


def run_watch(watch: dict, settings: dict) -> dict:
    """Poll one watch. Returns {new: int, errors: [..]}."""
    history = [r["total"] for r in db.query(
        "SELECT total FROM listings WHERE watch_id = ? AND total IS NOT NULL", (watch["id"],))]
    polled = set(watch["polled_sources"])
    enabled = settings["sources_enabled"]
    new_rows, errors = [], []
    for name in watch["sources"]:
        if not enabled.get(name) or name not in SOURCES:
            continue
        try:
            items = SOURCES[name](watch, settings)
            state["source_errors"].pop(name, None)
        except SourceError as e:
            errors.append(f"{name}: {e}")
            state["source_errors"][name] = str(e)
            continue
        # A source's first successful check finds everything already listed;
        # store those but don't send them to Discord.
        first_from_source = name not in polled
        polled.add(name)
        for it in items:
            total = None
            if it["price"] is not None:
                total = round(it["price"] + (it["shipping"] or 0), 2)
            ok, _ = matching.check(it.get("match_text") or it["title"], watch, settings["junk_terms"],
                                   total, it.get("text", ""))
            if not ok:
                continue
            exists = db.query("SELECT 1 FROM listings WHERE watch_id = ? AND source = ? AND source_id = ?",
                              (watch["id"], it["source"], it["source_id"]))
            if exists:
                continue
            pct = matching.deal_pct(total, history)
            lid = db.execute("""INSERT INTO listings (watch_id, source, source_id, title, price, shipping, total,
                currency, url, image, location, condition, buying, first_seen, status, deal_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (watch["id"], it["source"], it["source_id"], it["title"], it["price"], it["shipping"], total,
                 it["currency"], it["url"], it["image"], it["location"], it["condition"], it["buying"],
                 time.time(), "new", pct))
            if total is not None:
                history.append(total)
            new_rows.append({**it, "id": lid, "total": total, "deal_pct": pct, "backlog": first_from_source})
    db.execute("UPDATE watches SET last_polled = ?, last_error = ?, polled_sources = ? WHERE id = ?",
               (time.time(), "; ".join(errors) or None, json.dumps(sorted(polled)), watch["id"]))
    to_send = [r for r in new_rows if not r["backlog"]]
    if settings["discord_deals_only"]:
        to_send = [r for r in to_send if matching.deal_label(r["deal_pct"])]
    if to_send and settings["discord_enabled"] and settings["discord_webhook"]:
        try:
            notify_discord(settings, watch, to_send)
        except Exception as e:  # noqa: BLE001 - a notification failure must not stop polling
            errors.append(f"discord: {e}")
    return {"new": len(new_rows), "errors": errors}


def run_all() -> None:
    with _run_lock:
        state["running"] = True
        try:
            settings = db.get_settings()
            for w in db.list_watches():
                if w["enabled"]:
                    try:
                        run_watch(w, settings)
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
                        db.execute("UPDATE watches SET last_error = ? WHERE id = ?",
                                   ("internal error, see console", w["id"]))
        finally:
            state["running"] = False
            state["last_cycle"] = time.time()


def run_one(watch_id: int) -> dict:
    with _run_lock:
        w = db.get_watch(watch_id)
        if not w:
            return {"new": 0, "errors": ["watch not found"]}
        return run_watch(w, db.get_settings())


def _loop() -> None:
    while True:
        run_all()
        minutes = max(5, int(db.get_settings()["poll_minutes"]))
        state["next_cycle"] = time.time() + minutes * 60
        _wake.wait(minutes * 60)
        _wake.clear()


def poll_now() -> None:
    _wake.set()


def start() -> None:
    threading.Thread(target=_loop, name="poller", daemon=True).start()
