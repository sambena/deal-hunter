"""Deal Hunter: local dashboard for watching used-hardware listings."""

from __future__ import annotations

import contextvars
import hmac
import json
import mimetypes
import secrets
import threading
import re
import statistics
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import ai
import auth
import db
import hardware
import matching
import homeassistant
import netguard
import poller
import product_recalls
import specs
import vehicles

STATIC_DIR = Path(__file__).resolve().parent / "static"
ROUTES: list[tuple[str, re.Pattern, callable]] = []


def route(method: str, pattern: str):
    def wrap(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn))
        return fn
    return wrap


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# The signed-in person for this request (set by the handler); routes read it through me().
_request = contextvars.ContextVar("deal_hunter_request", default=None)


def me() -> dict:
    """The signed-in user. Outside a request (tests, scripts) that's the first admin."""
    req = _request.get()
    if req and req.get("user"):
        return req["user"]
    return db.query("SELECT * FROM users WHERE id = ?", (db.current_user_id(),))[0]


def is_admin() -> bool:
    return me()["role"] == "admin"


def require_admin() -> None:
    if not is_admin():
        raise HTTPError(403, "only the admin can do that")


# ---- API --------------------------------------------------------------------

@route("GET", "/api/state")
def get_state(body, params):
    return {
        "watches": db.list_watches(),
        "machines": db.list_machines(),
        "settings": db.public_settings(admin=is_admin()),
        "me": auth.public_user(me()),
        "api_port": API_PORT["port"],
        "ai": {**ai.catalog(), "budget": ai.budget(ai.settings_for()),
               "owner": db.query("SELECT name FROM users WHERE id = ?", (db.admin_id(),))[0]["name"]},
        "poller": poller.state,
        "now": time.time(),
    }


@route("POST", "/api/watches")
def create_watch(body, params):
    if not body.get("name") or not body.get("query"):
        raise HTTPError(400, "A watch needs a name and a query")
    limit = me().get("watch_limit")
    if limit is not None and len(db.list_watches()) >= limit:
        raise HTTPError(403, f"watch limit reached ({limit}); delete one or ask Sam for more")
    wid = db.create_watch(body)
    if body.get("check") is False:  # many at once (deal radar): the caller starts one background check after
        return {"id": wid, "new": 0, "errors": []}
    return {"id": wid, **poller.run_one(wid)}


@route("PUT", r"/api/watches/(\d+)")
def update_watch(body, params, wid):
    marked_seen = db.update_watch(int(wid), body)
    if marked_seen is None:
        raise HTTPError(404, "watch not found")
    removed = _tidy_finds(int(wid))
    return {"ok": True, "removed": removed + db.prune_cheapest(db.get_watch(int(wid))), "marked_seen": marked_seen}


def _tidy_finds(watch_id: int) -> int:
    """After an edit: drop finds the watch's excludes or prices now rule out (starred ones stay), and
    re-work "under typical" from what's left, so the removed listings stop skewing it."""
    watch = db.get_watch(watch_id)
    rows = db.query("SELECT id, title, total, status, year, miles FROM listings WHERE watch_id = ?", (watch_id,))
    vehicle = watch.get("kind") == "vehicle"
    gone = [r["id"] for r in rows if r["status"] != "starred" and (
        not matching.still_wanted(r["title"], r["total"], watch)
        or vehicle and not matching.check_vehicle(r["year"], r["miles"], watch)[0])]
    for lid in gone:
        db.execute("DELETE FROM listings WHERE id = ?", (lid,))
    kept = [r for r in rows if r["id"] not in set(gone)]
    for r in kept:
        others = [(k["total"], k["year"]) for k in kept if k["id"] != r["id"] and k["total"] is not None]
        others = matching.vehicle_history(r["year"], others) if vehicle else [t for t, _ in others]
        db.execute("UPDATE listings SET deal_pct = ? WHERE id = ?", (matching.deal_pct(r["total"], others), r["id"]))
    return len(gone)


@route("DELETE", r"/api/watches/(\d+)")
def delete_watch(body, params, wid):
    if not db.delete_watch(int(wid)):
        raise HTTPError(404, "watch not found")
    return {"ok": True}


@route("POST", r"/api/watches/(\d+)/run")
def run_watch(body, params, wid):
    return poller.run_one(int(wid))


@route("POST", "/api/poll")
def poll_all(body, params):
    if is_admin():
        poller.poll_now()  # everyone's watches, on the normal loop
    else:
        poller.poll_user(me()["id"])  # just this person's
    return {"ok": True}


@route("GET", r"/api/watches/(\d+)/history")
def watch_history(body, params, wid):
    """Asking prices a watch has found, by day: for a small chart and "good price vs typical"."""
    watch = db.get_watch(int(wid))
    if not watch:
        raise HTTPError(404, "watch not found")
    try:
        days = max(1, min(365, int(params.get("days", 90))))
    except ValueError:
        days = 90
    since = time.time() - days * 86400
    rows = db.query("""SELECT first_seen, total, status FROM listings WHERE watch_id = ? AND total IS NOT NULL
                       AND first_seen >= ? ORDER BY first_seen""", (watch["id"], since))
    by_day: dict = {}
    for r in rows:
        by_day.setdefault(time.strftime("%Y-%m-%d", time.gmtime(r["first_seen"])), []).append(r["total"])
    points = [{"date": d, "min": min(p), "median": round(statistics.median(p), 2), "count": len(p)}
              for d, p in sorted(by_day.items())]
    typical = matching.typical_price([r["total"] for r in rows])
    live = [r["total"] for r in rows if r["status"] in ("new", "seen", "starred")]
    best = min(live) if live else None
    pct = matching.deal_pct(best, [r["total"] for r in rows]) if best is not None else None
    return {"watch_id": watch["id"], "days": days, "points": points,
            "typical": round(typical, 2) if typical else None,  # null until 5+ prices
            "best_now": best, "best_label": matching.deal_label(pct), "best_pct": pct}


