"""Pairing a phone through Cougar Cave central login (#541), to the pairing API contract v1 that every Cougar Cave
app speaks (cougar-cave-ui docs/work/502-*.md, with #526's changes), so one phone client works everywhere.

A person signed in centrally in a browser presses Add a phone and gets a 6-digit PIN (and a QR of it). The phone
sends the PIN and gets its own credential, which it then sends as `Authorization: Bearer` on both ports. The
credential belongs to the person's central identity, so everything that ends their central sessions ends the phone:
the identity disabled or unlinked, the account disabled, central sign-in switched off (403 no_access, checked live)
or a back-channel logout (401 device_revoked). A role change ends it too. Only hashes are stored, and the audit log
holds ids and names, never a PIN or a credential.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time

import auth
import central
import db

PAIR_SECONDS = 120
PAIR_WRONG_TRIES = 3          # failed pairings, app-wide, before every outstanding PIN dies
PIN_DIGITS = 6
DEVICE_SECONDS = 30 * 86400   # absolute, from pairing
DEVICE_IDLE_SECONDS = 7 * 86400
DEVICE_ROTATE_SECONDS = 86400
DEVICE_GRACE_SECONDS = 60
PAIR_LIMIT = 5                # pairing tries per address per minute
PAIR_LIMIT_ALL = 60           # and from everywhere together
PREFIX = "dhp_"               # tells a paired phone's credential from the app's own tokens (dht_)
ROTATE_HEADER = "Cave-Device-Rotate"
APP = "Deal Hunter"

_rotated: dict[str, tuple[dict, float]] = {}  # replaced hash -> (rotate reply, until): process memory only (#526)
_rotated_lock = threading.Lock()


class PairError(Exception):
    """A refusal in the contract's shape: {"error": code, "message": text}."""
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _credential(credential: str, device: str, rotated: float, expires: float) -> dict:
    return {"credential": credential, "device_id": device, "expires_at": int(expires),
            "rotate_after": int(rotated + DEVICE_ROTATE_SECONDS)}


def _fresh_central(user: dict | None) -> None:
    """Making a PIN needs a central browser sign-in with password and code from the last five minutes."""
    if user and user.get("device"):
        raise PairError(403, "bearer_not_allowed", "Add a phone from a signed-in browser, not from a phone.")
    if not user:
        raise PairError(401, "signed_out", "Sign in first.")
    try:
        methods = json.loads(user.get("amr") or "[]") if user.get("via") == "central" else []
    except ValueError:
        methods = []
    age = time.time() - float(user.get("auth_time") or 0)
    if not (user.get("identity_id") and isinstance(methods, list) and "mfa" in methods
            and -central.SKEW_SECONDS <= age <= central.FRESH_SECONDS):
        raise PairError(403, "needs_recent_signin",
                        "Adding a phone needs a recent Cougar Cave sign-in. Sign in again, then add the phone.")


def throttle(address: str) -> None:
    try:
        central.throttle_backchannel("pair:" + address, PAIR_LIMIT, window=60)
        central.throttle_backchannel("pair:all", PAIR_LIMIT_ALL, window=60)
    except central.CentralError:
        raise PairError(429, "rate_limited", "Too many tries. Wait a minute, then try again.") from None


def pair_code(user: dict | None) -> tuple[str, int]:
    """A PIN for the signed-in person. A new one cancels their earlier unused PINs."""
    _fresh_central(user)
    with central._write() as c:
        now = time.time()
        c.execute("DELETE FROM pair_codes WHERE expires <= ?", (now - 3600,))
        c.execute("UPDATE pair_codes SET used = 2 WHERE identity_id = ? AND used = 0", (user["identity_id"],))
        while True:  # never two live PINs alike
            pin = f"{secrets.randbelow(10 ** PIN_DIGITS):0{PIN_DIGITS}d}"
            if not c.execute("SELECT 1 FROM pair_codes WHERE pin_hash = ? AND used = 0 AND expires > ?",
                             (_digest(pin), now)).fetchone():
                break
        code_id = secrets.token_hex(8)
        c.execute("INSERT INTO pair_codes (id, pin_hash, identity_id, expires) VALUES (?, ?, ?, ?)",
                  (code_id, _digest(pin), user["identity_id"], now + PAIR_SECONDS))
        central.audit("pair_code", f"code {code_id} identity {user['identity_id']}", c)
    return pin, PAIR_SECONDS


