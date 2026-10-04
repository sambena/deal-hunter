"""SQLite storage for watches, listings, machines and settings."""

from __future__ import annotations

import json
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
    "sources_enabled": {"ebay": True, "ebay_local": True, "reddit": True, "bestbuy": False},
    "ebay_client_id": "",
    "ebay_client_secret": "",
    "ebay_marketplace": "EBAY_US",
    "zip_code": "",
    "local_radius_miles": 50,
    "bestbuy_api_key": "",
    "reddit_subs": ["hardwareswap", "homelabsales"],
    "discord_enabled": False,
    "discord_webhook": "",
    "discord_deals_only": True,
    "ai_provider": "off",  # off | ollama | claude
    "ollama_url": "http://192.168.86.82:11434",  # the home Ollama gateway on the Frigate box (LAN/VPN only)
    "ollama_model": "qwen3.5:4b",
    "anthropic_api_key": "",
    "openai_api_key": "",
    "openai_model": "gpt-6-luna",  # a model is always selected, so picking a Mode is enough
    "gemini_api_key": "",
    "gemini_model": "gemini-3.5-flash-lite",
    "ai_monthly_limit": 5.0,  # US dollars; AI buttons stop working once a request could pass it
    "ai_confirm": True,  # show the cost and ask before each paid AI request
    "claude_model": "claude-opus-5-5",
}

SECRET_KEYS = {"ebay_client_secret", "bestbuy_api_key", "discord_webhook", "anthropic_api_key",
               "openai_api_key", "gemini_api_key"}

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
    sources TEXT NOT NULL DEFAULT '["ebay","ebay_local","reddit","bestbuy"]',
    enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    machine_id INTEGER,
    created_at REAL NOT NULL,
    last_polled REAL,
    last_error TEXT,
    polled_sources TEXT NOT NULL DEFAULT '[]'
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
    cost REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_at ON ai_usage(at);
CREATE TABLE IF NOT EXISTS machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    parts TEXT NOT NULL DEFAULT '[]'
);
"""


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
            if _conn.execute("PRAGMA user_version").fetchone()[0] < 1:
                # eBay local pickup arrived: watches that search eBay search it locally too.
                for r in _conn.execute("SELECT id, sources FROM watches").fetchall():
                    srcs = json.loads(r["sources"])
                    if "ebay" in srcs and "ebay_local" not in srcs:
                        srcs.insert(srcs.index("ebay") + 1, "ebay_local")
                        _conn.execute("UPDATE watches SET sources = ? WHERE id = ?", (json.dumps(srcs), r["id"]))
                _conn.execute("PRAGMA user_version = 1")
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


# ---- settings -------------------------------------------------------------

def get_settings() -> dict:
    stored = {r["key"]: json.loads(r["value"]) for r in query("SELECT key, value FROM settings")}
    merged = {**DEFAULT_SETTINGS, **stored}
    # Sources added after the settings were saved start at their default on/off.
    merged["sources_enabled"] = {**DEFAULT_SETTINGS["sources_enabled"], **stored.get("sources_enabled", {})}
    return merged


def update_settings(changes: dict) -> None:
    for key, value in changes.items():
        if key not in DEFAULT_SETTINGS:
            continue
        execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))


def public_settings() -> dict:
    """Settings safe to send to the browser: secrets become booleans."""
    s = get_settings()
    for key in SECRET_KEYS:
        s[key] = bool(s[key])
    return s


# ---- watches --------------------------------------------------------------

WATCH_FIELDS = ("name", "query", "exclude", "min_price", "max_price", "condition",
                "include_auctions", "sources", "enabled", "notes", "machine_id")
# Changing any of these makes the next check find a fresh backlog of older listings.
MATCH_FIELDS = ("query", "exclude", "min_price", "max_price", "condition", "include_auctions")


def _watch_row(r: dict) -> dict:
    r["exclude"] = json.loads(r["exclude"])
    r["sources"] = json.loads(r["sources"])
    r["polled_sources"] = json.loads(r["polled_sources"])
    r["enabled"] = bool(r["enabled"])
    r["include_auctions"] = bool(r["include_auctions"])
    return r


def list_watches() -> list[dict]:
    rows = query("""
        SELECT w.*,
          (SELECT COUNT(*) FROM listings l WHERE l.watch_id = w.id AND l.status = 'new') AS new_count,
          (SELECT COUNT(*) FROM listings l WHERE l.watch_id = w.id) AS total_count,
          (SELECT MIN(total) FROM listings l WHERE l.watch_id = w.id AND l.status != 'dismissed') AS best_price
        FROM watches w ORDER BY w.created_at DESC""")
    return [_watch_row(r) for r in rows]


def get_watch(watch_id: int) -> dict | None:
    rows = query("SELECT * FROM watches WHERE id = ?", (watch_id,))
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
        out[f] = v
    return out


def create_watch(data: dict) -> int:
    vals = _watch_values(data)
    vals["created_at"] = time.time()
    cols = ", ".join(vals)
    return execute(f"INSERT INTO watches ({cols}) VALUES ({', '.join('?' * len(vals))})", tuple(vals.values()))


def update_watch(watch_id: int, data: dict) -> None:
    vals = _watch_values(data)
    old = get_watch(watch_id)
    if old and any(f in vals and _watch_values({f: old[f]})[f] != vals[f] for f in MATCH_FIELDS):
        # New criteria find a new backlog; treat it like a fresh watch so it isn't sent to Discord.
        vals["polled_sources"] = "[]"
    if vals:
        sets = ", ".join(f"{k} = ?" for k in vals)
        execute(f"UPDATE watches SET {sets} WHERE id = ?", (*vals.values(), watch_id))


def delete_watch(watch_id: int) -> None:
    execute("DELETE FROM watches WHERE id = ?", (watch_id,))


# ---- machines -------------------------------------------------------------

def list_machines() -> list[dict]:
    rows = query("SELECT * FROM machines ORDER BY name")
    for r in rows:
        r["parts"] = json.loads(r["parts"])
    return rows


def get_machine(machine_id: int) -> dict | None:
    rows = [m for m in list_machines() if m["id"] == machine_id]
    return rows[0] if rows else None


def save_machine(data: dict, machine_id: int | None = None) -> int:
    parts = json.dumps([p for p in data.get("parts", []) if str(p.get("model", "")).strip()])
    if machine_id is None:
        return execute("INSERT INTO machines (name, notes, parts) VALUES (?, ?, ?)",
                       (data.get("name", "Untitled"), data.get("notes", ""), parts))
    execute("UPDATE machines SET name = ?, notes = ?, parts = ? WHERE id = ?",
            (data.get("name", "Untitled"), data.get("notes", ""), parts, machine_id))
    return machine_id


def delete_machine(machine_id: int) -> None:
    execute("UPDATE watches SET machine_id = NULL WHERE machine_id = ?", (machine_id,))
    execute("DELETE FROM machines WHERE id = ?", (machine_id,))
