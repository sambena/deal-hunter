"""Relay PR 18's logout contract over Deal Hunter's real HTTP, database and session paths.

Only generated disposable keys and the existing fake provider are used. No live service is contacted.
"""
import contextlib
import http.client
import io
import json
import sys
import time
import unittest
import urllib.parse
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt
import auth
import central
import db
import oidc
from cougar_login.login_validation import LoginRejected
from tests import test_central_login as fixture


def logout_token(sub="owner-uuid", key=fixture.KEY, age=1, exp_in=300, drop=(), headers=None, **changes):
    now = int(time.time())
    claims = {"iss": fixture.ISSUER, "sub": sub, "aud": fixture.CLIENT, "iat": now - age,
              "exp": now + exp_in, "jti": uuid.uuid4().hex, "events": {oidc.BACKCHANNEL_EVENT: {}}, **changes}
    for field in drop:
        claims.pop(field, None)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "good", **(headers or {})})


class BackchannelTest(unittest.TestCase):
    # Reuse the fake provider and real HTTP fixture, without inheriting its unrelated test cases.
    setUpClass = classmethod(fixture.CentralLoginTest.setUpClass.__func__)
    browser = fixture.CentralLoginTest.browser
    begin = fixture.CentralLoginTest.begin
    sign_in = fixture.CentralLoginTest.sign_in
    state_of = fixture.CentralLoginTest.state_of

    @classmethod
    def tearDownClass(cls):
        fixture.CentralLoginTest.tearDownClass.__func__(cls)
        cls.ui.server_close()
        cls.api.server_close()

    def setUp(self):
        fixture.CentralLoginTest.setUp(self)
        db.execute("DELETE FROM central_logout_attempts")
        db.execute("DELETE FROM central_logout_jti")
        db.execute("DELETE FROM tokens")

    def tearDown(self):
        fixture.CentralLoginTest.tearDown(self)

    def post(self, token=None, raw=None, method="POST", port=None, headers=None, path=oidc.BACKCHANNEL_PATH):
        if raw is None:
            raw = urllib.parse.urlencode({"logout_token": token}).encode() if token is not None else b""
        conn = http.client.HTTPConnection("127.0.0.1", port or self.ui.server_port, timeout=10)
        conn.request(method, path, raw, {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})
        response = conn.getresponse()
        status, h, data = response.status, response.headers, response.read()
        conn.close()
        return status, h, data

    def signed_in(self, sub="owner-uuid", sid=None):
        status, _, browser, _ = self.sign_in(sub=sub, **({"extra": {"sid": sid}} if sid else {}))
        self.assertEqual(status, 200)
        self.assertEqual(self.state_of(browser), 200)
        return browser

    def snapshot(self):
        return {table: db.query(f"SELECT * FROM {table} ORDER BY rowid") for table in
                ("tokens", "central_identities", "central_logout_jti", "central_audit")}

    def bare(self, response, status):
        self.assertEqual(response[0], status)
        self.assertEqual(response[2], b'{"error": "invalid_request"}')
        self.assertEqual(response[1]["Cache-Control"], "no-store")
        self.assertIsNone(response[1].get("Set-Cookie"))

    def control(self, browser):
        response = self.post(logout_token())
        self.assertEqual(response[0], 200)
        self.assertEqual(response[2], b"")
        self.assertEqual(self.state_of(browser), 401)

    def rejected(self, reason, **args):
        browser, other = self.signed_in(), self.signed_in("member-uuid")
        token = logout_token(**args)
        before = self.snapshot()
        self.bare(self.post(token), 400)
        self.assertEqual(self.snapshot(), before)
        # Establish the specific validator reason, as well as the externally bare refusal.
        with self.assertRaisesRegex(LoginRejected, "^" + reason + "$"):
            self.login.validate_logout_token(token)
        self.assertEqual(self.state_of(browser), 200)
        self.control(browser)
        self.assertEqual(self.state_of(other), 200)

    def test_wrong_issuer(self):
        self.rejected("issuer", iss="http://127.0.0.1:9/application/o/other/")

    def test_wrong_audience(self):
        self.rejected("audience", aud="another-test-client")

    def test_forged_signature(self):
        self.rejected("signature", key=fixture.OTHER_KEY)

    def test_expired(self):
        self.rejected("expired-token", exp_in=-120, age=1)

    def test_too_old(self):
        self.rejected("time", age=900)

    def test_no_events(self):
        self.rejected("missing-events", drop=("events",))

    def test_wrong_event(self):
        self.rejected("event", events={"http://schemas.openid.net/event/other": {}})

    def test_has_nonce(self):
        self.rejected("nonce", nonce="")  # presence is forbidden even when empty

    def test_no_jti(self):
        self.rejected("missing-jti", drop=("jti",))

    def test_empty_jti(self):
        self.rejected("jti", jti="")

    def test_no_subject(self):
        self.rejected("subject", drop=("sub",))

    def test_wrong_authorized_party(self):
        self.rejected("authorized-party", azp="another-test-client")

    def test_header_and_pinned_key_checks(self):
        for headers, reason in (({"jku": "https://untrusted.example.test/keys"}, "header"),
                                ({"kid": ""}, "header"), ({"kid": "unknown"}, "key")):
            with self.subTest(headers=headers):
                self.rejected(reason, headers=headers)

    def test_pinned_key_must_be_for_signatures(self):
        browser = self.signed_in()
        token = logout_token()
        before = self.snapshot()
        with patch.object(self.login.cache, 'get', return_value={"keys": [{**fixture.JWK, "use": "enc"}]}):
            self.bare(self.post(token), 400)
            with self.assertRaisesRegex(LoginRejected, "^key$"):
                self.login.validate_logout_token(token)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.state_of(browser), 200)
        self.control(browser)

    def test_missing_issuer_audience_or_iat(self):
        for field in ("iss", "aud", "iat"):
            with self.subTest(field=field):
                self.rejected("missing-" + field, drop=(field,))

    def test_unknown_person_is_accepted_with_nothing_ended(self):
        """#528: a valid token for a subject never linked here is 200 (Back-Channel Logout 1.0, 2.8), changes no
        session, audits without the subject or token, and its replay is still refused."""
        browser = self.signed_in()
        token = logout_token(sub="stranger-uuid")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            response = self.post(token)
        self.assertEqual((response[0], response[2]), (200, b""))
        self.assertEqual(self.state_of(browser), 200)
        audit = central.audit_log(1)[0]
        self.assertEqual((audit["action"], audit["detail"]), ("logout_backchannel", "a subject not linked here: nothing to end"))
        self.assertNotIn("stranger-uuid", json.dumps(audit) + output.getvalue())
        self.assertNotIn(token, json.dumps(audit) + output.getvalue())
        before = self.snapshot()
        self.bare(self.post(token), 400)  # replay
        self.assertEqual(self.snapshot(), before)
        self.control(browser)

    def test_valid_ends_only_that_identitys_sessions_and_audits_without_token(self):
        first, second, other = self.signed_in(), self.signed_in(), self.signed_in("member-uuid")
        password = auth.create_token(self.admin, "web", "password test")
        device = auth.create_token(self.admin, "device", "device test")
        before_links = db.query("SELECT * FROM central_identities")
        token = logout_token()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            response = self.post(token)
        self.assertEqual((response[0], response[2]), (200, b""))
        self.assertEqual((self.state_of(first), self.state_of(second), self.state_of(other)), (401, 401, 200))
        self.assertIsNotNone(auth.user_for_token(password))
        self.assertIsNotNone(auth.user_for_token(device))
        self.assertEqual(db.query("SELECT * FROM central_identities"), before_links)
        audit = central.audit_log(1)[0]
        self.assertEqual(audit["action"], "logout_backchannel")
        self.assertEqual(audit["detail"], f"identity {self.owner_ident}: 2 sessions ended")
        self.assertNotIn(token, output.getvalue() + json.dumps(audit))

    # ---- #551: a logout token with a sid ends only that central login session ----

    def test_a_sid_logout_ends_only_that_sign_in(self):
        a, b = self.signed_in(sid="sid-A"), self.signed_in(sid="sid-B")
        other = self.signed_in("member-uuid", sid="sid-A")      # another person's session with the same sid value
        self.assertEqual(db.query("SELECT sid FROM tokens WHERE via = 'central' ORDER BY id")[0]["sid"], "sid-A")
        response = self.post(logout_token(sid="sid-A"))
        self.assertEqual((response[0], response[2]), (200, b""))
        self.assertEqual(self.state_of(a), 401)
        self.assertEqual((self.state_of(b), self.state_of(other)), (200, 200))   # the control: B still works
        audit = central.audit_log(1)[0]
        self.assertEqual(audit["detail"], f"identity {self.owner_ident} (one sign-in): 1 sessions ended")
        self.assertNotIn("sid-A", json.dumps(audit))

    def test_a_sid_logout_also_ends_that_persons_sessions_with_no_sid(self):
        a, old = self.signed_in(sid="sid-A"), self.signed_in()  # signed in before sids were kept: no sid
        b = self.signed_in(sid="sid-B")
        self.assertEqual(self.post(logout_token(sid="sid-A"))[0], 200)
        self.assertEqual((self.state_of(a), self.state_of(old), self.state_of(b)), (401, 401, 200))

    def test_a_sid_logout_leaves_the_apps_own_sign_ins(self):
        a = self.signed_in(sid="sid-A")
        password = auth.create_token(self.admin, "web", "password test")
        device = auth.create_token(self.admin, "device", "device test")
        self.assertEqual(self.post(logout_token(sid="sid-A"))[0], 200)
        self.assertEqual(self.state_of(a), 401)
        self.assertIsNotNone(auth.user_for_token(password))
        self.assertIsNotNone(auth.user_for_token(device))

    def test_a_sub_only_logout_still_ends_every_sign_in(self):
        a, b, old = self.signed_in(sid="sid-A"), self.signed_in(sid="sid-B"), self.signed_in()
        other = self.signed_in("member-uuid", sid="sid-A")
        self.assertEqual(self.post(logout_token())[0], 200)
        self.assertEqual((self.state_of(a), self.state_of(b), self.state_of(old)), (401, 401, 401))
        self.assertEqual(self.state_of(other), 200)

    def test_a_sid_logout_for_an_unknown_subject_is_200_and_its_replay_400(self):
        a = self.signed_in(sid="sid-A")
        token = logout_token(sub="stranger-uuid", sid="sid-A")
        self.assertEqual(self.post(token)[0], 200)
        self.assertEqual(self.state_of(a), 200)                  # the same sid value, another person: nothing ends
        self.bare(self.post(token), 400)
        replay = logout_token(sid="sid-A")
        self.assertEqual(self.post(replay)[0], 200)
        self.bare(self.post(replay), 400)

    def test_replay_after_database_reopen_changes_nothing(self):
        first = self.signed_in()
        token = logout_token()
        self.assertEqual(self.post(token)[0], 200)
        self.assertEqual(self.state_of(first), 401)
        again = self.signed_in()
        # Persisted replay state survives reopening the app database.
        db._conn.close()
        db._conn = None
        claims = self.login.validate_logout_token(token)
        with self.assertRaisesRegex(central.CentralError, "^Logout token already used[.]$"):
            central.backchannel_logout(claims['iss'], claims['sub'], claims['jti'])
        before = self.snapshot()
        self.bare(self.post(token), 400)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.state_of(again), 200)
        self.control(again)

    def test_wrong_method(self):
        browser = self.signed_in()
        for method in ("GET", "PUT", "DELETE"):
            before = self.snapshot()
            self.bare(self.post(logout_token(), method=method), 405)
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.state_of(browser), 200)
        self.control(browser)

    def test_oversize_and_bad_forms(self):
        browser = self.signed_in()
        for raw, code in ((b"x" * 20000, 413), (b"", 400), (b"logout_token=", 400),
                          (b"logout_token=x&logout_token=y", 400), (b"\xff", 400),
                          (b"not-a-form", 400), (b"logout_token=garbage", 400)):
            before = self.snapshot()
            self.bare(self.post(raw=raw), code)
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.state_of(browser), 200)
        self.control(browser)

    def test_ambiguous_body_framing(self):
        browser = self.signed_in()
        raw = urllib.parse.urlencode({"logout_token": logout_token()}).encode()
        for lengths, transfer in (([], None), ([str(len(raw)), str(len(raw))], None),
                                  ([str(len(raw))], "chunked"), (["-1"], None), (["invalid"], None)):
            before = self.snapshot()
            conn = http.client.HTTPConnection("127.0.0.1", self.ui.server_port, timeout=10)
            conn.putrequest("POST", oidc.BACKCHANNEL_PATH)
            for length in lengths:
                conn.putheader("Content-Length", length)
            if transfer:
                conn.putheader("Transfer-Encoding", transfer)
            conn.endheaders(raw)
            response = conn.getresponse()
            self.bare((response.status, response.headers, response.read()), 400)
            conn.close()
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.state_of(browser), 200)
        self.control(browser)

    def test_throttle(self):
        browser = self.signed_in()
        for _ in range(oidc.BACKCHANNEL_LIMIT):
            self.bare(self.post(raw=b"logout_token=garbage"), 400)
        before = self.snapshot()
        self.bare(self.post(logout_token()), 429)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.state_of(browser), 200)
        db.execute("UPDATE central_logout_attempts SET until = ?", (time.time() - 1,))
        self.control(browser)

    def test_global_throttle(self):
        browser = self.signed_in()
        db.execute("INSERT INTO central_logout_attempts VALUES ('bcl:all', ?, ?)",
                   (oidc.BACKCHANNEL_LIMIT * 10, time.time() + 600))
        before = self.snapshot()
        self.bare(self.post(logout_token()), 429)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.state_of(browser), 200)
        db.execute("DELETE FROM central_logout_attempts")
        self.control(browser)

    def test_api_port_cannot_receive_logout(self):
        browser = self.signed_in()
        before = self.snapshot()
        self.assertEqual(self.post(logout_token(), port=self.api.server_port)[0], 404)
        self.assertEqual(self.snapshot(), before)
        self.control(browser)

    def test_unexpected_failure_is_bare_and_does_not_log_its_message(self):
        browser = self.signed_in()
        before = self.snapshot()
        output = io.StringIO()
        with patch.object(self.login, 'validate_logout_token', side_effect=RuntimeError("private-test-detail")):
            with contextlib.redirect_stdout(output):
                self.bare(self.post(logout_token()), 500)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("private-test-detail", output.getvalue())
        self.control(browser)


if __name__ == "__main__":
    unittest.main()