@route("GET", "/api/listings")
def list_listings(body, params):
    where, args = ["w.user_id = ?"], [me()["id"]]
    if params.get("watch"):
        where.append("l.watch_id = ?")
        args.append(int(params["watch"]))
    since = params.get("since_id")
    if since is not None:
        # For apps that alert: only finds added after the last one they saw, oldest first, any status unless asked.
        where.append("l.id > ?")
        args.append(int(since))
    status = params.get("status", "all" if since is not None else "active")
    if status == "active":
        where.append("l.status IN ('new', 'seen', 'starred')")
    elif status != "all":
        where.append("l.status = ?")
        args.append(status)
    order = {
        "newest": "l.first_seen DESC",
        "price": "l.total IS NULL, l.total ASC",
        "deal": "l.deal_pct IS NULL, l.deal_pct DESC",
    }.get(params.get("sort"), "l.id ASC" if since is not None else "l.first_seen DESC")
    sql = f"""SELECT l.*, w.name AS watch_name FROM listings l JOIN watches w ON w.id = l.watch_id
              WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 500"""
    return {"listings": db.query(sql, tuple(args))}


@route("POST", r"/api/listings/(\d+)")
def update_listing(body, params, lid):
    if body.get("status") not in ("new", "seen", "starred", "dismissed"):
        raise HTTPError(400, "bad status")
    changed = db.execute_count("""UPDATE listings SET status = ? WHERE id = ?
                                  AND watch_id IN (SELECT id FROM watches WHERE user_id = ?)""",
                               (body["status"], int(lid), me()["id"]))
    if not changed:
        raise HTTPError(404, "listing not found")
    # Starring, dismissing or restoring frees or takes a place among a watch's cheapest N.
    watch = db.query("SELECT * FROM watches WHERE id = (SELECT watch_id FROM listings WHERE id = ?)", (int(lid),))[0]
    if watch["keep_cheapest"]:
        db.prune_cheapest(watch)
    return {"ok": True}


@route("POST", "/api/listings/mark-seen")
def mark_all_seen(body, params):
    mine = "watch_id IN (SELECT id FROM watches WHERE user_id = ?)"
    if body.get("watch"):
        db.execute(f"UPDATE listings SET status = 'seen' WHERE status = 'new' AND watch_id = ? AND {mine}",
                   (int(body["watch"]), me()["id"]))
    else:
        db.execute(f"UPDATE listings SET status = 'seen' WHERE status = 'new' AND {mine}", (me()["id"],))
    return {"ok": True}


def _ai_prompt(action: str, body: dict, ref: str | None = None) -> tuple[str, dict, dict]:
    """The prompt for an AI button, plus what's needed to use its answer."""
    if action == "judge":
        rows = db.query("""SELECT l.* FROM listings l JOIN watches w ON w.id = l.watch_id
                           WHERE l.id = ? AND w.user_id = ?""", (int(ref or body.get("listing_id")), me()["id"]))
        if not rows:
            raise HTTPError(404, "listing not found")
        watch = db.get_watch(rows[0]["watch_id"])
        machine = db.get_machine(watch["machine_id"]) if watch.get("machine_id") else None
        return (*ai.judge_prompt(rows[0], watch, machine), {"listing": rows[0]})
    if action == "upgrades":
        machine = db.get_machine(int(ref or body.get("machine_id")))
        if not machine:
            raise HTTPError(404, "machine not found")
        return (*ai.upgrades_prompt(machine), {"machine": machine})
    if action == "specs":
        if not str(body.get("text", "")).strip():
            raise HTTPError(400, "Paste some text about the computer first")
        machine = db.get_machine(int(ref or body.get("machine_id")))
        if not machine:
            raise HTTPError(404, "machine not found")
        return (*ai.specs_prompt(machine, body["text"]), {"machine": machine})
    if action == "draft":
        if not str(body.get("description", "")).strip():
            raise HTTPError(400, "Describe what you're looking for")
        return (*ai.draft_prompt(body["description"], db.list_machines()), {})
    if action == "radar":
        gear = radar_gear()
        if not gear:
            raise HTTPError(400, "No devices with a make and model for AI to look at")
        return (*ai.radar_prompt(gear), {"gear": gear})
    raise HTTPError(400, "unknown AI action")


# ---- deal radar -------------------------------------------------------------
# One pass over My Stuff proposing a watch per device: built-in rules for PCs (free), one AI request
# for everything else. Nothing is created until the user ticks and confirms.

RADAR_SKIP_KINDS = {"smart home", "vehicle"}  # cheap gadgets and cars: not what this is for


def radar_gear() -> list[dict]:
    return [m for m in db.list_machines()
            if m.get("kind", "pc") not in db.PARTS_KINDS | RADAR_SKIP_KINDS and m.get("model")]


