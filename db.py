"""SQLite storage for watches, listings, machines and settings."""

from __future__ import annotations

import contextlib
import contextvars
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "deal-hunter.db"

_lock = threading.RLock()

DEFAULT_SETTINGS = {
    "poll_minutes": 15,
    "junk_terms": [
        "for parts", "parts only", "not working", "non working", "broken", "as is", "as-is",
        "untested", "box only", "empty box", "bent pin", "cracked", "damaged", "faulty",
        "engineering sample", "qualification sample", "replica", "wtb", "want to buy",
        "looking for", "read description",
    ],
    "sources_enabled": {"ebay": True, "ebay_local": True, "reddit": True, "bestbuy": False,
                        "slickdeals": True, "buildapcsales": True, "ksl": True,
                        "craigslist": True, "offerup": True,
                        "ksl_cars": True},
    "ebay_client_id": "",
    "ebay_client_secret": "",
    "ebay_marketplace": "EBAY_US",
    "zip_code": "",
    "local_radius_miles": 50,
    "bestbuy_api_key": "",
    "reddit_subs": ["hardwareswap", "homelabsales"],
    "deal_max_age_days": 14,  # Slickdeals search reaches back years; older deals are skipped
    "discord_enabled": False,
    "discord_webhook": "",
    "discord_deals_only": True,
    "ai_provider": "off",  # off | ollama | claude
    "ollama_url": "http://192.168.86.82:11434",  # the home Ollama gateway on the Frigate box (LAN/VPN only)
    "ollama_model": "qwen3.5:4b",
    "api_key": "",  # for other apps calling the API port; made in Settings > API
    "ha_url": "",  # Home Assistant, for importing devices into My hardware
    "ha_token": "",
    "anthropic_api_key": "",
    "openai_api_key": "",
    "openai_model": "gpt-6-luna",  # a model is always selected, so picking a Mode is enough
    "gemini_api_key": "",
    "gemini_model": "gemini-3.5-flash-lite",
    "gemini_free_tier": False,  # key has no billing in Google, so requests cost $0 and skip the monthly limit
    "ai_monthly_limit": 5.0,  # US dollars; AI buttons stop working once a request could pass it
    "ai_confirm": True,  # show the cost and ask before each paid AI request
    "ai_source": "shared",  # members: "shared" = the admin's AI on their allowance, "own" = their own key
    "claude_model": "claude-opus-5-5",
}

SECRET_KEYS = {"ebay_client_secret", "bestbuy_api_key", "discord_webhook", "anthropic_api_key",
               "openai_api_key", "gemini_api_key", "ha_token", "api_key"}

