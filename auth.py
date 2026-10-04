"""Accounts: password hashing, sign-in tokens and a simple guard against password guessing.

Built to be safe on the open internet later (Sam plans a public API): passwords are stored only as
scrypt hashes, tokens only as SHA-256 hashes, comparisons are constant-time, and repeated failed sign-ins
from one address are slowed down.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

import db

SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
MIN_PASSWORD = 8


class AuthError(Exception):
    pass


# ---- passwords ------------------------------------------------------------------

def hash_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return "scrypt${n}${r}${p}${salt}${hash}".format(
        **SCRYPT, salt=base64.b64encode(salt).decode(), hash=base64.b64encode(digest).decode())


def verify_password(password: str, stored: str | None) -> bool:
    try:
        _, n, r, p, salt, digest = (stored or "").split("$")
        got = hashlib.scrypt((password or "").encode(), salt=base64.b64decode(salt), dklen=32,
                             n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# ---- tokens ---------------------------------------------------------------------
# One token per browser or device ("Pixel 10"). Only a hash is stored, so a copy of the database can't
# be used to sign in.

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(user_id: int, kind: str, name: str) -> str:
    token = "dht_" + secrets.token_urlsafe(32)
    db.execute("INSERT INTO tokens (user_id, token_hash, kind, name, created_at) VALUES (?, ?, ?, ?, ?)",
               (user_id, _hash(token), kind, (name or kind)[:60], time.time()))
    return token


def user_for_token(token: str | None) -> dict | None:
    if not token or not token.startswith("dht_"):
        return None
    rows = db.query("""SELECT u.*, t.id AS token_id FROM tokens t JOIN users u ON u.id = t.user_id
                       WHERE t.token_hash = ?""", (_hash(token),))
    if not rows or rows[0]["disabled"]:
        return None
    db.execute("UPDATE tokens SET last_used = ? WHERE id = ?", (time.time(), rows[0]["token_id"]))
    return rows[0]


def revoke_token(token: str) -> None:
    db.execute("DELETE FROM tokens WHERE token_hash = ?", (_hash(token),))


# ---- password guessing ----------------------------------------------------------

FAIL_LIMIT = 5
FAIL_WINDOW = 600  # seconds
_fails: dict[str, list[float]] = {}
_fail_lock = threading.Lock()


def check_rate(address: str) -> None:
    with _fail_lock:
        recent = [t for t in _fails.get(address, []) if time.time() - t < FAIL_WINDOW]
        _fails[address] = recent
        if len(recent) >= FAIL_LIMIT:
            raise AuthError("Too many failed sign-ins; try again in a few minutes")


def note_failure(address: str) -> None:
    with _fail_lock:
        _fails.setdefault(address, []).append(time.time())


# Checked against when the email doesn't exist, so a wrong email takes as long as a wrong password and
# timing can't reveal who has an account.
_DUMMY_HASH = hash_password("not-a-real-password")


def sign_in(email: str, password: str, address: str) -> dict:
    email_key = "email:" + (email or "").strip().lower()
    check_rate(address)
    check_rate(email_key)  # guessing one account from many addresses is limited too
    rows = db.query("SELECT * FROM users WHERE lower(email) = lower(?)", ((email or "").strip(),))
    user = rows[0] if rows else None
    ok = verify_password(password, user["password_hash"] if user else _DUMMY_HASH)
    if not user or user["disabled"] or not ok:
        note_failure(address)
        note_failure(email_key)
        raise AuthError("Wrong email or password")
    return user


def public_user(user: dict) -> dict:
    return {k: user.get(k) for k in ("id", "name", "email", "role")}
