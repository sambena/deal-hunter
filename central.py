"""Cougar Cave central sign-in: who may sign in here, and the switch that turns it off.

A validated central login is only an identity. It becomes a Deal Hunter session only for a person the
owner linked by exact (issuer, subject) to an existing account, never by email, while both the link and
the account are active. Standard library only; the protocol side is in oidc.py.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time

import auth
import db


class CentralError(Exception):
    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


OWNER_FACTORS = ("Admin sign-in needs your password and your authenticator code in the same sign-in. This "
                 "browser's Cougar Cave session may have started with a code only. Sign in from a private window, "
                 "or clear this browser's cookies for the sign-in service, then try again.")


@contextlib.contextmanager
def _write():
    """One SQLite write transaction. BEGIN IMMEDIATE takes the database's write lock, so a check and the change
    that depends on it can't be split by a disable or a switch-off, from this server or from central_admin.py."""
    with db._lock:
        c = db.conn()
        if c.in_transaction:  # left open by an earlier failure: never let it carry this change, or hide in it
            c.rollback()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.commit()  # inside the try: a commit that fails (database busy) rolls back instead of staying open
        except BaseException:
            c.rollback()
            raise


def audit(action: str, detail: str, conn=None) -> None:
    # Ids, names and roles only: never codes, tokens, secrets or email addresses.
    (conn or db).execute("INSERT INTO central_audit (at, action, detail) VALUES (?, ?, ?)",
                         (time.time(), action, detail[:300]))


def audit_log(limit: int = 50) -> list[dict]:
    return db.query("SELECT at, action, detail FROM central_audit ORDER BY id DESC LIMIT ?", (int(limit),))


# ---- linking (owner only, from central_admin.py) ---------------------------------

def link(user_id: int, issuer: str, subject: str) -> int:
    """Attach one central identity to one existing account. Exact subject; never matched by email."""
    issuer, subject = str(issuer or "").strip(), str(subject or "").strip()
    if not issuer or not subject or len(subject) > 255:
        raise CentralError("Issuer and subject are required.", 400)
    users = db.query("SELECT id, role, disabled FROM users WHERE id = ?", (int(user_id),))
    if not users:
        raise CentralError("No such account.", 404)
    if users[0]["disabled"]:
        raise CentralError("That account is disabled; enable it before linking.", 409)
    if db.query("SELECT 1 FROM central_identities WHERE issuer = ? AND subject = ?", (issuer, subject)):
        raise CentralError("That central identity is already linked.", 409)
    if db.query("SELECT 1 FROM central_identities WHERE user_id = ?", (int(user_id),)):
        raise CentralError("That account already has a central identity.", 409)
    ident = db.execute("INSERT INTO central_identities (issuer, subject, user_id, created_at) VALUES (?, ?, ?, ?)",
                       (issuer, subject, int(user_id), time.time()))
    audit("link", f"identity {ident} to account {int(user_id)} ({users[0]['role']}), by exact subject")
    return ident


def identities() -> list[dict]:
    return db.query("""SELECT c.id, c.issuer, c.subject, c.user_id, c.status, c.created_at, u.name, u.role
                       FROM central_identities c JOIN users u ON u.id = c.user_id ORDER BY c.id""")


def set_status(ident: int, status: str) -> None:
    """Disable or re-enable a link. Disabling ends that person's central sessions at once."""
    if status not in ("active", "disabled"):
        raise CentralError("Status must be active or disabled.", 400)
    with _write() as c:
        if not c.execute("UPDATE central_identities SET status = ? WHERE id = ?", (status, int(ident))).rowcount:
            raise CentralError("No such identity.", 404)
        if status == "disabled":
            c.execute("DELETE FROM tokens WHERE identity_id = ?", (int(ident),))
        audit("identity_" + status, f"identity {int(ident)}", c)


# ---- the switch (rollback) -----------------------------------------------------------

def switch_on() -> bool:
    return auth.central_on()


def set_switch(state: str) -> None:
    """Off ends every central session and refuses new ones. Links, disabled people and the app's own
    sign-in are untouched, so turning it back on revives nobody who was disabled."""
    if state not in ("on", "off"):
        raise CentralError("State must be on or off.", 400)
    with _write() as c:
        c.execute("INSERT OR REPLACE INTO central_state (key, value) VALUES ('central', ?)", (state,))
        if state == "off":
            c.execute("DELETE FROM tokens WHERE via = 'central'")
        audit("central_" + state, "central sign-in " + state, c)


# ---- turning a validated login into a session --------------------------------------