# Each person's own settings. Everything else (source keys, check interval, Reddit subs...) is shared and
# only the admin can change it.
USER_KEYS = {"zip_code", "local_radius_miles", "junk_terms", "discord_enabled", "discord_webhook",
             "discord_deals_only", "ai_provider", "ollama_url", "ollama_model", "anthropic_api_key", "claude_model",
             "openai_api_key", "openai_model", "gemini_api_key", "gemini_model", "gemini_free_tier",
             "ai_monthly_limit", "ai_confirm", "ai_source", "ha_url", "ha_token"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    exclude TEXT NOT NULL DEFAULT '[]',
    min_price REAL,
    max_price REAL,
    condition TEXT NOT NULL DEFAULT 'any',
    include_auctions INTEGER NOT NULL DEFAULT 0,
    sources TEXT NOT NULL DEFAULT '["ebay","ebay_local","ksl","craigslist","offerup","reddit","slickdeals","buildapcsales"]',
    enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    machine_id INTEGER,
    created_at REAL NOT NULL,
    last_polled REAL,
    last_error TEXT,
    polled_sources TEXT NOT NULL DEFAULT '[]',
    keep_cheapest INTEGER,           -- keep only this many cheapest finds; NULL = keep all
    color TEXT,                      -- "#RRGGBB" the person picked; NULL = from the palette by id
    kind TEXT NOT NULL DEFAULT 'item',  -- item | vehicle (cars, trucks: year range and miles)
    year_min INTEGER,
    year_max INTEGER,
    max_miles INTEGER
);
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL,
    shipping REAL,
    total REAL,
    currency TEXT,
    url TEXT,
    image TEXT,
    location TEXT,
    condition TEXT,
    buying TEXT,
    first_seen REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    deal_pct REAL,
    ai_note TEXT,
    seen_at REAL,
    year INTEGER,                    -- vehicles: model year, odometer miles, title (clean/salvage/rebuilt)
    miles INTEGER,
    title_status TEXT,
    UNIQUE (watch_id, source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_watch ON listings(watch_id, first_seen);
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    action TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    paid_by INTEGER  -- whose AI key it ran on: the person's own, or the admin's when shared
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_at ON ai_usage(at);
CREATE TABLE IF NOT EXISTS machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    parts TEXT NOT NULL DEFAULT '[]',
    kind TEXT NOT NULL DEFAULT 'pc',
    model TEXT NOT NULL DEFAULT '',
    source_ref TEXT,
    make TEXT NOT NULL DEFAULT '',
    year INTEGER,
    msrp REAL,
    purchased TEXT NOT NULL DEFAULT '',  -- purchase date, as the user typed it (YYYY-MM-DD from the form)
    price_paid REAL,
    custom TEXT NOT NULL DEFAULT '[]'  -- the user's own fields: [{"label": "Serial number", "value": "..."}]
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT,              -- NULL until the person sets one (the first admin, or an invite)
    role TEXT NOT NULL DEFAULT 'member',  -- admin | member
    disabled INTEGER NOT NULL DEFAULT 0,
    watch_limit INTEGER,             -- NULL = no limit
    created_at REAL NOT NULL,
    ai_shared INTEGER NOT NULL DEFAULT 1,     -- may use the admin's AI
    ai_allowance REAL NOT NULL DEFAULT 0,     -- US $ a month of the admin's paid AI (0 = free models only)
    ai_daily_cap INTEGER NOT NULL DEFAULT 20  -- requests a day on the admin's AI
);
CREATE TABLE IF NOT EXISTS tokens (    -- one per signed-in browser or device; only a hash is kept
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,              -- web | device
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_used REAL
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS invites (   -- one-time links: a new account, or a password reset
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash TEXT NOT NULL UNIQUE,     -- only a hash; the code itself is in the link
    kind TEXT NOT NULL,                 -- invite | reset
    note TEXT NOT NULL DEFAULT '',      -- who it's for, as the admin wrote it
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- reset: whose password
    watch_limit INTEGER,                -- invite: the new account's limit
    created_by INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL
);
"""

DEFAULT_WATCH_LIMIT = 10  # for invited friends; the admin has none

# Who the current request (or poller pass) is acting for. Unset means the first admin, which is what
# scripts, tests and single-user installs want; the web server always sets it after signing someone in.
_current_user: contextvars.ContextVar[int | None] = contextvars.ContextVar("deal_hunter_user", default=None)


def admin_id() -> int:
    rows = query("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
    return rows[0]["id"]


def current_user_id() -> int:
    uid = _current_user.get()
    return uid if uid is not None else admin_id()


@contextlib.contextmanager
def as_user(user_id: int):
    token = _current_user.set(user_id)
    try:
        yield
    finally:
        _current_user.reset(token)


def set_user(user_id: int | None):
    return _current_user.set(user_id)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect()
            _conn.executescript(SCHEMA)
            cols = {r["name"] for r in _conn.execute("PRAGMA table_info(watches)")}
            if "polled_sources" not in cols:  # databases created before this column existed
                _conn.execute("ALTER TABLE watches ADD COLUMN polled_sources TEXT NOT NULL DEFAULT '[]'")
                _conn.execute("""UPDATE watches SET polled_sources =
                    (SELECT json_group_array(DISTINCT source) FROM listings WHERE listings.watch_id = watches.id)""")
            if "keep_cheapest" not in cols:
                _conn.execute("ALTER TABLE watches ADD COLUMN keep_cheapest INTEGER")
            if "color" not in cols:
                _conn.execute("ALTER TABLE watches ADD COLUMN color TEXT")
            for col, ddl in (("kind", "TEXT NOT NULL DEFAULT 'item'"), ("year_min", "INTEGER"),
                             ("year_max", "INTEGER"), ("max_miles", "INTEGER")):
                if col not in cols:
                    _conn.execute(f"ALTER TABLE watches ADD COLUMN {col} {ddl}")
            lcols = {r["name"] for r in _conn.execute("PRAGMA table_info(listings)")}
            for col, ddl in (("year", "INTEGER"), ("miles", "INTEGER"), ("title_status", "TEXT")):
                if col not in lcols:
                    _conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {ddl}")
            mcols = {r["name"] for r in _conn.execute("PRAGMA table_info(machines)")}
            for col, ddl in (("kind", "TEXT NOT NULL DEFAULT 'pc'"), ("model", "TEXT NOT NULL DEFAULT ''"),
                             ("source_ref", "TEXT"),  # devices other than PCs, and where they were imported from
                             ("make", "TEXT NOT NULL DEFAULT ''"), ("year", "INTEGER"), ("msrp", "REAL"),
                             ("purchased", "TEXT NOT NULL DEFAULT ''"), ("price_paid", "REAL"),
                             ("custom", "TEXT NOT NULL DEFAULT '[]'")):
                if col not in mcols:
                    _conn.execute(f"ALTER TABLE machines ADD COLUMN {col} {ddl}")
            if "seen_at" not in {r["name"] for r in _conn.execute("PRAGMA table_info(listings)")}:
                _conn.execute("ALTER TABLE listings ADD COLUMN seen_at REAL")  # last check that still found it
            if _conn.execute("PRAGMA user_version").fetchone()[0] < 1:
                # eBay local pickup arrived: watches that search eBay search it locally too.
                for r in _conn.execute("SELECT id, sources FROM watches").fetchall():
                    srcs = json.loads(r["sources"])
                    if "ebay" in srcs and "ebay_local" not in srcs:
                        srcs.insert(srcs.index("ebay") + 1, "ebay_local")
                        _conn.execute("UPDATE watches SET sources = ? WHERE id = ?", (json.dumps(srcs), r["id"]))
                _conn.execute("PRAGMA user_version = 1")
            if _conn.execute("PRAGMA user_version").fetchone()[0] < 2:
                # Retail deal feeds arrived: every existing watch checks them too.
                for r in _conn.execute("SELECT id, sources FROM watches").fetchall():
                    srcs = json.loads(r["sources"])
                    srcs += [s for s in ("slickdeals", "buildapcsales") if s not in srcs]
                    _conn.execute("UPDATE watches SET sources = ? WHERE id = ?", (json.dumps(srcs), r["id"]))
                _conn.execute("PRAGMA user_version = 2")
            if _conn.execute("PRAGMA user_version").fetchone()[0] < 3:
                # KSL Classifieds arrived: every existing watch checks it too (it uses the Local area).
                for r in _conn.execute("SELECT id, sources FROM watches").fetchall():
                    srcs = json.loads(r["sources"])
                    if "ksl" not in srcs:
                        srcs.insert(srcs.index("ebay_local") + 1 if "ebay_local" in srcs else len(srcs), "ksl")
                        _conn.execute("UPDATE watches SET sources = ? WHERE id = ?", (json.dumps(srcs), r["id"]))
                _conn.execute("PRAGMA user_version = 3")
            if _conn.execute("PRAGMA user_version").fetchone()[0] < 4:
                # Make got its own field: imported devices had "LG OLED65B2AUA" in model; split off the maker.
                # Hand-entered devices (no source_ref) are left as typed.
                for r in _conn.execute("""SELECT id, model FROM machines WHERE source_ref IS NOT NULL
                                          AND make = '' AND kind NOT IN ('pc', 'server')""").fetchall():
                    words = (r["model"] or "").split(" ", 1)
                    if len(words) == 2:
                        _conn.execute("UPDATE machines SET make = ?, model = ? WHERE id = ?", (*words, r["id"]))
                _conn.execute("PRAGMA user_version = 4")
            for table in ("watches", "machines", "ai_usage"):  # every row belongs to someone
                if "user_id" not in {r["name"] for r in _conn.execute(f"PRAGMA table_info({table})")}:
                    _conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
            if not _conn.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone():
                # The first admin: on an existing install that's the owner of everything so far.
                _conn.execute("INSERT INTO users (name, role, created_at) VALUES ('Admin', 'admin', ?)", (time.time(),))
            if _conn.execute("PRAGMA user_version").fetchone()[0] < 5:
                # Accounts arrived: existing data and personal settings move into the first admin's account.
                admin = _conn.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()[0]
                for table in ("watches", "machines", "ai_usage"):
                    _conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (admin,))
                for r in _conn.execute("SELECT key, value FROM settings").fetchall():
                    if r["key"] in USER_KEYS:
                        _conn.execute("INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
                                      (admin, r["key"], r["value"]))
                        _conn.execute("DELETE FROM settings WHERE key = ?", (r["key"],))
                _conn.execute("PRAGMA user_version = 5")
            ucols = {r["name"] for r in _conn.execute("PRAGMA table_info(users)")}
            for col, ddl in (("ai_shared", "INTEGER NOT NULL DEFAULT 1"), ("ai_allowance", "REAL NOT NULL DEFAULT 0"),
                             ("ai_daily_cap", "INTEGER NOT NULL DEFAULT 20")):
                if col not in ucols:
                    _conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
            if "paid_by" not in {r["name"] for r in _conn.execute("PRAGMA table_info(ai_usage)")}:
                _conn.execute("ALTER TABLE ai_usage ADD COLUMN paid_by INTEGER")
            if _conn.execute("PRAGMA user_version").fetchone()[0] < 6:
                # Shared AI arrived: everything so far ran on the person's own key.
                _conn.execute("UPDATE ai_usage SET paid_by = user_id WHERE paid_by IS NULL")
                _conn.execute("PRAGMA user_version = 6")
            if _conn.execute("PRAGMA user_version").fetchone()[0] < 7:
                # Craigslist arrived: watches that search locally (KSL) search Craigslist too.
                for r in _conn.execute("SELECT id, sources FROM watches").fetchall():
                    srcs = json.loads(r["sources"])
                    if "ksl" in srcs and "craigslist" not in srcs:
                        srcs.insert(srcs.index("ksl") + 1, "craigslist")
                        _conn.execute("UPDATE watches SET sources = ? WHERE id = ?", (json.dumps(srcs), r["id"]))
                _conn.execute("PRAGMA user_version = 7")
            if _conn.execute("PRAGMA user_version").fetchone()[0] < 8:
                # OfferUp arrived: watches that search locally (KSL) search OfferUp too.
                for r in _conn.execute("SELECT id, sources FROM watches").fetchall():
                    srcs = json.loads(r["sources"])
                    if "ksl" in srcs and "offerup" not in srcs:
                        at = srcs.index("craigslist") if "craigslist" in srcs else srcs.index("ksl")
                        srcs.insert(at + 1, "offerup")
                        _conn.execute("UPDATE watches SET sources = ? WHERE id = ?", (json.dumps(srcs), r["id"]))
                _conn.execute("PRAGMA user_version = 8")
            _conn.execute("CREATE INDEX IF NOT EXISTS idx_watches_user ON watches(user_id)")
            _conn.execute("CREATE INDEX IF NOT EXISTS idx_machines_user ON machines(user_id)")
            _conn.commit()
        return _conn


def query(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def execute(sql: str, args: tuple = ()) -> int:
    with _lock:
        cur = conn().execute(sql, args)
        conn().commit()
        return cur.lastrowid


def execute_count(sql: str, args: tuple = ()) -> int:
    """Like execute, but returns how many rows changed."""
    with _lock:
        cur = conn().execute(sql, args)
        conn().commit()
        return cur.rowcount


# ---- settings -------------------------------------------------------------

def get_settings(user_id: int | None = None) -> dict:
    """Shared settings plus one person's own (the current user's unless given)."""
    uid = user_id if user_id is not None else current_user_id()
    shared = {r["key"]: json.loads(r["value"]) for r in query("SELECT key, value FROM settings")}
    mine = {r["key"]: json.loads(r["value"])
            for r in query("SELECT key, value FROM user_settings WHERE user_id = ?", (uid,))}
    merged = {**DEFAULT_SETTINGS, **{k: v for k, v in shared.items() if k not in USER_KEYS}, **mine}
    # Sources added after the settings were saved start at their default on/off.
    merged["sources_enabled"] = {**DEFAULT_SETTINGS["sources_enabled"], **shared.get("sources_enabled", {})}
    return merged


def update_settings(changes: dict, user_id: int | None = None, shared_allowed: bool = True) -> None:
    """Personal keys go to the person's own settings; shared keys only when allowed (the admin)."""
    uid = user_id if user_id is not None else current_user_id()
    for key, value in changes.items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key in USER_KEYS:
            execute("INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
                    (uid, key, json.dumps(value)))
        elif shared_allowed:
            execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))