@route("POST", "/api/radar")
def deal_radar(body, params):
    watched = {w["query"].strip().lower() for w in db.list_watches()}
    items, skipped = [], []
    for m in db.list_machines():
        if m.get("kind", "pc") not in db.PARTS_KINDS:
            continue
        rules = hardware.suggest(m)
        if not rules["platform"]:
            skipped.append({"machine_id": m["id"], "name": m["name"],
                            "why": "needs specs (press Get specs)" if not m["parts"] else rules["explanation"]})
            continue
        usable = [s for s in rules["suggestions"] if s["query"].strip()]  # "RAM: DDR4 or DDR5?" has no query
        picks = [s for s in usable if s["category"] == "cpu"][:1] + [s for s in usable if s["category"] == "ram"][:1]
        if not picks:
            skipped.append({"machine_id": m["id"], "name": m["name"], "why": "already has the best drop-in CPU"})
        for s in picks:
            items.append({**s, "machine_id": m["id"], "machine_name": m["name"], "from": "rules"})
    gear = radar_gear()
    if gear and body.get("use_ai"):
        prompt, schema, _ = _ai_prompt("radar", body)
        names = {m["id"]: m["name"] for m in gear}
        for s in ai.run(ai.settings_for(), "radar", prompt, schema)["suggestions"]:
            if s.get("device_id") in names and s.get("query", "").strip():
                items.append({**{k: v for k, v in s.items() if k != "device_id"}, "machine_id": s["device_id"],
                              "machine_name": names[s["device_id"]], "from": "ai"})
    elif gear:
        skipped += [{"machine_id": m["id"], "name": m["name"], "why": "needs AI (turn it on in Settings)"}
                    for m in gear]
    for it in items:
        it["already"] = it["query"].strip().lower() in watched
    return {"items": items, "skipped": skipped, "gear_count": len(gear)}


@route("POST", "/api/ai/estimate")
def ai_estimate(body, params):
    prompt, schema, _ = _ai_prompt(body.get("action"), body)
    return ai.estimate(ai.settings_for(), body["action"], prompt, schema)


@route("GET", "/api/ai/usage")
def ai_usage(body, params):
    return ai.usage_summary(ai.settings_for())


@route("POST", r"/api/listings/(\d+)/ask-ai")
def ask_ai_listing(body, params, lid):
    prompt, schema, _ = _ai_prompt("judge", body, lid)
    result = ai.run(ai.settings_for(), "judge", prompt, schema)
    note = f"{result['verdict'].upper()}: {result['note']}"
    db.execute("UPDATE listings SET ai_note = ? WHERE id = ?", (note, int(lid)))
    return {"ai_note": note}


@route("POST", "/api/machines")
def create_machine(body, params):
    return {"id": db.save_machine(body)}


@route("PUT", r"/api/machines/(\d+)")
def update_machine(body, params, mid):
    try:
        db.save_machine(body, int(mid))
    except LookupError as e:
        raise HTTPError(404, "device not found") from e
    return {"ok": True}


@route("DELETE", r"/api/machines/(\d+)")
def delete_machine(body, params, mid):
    if not db.delete_machine(int(mid)):
        raise HTTPError(404, "device not found")
    return {"ok": True}


@route("POST", "/api/vin")
def vin_lookup(body, params):
    """Decode a VIN (NHTSA vPIC): make, model, year, trim, body, drive, engine."""
    try:
        return vehicles.decode_vin(body.get("vin", ""))
    except vehicles.LookupFailed as e:
        raise HTTPError(400, str(e)) from e


@route("GET", r"/api/machines/(\d+)/recalls")
def machine_recalls(body, params, mid):
    machine = db.get_machine(int(mid))
    if not machine:
        raise HTTPError(404, "device not found")
    kind = machine.get("kind") or "pc"
    if kind in db.PARTS_KINDS:
        raise HTTPError(400, "recalls are for cars, appliances and other products, not built computers")
    try:
        if kind == "vehicle":  # NHTSA
            found = vehicles.recalls(machine.get("make", ""), machine.get("model", ""), machine.get("year"))
            what = f"{machine.get('year')} {machine.get('make')} {machine.get('model')}"
        else:  # CPSC
            found = product_recalls.recalls(machine)
            what = " ".join(x for x in (machine.get("make"), machine.get("model") or machine.get("name")) if x)
    except vehicles.LookupFailed as e:
        raise HTTPError(400, str(e)) from e
    return {"recalls": found, "vehicle": what, "source": "nhtsa" if kind == "vehicle" else "cpsc"}


@route("POST", r"/api/machines/(\d+)/suggest")
def suggest_upgrades(body, params, mid):
    machine = db.get_machine(int(mid))
    if not machine:
        raise HTTPError(404, "machine not found")
    if machine.get("kind", "pc") in db.PARTS_KINDS:
        rules = hardware.suggest(machine)
    else:
        rules = {"platform": None, "suggestions": [],
                 "explanation": "the built-in rules only know PC parts; use AI or Watch this model"}
    if body.get("use_ai"):
        prompt, schema, _ = _ai_prompt("upgrades", body, mid)
        return {"platform": rules["platform"], "explanation": "suggested by AI",
                "suggestions": ai.run(ai.settings_for(), "upgrades", prompt, schema)["suggestions"]}
    return rules


@route("GET", "/api/import/ha")
def ha_candidates(body, params):
    require_admin()
    s = db.get_settings()
    try:
        devices = homeassistant.candidates(s["ha_url"], s["ha_token"], db.list_machines(), local_ok=is_admin())
    except homeassistant.HAError as e:
        raise HTTPError(400, str(e)) from e
    return {"devices": devices, "kinds": db.DEVICE_KINDS}


