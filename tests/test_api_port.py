"""The API port and accounts over real HTTP: sign-in, device tokens, the old single key, and limits."""

import http.cookiejar
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402
import db  # noqa: E402


class Client:
    """A browser (keeps cookies) or an app (sends a Bearer token)."""
    def __init__(self, port, token=None):
        self.port, self.token = port, token
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     None if body is None else json.dumps(body).encode(), h, method=method)
        try:
            with self.opener.open(req) as r:
                return r.status, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers


class AccountsOverHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server
        cls.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(cls.tmp.name) / "t.db"
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        cls.ui = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        for s in (cls.api, cls.ui):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.api.shutdown()
        cls.ui.shutdown()
        if db._conn:
            db._conn.close()
        db._conn = None
        cls.tmp.cleanup()

    def setUp(self):
        auth._fails.clear()

    def test_1_setup_sign_in_and_tokens(self):
        browser = Client(self.ui.server_port)
        self.assertEqual(browser.call("GET", "/api/state")[0], 401)               # signed out
        status, body, _ = browser.call("GET", "/api/auth/status")
        self.assertEqual((status, body["setup_needed"], body["user"]), (200, True, None))

        # Setup can't be done through the API port, and a short password is refused.
        self.assertEqual(Client(self.api.server_port).call("POST", "/api/auth/setup", {})[0], 403)
        self.assertEqual(browser.call("POST", "/api/auth/setup", {"email": "sam@example.com", "password": "short"})[0], 400)

        status, body, headers = browser.call("POST", "/api/auth/setup",
                                             {"name": "Sam", "email": "sam@example.com", "password": "correct horse 1"})
        self.assertEqual((status, body["user"]["role"]), (200, "admin"))
        self.assertNotIn("token", body)                                           # the browser gets a cookie only
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertEqual(browser.call("GET", "/api/state")[1]["me"]["name"], "Sam")
        self.assertEqual(browser.call("POST", "/api/auth/setup", {"email": "x@y.z", "password": "another pass"})[0], 409)

        # A phone signs in on the API port and gets its own token.
        app = Client(self.api.server_port)
        status, body, _ = app.call("POST", "/api/auth/login",
                                   {"email": "SAM@example.com", "password": "correct horse 1", "device_name": "Pixel 10"})
        self.assertEqual(status, 200)
        token = body["token"]
        self.assertTrue(token.startswith("dht_"))
        app.token = token
        self.assertEqual(app.call("GET", "/api/me")[1]["email"], "sam@example.com")
        devices = {t["name"]: t for t in browser.call("GET", "/api/tokens")[1]["tokens"]}
        self.assertEqual(set(devices), {"web browser", "Pixel 10"})

        # Revoking the phone from the browser signs it out.
        self.assertEqual(browser.call("DELETE", f"/api/tokens/{devices['Pixel 10']['id']}")[0], 200)
        self.assertEqual(app.call("GET", "/api/me")[0], 401)

        # The API port still refuses settings, and the page itself.
        status, body, _ = app.call("POST", "/api/auth/login",
                                   {"email": "sam@example.com", "password": "correct horse 1", "device_name": "Pixel 10"})
        app.token = body["token"]
        self.assertEqual(app.call("PUT", "/api/settings", {"poll_minutes": 5})[0], 403)

        # The old single API key still works on the API port, as the admin.
        status, body, _ = browser.call("POST", "/api/api-key", {})
        legacy = Client(self.api.server_port, body["api_key"])
        self.assertEqual(legacy.call("GET", "/api/me")[1]["role"], "admin")
        self.assertEqual(Client(self.api.server_port, "dh_wrong").call("GET", "/api/me")[0], 401)

        # Sign out clears the cookie and kills that session.
        status, _, headers = browser.call("POST", "/api/auth/logout", {})
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(browser.call("GET", "/api/state")[0], 401)

    def test_2_wrong_passwords_are_slowed_down(self):
        app = Client(self.api.server_port)
        codes = [app.call("POST", "/api/auth/login", {"email": "sam@example.com", "password": f"nope{i}"})[0]
                 for i in range(6)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)
        # Even the right password waits until the window passes.
        self.assertEqual(app.call("POST", "/api/auth/login",
                                  {"email": "sam@example.com", "password": "correct horse 1"})[0], 429)


    def test_3_public_hardening(self):
        import auth as auth_mod
        app = Client(self.api.server_port)
        # Spoofed Cloudflare header on the API port doesn't buy fresh attempts.
        codes = [app.call("POST", "/api/auth/login", {"email": "sam@example.com", "password": f"x{i}"},
                          headers={"Cf-Connecting-Ip": f"10.0.0.{i}"})[0] for i in range(6)]
        self.assertEqual(codes[-1], 429)
        auth_mod._fails.clear()
        # One account guessed from many addresses (through the web port, where the header is trusted) is limited too.
        web = Client(self.ui.server_port)
        codes = [web.call("POST", "/api/auth/login", {"email": "sam@example.com", "password": f"y{i}"},
                          headers={"Cf-Connecting-Ip": f"203.0.113.{i}"})[0] for i in range(6)]
        self.assertEqual(codes[-1], 429)
        auth_mod._fails.clear()
        # A wrong email reads the same as a wrong password.
        self.assertEqual(app.call("POST", "/api/auth/login", {"email": "nobody@example.com", "password": "whatever1"})[1],
                         {"error": "Wrong email or password"})
        # Security headers on every response; big bodies refused.
        status, _, headers = web.call("GET", "/api/auth/status")
        self.assertEqual((headers["X-Frame-Options"], headers["X-Content-Type-Options"]), ("DENY", "nosniff"))
        status, body, _ = web.call("POST", "/api/auth/login", {"pad": "x" * 1_100_000})
        self.assertEqual(status, 413)

    def test_4_errors_do_not_leak_details(self):
        import server
        server.route("GET", "/api/boom")(lambda body, params: 1 / 0)
        status, body, _ = Client(self.api.server_port, self.legacy_or_token()).call("GET", "/api/boom")
        self.assertEqual((status, body), (500, {"error": "Something went wrong on the server"}))

    def legacy_or_token(self):
        import auth as auth_mod
        return auth_mod.create_token(db.admin_id(), "device", "test")


if __name__ == "__main__":
    unittest.main()