def pair(pin, name, user: dict | None = None) -> dict:
    """Turn a PIN into a credential for the person who made it. A PIN carries no id, so the guess limit is app-wide:
    every failed attempt counts against every outstanding PIN, and after PAIR_WRONG_TRIES all of them are dead. Wrong,
    used, expired, cancelled or someone else's (user: a browser on the same request) is one refusal, pin_invalid."""
    if not isinstance(pin, str) or not isinstance(name, str) or len(name) > 60:
        raise PairError(400, "bad_request", 'Send {"pin": "123456", "name": "the phone, at most 60 characters"}.')
    name = "".join(ch for ch in " ".join(name.split()) if ch.isprintable()) or "Phone"
    valid = len(pin) == PIN_DIGITS and pin.isascii() and pin.isdigit()
    given, refusal, why = _digest(pin), None, ""
    with central._write() as c:
        now = time.time()
        row = None
        for code in c.execute("SELECT * FROM pair_codes WHERE used = 0 AND expires > ? AND wrong < ?",
                              (now, PAIR_WRONG_TRIES)).fetchall():
            if secrets.compare_digest(given, code["pin_hash"]):  # every row, no early exit
                row = code
        person = row and c.execute(
            "SELECT c.id, c.status, c.user_id, u.name, u.role, u.disabled FROM central_identities c "
            "JOIN users u ON u.id = c.user_id WHERE c.id = ?", (row["identity_id"],)).fetchone()
        if not row:
            old = c.execute("SELECT used, expires FROM pair_codes WHERE pin_hash = ? ORDER BY expires DESC",
                            (given,)).fetchone() if valid else None
            why = ("not a pin" if not valid else "wrong" if not old else "used" if old["used"] == 1 else
                   "cancelled" if old["used"] == 2 else "expired" if old["expires"] <= now else "too many tries")
        elif user and user.get("identity_id") != row["identity_id"]:
            why = "someone else's"
        elif not person or person["status"] != "active" or person["disabled"] or not auth.central_on():
            refusal, why = PairError(403, "no_access", f"This account has no access to {APP}."), "no access"
        if why:  # counted and kept: the refusal is raised after this transaction commits
            refusal = refusal or PairError(400, "pin_invalid", "That PIN is not valid. Check it, or press Add a phone again.")
            c.execute("UPDATE pair_codes SET wrong = wrong + 1 WHERE used = 0 AND expires > ?", (now,))
            central.audit("pair_refused", why, c)
        else:
            c.execute("UPDATE pair_codes SET used = 1 WHERE id = ?", (row["id"],))
            credential, device = PREFIX + secrets.token_urlsafe(32), secrets.token_hex(12)
            c.execute("INSERT INTO paired_devices (id, identity_id, role, name, cred_hash, created, rotated, seen, expires) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (device, person["id"], person["role"], name,
                                                             _digest(credential), now, now, now, now + DEVICE_SECONDS))
            central.audit("pair", f"device {device} identity {person['id']} {person['role']}", c)
    if refusal:
        raise refusal
    return {**_credential(credential, device, now, now + DEVICE_SECONDS),
            "person": {"name": person["name"], "role": "owner" if person["role"] == "admin" else "member"}}


def device(credential: str | None) -> dict:
    """The account behind a phone's credential, checked live on every request like a central session. Returns the
    user row with device details, or raises PairError: signed_out (unknown), device_revoked (removed, logged out,
    role changed, or an old credential reused after its grace), device_expired (idle or absolute), no_access (the
    identity or account disabled, or central sign-in off; re-enabling brings the phone back)."""
    if not credential or len(credential) > 128 or not credential.startswith(PREFIX):
        raise PairError(401, "signed_out", "Sign in first.")
    now, h = time.time(), _digest(credential)
    with central._write() as c:
        row = c.execute(
            "SELECT d.*, c.status AS identity_status, c.user_id, u.role AS user_role, u.disabled "
            "FROM paired_devices d LEFT JOIN central_identities c ON c.id = d.identity_id "
            "LEFT JOIN users u ON u.id = c.user_id WHERE d.cred_hash = ? OR d.old_hash = ?", (h, h)).fetchone()
        if not row:
            raise PairError(401, "signed_out", "Sign in first.")
        via_old = row["cred_hash"] != h
        if via_old and now > row["old_until"] and not row["revoked"]:
            # A rotated-away credential after its grace: a copy exists, so the phone ends (RFC 9700's reuse rule).
            c.execute("UPDATE paired_devices SET revoked = 1, why = 'reuse' WHERE id = ?", (row["id"],))
            central.audit("device_reuse", f"device {row['id']}: revoked", c)
            revoked = True
        else:
            revoked = bool(row["revoked"])
        if not revoked and (row["user_id"] is None or row["identity_status"] != "active" or row["disabled"]
                            or not auth.central_on() or not auth.central_configured()):
            raise PairError(403, "no_access", f"This account has no access to {APP}.")
        if not revoked and row["user_role"] != row["role"]:
            revoked = True
        if not revoked and (row["expires"] <= now or now - row["seen"] > DEVICE_IDLE_SECONDS):
            raise PairError(401, "device_expired", "This phone has been signed out. Pair it again.")
        if not revoked and now - row["seen"] > 60:
            c.execute("UPDATE paired_devices SET seen = ? WHERE id = ?", (now, row["id"]))
    if revoked:
        raise PairError(401, "device_revoked", "This phone was removed. Pair it again.")
    user = db.query("SELECT * FROM users WHERE id = ?", (row["user_id"],))[0]
    return {**user, "token_id": None, "via": "device-pair", "identity_id": row["identity_id"], "auth_time": None,
            "amr": None, "device": row["id"], "via_old": via_old,
            "rotate_due": not via_old and now >= row["rotated"] + DEVICE_ROTATE_SECONDS}