def public_settings(user_id: int | None = None) -> dict:
    """Settings safe to send to the browser: secrets become booleans."""
    s = get_settings(user_id)
    for key in SECRET_KEYS:
        s[key] = bool(s[key])
    return s


# ---- watches --------------------------------------------------------------

WATCH_FIELDS = ("name", "query", "exclude", "min_price", "max_price", "condition",
                "include_auctions", "sources", "enabled", "notes", "machine_id", "keep_cheapest", "color",
                "kind", "year_min", "year_max", "max_miles")
# Changing any of these makes the next check find a fresh backlog of older listings.
MATCH_FIELDS = ("query", "exclude", "min_price", "max_price", "condition", "include_auctions",
                "kind", "year_min", "year_max", "max_miles")


def _watch_row(r: dict) -> dict:
    r["exclude"] = json.loads(r["exclude"])
    r["sources"] = json.loads(r["sources"])
    r["polled_sources"] = json.loads(r["polled_sources"])
    r["enabled"] = bool(r["enabled"])
    r["include_auctions"] = bool(r["include_auctions"])
    return r


def list_watches(all_users: bool = False) -> list[dict]:
    """The current user's watches (everyone's only for the poller)."""
    where, args = ("", ()) if all_users else ("WHERE w.user_id = ?", (current_user_id(),))
    rows = query(f"""
        SELECT w.*,
          (SELECT COUNT(*) FROM listings l WHERE l.watch_id = w.id AND l.status = 'new') AS new_count,
          (SELECT COUNT(*) FROM listings l WHERE l.watch_id = w.id) AS total_count,
          (SELECT MIN(total) FROM listings l WHERE l.watch_id = w.id AND l.status != 'dismissed') AS best_price
        FROM watches w {where} ORDER BY w.created_at DESC""", args)
    return [_watch_row(r) for r in rows]


