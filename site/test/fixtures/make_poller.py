"""Records the Python app's checks (poller.py) for test/poller.test.ts: a database made with the real db.py, then full
passes, one watch, one person's watches and the Best Buy purge, with every source replaced by made-up answers (SPEC)
and Discord by a stand-in that notes each post. Nothing is fetched. Made-up people and listings only.

Usage, from the repository root: python make_poller.py <new database file> <issuer> <spec.json>
The database is copied to <file> before any check runs. Prints JSON: each step's answer, the sources asked (with the
watch's query and fitment), the Discord posts, the poller's state, and the watches and finds after each step."""
import copy
import io
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

path, issuer, spec_path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
work = path.with_suffix(".work.db")
SPEC = json.loads(spec_path.read_text(encoding="utf-8"))
T0 = 1793000000.0
clock = [T0 - 86400]
time.time = lambda: clock[0]

import central  # noqa: E402 - after the clock is fixed
import db  # noqa: E402
import poller  # noqa: E402
from sources import SourceError  # noqa: E402

db.DB_PATH = work
db.conn()
OWNER, MEMBER = "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"
admin = db.admin_id()
lee = db.execute("INSERT INTO users (email, name, role, created_at) VALUES ('m@example.test', 'Member', 'member', ?)", (clock[0],))
central.link(admin, issuer, OWNER)
central.link(lee, issuer, MEMBER)
ALL_ON = {k: True for k in ("ebay", "ebay_local", "reddit", "bestbuy", "slickdeals", "buildapcsales", "ksl", "craigslist",
                            "offerup", "ksl_cars", "poshmark")}
db.update_settings({"sources_enabled": ALL_ON, "poll_minutes": 30}, user_id=admin)
db.update_settings({"zip_code": "84101", "discord_enabled": True, "discord_deals_only": False, "junk_terms": ["for parts"],
                    "discord_webhook": "https://discord.com/api/webhooks/1/made-up-owner"}, user_id=admin)
db.update_settings({"zip_code": "84101", "discord_enabled": True, "discord_deals_only": True, "junk_terms": ["broken"],
                    "discord_webhook": "https://discord.com/api/webhooks/2/made-up-member"}, user_id=lee)


def tick():
    clock[0] += 1


with db.as_user(admin):
    truck = db.save_machine({"name": "Truck", "kind": "vehicle", "make": "Toyota", "model": "Tacoma", "year": 2018})
    tick(); gpu = db.create_watch({"name": "RTX", "query": "rtx 3080", "exclude": ["mining"], "max_price": 500, "keep_cheapest": 2,
                                   "sources": ["ebay", "ebay_local", "ksl", "reddit", "bestbuy", "nope"]})
    tick(); pickup = db.create_watch({"name": "Pickup", "query": "toyota pickup", "kind": "vehicle", "year_min": 2015, "year_max": 2020,
                                      "max_miles": 100000, "sources": ["ebay", "craigslist", "ksl_cars", "reddit"]})
    tick(); shoes = db.create_watch({"name": "Shoes", "query": "running shoes", "kind": "clothing", "size": "10.5",
                                     "sources": ["poshmark", "ebay", "reddit"]})
    tick(); parts = db.create_watch({"name": "Brakes", "query": "brake pads", "machine_id": truck, "sources": ["ebay"]})
    tick(); db.create_watch({"name": "Off", "query": "rtx 3080", "enabled": False, "sources": ["ebay"]})
    tick(); broken = db.create_watch({"name": "Failing", "query": "fails", "sources": ["ksl", "slickdeals"]})
with db.as_user(lee):
    tick(); same = db.create_watch({"name": "Same card", "query": "rtx 3080", "sources": ["ebay", "ksl"], "max_price": 450})
db.conn().commit()
shutil.copyfile(work, path)

current = {"pass": 0}
asked = []


def fake(name):
    def run(watch, settings):
        asked.append({"source": name, "query": watch["query"], "fits": watch.get("fits")})
        answer = SPEC.get(name, {}).get(watch["query"], [[]])
        answer = answer[min(current["pass"], len(answer) - 1)]
        if isinstance(answer, dict) and "error" in answer:
            raise SourceError(answer["error"])
        return copy.deepcopy(answer)
    return run


poller.SOURCES = {n: fake(n) for n in ALL_ON}
posts = []


class Answer(io.BytesIO):
    def close(self):
        pass


def fake_urlopen(req, timeout=None):
    posts.append({"url": req.full_url, "body": req.data.decode(), "headers": {k.lower(): v for k, v in req.header_items()}, "timeout": timeout})
    if "made-up-member" in req.full_url:
        raise urllib.error.HTTPError(req.full_url, 404, "made up", {}, io.BytesIO(b""))
    return Answer(b"")


urllib.request.urlopen = fake_urlopen


def dump():
    watches = db.query("SELECT id, last_polled, last_error, polled_sources, last_check FROM watches ORDER BY id")
    for w in watches:
        w["polled_sources"] = json.loads(w["polled_sources"])
        w["last_check"] = json.loads(w["last_check"] or "null")
    listings = db.query("SELECT * FROM listings ORDER BY id")
    return {"watches": watches, "listings": listings}


steps = []


def step(name, fn, advance):
    clock[0] = T0 + advance
    asked.clear()
    posts.clear()
    try:
        answer = {"out": fn()}
    except Exception as e:  # noqa: BLE001
        answer = {"error": type(e).__name__, "message": str(e)}
    steps.append({"name": name, "at": clock[0], **answer, "asked": list(asked), "posts": list(posts),
                  "state": json.loads(json.dumps({k: poller.state[k] for k in ("source_errors", "fetches")})), **dump()})


def as_owner(fn, user=admin):
    def go():
        with db.as_user(user):
            return fn()
    return go


step("first pass", lambda: poller.run_all(), 0)
current["pass"] = 1
step("second pass", lambda: poller.run_all(), 900)
step("one watch", as_owner(lambda: poller.run_one(gpu)), 1000)
step("someone else's watch", as_owner(lambda: poller.run_one(same)), 1010)
current["pass"] = 2


def member_poll():
    shared = {}
    with db.as_user(lee):
        return [poller._run_as_owner(w, shared) for w in db.list_watches() if w["enabled"]]


step("one person's watches", member_poll, 2000)
step("a pass three days on", lambda: poller.run_all(), 900 + 73 * 3600)
steps.append({"name": "next check", "cases": [
    {"settings": s, "now": T0, "out": poller.next_check(s, T0)} for s in [
        {"check_mode": "interval", "poll_minutes": 30}, {"check_mode": "interval", "poll_minutes": 1},
        {"check_mode": "interval", "poll_minutes": None}, {"check_mode": "interval", "poll_minutes": "7"},
        {"check_mode": "times", "check_times": [], "poll_minutes": 20}]]})
db.conn().close()
work.unlink()
json.dump({"steps": steps, "ids": {"admin": admin, "lee": lee, "gpu": gpu, "same": same, "pickup": pickup, "shoes": shoes,
                                   "parts": parts, "broken": broken}}, sys.stdout, ensure_ascii=False, indent=0)
sys.stdout.write("\n")