def sign_in(issuer: str, subject: str, auth_time: float, amr) -> tuple[dict, str]:
    """Mint a browser session for a validated central login. Refuses anyone not linked and active. The checks
    and the new session are one transaction, so a disable or switch-off can't land between them."""
    with _write() as c:
        state = c.execute("SELECT value FROM central_state WHERE key = 'central'").fetchone()
        if state is not None and state["value"] != "on":
            raise CentralError("Cougar Cave sign-in is turned off for Deal Hunter.")
        row = c.execute("""SELECT c.id AS identity_id, c.status, u.* FROM central_identities c
                           JOIN users u ON u.id = c.user_id WHERE c.issuer = ? AND c.subject = ?""",
                        (issuer, subject)).fetchone()
        if row is None or row["status"] != "active" or row["disabled"]:
            raise CentralError("This account has no access to Deal Hunter.")
        user = dict(row)
        # A code-only sign-in carries amr ["mfa"] with no "pwd"; admin access needs both in the same sign-in.
        both = isinstance(amr, list) and "pwd" in amr and "mfa" in amr
        if user["role"] == "admin" and not both:
            raise CentralError(OWNER_FACTORS)
        token = _mint(c, user, float(auth_time), amr)
        audit("sign_in", f"account {user['id']} ({user['role']}) via identity {user['identity_id']}", c)
    return user, token


def _mint(conn, user: dict, auth_time: float, amr=None) -> str:
    methods = json.dumps([m for m in amr if isinstance(m, str)]) if isinstance(amr, list) else "[]"
    return auth.create_token(user["id"], "web", "Cougar Cave sign-in", via="central", auth_time=auth_time,
                             identity_id=user["identity_id"], conn=conn, amr=methods)


# ---- privileged actions (#412) -----------------------------------------------------------

FRESH_SECONDS = 300  # a privileged change needs a sign-in this recent
SKEW_SECONDS = 30

NEEDS_BOTH = ("This needs a Cougar Cave sign-in that used both your password and your authenticator code. "
              "Sign out, then use Sign in with Cougar Cave.")


def stale_message(age: float) -> str:
    return (f"For this change, sign in again: your last Cougar Cave sign-in was {int(age // 60)} minutes ago. "
            "Sign out, then use Sign in with Cougar Cave from a private window so it asks for your password and code.")


def enforced_for(user_id: int) -> bool:
    """The guard applies to accounts linked to a central identity while central sign-in is on. Accounts not
    linked yet keep the app's own sign-in for everything until they link (#323), so nobody is locked out."""
    return auth.central_configured() and auth.central_on() and bool(
        db.query("SELECT 1 FROM central_identities WHERE user_id = ?", (int(user_id),)))


def check_privileged(user: dict, write: bool, now: float | None = None) -> None:
    """Refuse a privileged request unless this session came from a central sign-in with both pwd and mfa, and,
    for a change, one made in the last five minutes. There is no forced re-authentication yet (the provider
    ignores max_age=0 and shows "Not Found" for prompt=login), so a stale sign-in gets a message, not a loop."""
    if not enforced_for(user["id"]):
        return
    try:
        methods = json.loads(user.get("amr") or "[]") if user.get("via") == "central" else []
    except ValueError:
        methods = []
    if not (isinstance(methods, list) and "pwd" in methods and "mfa" in methods):
        raise CentralError(NEEDS_BOTH)
    if write:
        age = (now if now is not None else time.time()) - float(user.get("auth_time") or 0)
        if not -SKEW_SECONDS <= age <= FRESH_SECONDS:
            raise CentralError(stale_message(age))


# ---- throttling the unauthenticated begin route ---------------------------------------

BEGIN_LIMIT = 30  # per address in ten minutes; ten times that for everyone together
_begins: dict[str, list[float]] = {}
_begin_lock = threading.Lock()


def throttle(address: str, window: float = 600) -> None:
    with _begin_lock:
        now = time.time()
        for key, limit in (("ip:" + address, BEGIN_LIMIT), ("all", BEGIN_LIMIT * 10)):
            recent = [t for t in _begins.get(key, []) if now - t < window]
            if len(recent) >= limit:
                raise CentralError("Too many sign-in attempts. Try again in ten minutes.", 429)
            _begins[key] = recent
        for key in ("ip:" + address, "all"):
            _begins[key].append(now)
        if len(_begins) > 5000:  # random addresses can't grow the table without bound
            for key in [k for k, ts in _begins.items() if k != "all" and (not ts or now - ts[-1] >= window)]:
                del _begins[key]
