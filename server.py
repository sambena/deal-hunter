"""Deal Hunter: local dashboard for watching used-hardware listings."""

from __future__ import annotations

import json
import mimetypes
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import ai
import db
import hardware
import homeassistant
import poller
import specs

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


# ---- API --------------------------------------------------------------------

@route("GET", "/api/state")
def get_state(body, params):
    return {
        "watches": db.list_watches(),
        "machines": db.list_machines(),
        "settings": db.public_settings(),
        "ai": {**ai.catalog(), "budget": ai.budget(db.get_settings())},
        "poller": poller.state,
        "now": time.time(),
    }


@route("POST", "/api/watches")
def create_watch(body, params):
    if not body.get("name") or not body.get("query"):
        raise HTTPError(400, "A watch needs a name and a query")
    wid = db.create_watch(body)
    return {"id": wid, **poller.run_one(wid)}


@route("PUT", r"/api/watches/(\d+)")
def update_watch(body, params, wid):
    db.update_watch(int(wid), body)
    return {"ok": True}


@route("DELETE", r"/api/watches/(\d+)")
def delete_watch(body, params, wid):
    db.delete_watch(int(wid))
    return {"ok": True}


@route("POST", r"/api/watches/(\d+)/run")
def run_watch(body, params, wid):
    return poller.run_one(int(wid))


@route("POST", "/api/poll")
def poll_all(body, params):
    poller.poll_now()
    return {"ok": True}


@route("GET", "/api/listings")
def list_listings(body, params):
    where, args = [], []
    if params.get("watch"):
        where.append("l.watch_id = ?")
        args.append(int(params["watch"]))
    status = params.get("status", "active")
    if status == "active":
        where.append("l.status IN ('new', 'seen', 'starred')")
    elif status != "all":
        where.append("l.status = ?")
        args.append(status)
    order = {
        "newest": "l.first_seen DESC",
        "price": "l.total IS NULL, l.total ASC",
        "deal": "l.deal_pct IS NULL, l.deal_pct DESC",
    }.get(params.get("sort"), "l.first_seen DESC")
    sql = f"""SELECT l.*, w.name AS watch_name FROM listings l JOIN watches w ON w.id = l.watch_id
              {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY {order} LIMIT 500"""
    return {"listings": db.query(sql, tuple(args))}


@route("POST", r"/api/listings/(\d+)")
def update_listing(body, params, lid):
    if body.get("status") not in ("new", "seen", "starred", "dismissed"):
        raise HTTPError(400, "bad status")
    db.execute("UPDATE listings SET status = ? WHERE id = ?", (body["status"], int(lid)))
    return {"ok": True}


@route("POST", "/api/listings/mark-seen")
def mark_all_seen(body, params):
    if body.get("watch"):
        db.execute("UPDATE listings SET status = 'seen' WHERE status = 'new' AND watch_id = ?", (int(body["watch"]),))
    else:
        db.execute("UPDATE listings SET status = 'seen' WHERE status = 'new'")
    return {"ok": True}


def _ai_prompt(action: str, body: dict, ref: str | None = None) -> tuple[str, dict, dict]:
    """The prompt for an AI button, plus what's needed to use its answer."""
    if action == "judge":
        rows = db.query("SELECT * FROM listings WHERE id = ?", (int(ref or body.get("listing_id")),))
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
    raise HTTPError(400, "unknown AI action")


@route("POST", "/api/ai/estimate")
def ai_estimate(body, params):
    prompt, schema, _ = _ai_prompt(body.get("action"), body)
    return ai.estimate(db.get_settings(), body["action"], prompt, schema)


@route("GET", "/api/ai/usage")
def ai_usage(body, params):
    return ai.usage_summary(db.get_settings())


@route("POST", r"/api/listings/(\d+)/ask-ai")
def ask_ai_listing(body, params, lid):
    prompt, schema, _ = _ai_prompt("judge", body, lid)
    result = ai.run(db.get_settings(), "judge", prompt, schema)
    note = f"{result['verdict'].upper()}: {result['note']}"
    db.execute("UPDATE listings SET ai_note = ? WHERE id = ?", (note, int(lid)))
    return {"ai_note": note}


@route("POST", "/api/machines")
def create_machine(body, params):
    return {"id": db.save_machine(body)}


@route("PUT", r"/api/machines/(\d+)")
def update_machine(body, params, mid):
    db.save_machine(body, int(mid))
    return {"ok": True}


@route("DELETE", r"/api/machines/(\d+)")
def delete_machine(body, params, mid):
    db.delete_machine(int(mid))
    return {"ok": True}


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
                "suggestions": ai.run(db.get_settings(), "upgrades", prompt, schema)["suggestions"]}
    return rules


@route("GET", "/api/import/ha")
def ha_candidates(body, params):
    s = db.get_settings()
    try:
        devices = homeassistant.candidates(s["ha_url"], s["ha_token"], db.list_machines())
    except homeassistant.HAError as e:
        raise HTTPError(400, str(e)) from e
    return {"devices": devices, "kinds": db.DEVICE_KINDS}


@route("POST", "/api/import/ha")
def ha_import(body, params):
    """Add the ticked devices. A computer that matches one already here is linked to it, not added again."""
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
        model = homeassistant.full_model(d.get("make") or "", d.get("model") or "")
        note = "Imported from Home Assistant" + (f" ({d['area']})" if d.get("area") else "") + "."
        if d.get("computer"):
            note += " Press Get specs to fill in its parts."
        db.save_machine({"name": d.get("name") or model or "Device", "kind": d.get("kind"), "model": model,
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
        parts, method = ai.run(db.get_settings(), "specs", prompt, schema)["parts"], "ai"
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
    return {"suggestions": ai.run(db.get_settings(), "draft", prompt, schema)["suggestions"]}


@route("PUT", "/api/settings")
def put_settings(body, params):
    # Blank secret fields in the form mean "keep the current value".
    changes = {k: v for k, v in body.items() if not (k in db.SECRET_KEYS and v in ("", None, True))}
    db.update_settings(changes)
    return db.public_settings()


@route("POST", "/api/test-discord")
def test_discord(body, params):
    s = db.get_settings()
    if not s["discord_webhook"]:
        raise HTTPError(400, "No Discord webhook saved")
    poller.send_discord(s["discord_webhook"], {"content": "Deal Hunter test message: notifications work."})
    return {"ok": True}


# ---- HTTP plumbing ----------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        if not self.path.startswith("/api/state"):
            print(f"{self.address_string()} - {format % args}")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        if method == "GET" and not url.path.startswith("/api/"):
            return self._static(url.path)
        for m, pattern, fn in ROUTES:
            match = pattern.match(url.path)
            if m == method and match:
                break
        else:
            return self._json(404, {"error": "not found"})
        if method != "GET" and self.headers.get_content_type() != "application/json":
            # Browsers can send cross-site form/text POSTs without asking first, but not JSON ones.
            return self._json(415, {"error": "send JSON"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            self._json(200, fn(body, params, *match.groups()))
        except HTTPError as e:
            self._json(e.status, {"error": str(e)})
        except (ValueError, TypeError) as e:  # bad numbers or JSON from the client
            self._json(400, {"error": f"bad request: {e}"})
        except ai.AIError as e:
            self._json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
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
    if "--no-poll" not in sys.argv:
        poller.start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Deal Hunter running at http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
