"""Owner commands for Cougar Cave central sign-in. Run on the machine that holds Deal Hunter's database.

    python central_admin.py accounts               accounts here: id, name, role, linked or not
    python central_admin.py link ACCOUNT SUBJECT   link one account to a central person (their central user id)
    python central_admin.py list                   linked identities
    python central_admin.py disable IDENTITY       end that person's central sessions and refuse new ones
    python central_admin.py enable IDENTITY
    python central_admin.py central on|off         the rollback switch (off ends every central session)
    python central_admin.py audit                  the last 50 central sign-in events

Linking is by exact subject, never by email. The issuer comes from config.json "oidc", so it can't be mistyped.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import central
import db

CONFIG = Path(__file__).resolve().parent / "config.json"


def _issuer() -> str:
    config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    issuer = (config.get("oidc") or {}).get("issuer")
    if not issuer:
        raise SystemExit('config.json has no "oidc" issuer; set up central login first')
    return issuer


def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def run(args: list[str], out=print) -> int:
    cmd, rest = (args[0], args[1:]) if args else ("", [])
    try:
        if cmd == "accounts" and not rest:
            linked = {r["user_id"]: r for r in central.identities()}
            for u in db.query("SELECT id, name, role, disabled FROM users ORDER BY id"):
                c = linked.get(u["id"])
                line = f"{u['id']:>4}  {u['name']:<24} {u['role']:<7}"
                if u["disabled"]:
                    line += " disabled"
                if c:
                    line += f"  linked (identity {c['id']}, {c['status']})"
                out(line)
        elif cmd == "link" and len(rest) == 2:
            ident = central.link(int(rest[0]), _issuer(), rest[1])
            out(f"Linked account {int(rest[0])} as identity {ident}.")
        elif cmd == "list" and not rest:
            for r in central.identities():
                out(f"{r['id']:>4}  account {r['user_id']} {r['name']} ({r['role']})  {r['status']}  "
                    f"subject {r['subject']}  since {_when(r['created_at'])}")
        elif cmd in ("disable", "enable") and len(rest) == 1:
            central.set_status(int(rest[0]), "disabled" if cmd == "disable" else "active")
            out(f"Identity {int(rest[0])} {cmd}d.")
        elif cmd == "central" and len(rest) == 1:
            central.set_switch(rest[0])
            out(f"Central sign-in is {rest[0]}.")
        elif cmd == "audit" and not rest:
            for r in central.audit_log():
                out(f"{_when(r['at'])}  {r['action']:<16} {r['detail']}")
        else:
            out(__doc__)
            return 2
    except central.CentralError as e:
        out(f"Refused: {e}")
        return 1
    except ValueError:
        out("ACCOUNT and IDENTITY are numbers; see `python central_admin.py accounts` and `list`.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
