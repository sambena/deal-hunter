"""Test helper: the owner console's shared AI settings file, as a temp file with fake keys, and
COUGAR_AI_SETTINGS pointing at it for the length of one test."""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

_tick = [1_700_000_000_000_000_000]


def contract(provider="anthropic", key="fake-key", monthly=5.0, model=None, ai=True, app="deals", keys=None):
    entry = {"ai": ai, "provider": provider, "monthly_usd": monthly}
    if model:
        entry["model"] = model
    all_keys = {"anthropic": "", "openai": "", "gemini": ""}
    all_keys[provider] = key
    all_keys.update(keys or {})
    return {"version": 1, "updated_at": "2026-10-25T21:00:00Z", "keys": all_keys, "apps": {app: entry}}


def write(path: Path, data) -> None:
    """Write the file with a new modified time, as the console's write-then-rename gives."""
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    _tick[0] += 1_000_000_000
    os.utime(path, ns=(_tick[0], _tick[0]))


def use(test, **kw) -> Path:
    """Point this test at a fresh settings file built from contract(**kw); undone when the test ends."""
    folder = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, folder, True)
    path = folder / "settings.json"
    write(path, contract(**kw))
    patcher = mock.patch.dict(os.environ, {"COUGAR_AI_SETTINGS": str(path)})
    patcher.start()
    test.addCleanup(patcher.stop)
    return path
