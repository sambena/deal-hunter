"""#322: the owner switches Deal Hunter's own sign-in off, so Cougar Cave central login is the only way in, over real
HTTP. Off refuses the password sign-in, setup, invite and reset links, the old API key, self-service linking and the
sessions and device tokens the own sign-in made, but deletes none of them: switching it back on is the rollback. A
central session keeps working throughout (the valid control)."""

import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402
import central  # noqa: E402
import central_admin  # noqa: E402
import db  # noqa: E402
from tests.test_api_port import Client  # noqa: E402

ISSUER = "https://login.example.test/application/o/deals/"


class OwnLoginOffTest(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        auth._fails.clear()
        self.admin = db.admin_id()
        db.execute("UPDATE users SET email = 'owner@example.com', name = 'Owner', password_hash = ? WHERE id = ?",
                   (auth.hash_password("owner pass 1"), self.admin))
        self.member = db.execute("INSERT INTO users (email, name, password_hash, role, created_at) VALUES "
                                 "('member@example.com', 'Member', ?, 'member', ?)",
                                 (auth.hash_password("member pass 1"), time.time()))
        db.update_settings({"api_key": "legacy-key-for-tests"})
        self.ident = central.link(self.admin, ISSUER, "owner-uuid")
        # Central login counts as configured, as on the live server.
        self.configured = patch.object(auth, "central_configured", return_value=True)
        self.configured.start()
        self.ui = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        for s in (self.ui, self.api):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        # Before the switch: a browser and a phone signed in with the own sign-in, and a central session.
        self.browser = Client(self.ui.server_port)
        self.assertEqual(self.browser.call("POST", "/api/auth/login",
                                           {"email": "owner@example.com", "password": "owner pass 1"})[0], 200)
        status, body, _ = Client(self.api.server_port).call(
            "POST", "/api/auth/login", {"email": "member@example.com", "password": "member pass 1", "device_name": "phone"})
        self.assertEqual(status, 200)
        self.phone = Client(self.api.server_port, body["token"])
        self.central = Client(self.ui.server_port, auth.create_token(self.admin, "web", "central browser", via="central",
                                                                      auth_time=time.time(), identity_id=self.ident))

    def tearDown(self):
        for s in (self.ui, self.api):
            s.shutdown()
            s.server_close()
        self.configured.stop()
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def switch(self, state):
        lines = []
        self.assertEqual(central_admin.run(["own-login", state], lines.append), 0)
        self.assertEqual(lines, [f"Deal Hunter's own sign-in is {state}."])

    def test_off_refuses_every_own_sign_in_path_and_central_still_works(self):
        tokens_before = db.query("SELECT COUNT(*) AS n FROM tokens")[0]["n"]
        self.switch("off")

        # The valid control: the central session reads the owner's state.
        status, body, _ = self.central.call("GET", "/api/auth/status")
        self.assertEqual((status, body["user"]["name"], body["setup_needed"]), (200, "Owner", False))
        self.assertIsNone(body["signin"]["password"])  # the sign-in card offers central only

        # Sessions and device tokens made by the own sign-in stop working.
        self.assertEqual(self.browser.call("GET", "/api/state")[0], 401)
        self.assertEqual(self.phone.call("GET", "/api/state")[0], 401)
        # The old single API key no longer acts as the admin.
        legacy = Client(self.api.server_port, "legacy-key-for-tests")
        self.assertEqual(legacy.call("GET", "/api/state")[0], 401)

        # Every own sign-in route is refused with the same reason.
        stranger = Client(self.ui.server_port)
        for method, path, body in (("POST", "/api/auth/login", {"email": "owner@example.com", "password": "owner pass 1"}),
                                   ("POST", "/api/auth/setup", {"email": "x@example.com", "password": "long enough 1"}),
                                   ("GET", "/api/auth/link?code=whatever", None),
                                   ("POST", "/api/auth/accept", {"code": "whatever", "password": "long enough 1"})):
            status, reply, _ = stranger.call(method, path, body)
            self.assertEqual((status, reply.get("error")), (410, self.server.OWN_LOGIN_OFF), path)
        for method, path, body in (("POST", "/api/admin/invites", {"note": "friend"}),
                                   ("POST", f"/api/admin/users/{self.member}/reset", {}),
                                   ("POST", "/api/api-key", {}),
                                   ("POST", "/api/me", {"new_password": "another pass 1", "current_password": "owner pass 1"})):
            with patch.object(central, "check_privileged"):  # the fresh-MFA rule is #412's; this checks the switch
                status, reply, _ = self.central.call(method, path, body)
            self.assertEqual((status, reply.get("error")), (410, self.server.OWN_LOGIN_OFF), path)
        # An ordinary change through the central session still works.
        self.assertEqual(self.central.call("POST", "/api/me", {"name": "Owner"})[0], 200)

        # Nothing was deleted: the tokens and password hashes are still there for the restorable week.
        self.assertEqual(db.query("SELECT COUNT(*) AS n FROM tokens")[0]["n"], tokens_before)
        self.assertTrue(auth.verify_password("owner pass 1", db.query("SELECT password_hash FROM users WHERE id = ?",
                                                                        (self.admin,))[0]["password_hash"]))
        self.assertEqual(central.audit_log(1)[0]["action"], "own_login_off")

    def test_on_again_is_the_rollback(self):
        self.switch("off")
        self.assertEqual(self.phone.call("GET", "/api/state")[0], 401)
        self.switch("on")
        self.assertEqual(self.browser.call("GET", "/api/state")[0], 200)
        self.assertEqual(self.phone.call("GET", "/api/state")[0], 200)
        self.assertEqual(Client(self.ui.server_port).call(
            "POST", "/api/auth/login", {"email": "owner@example.com", "password": "owner pass 1"})[0], 200)
        self.assertEqual(self.central.call("GET", "/api/state")[0], 200)

    def test_default_is_on(self):
        self.assertTrue(central.own_login_on())
        self.assertEqual(self.browser.call("GET", "/api/state")[0], 200)
        self.assertEqual(central_admin.run(["own-login", "maybe"], lambda *_: None), 1)


if __name__ == "__main__":
    unittest.main()
