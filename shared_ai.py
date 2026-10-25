"""The admin's AI settings from the owner console's shared settings file (version 1 of its format).

    {"version": 1, "keys": {"anthropic": "...", "openai": "", "gemini": ""},
     "apps": {"deals": {"ai": true, "provider": "anthropic", "monthly_usd": 1.0, "model": "optional"}}}

load() returns this app's entry with only the key for its own provider, or None, which means the admin's AI is off.
It fails closed: a missing or unreadable file, bad JSON, an unknown version, no entry, "ai" not true, an unknown
provider, a bad monthly limit or an empty key all give None, with a one-line reason in the log (never the contents).

The path is the COUGAR_AI_SETTINGS environment variable, or the format's default. The file is read again only when
its modified time or size changes, checked when load() is called; there is no thread, timer or polling.
"""

from __future__ import annotations

import json
import logging
import os
import threading

APP = "deals"
DEFAULT_PATH = "/run/cougar-ai/settings.json"
VERSION = 1
# The file's provider names, and Deal Hunter's names for them.
PROVIDERS = {"anthropic": "claude", "openai": "openai", "gemini": "gemini"}

log = logging.getLogger("deal-hunter.ai")
_lock = threading.Lock()
_cache: dict = {}    # path -> ((mtime_ns, size), parsed JSON or None, reason)
_logged: dict = {}   # (path, app) -> the last reason logged
reads = 0            # how many times the file was actually read (the tests count it)


def path() -> str:
    return os.environ.get("COUGAR_AI_SETTINGS") or DEFAULT_PATH


def _read(where: str):
    global reads
    try:
        st = os.stat(where)
    except OSError:
        with _lock:
            _cache.pop(where, None)
        return None, "the AI settings file is missing"
    stamp = (st.st_mtime_ns, st.st_size)
    with _lock:
        cached = _cache.get(where)
        if cached and cached[0] == stamp:
            return cached[1], cached[2]
        reads += 1
        try:
            with open(where, encoding="utf-8") as f:
                data, reason = json.load(f), ""
        except OSError:
            data, reason = None, "the AI settings file can't be read"
        except ValueError:
            data, reason = None, "the AI settings file isn't valid JSON"
        _cache[where] = (stamp, data, reason)
        return data, reason


def _entry(data, app: str):
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return None, "the AI settings file has an unknown version"
    apps = data.get("apps") if isinstance(data.get("apps"), dict) else {}
    entry = apps.get(app)
    if not isinstance(entry, dict):
        return None, f"the AI settings file has no entry for {app}"
    if entry.get("ai") is not True:
        return None, f"AI is off for {app} in the AI settings file"
    provider = entry.get("provider")
    if provider not in PROVIDERS:
        return None, f"{app} has an unknown provider in the AI settings file"
    limit = entry.get("monthly_usd")
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not 0 <= limit <= 1000:
        return None, f"{app} has no valid monthly_usd in the AI settings file"
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    key = keys.get(provider)
    if not isinstance(key, str) or not key.strip():
        return None, f"the AI settings file has no {provider} key for {app}"
    model = entry.get("model")
    return {"provider": PROVIDERS[provider], "model": model if isinstance(model, str) and model else None,
            "monthly_usd": float(limit), "key": key.strip()}, ""


def load(app: str = APP, where: str | None = None) -> dict | None:
    """This app's AI from the shared file: {"provider", "model" (or None), "monthly_usd", "key"}, or None (off)."""
    where = where or path()
    data, reason = _read(where)
    result, why = (None, reason) if data is None else _entry(data, app)
    with _lock:
        if why and _logged.get((where, app)) != why:
            log.warning("AI settings: %s; the admin's AI is off", why)
        _logged[(where, app)] = why
    return result