@route("POST", "/api/import/ha")
def ha_import(body, params):
    """Add the ticked devices. A computer that matches one already here is linked to it, not added again."""
    require_admin()
    machines = db.list_machines()
    known = {r for m in machines for r in (m.get("source_ref") or "").split()}
    added = linked = 0
    for d in body.get("devices", []):
        refs = [r for r in d.get("refs") or [] if r]
        if refs and set(refs) <= known:
            continue
        match = next((m for m in machines if d.get("match_id") and m["id"] == d["match_id"]), None)
        if match:
            parts = match["parts"] + [p for p in d.get("parts") or [] if p not in match["parts"]]
            mine = (match.get("source_ref") or "").split()
            db.save_machine({**match, "parts": parts,
                             "source_ref": " ".join(dict.fromkeys(mine + refs))}, match["id"])
            linked += 1
            continue
        make, model = homeassistant.split_make_model(d.get("make") or "", d.get("model") or "")
        note = "Imported from Home Assistant" + (f" ({d['area']})" if d.get("area") else "") + "."
        if d.get("computer"):
            note += " Press Get specs to fill in its parts."
        db.save_machine({"name": d.get("name") or model or "Device", "kind": d.get("kind"), "make": make, "model": model,
                         "parts": d.get("parts") or [], "notes": note, "source_ref": " ".join(refs)})
        added += 1
    return {"added": added, "linked": linked}


@route("GET", "/api/specs/commands")
def spec_commands(body, params):
    return {"windows": specs.WINDOWS_COMMAND, "linux": specs.LINUX_COMMAND}


@route("POST", r"/api/machines/(\d+)/specs")
def fill_specs(body, params, mid):
    """Parts from pasted text: the Get specs command's output, or (with AI) anything else."""
    machine = db.get_machine(int(mid))
    if not machine:
        raise HTTPError(404, "machine not found")
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPError(400, "Paste the command's output, or any text describing the computer")
    if specs.looks_like_report(text):
        parts, method = specs.parse_report(text), "report"
    elif body.get("use_ai"):
        prompt, schema, _ = _ai_prompt("specs", body, mid)
        parts, method = ai.run(ai.settings_for(), "specs", prompt, schema)["parts"], "ai"
    else:
        raise HTTPError(422, "need_ai")
    parts = [p for p in parts if str(p.get("model", "")).strip()]
    if not parts:
        raise HTTPError(400, "Couldn't find any parts in that text")
    merged = specs.merge_parts(machine["parts"], parts)
    db.save_machine({**machine, "parts": merged}, machine["id"])
    return {"parts": merged, "method": method, "found": len(parts)}


@route("POST", "/api/ai/draft-watches")
def draft_watches(body, params):
    prompt, schema, _ = _ai_prompt("draft", body)
    return {"suggestions": ai.run(ai.settings_for(), "draft", prompt, schema)["suggestions"]}


@route("PUT", "/api/settings")
def put_settings(body, params):
    # Blank secret fields in the form mean "keep the current value".
    changes = {k: v for k, v in body.items() if not (k in db.SECRET_KEYS and v in ("", None, True))}
    if "check_times" in changes:
        changes["check_times"] = poller.clean_times(changes["check_times"])
    if "check_mode" in changes and changes["check_mode"] not in ("interval", "times"):
        changes.pop("check_mode")
    if "sizes" in changes:  # {"department", "shoe", "top", "pants"}: short strings only
        sizes = changes["sizes"] if isinstance(changes["sizes"], dict) else {}
        changes["sizes"] = {k: " ".join(str(sizes.get(k) or "").split())[:20]
                            for k in ("department", "shoe", "top", "pants") if sizes.get(k)}
    # Addresses the server will fetch: Discord only for webhooks, and public addresses only for members.
    current = db.get_settings()
    try:
        if changes.get("discord_webhook") and changes["discord_webhook"] != current["discord_webhook"]:
            netguard.check_discord(changes["discord_webhook"])
        for key, what in (("ollama_url", "Ollama address"), ("ha_url", "Home Assistant address")):
            if changes.get(key) and changes[key] != current[key]:
                netguard.check(changes[key], local_ok=is_admin(), what=what)
    except netguard.BlockedURL as e:
        raise HTTPError(400, str(e)) from e
    db.update_settings(changes, shared_allowed=is_admin())  # members can only change their own settings
    return db.public_settings(admin=is_admin())


@route("POST", "/api/api-key")
def new_api_key(body, params):
    """Make a new API key (the old one stops working). Shown once; afterwards Settings only says it's set."""
    if not is_admin():
        raise HTTPError(403, "only the admin can do that")
    key = "dh_" + secrets.token_urlsafe(32)
    db.update_settings({"api_key": key})
    return {"api_key": key}


@route("POST", "/api/test-discord")
def test_discord(body, params):
    s = db.get_settings()
    if not s["discord_webhook"]:
        raise HTTPError(400, "No Discord webhook saved")
    try:
        poller.send_discord(s["discord_webhook"], {"content": "Deal Hunter test message: notifications work."})
    except netguard.BlockedURL as e:
        raise HTTPError(400, str(e)) from e
    except OSError as e:
        raise HTTPError(502, "Discord didn't accept the message; check the webhook address") from e
    return {"ok": True}


# ---- accounts -----------------------------------------------------------------
# Sign-in is by email + password. A browser gets an HttpOnly cookie; a phone or script asks for a device
# token (device_name) and sends it as a Bearer header. The first admin sets their password on first visit.