def rotate(user: dict | None, presented: str) -> dict:
    """A new credential for the phone asking. The one it sent keeps working for DEVICE_GRACE_SECONDS; asked again
    with it inside that window, the same reply comes back (from process memory only), so a lost reply never locks
    the phone out. Otherwise an old credential cannot rotate again, and no second credential is ever minted."""
    if not user or not user.get("device"):
        raise PairError(401, "signed_out", "Only a paired phone can do that.")
    with central._write() as c, _rotated_lock:
        now = time.time()
        for k in [k for k, (_, until) in _rotated.items() if until <= now]:
            del _rotated[k]
        row = c.execute("SELECT * FROM paired_devices WHERE id = ? AND revoked = 0", (user["device"],)).fetchone()
        if not row:
            raise PairError(401, "device_revoked", "This phone was removed. Pair it again.")
        if user.get("via_old"):
            reply, _ = _rotated.get(_digest(presented), (None, 0))
            if reply and row["old_hash"] == _digest(presented) and row["cred_hash"] == _digest(reply["credential"]):
                central.audit("device_rotate_replay", f"device {row['id']}", c)
                return dict(reply)
            raise PairError(401, "device_rotated", "This credential was already replaced. Use the newer one.")
        credential = PREFIX + secrets.token_urlsafe(32)
        c.execute("UPDATE paired_devices SET cred_hash = ?, old_hash = ?, old_until = ?, rotated = ?, seen = ? WHERE id = ?",
                  (_digest(credential), row["cred_hash"], now + DEVICE_GRACE_SECONDS, now, now, row["id"]))
        central.audit("device_rotate", f"device {row['id']}", c)
        reply = _credential(credential, row["id"], now, row["expires"])
        _rotated[row["cred_hash"]] = (reply, now + DEVICE_GRACE_SECONDS)
    return dict(reply)


def devices(user: dict) -> list[dict]:
    """This person's phones that still work. Never a credential."""
    now = time.time()
    return [{"id": r["id"], "name": r["name"], "created": int(r["created"]), "last_used": int(r["seen"]),
             "current": r["id"] == user.get("device")} for r in db.query(
        "SELECT id, name, created, seen FROM paired_devices WHERE identity_id = ? AND revoked = 0 AND expires > ? "
        "AND seen > ? ORDER BY created", (user.get("identity_id") or 0, now, now - DEVICE_IDLE_SECONDS))]


def revoke(user: dict, device_id: str) -> None:
    with central._write() as c:
        n = c.execute("UPDATE paired_devices SET revoked = 1, why = 'removed' WHERE id = ? AND identity_id = ? AND revoked = 0",
                      (device_id, user.get("identity_id") or 0)).rowcount
        if not n:
            raise PairError(404, "not_found", "No such phone.")
        central.audit("device_revoke", f"device {device_id}", c)


def revoke_identity(identity_id: int, why: str, c) -> int:
    """Every phone of one identity, inside the caller's transaction (a back-channel logout)."""
    return c.execute("UPDATE paired_devices SET revoked = 1, why = ? WHERE identity_id = ? AND revoked = 0",
                     (why, identity_id)).rowcount