def get_watch(watch_id: int, any_user: bool = False) -> dict | None:
    if any_user:
        rows = query("SELECT * FROM watches WHERE id = ?", (watch_id,))
    else:
        rows = query("SELECT * FROM watches WHERE id = ? AND user_id = ?", (watch_id, current_user_id()))
    return _watch_row(rows[0]) if rows else None


def _watch_values(data: dict) -> dict:
    out = {}
    for f in WATCH_FIELDS:
        if f not in data:
            continue
        v = data[f]
        if f in ("exclude", "sources"):
            v = json.dumps(v if isinstance(v, list) else [])
        elif f in ("enabled", "include_auctions"):
            v = 1 if v else 0
        elif f in ("min_price", "max_price"):
            v = float(v) if v not in (None, "") else None
        elif f == "keep_cheapest":
            try:
                v = int(v) if v not in (None, "", 0, "0") else None
            except (TypeError, ValueError):
                v = None
            v = v if v is None or v > 0 else None
        elif f == "kind":
            v = v if v in ("item", "vehicle") else "item"
        elif f in ("year_min", "year_max", "max_miles"):
            try:
                v = int(float(v)) if v not in (None, "") else None
            except (TypeError, ValueError):
                v = None
            if v is not None and (v < 0 or (f != "max_miles" and not 1900 <= v <= 2100)):
                v = None
        elif f == "color":
            v = v.lower() if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) else None
        elif f == "machine_id" and v is not None and not get_machine(int(v)):
            v = None  # only link to your own devices
        out[f] = v
    return out


