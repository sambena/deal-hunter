"""Records the Python app's answers to a list of API requests, for test/api.test.ts. Made-up people and listings only;
nothing is fetched (a check that would search the shops is refused here).

Usage, from the repository root: python make_api.py <new database file> <issuer>
It builds an old database with the real Python code (db.py, central.py), copies it to <file> before any request (the
test copies that into the new app), then sends every request in REQUESTS through server.py's own routes, with its
privileged check and its own-sign-in switch, at a fixed clock. Prints JSON: {"requests": [...], "answers": [...]}."""
import json
import shutil
import sys
import time
import urllib.parse
from pathlib import Path

path, issuer = Path(sys.argv[1]), sys.argv[2]
work = path.with_suffix(".work.db")

T0 = 1793000000.0
clock = [T0]
_localtime = time.localtime
time.time = lambda: clock[0]
time.localtime = lambda secs=None: _localtime(clock[0] if secs is None else secs)

import ai  # noqa: E402 - after the clock is fixed
import central  # noqa: E402
import db  # noqa: E402
import poller  # noqa: E402
import server  # noqa: E402

db.DB_PATH = work
db.conn()


def no_fetch(*_args, **_kwargs):
    raise AssertionError("a request here would search the shops")


poller.run_one = poller.poll_now = poller.poll_user = no_fetch

OWNER = "00000000-0000-4000-8000-000000000001"    # the framework's dev login people
MEMBER = "00000000-0000-4000-8000-000000000002"
KIM = "00000000-0000-4000-8000-0000000000c3"

admin = db.admin_id()
db.execute("UPDATE users SET name = 'Owner (dev)', email = 'dev-owner@example.test', created_at = ? WHERE id = ?", (T0 - 900, admin))
lee = db.execute("INSERT INTO users (email, name, role, created_at, watch_limit, ai_allowance, ai_daily_cap) VALUES "
                 "('dev-member@example.test', 'Member (dev)', 'member', ?, 3, 1.5, 7)", (T0 - 800,))
kim = db.execute("INSERT INTO users (email, name, role, created_at, watch_limit) VALUES ('kim@example.test', 'Kim', 'member', ?, 10)",
                 (T0 - 700,))
for uid, subject in ((admin, OWNER), (lee, MEMBER), (kim, KIM)):
    central.link(uid, issuer, subject)
central.set_own_login("off")   # the state the new app starts from: central sign-in only

db.update_settings({"poll_minutes": 30, "reddit_subs": ["hardwareswap"], "ebay_client_secret": "made-up",
                    "sources_enabled": {"ebay": True, "bestbuy": True}}, user_id=admin)
db.update_settings({"zip_code": "00000", "junk_terms": ["for parts"], "discord_webhook": "https://discord.com/api/webhooks/1/made-up",
                    "sizes": {"shoe": "10.5"}}, user_id=lee)

step = [0]


def tick():
    step[0] += 1
    clock[0] = T0 - 5000 + step[0] * 37.5


with db.as_user(admin):
    tick(); pc = db.save_machine({"name": "b desk PC", "parts": [{"category": "cpu", "model": "i7-4790K"}]})
    tick(); db.save_machine({"name": "A truck", "kind": "vehicle", "make": "Toyota", "model": "Tacoma", "year": 2018})
    tick(); db.save_machine({"name": "Ünïcode fridge", "kind": "appliance", "make": "Samsung"})
    tick(); gpu = db.create_watch({"name": "RTX 3080", "query": "rtx 3080", "exclude": ["mining"], "max_price": 500,
                                   "keep_cheapest": 3, "machine_id": pc})
    tick(); truck = db.create_watch({"name": "Tacoma", "query": "toyota tacoma", "kind": "vehicle", "year_min": 2016,
                                     "year_max": 2020, "max_miles": 90000})
    tick(); shoes = db.create_watch({"name": "Shoes", "query": "running shoes", "kind": "clothing", "size": "10.5"})
with db.as_user(lee):
    tick(); lamp = db.create_watch({"name": "Lamp", "query": "lamp", "max_price": 40})
with db.as_user(kim):
    tick(); secret = db.create_watch({"name": "Secret", "query": "secret"})


