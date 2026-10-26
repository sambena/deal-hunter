"""Builds an old Deal Hunter database with the real Python code (db.py and central.py), for the copy and checker tests.
Made-up people and listings only; nothing is fetched. Usage: python make_legacy.py <new database file> <issuer>

Prints JSON: {"accounts": {name: id}} for the tests to refer to."""
import json
import sys
import time
from pathlib import Path

import central
import db

path, issuer = Path(sys.argv[1]), sys.argv[2]
db.DB_PATH = path
db.conn()   # makes every table and runs every migration, as the app does at start

T = 1767323045.123456   # a fixed epoch-seconds float, as time.time() gives


def account(name, role="member", **limits):
    uid = db.execute("INSERT INTO users (email, name, role, created_at) VALUES (?, ?, ?, ?)",
                     (f"{name.lower()}@example.test", name, role, T + len(name)))
    for key, value in limits.items():
        db.execute(f"UPDATE users SET {key} = ? WHERE id = ?", (value, uid))
    return uid


admin = db.admin_id()   # db.conn() made the first admin, as on an existing install
db.execute("UPDATE users SET name = 'Pat', email = 'pat@example.test' WHERE id = ?", (admin,))
lee = account("Lee", watch_limit=10, ai_allowance=1.25, ai_daily_cap=5)
kim = account("Kim", watch_limit=3, ai_shared=0)
gone = account("Gone")                 # linked, then disabled: copied as disabled
nolink = account("Nolink")             # never linked: the copy stops and lists it
other = account("Other")               # linked to another login service only
paused = account("Paused")             # linked, but the link is disabled

central.link(admin, issuer, "00000000-0000-4000-8000-0000000000a1")
central.link(lee, issuer, "00000000-0000-4000-8000-0000000000a2")
central.link(kim, issuer, "00000000-0000-4000-8000-0000000000a3")
central.link(gone, issuer, "00000000-0000-4000-8000-0000000000a4")
db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (gone,))
central.link(other, "https://other.example.test/application/o/deals/", "00000000-0000-4000-8000-0000000000a5")
ident = central.link(paused, issuer, "00000000-0000-4000-8000-0000000000a6")
central.set_status(ident, "disabled")

# Shared settings, and each person's own, through the app's own function.
db.update_settings({"poll_minutes": 30, "check_mode": "times", "check_times": ["07:00", "18:30"],
                    "sources_enabled": {"ebay": True, "bestbuy": False, "ksl": True},
                    "ebay_client_id": "made-up-id", "reddit_subs": ["hardwareswap"]}, user_id=admin)
db.update_settings({"zip_code": "00000", "local_radius_miles": 25, "junk_terms": ["for parts", "naïve ünïcode"],
                    "ai_monthly_limit": 5.0, "sizes": {"shoe": "10.5", "top": "M"}}, user_id=lee)
db.update_settings({"focus": "cars"}, user_id=kim)
db.update_settings({"focus": "home"}, user_id=nolink)

with db.as_user(admin):
    pc = db.save_machine({"name": "Desk PC", "parts": [{"category": "cpu", "model": "i9-10980XE"}],
                          "custom": [{"label": "Serial number", "value": "SN-0001"}], "msrp": 1999.99})
    gpu = db.create_watch({"name": "RTX 3080", "query": "rtx 3080 -mining", "exclude": ["mining"], "max_price": 450.5,
                           "machine_id": pc, "keep_cheapest": 5, "color": "#A1B2C3", "include_auctions": True})
    spare = db.create_watch({"name": "Spare", "query": "spare"})
    db.delete_watch(spare)   # the sequence runs past the highest id left
    db.execute("UPDATE watches SET last_polled = ?, polled_sources = ?, last_check = ? WHERE id = ?",
               (T + 60, json.dumps(["ebay", "ksl"]), json.dumps({"ebay": {"returned": 40, "kept": 2}}), gpu))
with db.as_user(lee):
    truck = db.save_machine({"name": "Truck", "kind": "vehicle", "make": "Toyota", "model": "Tacoma", "year": 2018,
                             "vin": "1abcd23efgh456789", "odometer": 61000})
    tacoma = db.create_watch({"name": "Tacoma", "query": "toyota tacoma", "kind": "vehicle", "year_min": 2016,
                              "year_max": 2020, "max_miles": 90000, "enabled": False})
    shoes = db.create_watch({"name": "Shoes", "query": "running shoes", "kind": "clothing", "size": "10.5",
                             "department": "men", "category": "clothes"})
with db.as_user(nolink):
    db.create_watch({"name": "Lamp", "query": "lamp"})
with db.as_user(kim):
    db.create_watch({"name": "Monitor", "query": "monitor"})


def listing(watch, source, sid, title, total, **more):
    cols = {"watch_id": watch, "source": source, "source_id": sid, "title": title, "price": total, "shipping": 0.0,
            "total": total, "currency": "USD", "url": f"https://shop.example.test/{sid}", "first_seen": T, **more}
    return db.execute(f"INSERT INTO listings ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))


listing(gpu, "ebay", "e1", "RTX 3080 FE", 439.99, status="starred", deal_pct=0.1234567891, seen_at=T + 900)
listing(gpu, "ksl", "k1", "RTX 3080 — local “pickup”", 400.0, location="Somewhere, UT", status="seen")
dropped = listing(gpu, "reddit", "r1", "[H] 3080 [W] PayPal", None)
db.execute("DELETE FROM listings WHERE id = ?", (dropped,))
listing(tacoma, "ksl_cars", "c1", "2018 Toyota Tacoma TRD", 27500.0, year=2018, miles=61000, title_status="clean",
        make="Toyota", ai_note="Fair price.")
listing(shoes, "poshmark", "p1", "Running shoes 10.5", 35.0, size="10.5", status="dismissed")
last = listing(shoes, "poshmark", "p2", "Gone shoes", 20.0)
db.execute("DELETE FROM listings WHERE id = ?", (last,))   # the highest id given is gone: the next id must skip it

for at, who, paid, cost in ((T, admin, admin, 0.000123), (T + 1, lee, admin, 0.0042), (T + 2, lee, lee, 0.0)):
    db.execute("""INSERT INTO ai_usage (at, provider, model, action, input_tokens, output_tokens, cost, user_id, paid_by)
                  VALUES (?, 'claude', 'claude-haiku-5-5', 'judge', 812, 96, ?, ?, ?)""", (at, cost, who, paid))

# What the copy leaves behind: the app's own sign-in and the central session state.
db.execute("INSERT INTO tokens (user_id, token_hash, kind, name, created_at) VALUES (?, 'made-up-hash', 'web', 'browser', ?)",
           (admin, T))
db.conn().commit()
db.conn().close()
print(json.dumps({"accounts": {"pat": admin, "lee": lee, "kim": kim, "gone": gone, "nolink": nolink, "other": other,
                               "paused": paused}}))