def create_watch(data: dict) -> int:
    vals = _watch_values(data)
    vals["created_at"] = time.time()
    vals["user_id"] = current_user_id()
    cols = ", ".join(vals)
    return execute(f"INSERT INTO watches ({cols}) VALUES ({', '.join('?' * len(vals))})", tuple(vals.values()))


def update_watch(watch_id: int, data: dict) -> int | None:
    """Returns None if there's no such watch, else how many new finds turning it off marked seen."""
    old = get_watch(watch_id)
    if not old:
        return None
    vals = _watch_values(data)
    if any(f in vals and _watch_values({f: old[f]})[f] != vals[f] for f in MATCH_FIELDS):
        # New criteria find a new backlog; treat it like a fresh watch so it isn't sent to Discord.
        vals["polled_sources"] = "[]"
    if vals:
        sets = ", ".join(f"{k} = ?" for k in vals)
        execute(f"UPDATE watches SET {sets} WHERE id = ? AND user_id = ?", (*vals.values(), watch_id, old["user_id"]))
    if old["enabled"] and vals.get("enabled") == 0:
        # Turning a watch off: its unread finds count as viewed, so they stop showing as new everywhere.
        return execute_count("UPDATE listings SET status = 'seen' WHERE watch_id = ? AND status = 'new'",
                             (watch_id,))
    return 0