COOKIE = "dh_session"
COOKIE_DAYS = 365
DEVICE_COOKIE = "dh_device"  # "this browser has signed in to this account before" (see auth.device_mark)


def _setup_needed() -> bool:
    return not db.query("SELECT 1 FROM users WHERE role = 'admin' AND password_hash IS NOT NULL")


def _signed_in(user: dict, kind: str, name: str) -> dict:
    token = auth.create_token(user["id"], kind, name)
    out = {"user": auth.public_user(user)}
    if kind == "web":
        out["__set_cookie__"] = token  # the handler turns this into the cookie; never sent to page scripts
        out["__device_mark__"] = auth.device_mark(user["id"])
    else:
        out["token"] = token
    return out


@route("GET", "/api/auth/status")
def auth_status(body, params):
    req = _request.get() or {}
    return {"setup_needed": _setup_needed(),
            "user": auth.public_user(req["user"]) if req.get("user") else None}


@route("POST", "/api/auth/setup")
def auth_setup(body, params):
    """First visit after accounts arrived: the admin picks their name, email and password."""
    req = _request.get() or {}
    if req.get("api_port"):
        raise HTTPError(403, "set up Deal Hunter from its web page")
    if not _setup_needed():
        raise HTTPError(409, "already set up; sign in instead")
    email = str(body.get("email") or "").strip()
    if "@" not in email:
        raise HTTPError(400, "Enter your email address")
    try:
        password = auth.hash_password(str(body.get("password") or ""))
    except auth.AuthError as e:
        raise HTTPError(400, str(e)) from e
    admin = db.admin_id()
    db.execute("UPDATE users SET email = ?, name = ?, password_hash = ? WHERE id = ?",
               (email, str(body.get("name") or "").strip() or email.split("@")[0], password, admin))
    user = db.query("SELECT * FROM users WHERE id = ?", (admin,))[0]
    return _signed_in(user, "web", "web browser")


@route("POST", "/api/auth/login")
def auth_login(body, params):
    req = _request.get() or {}
    try:
        user = auth.sign_in(str(body.get("email") or ""), str(body.get("password") or ""), req.get("address", "?"),
                            req.get("device_mark"))
    except auth.AuthError as e:
        raise HTTPError(429 if "Too many" in str(e) else 401, str(e)) from e
    device = str(body.get("device_name") or "").strip()
    if device or req.get("api_port"):
        return _signed_in(user, "device", device or "app")
    return _signed_in(user, "web", "web browser")


@route("POST", "/api/auth/logout")
def auth_logout(body, params):
    req = _request.get() or {}
    if req.get("token"):
        auth.revoke_token(req["token"])
    return {"ok": True, "__clear_cookie__": True}


@route("GET", "/api/me")
def get_me(body, params):
    return auth.public_user(me())


@route("POST", "/api/me")
def update_me(body, params):
    """Change your name, email or password (the current password is needed for email/password)."""
    user = me()
    sets, args = [], []
    if body.get("name") is not None:
        sets.append("name = ?")
        args.append(str(body["name"]).strip()[:60] or user["name"])
    if body.get("email") or body.get("new_password"):
        if not auth.verify_password(str(body.get("current_password") or ""), user["password_hash"]):
            raise HTTPError(403, "Your current password is wrong")
        if body.get("email"):
            sets.append("email = ?")
            args.append(str(body["email"]).strip())
        if body.get("new_password"):
            try:
                sets.append("password_hash = ?")
                args.append(auth.hash_password(str(body["new_password"])))
            except auth.AuthError as e:
                raise HTTPError(400, str(e)) from e
    if sets:
        db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", (*args, user["id"]))
    return auth.public_user(db.query("SELECT * FROM users WHERE id = ?", (user["id"],))[0])


@route("GET", "/api/tokens")
def list_tokens(body, params):
    """Where you're signed in: browsers and devices, with the one making this request marked."""
    req = _request.get() or {}
    rows = db.query("SELECT id, kind, name, created_at, last_used FROM tokens WHERE user_id = ? ORDER BY last_used DESC",
                    (me()["id"],))
    for r in rows:
        r["current"] = r["id"] == req.get("token_id")
    return {"tokens": rows}


@route("DELETE", r"/api/tokens/(\d+)")
def revoke_token(body, params, tid):
    if not db.execute_count("DELETE FROM tokens WHERE id = ? AND user_id = ?", (int(tid), me()["id"])):
        raise HTTPError(404, "not found")
    return {"ok": True}


# ---- invites and password resets ------------------------------------------------
# The admin makes a one-time link (7 days) and sends it however they like. Opening it lets a friend
# create an account, or lets someone set a new password. Only a hash of the code is stored.

INVITE_DAYS = 7


def _require_admin() -> dict:
    if not is_admin():
        raise HTTPError(403, "only the admin can do that")
    return me()