def listing(watch, sid, title, total, **more):
    tick()
    cols = {"watch_id": watch, "source": more.pop("source", "ebay"), "source_id": sid, "title": title, "price": total,
            "shipping": 0.0 if total is not None else None, "total": total, "currency": "USD",
            "url": f"https://shop.example.test/{sid}", "first_seen": clock[0], **more}
    return db.execute(f"INSERT INTO listings ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))


for i, (total, status) in enumerate([(450.0, "new"), (399.99, "seen"), (480.0, "starred"), (None, "new"), (420.5, "dismissed"),
                                     (430.0, "new"), (410.0, "seen"), (499.0, "new")]):
    listing(gpu, f"g{i}", f"RTX 3080 card {i}" + (" mining rig" if i == 6 else ""), total, status=status,
            deal_pct=None if total is None else round((440 - total) / 440, 3), source="ebay" if i % 2 else "ksl")
for i, (year, miles, make) in enumerate([(2018, 61000, "Toyota"), (2015, 50000, "toyota"), (2019, None, "Ford"),
                                         (None, 20000, None), (2020, 95000, "TOYOTA"), (2017, 70000, "Ébay")]):
    listing(truck, f"t{i}", f"{year or ''} Toyota Tacoma {i}", 20000.0 + i * 1500, year=year, miles=miles, make=make,
            source="ksl_cars", status="new" if i % 2 else "seen")
for i, size in enumerate(["10.5", "M", "32x30", None, "9", "1e1", " 7.5", "abc", "0"]):
    listing(shoes, f"s{i}", f"Running shoes {i}", 30.0 + i, size=size, source="poshmark")
listing(lamp, "l0", "Desk lamp", 25.0)
listing(lamp, "l1", "Floor lamp", 45.0, status="seen")
kim_find = listing(secret, "k0", "Secret thing", 10.0)
db.execute("INSERT INTO ai_usage (at, provider, model, action, input_tokens, output_tokens, cost, user_id, paid_by) "
           "VALUES (?, 'claude', 'm', 'judge', 1, 1, 0.25, ?, ?)", (T0 - 10, lee, admin))
db.conn().commit()
shutil.copyfile(work, path)   # the starting point, before any request

R = []


def req(who, method, url, body=None):
    R.append({"who": who, "method": method, "url": url, **({"body": body} if body is not None else {})})


O, M = "owner", "member"
req(O, "GET", "/api/state")
req(M, "GET", "/api/state")
req(O, "GET", "/api/me")
req(M, "GET", "/api/me")
for q in ["", "?sort=price", "?sort=price_desc", "?sort=deal", "?sort=miles", "?sort=year_desc", "?sort=year_asc", "?sort=make",
          "?sort=size", "?sort=newest", "?sort=bogus", f"?watch={gpu}", f"?watch={truck}&sort=make", "?source=ebay,ksl_cars",
          "?source=poshmark&status=all", "?status=starred", "?status=dismissed", "?status=pruned", "?since_id=5",
          "?since_id=5&status=new", f"?watch={secret}", "?watch=abc", "?watch=", f"?watch={shoes}&sort=size&status=all"]:
    req(O, "GET", "/api/listings" + q)
req(M, "GET", "/api/listings")
req(M, "GET", f"/api/listings?watch={gpu}")
for q in ["", "?days=7", "?days=abc", "?days=1000", "?days=0", "?days=1"]:
    req(O, "GET", f"/api/watches/{gpu}/history" + q)
req(O, "GET", f"/api/watches/{truck}/history?days=365")
req(M, "GET", f"/api/watches/{gpu}/history")
req(O, "GET", f"/api/watches/{secret}/history")
req(O, "POST", "/api/watches", {"name": "", "query": "x", "check": False})
req(O, "POST", "/api/watches", {"name": "New", "check": False})
req(O, "POST", "/api/watches", {"name": "New one", "query": "new thing", "max_price": "120.5", "min_price": "", "keep_cheapest": "0",
                                "color": "#ABCDEF", "kind": "vehicle", "year_min": "2010.7", "year_max": 1800, "max_miles": "-5",
                                "category": "home", "size": "  10   1/2  ", "department": "kids", "machine_id": None,
                                "exclude": "not a list", "sources": ["ebay"], "enabled": 0, "check": False})
req(O, "POST", "/api/watches", {"name": "Bad price", "query": "x", "max_price": "cheap", "check": False})
req(O, "POST", "/api/watches", {"name": 5, "query": True, "notes": 1.5, "check": False})
req(O, "POST", "/api/watches", {"name": "Not my device", "query": "x", "machine_id": 999, "keep_cheapest": "lots", "check": False})
req(M, "POST", "/api/watches", {"name": "Second", "query": "second", "check": False})
req(M, "POST", "/api/watches", {"name": "Third", "query": "third", "check": False})
req(M, "POST", "/api/watches", {"name": "Fourth", "query": "fourth", "check": False})
req(M, "POST", "/api/watches", {"name": "Device", "query": "x", "machine_id": pc, "check": False})
req(O, "GET", "/api/state")
req(O, "PUT", f"/api/watches/{gpu}", {"exclude": ["mining", "card 7"], "keep_cheapest": 2})
req(O, "PUT", f"/api/watches/{truck}", {"max_miles": 65000, "year_min": "2016"})
req(O, "PUT", f"/api/watches/{shoes}", {"enabled": False})
req(O, "PUT", f"/api/watches/{shoes}", {"name": "Shoes again", "notes": "just a note"})
req(O, "PUT", f"/api/watches/{secret}", {"name": "Not mine"})
req(O, "PUT", "/api/watches/9999", {"name": "Nothing"})
req(O, "PUT", f"/api/watches/{gpu}", {"max_price": "abc"})
req(O, "GET", "/api/state")
req(O, "GET", "/api/listings?status=all&sort=price")
req(O, "POST", "/api/listings/1", {"status": "wrong"})
req(O, "POST", "/api/listings/2", {"status": "starred"})
req(O, "POST", "/api/listings/3", {"status": "dismissed"})
req(O, "POST", f"/api/listings/{kim_find}", {"status": "starred"})
req(M, "POST", "/api/listings/1", {"status": "seen"})
req(O, "POST", "/api/listings/99999", {"status": "seen"})
req(O, "GET", "/api/listings?status=all&sort=price")
req(O, "POST", "/api/listings/mark-seen", {"watch": truck})
req(O, "POST", "/api/listings/mark-seen", {"watch": "x"})
req(M, "POST", "/api/listings/mark-seen", {})
req(O, "GET", "/api/listings?status=all")
req(M, "GET", "/api/listings?status=all")
req(O, "GET", f"/api/listings?watch={secret}&status=all")
req(O, "DELETE", f"/api/watches/{secret}")
req(O, "DELETE", f"/api/watches/{truck}")
req(O, "DELETE", f"/api/watches/{truck}")
req(M, "PUT", "/api/settings", {"zip_code": "00001", "poll_minutes": 1, "ebay_client_secret": "", "discord_webhook": "",
                                "sizes": {"shoe": "  11  ", "top": "M", "pants": None, "junk": "x"}, "focus": "cars"})
req(M, "PUT", "/api/settings", {"discord_webhook": "https://example.test/hook"})
req(M, "PUT", "/api/settings", {"ollama_url": "http://10.0.0.5:11434"})
req(M, "PUT", "/api/settings", {"ollama_url": "ftp://8.8.8.8"})
req(M, "PUT", "/api/settings", {"ollama_url": "http://8.8.8.8:11434", "sizes": "not a dict"})
req(O, "PUT", "/api/settings", {"poll_minutes": 20, "check_times": "7:00; 18:30, 25:00, 07:00", "check_mode": "never",
                                "ha_url": "http://10.0.0.5:8123", "ebay_client_secret": True, "zip_code": "00002",
                                "ai_provider": "claude", "ai_monthly_limit": 50})
req(O, "PUT", "/api/settings", {"check_mode": "times", "check_times": ["6:05", "bad", 7]})
req(M, "GET", "/api/state")
req(O, "GET", "/api/state")
req(O, "GET", "/api/admin/users")
req(M, "GET", "/api/admin/users")
req(O, "PUT", f"/api/admin/users/{lee}", {"watch_limit": "5", "ai_shared": 0, "ai_daily_cap": "", "ai_allowance": "2.345"})
req(O, "PUT", f"/api/admin/users/{admin}", {"disabled": True})
req(O, "PUT", f"/api/admin/users/{kim}", {"disabled": 1, "watch_limit": ""})
req(O, "PUT", f"/api/admin/users/{kim}", {"watch_limit": "many"})
req(O, "PUT", "/api/admin/users/999", {"disabled": True})
req(M, "PUT", f"/api/admin/users/{lee}", {"watch_limit": 100})
req(O, "GET", "/api/admin/users")
req(O, "GET", "/api/nothing-here")
req(O, "POST", "/api/auth/login", {"email": "x", "password": "y"})
req(O, "GET", "/api/app/android")


def user_row(who):
    uid = admin if who == "owner" else lee
    row = db.query("SELECT * FROM users WHERE id = ?", (uid,))[0]
    return {**row, "token_id": None, "via": "central", "auth_time": clock[0], "amr": '["pwd", "mfa"]',
            "identity_id": None, "sid": None}


def call(r):
    url = urllib.parse.urlparse(r["url"])
    method, body = r["method"], r.get("body") or {}
    for m, pattern, fn in server.ROUTES:
        match = pattern.match(url.path)
        if m == method and match:
            break
    else:
        return {"status": 404, "message": "not found"}
    user = user_row(r["who"])
    token = server._request.set({"user": user, "token": None, "token_id": None, "api_port": False, "address": "127.0.0.1",
                                 "device_mark": None, "base_url": "http://127.0.0.1"})
    utoken = db.set_user(user["id"])
    try:
        params = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        server._check_privileged(method, url.path, body, user)
        if server._own_login_route(method, url.path, body) and not central.own_login_on():
            raise server.HTTPError(410, server.OWN_LOGIN_OFF)
        result = fn(body, params, *match.groups())
        return {"status": 200, "body": json.loads(json.dumps(result))}
    except server.HTTPError as e:
        return {"status": e.status, "message": str(e)}
    except (ValueError, TypeError):
        return {"status": 400, "message": "bad request: a value sent was the wrong type or format"}
    finally:
        server._request.reset(token)
        db._current_user.reset(utoken)


answers = []
for i, r in enumerate(R):
    clock[0] = T0 + i * 60
    r["at"] = clock[0]
    answers.append(call(r))
db.conn().close()
work.unlink()
print(json.dumps({"requests": R, "answers": answers, "ids": {"admin": admin, "lee": lee, "kim": kim}}))
