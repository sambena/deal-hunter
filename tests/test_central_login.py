"""Cougar Cave central sign-in over real HTTP: a working valid sign-in beside each specific rejection.

A fake provider signs RS256 ID tokens; Deal Hunter's real transaction store, token validation, linking,
sessions and server run for real. Nothing here contacts the real login service. Needs requirements.txt.
"""

import contextlib
import http.client
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt  # noqa: E402  (requirements.txt: these tests fail, not skip, without the packages)
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import auth  # noqa: E402
import central  # noqa: E402
import central_admin  # noqa: E402
import db  # noqa: E402
import oidc  # noqa: E402
from cougar_login.login_validation import Provider, Transactions  # noqa: E402

ISSUER = "http://127.0.0.1:9/application/o/deals/"
CLIENT = "deals-test-client"
CALLBACK = "http://127.0.0.1/auth/callback"


def make_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    return private, {**jwk, "kid": "good", "use": "sig", "alg": "RS256"}


KEY, JWK = make_key()
OTHER_KEY, _ = make_key()  # same kid, different key: a forged signature


class FakeCache:
    def get(self, kid):
        return {"keys": [JWK]}


class Provided:
    tokens = None   # what the fake token endpoint returns next
    seen = None     # what Deal Hunter sent it


def fake_exchange(code, verifier):
    Provided.seen = (code, verifier)
    return Provided.tokens


def id_token(nonce, sub="owner-uuid", key=KEY, iss=ISSUER, aud=CLIENT, amr=("pwd", "mfa"), exp_in=300, extra=None):
    now = int(time.time())
    claims = {"iss": iss, "sub": sub, "aud": aud, "iat": now, "exp": now + exp_in, "nonce": nonce,
              "auth_time": now - 1, "amr": list(amr), **(extra or {})}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "good"})


class Browser:
    """Keeps cookies by name, follows no redirects, and reports exactly what the server answered."""
    def __init__(self, port):
        self.port, self.jar = port, {}

    def get(self, path, method="GET", body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if self.jar:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.jar.items())
        if body is not None:
            h["Content-Type"] = "application/json"
        conn.request(method, path, None if body is None else json.dumps(body), h)
        r = conn.getresponse()
        data = r.read()
        cookies = r.headers.get_all("Set-Cookie") or []
        for c in cookies:
            name, _, rest = c.partition("=")
            value = rest.split(";", 1)[0]
            if "Max-Age=0" in c:
                self.jar.pop(name, None)
            else:
                self.jar[name] = value
        conn.close()
        return r.status, r.headers, data, cookies


class CentralLoginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server
        cls.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(cls.tmp.name) / "t.db"
        cls.ui = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        for s in (cls.ui, cls.api):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        provider = Provider(ISSUER, CLIENT, CALLBACK, allow_loopback=True)
        cls.login = oidc.CentralLogin(provider, "http://127.0.0.1:9/application/o/authorize/",
                                      Transactions(Path(cls.tmp.name) / "tx.db"), FakeCache(), fake_exchange)
        cls.admin = db.admin_id()
        db.execute("UPDATE users SET email = 'sam@example.com', name = 'Sam', password_hash = ? WHERE id = ?",
                   (auth.hash_password("admin pass 1"), cls.admin))
        cls.member = db.execute("INSERT INTO users (email, name, password_hash, role, created_at) "
                                "VALUES ('sis@example.com', 'Sis', ?, 'member', ?)",
                                (auth.hash_password("sister pass 1"), time.time()))
        cls.owner_ident = central.link(cls.admin, ISSUER, "owner-uuid")
        cls.member_ident = central.link(cls.member, ISSUER, "member-uuid")

    @classmethod
    def tearDownClass(cls):
        oidc.LOGIN["central"] = None
        cls.ui.shutdown()
        cls.api.shutdown()
        if db._conn:
            db._conn.close()
        db._conn = None
        cls.tmp.cleanup()

    def setUp(self):
        oidc.LOGIN["central"] = self.login
        central._begins.clear()
        auth._fails.clear()

    def tearDown(self):
        # Leave every test with the switch on and both people linked and active.
        central.set_switch("on")
        central.set_status(self.owner_ident, "active")
        central.set_status(self.member_ident, "active")
        db.execute("UPDATE users SET disabled = 0")

    # ---- helpers -------------------------------------------------------------------

    def browser(self, port=None):
        return Browser(port or self.ui.server_port)

    def begin(self, b):
        status, headers, _, cookies = b.get("/auth/central")
        self.assertEqual(status, 302)
        location = urllib.parse.urlsplit(headers["Location"])
        return urllib.parse.parse_qs(location.query), location, cookies

    def sign_in(self, b=None, state=None, sub="owner-uuid", **token_args):
        """One whole central sign-in; returns (status, body, the browser, the authorization request)."""
        b = b or self.browser()
        q, _, _ = self.begin(b)
        Provided.tokens = {"token_type": "Bearer", "access_token": "opaque-access",
                           "id_token": id_token(q["nonce"][0], sub=sub, **token_args)}
        status, _, body, _ = b.get("/auth/callback?" + urllib.parse.urlencode(
            {"code": "one-time-code", "state": state or q["state"][0]}))
        return status, body.decode(), b, q

    def state_of(self, b):
        return b.get("/api/state")[0]

    # ---- the valid control -----------------------------------------------------------

    def test_valid_admin_sign_in_uses_pkce_and_reaches_the_app(self):
        b = self.browser()
        q, location, cookies = self.begin(b)
        self.assertTrue(location.path.endswith("/authorize/"))
        self.assertEqual((q["response_type"], q["client_id"], q["redirect_uri"], q["scope"], q["code_challenge_method"]),
                         (["code"], [CLIENT], [CALLBACK], ["openid"], ["S256"]))
        self.assertNotIn("prompt", q)
        self.assertNotIn("max_age", q)
        tx = [c for c in cookies if c.startswith("dh_login=")]
        self.assertEqual(len(tx), 1)
        self.assertIn("HttpOnly", tx[0])
        self.assertIn("SameSite=Lax", tx[0])
        self.assertEqual(self.state_of(b), 401)  # not signed in yet
        Provided.tokens = {"token_type": "Bearer", "access_token": "opaque-access", "id_token": id_token(q["nonce"][0])}
        status, _, body, _ = b.get("/auth/callback?" + urllib.parse.urlencode({"code": "abc", "state": q["state"][0]}))
        self.assertEqual(status, 200)
        self.assertIn(b'http-equiv="refresh" content="0;url=/"', body)
        self.assertEqual(Provided.seen[0], "abc")
        self.assertIn("dh_session", b.jar)
        self.assertNotIn("dh_login", b.jar)  # the one-use transaction cookie is cleared
        self.assertEqual(self.state_of(b), 200)
        _, _, me, _ = b.get("/api/me")
        self.assertEqual(json.loads(me)["id"], self.admin)
        row = db.query("SELECT kind, via, identity_id, auth_time FROM tokens WHERE via = 'central' ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual((row["kind"], row["identity_id"]), ("web", self.owner_ident))
        self.assertIsNotNone(row["auth_time"])

    def test_member_signs_in_as_themselves_not_as_the_admin(self):
        status, _, b, _ = self.sign_in(sub="member-uuid", amr=("mfa",))
        self.assertEqual(status, 200)
        _, _, me, _ = b.get("/api/me")
        self.assertEqual(json.loads(me)["id"], self.member)
        self.assertEqual(b.get("/api/admin/users")[0], 403)

    # ---- token rejections, each beside the valid sign-in above ---------------------------

    def test_invalid_id_tokens_are_refused_with_no_session(self):
        cases = {"wrong issuer": {"iss": "http://127.0.0.1:9/application/o/other/"},
                 "wrong audience": {"aud": "someone-else"},
                 "expired": {"exp_in": -120},
                 "forged signature": {"key": OTHER_KEY}}
        for label, args in cases.items():
            with self.subTest(label):
                status, body, b, _ = self.sign_in(**args)
                self.assertEqual(status, 400)
                self.assertIn("could not be completed", body)
                self.assertNotIn("dh_session", b.jar)
                self.assertEqual(self.state_of(b), 401)
        status, _, b, _ = self.sign_in()  # the control, unchanged
        self.assertEqual(status, 200)

    def test_wrong_nonce_is_refused(self):
        b = self.browser()
        q, _, _ = self.begin(b)
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token("not-the-nonce")}
        status, _, _, _ = b.get("/auth/callback?" + urllib.parse.urlencode({"code": "abc", "state": q["state"][0]}))
        self.assertEqual(status, 400)
        self.assertNotIn("dh_session", b.jar)

    def test_unlinked_person_is_refused_even_with_a_matching_email(self):
        status, body, b, _ = self.sign_in(sub="stranger-uuid", extra={"email": "sam@example.com", "email_verified": True})
        self.assertEqual(status, 403)
        self.assertIn("no access to Deal Hunter", body)
        self.assertNotIn("dh_session", b.jar)

    def test_admin_needs_both_password_and_code_markers(self):
        for amr in (("mfa",), ("pwd",), ()):
            with self.subTest(amr=amr):
                status, body, b, _ = self.sign_in(amr=amr)
                self.assertEqual(status, 403)
                self.assertIn("password and your authenticator code", body)
                self.assertNotIn("dh_session", b.jar)
        self.assertEqual(self.sign_in(amr=("pwd", "mfa"))[0], 200)
        self.assertEqual(self.sign_in(amr=("pwd", "otp", "mfa"))[0], 200)

    # ---- transaction rejections ------------------------------------------------------------

    def test_tampered_state_another_browser_and_replay_are_refused(self):
        self.assertEqual(self.sign_in(state="tampered")[0], 400)

        a, other = self.browser(), self.browser()
        q, _, _ = self.begin(a)
        self.begin(other)  # the other browser has its own transaction cookie
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token(q["nonce"][0])}
        callback = "/auth/callback?" + urllib.parse.urlencode({"code": "abc", "state": q["state"][0]})
        self.assertEqual(other.get(callback)[0], 400)           # A's state in B's browser
        self.assertNotIn("dh_session", other.jar)
        self.assertEqual(self.browser().get(callback)[0], 400)  # no transaction cookie at all
        self.assertEqual(a.get(callback)[0], 200)               # B's attempt did not use up A's transaction
        a.jar.pop("dh_session")
        a.jar["dh_login"] = "replayed"
        self.assertEqual(a.get(callback)[0], 400)               # replayed: one use only
        self.assertNotIn("dh_session", a.jar)

    def test_provider_error_and_missing_code_are_refused(self):
        b = self.browser()
        q, _, _ = self.begin(b)
        status, _, _, _ = b.get("/auth/callback?" + urllib.parse.urlencode({"error": "access_denied", "state": q["state"][0]}))
        self.assertEqual(status, 400)
        self.assertEqual(self.browser().get("/auth/callback")[0], 400)

    # ---- the live deny state ------------------------------------------------------------------

    def test_disabling_the_account_ends_the_central_session_and_refuses_sign_in(self):
        status, _, b, _ = self.sign_in(sub="member-uuid")
        self.assertEqual((status, self.state_of(b)), (200, 200))
        db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (self.member,))
        self.assertEqual(self.state_of(b), 401)
        self.assertEqual(self.sign_in(sub="member-uuid")[0], 403)
        db.execute("UPDATE users SET disabled = 0 WHERE id = ?", (self.member,))
        self.assertEqual(self.sign_in(sub="member-uuid")[0], 200)

    def test_disabling_the_link_ends_the_central_session_at_once(self):
        status, _, b, _ = self.sign_in(sub="member-uuid")
        self.assertEqual((status, self.state_of(b)), (200, 200))
        central.set_status(self.member_ident, "disabled")
        self.assertEqual(self.state_of(b), 401)
        self.assertEqual(self.sign_in(sub="member-uuid")[0], 403)
        central.set_status(self.member_ident, "active")
        self.assertEqual(self.sign_in(sub="member-uuid")[0], 200)

    def test_a_disabled_link_refuses_even_a_session_its_row_still_has(self):
        # Belt and braces: a central token whose rows survived (say, restored from a backup) still obeys the link.
        _, _, b, _ = self.sign_in(sub="member-uuid")
        db.execute("UPDATE central_identities SET status = 'disabled' WHERE id = ?", (self.member_ident,))
        self.assertEqual(self.state_of(b), 401)

    def test_a_sign_in_started_before_the_switch_went_off_is_refused(self):
        b = self.browser()
        q, _, _ = self.begin(b)
        central.set_switch("off")
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token(q["nonce"][0])}
        status, _, body, _ = b.get("/auth/callback?" + urllib.parse.urlencode({"code": "abc", "state": q["state"][0]}))
        self.assertEqual(status, 403)
        self.assertIn(b"turned off", body)
        self.assertNotIn("dh_session", b.jar)

    def test_switch_off_refuses_a_central_session_even_if_its_row_survives(self):
        # The switch is read on every request, not only when it is flipped (a restored backup keeps old rows).
        _, _, b, _ = self.sign_in()
        db.execute("INSERT OR REPLACE INTO central_state (key, value) VALUES ('central', 'off')")
        self.assertEqual(self.state_of(b), 401)
        db.execute("INSERT OR REPLACE INTO central_state (key, value) VALUES ('central', 'on')")
        self.assertEqual(self.state_of(b), 200)

    def test_switch_off_ends_central_sessions_and_keeps_links_and_password_sign_in(self):
        _, _, b, _ = self.sign_in()
        central.set_status(self.member_ident, "disabled")
        central.set_switch("off")
        self.assertEqual(self.state_of(b), 401)
        status, _, body, _ = self.browser().get("/auth/central")
        self.assertEqual(status, 403)
        self.assertIn(b"turned off", body)
        _, status_body = None, json.loads(self.browser().get("/api/auth/status")[2])
        self.assertFalse(status_body["central"])
        pw = self.browser()
        self.assertEqual(pw.get("/api/auth/login", "POST", {"email": "sis@example.com", "password": "sister pass 1"})[0], 200)
        self.assertEqual(self.state_of(pw), 200)  # the app's own sign-in is unchanged
        central.set_switch("on")
        self.assertEqual(self.state_of(b), 401)   # ended sessions stay ended
        self.assertEqual(self.sign_in()[0], 200)  # the link survived
        self.assertEqual(self.sign_in(sub="member-uuid")[0], 403)  # and nobody disabled came back
        self.assertEqual([r["action"] for r in central.audit_log(10)][:4],
                         ["sign_in", "central_on", "central_off", "identity_disabled"])

    # ---- routes, ports and logs ---------------------------------------------------------------

    def test_central_login_is_off_unless_configured(self):
        oidc.LOGIN["central"] = None
        b = self.browser()
        self.assertEqual(b.get("/auth/central")[0], 401)  # just a signed-out page, no redirect
        self.assertEqual(b.get("/auth/callback?code=x&state=y")[0], 401)
        self.assertFalse(json.loads(b.get("/api/auth/status")[2])["central"])
        self.assertIsNone(oidc.CentralLogin.from_config(None, Path(self.tmp.name)))
        self.assertIsNone(oidc.CentralLogin.from_config({"enabled": "yes"}, Path(self.tmp.name)))

    def test_status_offers_the_button_only_when_on(self):
        self.assertTrue(json.loads(self.browser().get("/api/auth/status")[2])["central"])

    def test_the_api_port_has_no_central_routes(self):
        b = self.browser(self.api.server_port)
        for path in ("/auth/central", "/auth/callback?code=x&state=y", "/auth/logout"):
            with self.subTest(path):
                self.assertEqual(b.get(path)[0], 404)

    def test_only_get_and_logout_needs_no_session(self):
        b = self.browser()
        self.assertEqual(b.get("/auth/callback", "POST", {})[0], 405)
        status, headers, _, _ = b.get("/auth/logout")
        self.assertEqual((status, headers["Location"]), (302, "/"))

    def test_begin_is_throttled_per_address(self):
        b = self.browser()
        for _ in range(central.BEGIN_LIMIT):
            self.assertEqual(b.get("/auth/central")[0], 302)
        status, _, body, _ = b.get("/auth/central")
        self.assertEqual(status, 429)
        self.assertIn(b"Too many", body)

    def test_callback_code_and_state_never_reach_the_log(self):
        b = self.browser()
        q, _, _ = self.begin(b)
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token(q["nonce"][0])}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = b.get("/auth/callback?" + urllib.parse.urlencode({"code": "SECRET-CODE", "state": q["state"][0]}))[0]
            b.get("/auth/callback?code=SECOND-CODE&state=bad")
        self.assertEqual(status, 200)
        logged = out.getvalue()
        self.assertIn("/auth/callback?[redacted]", logged)  # it did log the request
        for secret in ("SECRET-CODE", "SECOND-CODE", q["state"][0]):
            self.assertNotIn(secret, logged)

    def test_password_sign_in_is_unchanged_while_central_is_on(self):
        b = self.browser()
        self.assertEqual(b.get("/api/auth/login", "POST", {"email": "sam@example.com", "password": "admin pass 1"})[0], 200)
        self.assertEqual(self.state_of(b), 200)

    # ---- configuration ----------------------------------------------------------------------------

    def test_enabled_config_fails_loudly_when_incomplete(self):
        base = {"enabled": True, "issuer": "https://auth.example.test/application/o/deals/", "client_id": CLIENT,
                "callback": "https://deals.example.test/auth/callback",
                "authorize_url": "https://auth.example.test/application/o/authorize/",
                "token_url": "https://auth.example.test/application/o/token/",
                "jwks_url": "https://auth.example.test/application/o/deals/jwks/"}
        with self.assertRaises(SystemExit) as e:
            oidc.CentralLogin.from_config({**base, "issuer": ""}, Path(self.tmp.name))
        self.assertIn("issuer", str(e.exception))
        self.assertIn("client_secret_file", str(e.exception))
        secret = Path(self.tmp.name) / "client.secret"
        secret.write_text("")
        with self.assertRaises(SystemExit) as e:
            oidc.CentralLogin.from_config({**base, "client_secret_file": str(secret)}, Path(self.tmp.name))
        self.assertIn("missing or empty", str(e.exception))
        secret.write_text("not-a-real-secret\n")
        cfg = {**base, "client_secret_file": str(secret), "tx_db": str(Path(self.tmp.name) / "oidc2" / "tx.db")}
        login = oidc.CentralLogin.from_config(cfg, Path(self.tmp.name))
        self.assertEqual(login.provider.callback, base["callback"])
        for label, change, expect in (
                ("Relay's first mistake: a callback path the provider doesn't allow",
                 {"callback": "https://deals.example.test/login/callback"}, "/auth/callback"),
                ("an authorization endpoint off the issuer's origin",
                 {"authorize_url": "https://evil.example.test/authorize/"}, "origin"),
                ("a plain-HTTP issuer", {"issuer": "http://auth.example.test/application/o/deals/"}, "HTTPS")):
            with self.subTest(label):
                with self.assertRaises(SystemExit) as e:
                    oidc.CentralLogin.from_config({**cfg, **change}, Path(self.tmp.name))
                self.assertIn(expect, str(e.exception))
                self.assertNotIn("not-a-real-secret", str(e.exception))

    def config(self, **change):
        return {"enabled": True, "issuer": "https://auth.example.test/application/o/deals/", "client_id": CLIENT,
                "callback": "https://deals.example.test/auth/callback",
                "authorize_url": "https://auth.example.test/application/o/authorize/",
                "token_url": "https://auth.example.test/application/o/token/",
                "jwks_url": "https://auth.example.test/application/o/deals/jwks/", **change}

    def test_relative_config_paths_are_relative_to_the_app_folder(self):
        app = Path(self.tmp.name) / "app"
        (app / "secrets").mkdir(parents=True)
        (app / "secrets" / "client.secret").write_text("not-a-real-secret")
        cfg = self.config(client_secret_file="secrets/client.secret", tx_db="private/tx.db")
        self.assertIsNotNone(oidc.CentralLogin.from_config(cfg, Path(self.tmp.name), app))
        self.assertTrue((app / "private" / "tx.db").is_file())
        with self.assertRaises(SystemExit):  # the same relative path from another folder is not found
            oidc.CentralLogin.from_config(cfg, Path(self.tmp.name), Path(self.tmp.name) / "elsewhere")

    @unittest.skipUnless(os.name == "posix", "file modes are enforced on POSIX only (production runs on Linux)")
    def test_readable_secret_or_transaction_folder_stops_start_up(self):
        base = Path(self.tmp.name) / "perm"
        base.mkdir()
        secret = base / "client.secret"
        secret.write_text("not-a-real-secret")
        secret.chmod(0o644)
        cfg = self.config(client_secret_file=str(secret))
        with self.assertRaises(SystemExit) as e:
            oidc.CentralLogin.from_config(cfg, base)
        self.assertIn("chmod 600", str(e.exception))
        secret.chmod(0o600)
        (base / "oidc").mkdir()
        (base / "oidc").chmod(0o755)
        with self.assertRaises(SystemExit) as e:
            oidc.CentralLogin.from_config(cfg, base)
        self.assertIn("chmod 700", str(e.exception))
        (base / "oidc").chmod(0o700)
        self.assertIsNotNone(oidc.CentralLogin.from_config(cfg, base))

    # ---- hardening after the PR 60 review -----------------------------------------------------

    def test_https_uses_a_host_prefixed_secure_transaction_cookie_and_clears_it(self):
        https = {"X-Forwarded-Proto": "https"}
        b = self.browser()
        status, headers, _, cookies = b.get("/auth/central", headers=https)
        self.assertEqual(status, 302)
        tx = [c for c in cookies if c.startswith("__Host-dh_login=")]
        self.assertEqual(len(tx), 1)
        for attr in ("Path=/", "HttpOnly", "SameSite=Lax", "Secure"):
            self.assertIn(attr, tx[0])
        self.assertNotIn("Domain=", tx[0])
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(headers["Location"]).query)
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token(q["nonce"][0])}
        status, _, _, cookies = b.get("/auth/callback?" + urllib.parse.urlencode(
            {"code": "abc", "state": q["state"][0]}), headers=https)
        self.assertEqual(status, 200)
        cleared = [c for c in cookies if c.startswith("__Host-dh_login=;")]
        self.assertEqual(len(cleared), 1)
        self.assertIn("Max-Age=0", cleared[0])
        self.assertIn("Secure", cleared[0])
        self.assertIn("Secure", [c for c in cookies if c.startswith("dh_session=")][0])
        # Over HTTPS a plain-named cookie is not the transaction: one planted without the __Host- prefix is ignored.
        b2 = self.browser()
        _, headers, _, _ = b2.get("/auth/central", headers=https)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(headers["Location"]).query)
        b2.jar["dh_login"] = b2.jar.pop("__Host-dh_login")
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token(q["nonce"][0])}
        self.assertEqual(b2.get("/auth/callback?" + urllib.parse.urlencode(
            {"code": "abc", "state": q["state"][0]}), headers=https)[0], 400)

    def test_the_transaction_cookie_is_cleared_on_a_rejection_too(self):
        b = self.browser()
        q, _, _ = self.begin(b)
        self.assertIn("dh_login", b.jar)
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token(q["nonce"][0], key=OTHER_KEY)}
        status, _, _, cookies = b.get("/auth/callback?" + urllib.parse.urlencode({"code": "abc", "state": q["state"][0]}))
        self.assertEqual(status, 400)
        self.assertTrue(any(c.startswith("dh_login=;") and "Max-Age=0" in c for c in cookies))
        self.assertNotIn("dh_login", b.jar)

    def test_re_enabling_a_link_does_not_revive_its_old_session(self):
        _, _, b, _ = self.sign_in(sub="member-uuid")
        central.set_status(self.member_ident, "disabled")
        central.set_status(self.member_ident, "active")
        self.assertEqual(self.state_of(b), 401)
        self.assertEqual(self.sign_in(sub="member-uuid")[0], 200)  # a new sign-in works

    def test_a_disable_during_sign_in_cannot_leave_a_live_session(self):
        # Hold the sign-in between its checks and its insert, and disable the link from another thread then.
        # The disable must wait for the sign-in and then end its session; re-enabling must not bring it back.
        done = threading.Event()
        real = central._mint

        def disable():
            central.set_status(self.member_ident, "disabled")
            done.set()

        def slow_mint(conn, user, auth_time):
            threading.Thread(target=disable, daemon=True).start()
            done.wait(0.5)  # with the transaction the disable is blocked here; without it, it lands now
            return real(conn, user, auth_time)

        central._mint = slow_mint
        try:
            status, _, b, _ = self.sign_in(sub="member-uuid")
        finally:
            central._mint = real
        self.assertTrue(done.wait(5))
        self.assertEqual(status, 200)
        central.set_status(self.member_ident, "active")
        self.assertEqual(self.state_of(b), 401)

    def test_turning_central_login_off_in_config_ends_its_sessions(self):
        _, _, b, _ = self.sign_in()
        pw = self.browser()
        pw.get("/api/auth/login", "POST", {"email": "sis@example.com", "password": "sister pass 1"})
        oidc.LOGIN["central"] = None  # as after "enabled": false and a restart
        self.assertEqual(self.state_of(b), 401)
        self.assertEqual(self.state_of(pw), 200)  # the app's own sessions are not affected
        oidc.LOGIN["central"] = self.login
        self.assertEqual(self.state_of(b), 200)   # once it is back on, the switch and the link decide again

    def test_an_unexpected_error_gets_the_generic_page_a_cleared_cookie_and_a_quiet_log(self):
        b = self.browser()
        q, _, _ = self.begin(b)
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token(q["nonce"][0])}
        real = central.sign_in

        def broken(*args):
            raise RuntimeError("detail that must not be logged: SECRET-CLAIM")

        central.sign_in = broken
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                status, _, body, cookies = b.get("/auth/callback?" + urllib.parse.urlencode(
                    {"code": "abc", "state": q["state"][0]}))
        finally:
            central.sign_in = real
        self.assertEqual(status, 500)
        self.assertIn(b"could not be completed", body)
        self.assertTrue(any(c.startswith("dh_login=;") for c in cookies))
        self.assertNotIn("dh_session", b.jar)
        self.assertIn("RuntimeError", out.getvalue())
        self.assertNotIn("SECRET-CLAIM", out.getvalue())

    def test_an_unexpected_error_in_begin_still_answers(self):
        real = self.login.transactions.begin

        def broken(*args):
            raise RuntimeError("store unavailable")

        self.login.transactions.begin = broken
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                status, _, body, _ = self.browser().get("/auth/central")
        finally:
            self.login.transactions.begin = real
        self.assertEqual(status, 500)
        self.assertIn(b"could not be completed", body)
        self.assertNotIn("store unavailable", out.getvalue())

    def test_a_full_url_request_line_is_redacted_too(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            conn = http.client.HTTPConnection("127.0.0.1", self.ui.server_port, timeout=10)
            conn.putrequest("GET", "http://deals.example.test/auth/callback?code=FULL-URL-CODE&state=FULL-STATE",
                            skip_host=True)
            conn.putheader("Host", "deals.example.test")
            conn.endheaders()
            conn.getresponse().read()
            conn.close()
        self.assertNotIn("FULL-URL-CODE", out.getvalue())
        self.assertNotIn("FULL-STATE", out.getvalue())
        self.assertIn("?[redacted]", out.getvalue())

    def test_a_malformed_cookie_from_another_site_does_not_break_sign_in(self):
        b = self.browser()
        q, _, _ = self.begin(b)
        b.jar = {"theme; cf": '{"a":1}', "dh_login": b.jar["dh_login"], "a": '"b"c'}
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token(q["nonce"][0])}
        status, _, _, _ = b.get("/auth/callback?" + urllib.parse.urlencode({"code": "abc", "state": q["state"][0]}))
        self.assertEqual(status, 200)
        b.jar = {"x": '"unterminated', "dh_session": b.jar["dh_session"]}
        self.assertEqual(self.state_of(b), 200)  # the session cookie still reads beside a broken one

    def test_favicon_answers_signed_out_without_a_401(self):
        status, headers, body, _ = self.browser().get("/favicon.ico")
        self.assertEqual((status, body), (204, b""))
        self.assertIsNone(headers["Content-Length"])  # a 204 carries none (RFC 9110)
        self.assertIn("max-age", headers["Cache-Control"])

    # ---- robustness after the PR 61 review --------------------------------------------------------

    def test_a_failed_write_elsewhere_does_not_block_central_sign_in(self):
        with self.assertRaises(sqlite3.IntegrityError):  # e.g. the poller inserting a find for a deleted watch
            db.execute("INSERT INTO listings (watch_id, source, source_id, title, first_seen) VALUES (?, 'x', 'x', 'x', 0)",
                       (999999,))
        self.assertFalse(db.conn().in_transaction)  # the failed write was undone, not left open
        self.assertEqual(self.sign_in()[0], 200)

    def test_a_stray_open_transaction_is_not_carried_into_a_central_change(self):
        # Belt and braces: whatever left the shared connection mid-transaction, a central change starts clean.
        with db._lock:
            db.conn().execute("INSERT INTO central_audit (at, action, detail) VALUES (0, 'stray', 'never committed')")
        self.assertTrue(db.conn().in_transaction)
        self.assertEqual(self.sign_in()[0], 200)
        self.assertFalse(db.conn().in_transaction)
        self.assertFalse(db.query("SELECT 1 FROM central_audit WHERE action = 'stray'"))

    def test_a_failed_commit_rolls_back_and_makes_no_session(self):
        real_conn = db.conn()

        class CommitFails:
            def __getattr__(self, name):
                return getattr(real_conn, name)

            def commit(self):
                raise sqlite3.OperationalError("database is locked")

        before = db.query("SELECT COUNT(*) AS n FROM tokens WHERE via = 'central'")[0]["n"]
        real = db.conn
        db.conn = lambda: CommitFails()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status, _, b, _ = self.sign_in(sub="member-uuid")
        finally:
            db.conn = real
        self.assertEqual(status, 500)
        self.assertNotIn("dh_session", b.jar)
        self.assertFalse(real_conn.in_transaction)
        db.execute("INSERT INTO central_audit (at, action, detail) VALUES (0, 'later', 'an unrelated write')")
        self.assertEqual(db.query("SELECT COUNT(*) AS n FROM tokens WHERE via = 'central'")[0]["n"], before)
        self.assertEqual(self.sign_in(sub="member-uuid")[0], 200)

    def test_a_disable_from_another_process_during_sign_in_cannot_leave_a_live_session(self):
        # central_admin.py runs as its own process, so the thread lock alone can't hold it off: only the database's
        # write lock (BEGIN IMMEDIATE) does. Start the disable in a child process while the sign-in is between its
        # checks and its insert; it must wait for the sign-in, then end its session.
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; import db, central_admin; "
                "db.DB_PATH = Path(sys.argv[2]); sys.exit(central_admin.run(['disable', sys.argv[3]], lambda *a: None))")
        child = {}
        real = central._mint

        def slow_mint(conn, user, auth_time):
            child["p"] = subprocess.Popen([sys.executable, "-c", code, str(Path(__file__).resolve().parent.parent),
                                           str(db.DB_PATH), str(self.member_ident)])
            try:
                child["p"].wait(3)  # with the write lock the child is held here; without it, it finishes now
                child["waited"] = False
            except subprocess.TimeoutExpired:
                child["waited"] = True
            return real(conn, user, auth_time)

        central._mint = slow_mint
        try:
            status, _, b, _ = self.sign_in(sub="member-uuid")
        finally:
            central._mint = real
        self.assertEqual(child["p"].wait(20), 0)
        self.assertEqual(status, 200)
        self.assertTrue(child["waited"], "the other process's disable did not wait for the sign-in")
        self.assertEqual(central.identities()[1]["status"], "disabled")
        central.set_status(self.member_ident, "active")
        self.assertEqual(self.state_of(b), 401)

    def test_a_switch_off_during_sign_in_cannot_leave_a_live_session(self):
        done = threading.Event()
        real = central._mint

        def switch_off():
            central.set_switch("off")
            done.set()

        def slow_mint(conn, user, auth_time):
            threading.Thread(target=switch_off, daemon=True).start()
            done.wait(0.5)
            return real(conn, user, auth_time)

        central._mint = slow_mint
        try:
            status, _, b, _ = self.sign_in()
        finally:
            central._mint = real
        self.assertTrue(done.wait(5))
        self.assertEqual(status, 200)
        central.set_switch("on")
        self.assertEqual(self.state_of(b), 401)

    def raw(self, request_line: bytes) -> tuple[bytes, str, str]:
        """Send one raw request line; returns the response's first line, what was logged, and stderr."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            s = socket.create_connection(("127.0.0.1", self.ui.server_port), timeout=10)
            s.sendall(request_line + b"\r\nHost: x\r\n\r\n")
            data = b""
            while chunk := s.recv(65536):
                data += chunk
            s.close()
            time.sleep(0.1)  # let the handler thread finish logging
        return data.split(b"\r\n", 1)[0], out.getvalue(), err.getvalue()

    def test_malformed_request_lines_get_an_error_and_a_redacted_log(self):
        for line, code in ((b"GET /auth/callback?code=BAD-CODE&state=BAD-STATE HTTP/1.1 extra", b"400"),
                           (b"GET /auth/callback?code=BAD-CODE&state=BAD-STATE HTTP/9.9", b"505"),
                           (b"GET /auth/callback?code=BAD-CODE&state=" + b"S" * 70000 + b" HTTP/1.1", b"414")):
            with self.subTest(code=code):
                first, logged, err = self.raw(line)
                self.assertIn(code, first)
                self.assertNotIn("Traceback", err + logged)
                for secret in ("BAD-CODE", "BAD-STATE"):
                    self.assertNotIn(secret, logged + err)
                self.assertIn(code.decode(), logged)  # the request was logged

    def test_invite_and_reset_codes_are_redacted_too(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.browser().get("/api/auth/link?code=INVITE-CODE")
        self.assertNotIn("INVITE-CODE", out.getvalue())
        self.assertIn("/api/auth/link?[redacted]", out.getvalue())

    def test_cookie_value_matches_names_exactly_and_the_last_duplicate_wins(self):
        self.assertEqual(auth.cookie_value("dh_session=; dh_session=GOOD", "dh_session"), "GOOD")
        self.assertIsNone(auth.cookie_value("\xa0__Host-dh_login=PLANTED", "__Host-dh_login"))
        self.assertIsNone(auth.cookie_value("xdh_session=NO; dh_session_x=NO", "dh_session"))
        self.assertEqual(auth.cookie_value(" \tdh_session=OK\t ", "dh_session"), "OK")

    def test_an_unexpected_error_logs_where_it_happened_but_not_what_it_said(self):
        b = self.browser()
        q, _, _ = self.begin(b)
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token(q["nonce"][0])}
        real = central.sign_in

        def broken_sign_in(*args):
            raise RuntimeError("SECRET-CLAIM")

        central.sign_in = broken_sign_in
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                b.get("/auth/callback?" + urllib.parse.urlencode({"code": "abc", "state": q["state"][0]}))
        finally:
            central.sign_in = real
        self.assertRegex(out.getvalue(), r"RuntimeError at test_central_login\.py:\d+ in broken_sign_in")
        self.assertNotIn("SECRET-CLAIM", out.getvalue())

    def test_a_failure_after_the_session_is_made_still_clears_the_transaction_cookie(self):
        b = self.browser()
        q, _, _ = self.begin(b)
        Provided.tokens = {"token_type": "Bearer", "access_token": "x", "id_token": id_token(q["nonce"][0])}
        real = auth.device_mark

        def broken(user_id):
            raise RuntimeError("device key unreadable")

        auth.device_mark = broken
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                status, _, _, cookies = b.get("/auth/callback?" + urllib.parse.urlencode(
                    {"code": "abc", "state": q["state"][0]}))
        finally:
            auth.device_mark = real
        self.assertEqual(status, 500)
        self.assertTrue(any(c.startswith("dh_login=;") and "Max-Age=0" in c for c in cookies))
        self.assertNotIn("dh_session", b.jar)


class LinkingTest(unittest.TestCase):
    """The owner-run link: exact subject to an existing account, never by email."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        self.admin = db.admin_id()
        self.member = db.execute("INSERT INTO users (email, name, password_hash, role, created_at) "
                                 "VALUES ('sis@example.com', 'Sis', 'x', 'member', ?)", (time.time(),))

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_link_rules(self):
        ident = central.link(self.member, ISSUER, "member-uuid")
        self.assertEqual(central.identities()[0]["user_id"], self.member)
        for args, status in (((self.admin, ISSUER, "member-uuid"), 409),   # subject already linked
                             ((self.member, ISSUER, "another-uuid"), 409),  # account already linked
                             ((999, ISSUER, "x-uuid"), 404),                # no such account
                             ((self.admin, ISSUER, ""), 400)):              # no subject
            with self.subTest(args=args):
                with self.assertRaises(central.CentralError) as e:
                    central.link(*args)
                self.assertEqual(e.exception.status, status)
        db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (self.admin,))
        with self.assertRaises(central.CentralError) as e:
            central.link(self.admin, ISSUER, "owner-uuid")
        self.assertEqual(e.exception.status, 409)
        db.execute("UPDATE users SET disabled = 0 WHERE id = ?", (self.admin,))
        central.link(self.admin, ISSUER, "owner-uuid")  # the first admin links even before setting a password
        self.assertEqual(len(central.identities()), 2)
        self.assertNotIn("@", json.dumps(central.audit_log()))  # no email addresses in the audit
        self.assertIsInstance(ident, int)

    def test_owner_commands(self):
        lines = []
        config = Path(self.tmp.name) / "config.json"
        config.write_text(json.dumps({"oidc": {"issuer": ISSUER}}))
        old = central_admin.CONFIG
        central_admin.CONFIG = config
        try:
            self.assertEqual(central_admin.run(["link", str(self.member), "member-uuid"], lines.append), 0)
            self.assertEqual(central.identities()[0]["issuer"], ISSUER)
            self.assertEqual(central_admin.run(["link", str(self.member), "member-uuid"], lines.append), 1)
            self.assertIn("Refused", lines[-1])
            self.assertEqual(central_admin.run(["accounts"], lines.append), 0)
            self.assertTrue(any("linked (identity" in line for line in lines))
            self.assertEqual(central_admin.run(["disable", "1"], lines.append), 0)
            self.assertEqual(central.identities()[0]["status"], "disabled")
            self.assertEqual(central_admin.run(["central", "off"], lines.append), 0)
            self.assertFalse(central.switch_on())
            self.assertEqual(central_admin.run(["central", "sideways"], lines.append), 1)
            self.assertEqual(central_admin.run(["link", "one", "x"], lines.append), 2)
            self.assertEqual(central_admin.run([], lines.append), 2)
            self.assertEqual(central_admin.run(["audit"], lines.append), 0)
        finally:
            central_admin.CONFIG = old


if __name__ == "__main__":
    unittest.main()