def _make_link(kind: str, note: str = "", user_id: int | None = None, watch_limit: int | None = None) -> dict:
    code = secrets.token_urlsafe(24)
    expires = time.time() + INVITE_DAYS * 86400
    db.execute("""INSERT INTO invites (code_hash, kind, note, user_id, watch_limit, created_by, created_at, expires_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
               (auth._hash(code), kind, note[:80], user_id, watch_limit, me()["id"], time.time(), expires))
    req = _request.get() or {}
    base = req.get("base_url") or ""
    return {"link": f"{base}/#{kind}={code}", "expires_at": expires}


def _open_invite(code: str) -> dict:
    rows = db.query("SELECT * FROM invites WHERE code_hash = ?", (auth._hash(code or ""),))
    inv = rows[0] if rows else None
    if not inv or inv["used_at"] or inv["expires_at"] < time.time():
        raise HTTPError(410, "This link has expired or was already used; ask for a new one")
    return inv


@route("GET", "/api/auth/link")
def auth_link(body, params):
    """What a link is for, so the page can show the right form."""
    inv = _open_invite(params.get("code", ""))
    out = {"kind": inv["kind"], "note": inv["note"]}
    if inv["kind"] == "reset":
        out["email"] = db.query("SELECT email FROM users WHERE id = ?", (inv["user_id"],))[0]["email"]
    return out


@route("POST", "/api/auth/accept")
def auth_accept(body, params):
    """Use an invite (new account) or reset link (new password), then sign in."""
    req = _request.get() or {}
    try:
        auth.check_rate(req.get("address", "?"))
    except auth.AuthError as e:
        raise HTTPError(429, str(e)) from e
    inv = _open_invite(str(body.get("code") or ""))
    try:
        password = auth.hash_password(str(body.get("password") or ""))
    except auth.AuthError as e:
        raise HTTPError(400, str(e)) from e
    if inv["kind"] == "invite":
        email = str(body.get("email") or "").strip()
        if "@" not in email:
            raise HTTPError(400, "Enter your email address")
        if db.query("SELECT 1 FROM users WHERE lower(email) = lower(?)", (email,)):
            raise HTTPError(409, "That email already has an account; sign in instead")
    if not db.execute_count("UPDATE invites SET used_at = ? WHERE id = ? AND used_at IS NULL", (time.time(), inv["id"])):
        raise HTTPError(410, "This link has expired or was already used; ask for a new one")
    if inv["kind"] == "invite":
        uid = db.execute("""INSERT INTO users (email, name, password_hash, role, watch_limit, created_at)
                            VALUES (?, ?, ?, 'member', ?, ?)""",
                         (email, str(body.get("name") or "").strip()[:60] or email.split("@")[0], password,
                          inv["watch_limit"] if inv["watch_limit"] is not None else db.DEFAULT_WATCH_LIMIT, time.time()))
    else:
        uid = inv["user_id"]
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password, uid))
        db.execute("DELETE FROM tokens WHERE user_id = ?", (uid,))  # a reset signs out everywhere else
    user = db.query("SELECT * FROM users WHERE id = ?", (uid,))[0]
    device = str(body.get("device_name") or "").strip()
    return _signed_in(user, "device" if device else "web", device or "web browser")


# ---- people (admin) -------------------------------------------------------------
# Names, spend and limits only: the admin doesn't see anyone else's watches, finds or devices.

@route("GET", "/api/admin/users")
def admin_users(body, params):
    _require_admin()
    month = ai.month_start()
    users = db.query("""SELECT u.id, u.name, u.email, u.role, u.disabled, u.watch_limit, u.created_at,
                          u.password_hash IS NOT NULL AS has_password,
                          (SELECT COUNT(*) FROM watches w WHERE w.user_id = u.id) AS watches,
                          u.ai_shared, u.ai_allowance, u.ai_daily_cap,
                          (SELECT COALESCE(SUM(cost), 0) FROM ai_usage a
                             WHERE a.user_id = u.id AND a.paid_by = ? AND a.at >= ?) AS ai_spent,
                          (SELECT COUNT(*) FROM ai_usage a WHERE a.user_id = u.id AND a.paid_by = ? AND a.at >= ?)
                             AS ai_today,
                          COALESCE((SELECT json_extract(value, '$') FROM user_settings s
                             WHERE s.user_id = u.id AND s.key = 'ai_source'), 'shared') AS ai_source,
                          (SELECT MAX(last_used) FROM tokens t WHERE t.user_id = u.id) AS last_active
                        FROM users u ORDER BY u.role = 'admin' DESC, u.name""",
                     (db.admin_id(), month, db.admin_id(), ai._day_start()))
    invites = db.query("""SELECT id, kind, note, user_id, watch_limit, created_at, expires_at FROM invites
                          WHERE used_at IS NULL AND expires_at > ? ORDER BY created_at DESC""", (time.time(),))
    return {"users": users, "invites": invites, "default_watch_limit": db.DEFAULT_WATCH_LIMIT}


@route("POST", "/api/admin/invites")
def admin_invite(body, params):
    _require_admin()
    limit = body.get("watch_limit")
    limit = db.DEFAULT_WATCH_LIMIT if limit in (None, "") else max(0, int(limit))
    return _make_link("invite", str(body.get("note") or ""), watch_limit=limit)


@route("DELETE", r"/api/admin/invites/(\d+)")
def admin_cancel_invite(body, params, iid):
    _require_admin()
    if not db.execute_count("DELETE FROM invites WHERE id = ? AND used_at IS NULL", (int(iid),)):
        raise HTTPError(404, "not found")
    return {"ok": True}


@route("PUT", r"/api/admin/users/(\d+)")
def admin_update_user(body, params, uid):
    admin = _require_admin()
    uid = int(uid)
    if not db.query("SELECT 1 FROM users WHERE id = ?", (uid,)):
        raise HTTPError(404, "not found")
    if "disabled" in body:
        if uid == admin["id"]:
            raise HTTPError(400, "you can't disable yourself")
        db.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if body["disabled"] else 0, uid))
        if body["disabled"]:
            db.execute("DELETE FROM tokens WHERE user_id = ?", (uid,))  # signed out everywhere at once
    if "watch_limit" in body:
        limit = body["watch_limit"]
        db.execute("UPDATE users SET watch_limit = ? WHERE id = ?",
                   (None if limit in (None, "") else max(0, int(limit)), uid))
    # Their use of the admin's AI: allowed at all, requests a day, and dollars a month of paid models.
    if "ai_shared" in body:
        db.execute("UPDATE users SET ai_shared = ? WHERE id = ?", (1 if body["ai_shared"] else 0, uid))
    if "ai_daily_cap" in body:
        db.execute("UPDATE users SET ai_daily_cap = ? WHERE id = ?", (max(0, int(body["ai_daily_cap"] or 0)), uid))
    if "ai_allowance" in body:
        db.execute("UPDATE users SET ai_allowance = ? WHERE id = ?",
                   (max(0.0, round(float(body["ai_allowance"] or 0), 2)), uid))
    return {"ok": True}


@route("POST", r"/api/admin/users/(\d+)/reset")
def admin_reset_link(body, params, uid):
    _require_admin()
    rows = db.query("SELECT name FROM users WHERE id = ?", (int(uid),))
    if not rows:
        raise HTTPError(404, "not found")
    return _make_link("reset", f"password reset for {rows[0]['name']}", user_id=int(uid))


# ---- HTTP plumbing ----------------------------------------------------------

SIGNED_OUT_FILES = {"login.html", "login.js", "style.css"}
OPEN_ROUTES = {("GET", "/api/auth/status"), ("POST", "/api/auth/setup"), ("POST", "/api/auth/login"),
               ("GET", "/api/auth/link"), ("POST", "/api/auth/accept"), ("GET", "/api/app/android")}

# The Android app's latest build. The Android project publishes it into a folder (config "downloads_dir";
# on the Frigate box ~/deal-hunter-app mounted read-only) with android.json beside it:
# {"versionName", "versionCode", "sha256"}. Signed-in people can get it, and so can anyone holding a
# working invite or reset link, so an invited friend can install it before they have an account.
DOWNLOADS = {"dir": None}
ANDROID_APK = "deal-hunter.apk"


def _downloads() -> Path:
    return Path(DOWNLOADS["dir"]) if DOWNLOADS["dir"] else db.DB_PATH.parent / "downloads"


def _may_download(params) -> None:
    req = _request.get()
    if req is None or req["user"]:  # signed in (or called from inside the server)
        return
    _open_invite(params.get("code", ""))  # raises 410 for a missing, used or expired link


@route("GET", "/api/app/android")
def android_app(body, params):
    _may_download(params)
    apk = _downloads() / ANDROID_APK
    if not apk.is_file():
        return {"available": False}
    info_path = _downloads() / "android.json"
    try:
        info = json.loads(info_path.read_text()) if info_path.is_file() else {}
    except ValueError:
        info = {}
    st = apk.stat()
    return {"available": True, "url": "/download/android", "version": info.get("versionName") or info.get("version"),
            "version_code": info.get("versionCode"), "sha256": info.get("sha256"), "size": st.st_size,
            "published_at": st.st_mtime}
MAX_BODY = 1_000_000  # bytes; pasted specs and imports are far smaller
SECURITY_HEADERS = [  # for when the site faces the internet
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    # Scripts only from this site (no inline ones), so injected markup can't run code. Listing images come
    # from the shops' sites; inline style attributes are used for image backgrounds and meters.
    ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' https: data:; connect-src 'self'; object-src 'none'; "
                                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
    ("Referrer-Policy", "same-origin"),
]

# The API port (config "api_port") is a second door for other apps on the network: API only, every call
# needs the API key, and it can't read or change settings or keys. The web page's own port is unchanged.
API_PORT_BLOCKED = ("/api/settings", "/api/api-key", "/api/test-discord")
# The old single key acts as the admin, but only for reading and watches: it can't manage people, make
# invite or reset links, or see and end sign-ins. Those need a real sign-in.
LEGACY_KEY_BLOCKED = ("/api/admin/", "/api/tokens")
API_PORT = {"port": None}


class Handler(BaseHTTPRequestHandler):
    api_only = False

    def _identify(self) -> tuple[dict | None, str | None]:
        """Who is asking: a device token (Bearer / X-API-Key), the browser's cookie, or on the API port the
        old single API key (which acts as the first admin until apps move to their own sign-in)."""
        auth_header = self.headers.get("Authorization") or ""
        bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else             (self.headers.get("X-API-Key") or "").strip()
        if bearer:
            user = auth.user_for_token(bearer)
            if user:
                return user, bearer
            legacy = db.get_settings(db.admin_id()).get("api_key") or ""
            if self.api_only and legacy and hmac.compare_digest(bearer, legacy):
                return db.query("SELECT * FROM users WHERE id = ?", (db.admin_id(),))[0], None
            return None, None
        if not self.api_only:
            cookie = SimpleCookie(self.headers.get("Cookie") or "")
            if COOKIE in cookie:
                user = auth.user_for_token(cookie[COOKIE].value)
                if user:
                    return user, cookie[COOKIE].value
        return None, None

    def _address(self) -> str:
        # Cloudflare's header is only trustworthy on the web port, which is reachable only through the tunnel;
        # on the API port a LAN client could set it to dodge the sign-in limit.
        if not self.api_only and self.headers.get("Cf-Connecting-Ip"):
            return self.headers["Cf-Connecting-Ip"]
        return self.client_address[0]

    def _https(self) -> bool:
        return "https" in (self.headers.get("X-Forwarded-Proto") or "") or "https" in (self.headers.get("Cf-Visitor") or "")

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        if not self.path.startswith("/api/state"):
            print(f"{self.address_string()} - {format % args}")

    def _send(self, status: int, body: bytes, ctype: str, headers: list[tuple[str, str]] = ()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in SECURITY_HEADERS:
            self.send_header(k, v)
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload, headers: list[tuple[str, str]] = ()) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json", headers)

    def _cookie(self, value: str, max_age: int, name: str = COOKIE) -> tuple[str, str]:
        secure = "; Secure" if self._https() else ""
        return ("Set-Cookie", f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}")

    def _cookie_value(self, name: str) -> str | None:
        if self.api_only:
            return None
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        return cookie[name].value if name in cookie else None

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        if self.api_only:
            if not url.path.startswith("/api/"):
                return self._json(404, {"error": "API only on this port; see /api.html on the web address"})
            if url.path.startswith(API_PORT_BLOCKED):
                return self._json(403, {"error": "not available through the API port"})
        user, token = self._identify()
        if method == "GET" and url.path == "/download/android" and not self.api_only:
            return self._download(user, parse_qs(url.query).get("code", [""])[0])
        if method == "GET" and not url.path.startswith("/api/"):
            return self._static(url.path, user)
        # Signed out, everything but sign-in answers the same way, so nobody can map the API from outside.
        if not user and (method, url.path) not in OPEN_ROUTES:
            return self._json(401, {"error": "Sign in to Deal Hunter"})
        if user and token is None and url.path.startswith(LEGACY_KEY_BLOCKED):  # the old single key
            return self._json(403, {"error": "the old API key can't do that; sign in instead"})
        for m, pattern, fn in ROUTES:
            match = pattern.match(url.path)
            if m == method and match:
                break
        else:
            return self._json(404, {"error": "not found"})
        if method != "GET" and self.headers.get_content_type() != "application/json":
            # Browsers can send cross-site form/text POSTs without asking first, but not JSON ones.
            return self._json(415, {"error": "send JSON"})
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost"
        req = {"user": user, "token": token, "token_id": user.get("token_id") if user else None,
               "api_port": self.api_only, "address": self._address(), "device_mark": self._cookie_value(DEVICE_COOKIE),
               "base_url": f"{'https' if self._https() else 'http'}://{host}"}
        req_token, user_token = _request.set(req), db.set_user(user["id"] if user else None)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise HTTPError(413, "request too large")
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            result = fn(body, params, *match.groups())
            headers = []
            if isinstance(result, dict) and "__set_cookie__" in result:
                headers.append(self._cookie(result.pop("__set_cookie__"), COOKIE_DAYS * 86400))
            if isinstance(result, dict) and "__device_mark__" in result:
                headers.append(self._cookie(result.pop("__device_mark__"), COOKIE_DAYS * 86400, DEVICE_COOKIE))
            if isinstance(result, dict) and result.pop("__clear_cookie__", None):
                headers.append(self._cookie("", 0))
            self._json(200, result, headers)
        except HTTPError as e:
            self._json(e.status, {"error": str(e)})
        except (ValueError, TypeError) as e:  # bad numbers or JSON from the client
            print(f"bad request to {url.path}: {e!r}")
            self._json(400, {"error": "bad request: a value sent was the wrong type or format"})
        except ai.AIError as e:
            self._json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self._json(500, {"error": "Something went wrong on the server"})  # details stay in the log
        finally:
            _request.reset(req_token)
            db._current_user.reset(user_token)

    def _download(self, user: dict | None, code: str) -> None:
        if not user:
            try:
                _open_invite(code)
            except HTTPError:
                return self._send(401, b"Sign in to Deal Hunter to get the app", "text/plain")
        apk = _downloads() / ANDROID_APK
        if not apk.is_file():
            return self._send(404, b"The Android app hasn't been published yet", "text/plain")
        return self._send(200, apk.read_bytes(), "application/vnd.android.package-archive",
                          [("Content-Disposition", f'attachment; filename="{ANDROID_APK}"')])

    def _static(self, path: str, user: dict | None) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        if not user:
            # Signed out, the site is just its sign-in page: the app, its script and the API docs stay hidden.
            if rel == "index.html":
                rel = "login.html"
            elif rel not in SIGNED_OUT_FILES:
                return self._send(401, b"Sign in to Deal Hunter", "text/plain")
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")


def main() -> None:
    config_path = Path(__file__).resolve().parent / "config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    host = config.get("host", "127.0.0.1")
    port = int(config.get("port", 8780))
    db.conn()
    DOWNLOADS["dir"] = config.get("downloads_dir")
    if "--no-poll" not in sys.argv:
        poller.start()
    if config.get("api_port"):
        API_PORT["port"] = int(config["api_port"])
        api = ThreadingHTTPServer((config.get("api_host", "0.0.0.0"), API_PORT["port"]), ApiHandler)
        threading.Thread(target=api.serve_forever, name="api", daemon=True).start()
        print(f"Deal Hunter API (key required) on port {API_PORT['port']}")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Deal Hunter running at http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}")
    server.serve_forever()


class ApiHandler(Handler):
    api_only = True


if __name__ == "__main__":
    main()