def prune_cheapest(watch: dict, fresh: set | None = None) -> int:
    """Keep only the watch's N cheapest finds showing; the rest become 'pruned' (hidden, not deleted, so
    the next check doesn't find them again as new). Starred finds always stay and don't use up a place.
    Pruned finds come back if cheaper ones go (dismissed, excluded) or the limit is raised or cleared.
    `fresh` are finds this check just stored (hidden until now): the ones that make the cut become 'new'.
    Returns how many finds were newly hidden."""
    fresh = fresh or set()
    n = watch.get("keep_cheapest")
    rows = query("""SELECT id, status FROM listings WHERE watch_id = ? AND status IN ('new', 'seen', 'pruned')
                    ORDER BY total IS NULL, total, first_seen DESC, id""", (watch["id"],))
    keep = rows if not n else rows[:n]
    hide = [] if not n else rows[n:]
    for r in keep:
        if r["status"] == "pruned":
            execute("UPDATE listings SET status = ? WHERE id = ?", ("new" if r["id"] in fresh else "seen", r["id"]))
    newly = [r["id"] for r in hide if r["status"] != "pruned" and r["id"] not in fresh]
    for lid in newly:
        execute("UPDATE listings SET status = 'pruned' WHERE id = ?", (lid,))
    return len(newly)


def delete_watch(watch_id: int) -> bool:
    return execute_count("DELETE FROM watches WHERE id = ? AND user_id = ?", (watch_id, current_user_id())) > 0


# ---- machines -------------------------------------------------------------

# Everything someone owns, not just computers. PCs and servers have a parts list; the rest a make/model.
DEVICE_KINDS = ["pc", "server", "tv", "monitor", "phone", "tablet", "audio", "network", "console",
                "printer", "appliance", "smart home", "vehicle", "other"]
PARTS_KINDS = {"pc", "server"}


def _machine_row(r: dict) -> dict:
    r["parts"] = json.loads(r["parts"])
    r["custom"] = json.loads(r.get("custom") or "[]")
    return r


def list_machines() -> list[dict]:
    """The current user's devices."""
    return [_machine_row(r) for r in query("SELECT * FROM machines WHERE user_id = ? ORDER BY name",
                                           (current_user_id(),))]


def get_machine(machine_id: int, any_user: bool = False) -> dict | None:
    if any_user:
        rows = query("SELECT * FROM machines WHERE id = ?", (machine_id,))
    else:
        rows = query("SELECT * FROM machines WHERE id = ? AND user_id = ?", (machine_id, current_user_id()))
    return _machine_row(rows[0]) if rows else None


def _number(v, kind=float):
    try:
        return kind(str(v).replace("$", "").replace(",", "").strip()) if v not in (None, "") else None
    except ValueError:
        return None


def save_machine(data: dict, machine_id: int | None = None) -> int:
    parts = json.dumps([p for p in data.get("parts", []) if str(p.get("model", "")).strip()])
    kind = data.get("kind") if data.get("kind") in DEVICE_KINDS else "pc"
    year = _number(data.get("year"), int)
    vals = (data.get("name", "Untitled"), data.get("notes", ""), parts, kind,
            str(data.get("make") or "").strip(), str(data.get("model") or "").strip(),
            year if year and 1950 <= year <= 2100 else None, _number(data.get("msrp")),
            str(data.get("purchased") or "").strip(), _number(data.get("price_paid")),
            json.dumps([{"label": str(f.get("label", "")).strip()[:60], "value": str(f.get("value", "")).strip()[:500]}
                        for f in data.get("custom") or [] if str(f.get("label", "")).strip()]),
            data.get("source_ref") or None)
    uid = current_user_id()
    if machine_id is None:
        return execute("""INSERT INTO machines (name, notes, parts, kind, make, model, year, msrp, purchased, price_paid,
                          custom, source_ref, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (*vals, uid))
    changed = execute_count("""UPDATE machines SET name = ?, notes = ?, parts = ?, kind = ?, make = ?, model = ?,
               year = ?, msrp = ?, purchased = ?, price_paid = ?, custom = ?, source_ref = COALESCE(?, source_ref)
               WHERE id = ? AND user_id = ?""", (*vals, machine_id, uid))
    if not changed:
        raise LookupError("device not found")
    return machine_id


def delete_machine(machine_id: int) -> bool:
    uid = current_user_id()
    execute("UPDATE watches SET machine_id = NULL WHERE machine_id = ? AND user_id = ?", (machine_id, uid))
    return execute_count("DELETE FROM machines WHERE id = ? AND user_id = ?", (machine_id, uid)) > 0
