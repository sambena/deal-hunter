"""Invites, password resets, the admin's People list, and watch limits."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402
import db  # noqa: E402


class InviteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        import server
        self.s = server
        self.admin = db.admin_id()
        db.execute("UPDATE users SET email = 'sam@example.com', name = 'Sam', password_hash = ? WHERE id = ?",
                   (auth.hash_password("admin pass 1"), self.admin))
        auth._fails.clear()

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def code(self, link):
        return link["link"].split("=", 1)[1]

    def accept(self, code, **kw):
        return self.s.auth_accept({"code": code, "password": "friend pass 1", **kw}, {})

    def test_invite_creates_a_member_with_the_limit_once(self):
        link = self.s.admin_invite({"note": "for Sis"}, {})
        self.assertIn("#invite=", link["link"])
        code = self.code(link)
        self.assertEqual(self.s.auth_link({}, {"code": code}), {"kind": "invite", "note": "for Sis"})
        out = self.accept(code, name="Sis", email="sis@example.com")
        self.assertEqual(out["user"]["role"], "member")
        self.assertIn("__set_cookie__", out)
        sis = db.query("SELECT * FROM users WHERE email = 'sis@example.com'")[0]
        self.assertEqual(sis["watch_limit"], db.DEFAULT_WATCH_LIMIT)
        with self.assertRaises(self.s.HTTPError) as cm:  # one use only
            self.accept(code, name="Again", email="again@example.com")
        self.assertEqual(cm.exception.status, 410)

    def test_expired_bad_and_duplicate_email(self):
        code = self.code(self.s.admin_invite({}, {}))
        db.execute("UPDATE invites SET expires_at = ?", (time.time() - 1,))
        with self.assertRaises(self.s.HTTPError):
            self.s.auth_link({}, {"code": code})
        with self.assertRaises(self.s.HTTPError):
            self.s.auth_link({}, {"code": "made-up"})
        code = self.code(self.s.admin_invite({}, {}))
        with self.assertRaises(self.s.HTTPError) as cm:
            self.accept(code, email="SAM@example.com")
        self.assertEqual(cm.exception.status, 409)
        with self.assertRaises(self.s.HTTPError) as cm:
            self.s.auth_accept({"code": code, "email": "x@y.z", "password": "short"}, {})
        self.assertEqual(cm.exception.status, 400)

    def test_device_sign_up_gets_a_token(self):
        code = self.code(self.s.admin_invite({"watch_limit": 3}, {}))
        out = self.accept(code, email="bro@example.com", device_name="Pixel 9")
        self.assertTrue(out["token"].startswith("dht_"))
        self.assertEqual(db.query("SELECT watch_limit FROM users WHERE email = 'bro@example.com'")[0]["watch_limit"], 3)

    def test_reset_sets_a_new_password_and_signs_out_everywhere(self):
        uid = self.accept(self.code(self.s.admin_invite({}, {})), email="sis@example.com")["user"]["id"]
        auth.create_token(uid, "device", "old phone")
        link = self.s.admin_reset_link({}, {}, str(uid))
        info = self.s.auth_link({}, {"code": self.code(link)})
        self.assertEqual((info["kind"], info["email"]), ("reset", "sis@example.com"))
        self.s.auth_accept({"code": self.code(link), "password": "brand new pass"}, {})
        self.assertEqual(db.query("SELECT COUNT(*) AS n FROM tokens WHERE user_id = ? AND name = 'old phone'", (uid,))[0]["n"], 0)
        self.assertEqual(auth.sign_in("sis@example.com", "brand new pass", "test")["id"], uid)

    def test_people_list_shows_names_and_spend_not_contents(self):
        uid = self.accept(self.code(self.s.admin_invite({}, {})), name="Sis", email="sis@example.com")["user"]["id"]
        with db.as_user(uid):
            db.create_watch({"name": "secret wishlist", "query": "x", "sources": ["ebay"]})
        db.execute("INSERT INTO ai_usage (at, provider, model, action, input_tokens, output_tokens, cost, user_id) "
                   "VALUES (?, 'claude', 'm', 'judge', 1, 1, 0.25, ?)", (time.time(), uid))
        people = {u["name"]: u for u in self.s.admin_users({}, {})["users"]}
        self.assertEqual((people["Sis"]["watches"], people["Sis"]["ai_spent"]), (1, 0.25))
        self.assertNotIn("secret wishlist", str(people))

    def test_disable_signs_out_and_blocks_sign_in(self):
        uid = self.accept(self.code(self.s.admin_invite({}, {})), email="sis@example.com")["user"]["id"]
        token = auth.create_token(uid, "device", "phone")
        self.s.admin_update_user({"disabled": True}, {}, str(uid))
        self.assertIsNone(auth.user_for_token(token))
        with self.assertRaises(auth.AuthError):
            auth.sign_in("sis@example.com", "friend pass 1", "test")
        with self.assertRaises(self.s.HTTPError):
            self.s.admin_update_user({"disabled": True}, {}, str(self.admin))  # not yourself

    def test_members_cannot_use_admin_calls(self):
        uid = self.accept(self.code(self.s.admin_invite({}, {})), email="sis@example.com")["user"]["id"]
        with db.as_user(uid):
            for fn, args in [(self.s.admin_users, ({}, {})), (self.s.admin_invite, ({}, {})),
                             (self.s.admin_update_user, ({"watch_limit": 99}, {}, str(uid))),
                             (self.s.admin_reset_link, ({}, {}, str(self.admin)))]:
                with self.assertRaises(self.s.HTTPError) as cm:
                    fn(*args)
                self.assertEqual(cm.exception.status, 403)

    def test_watch_limit(self):
        uid = self.accept(self.code(self.s.admin_invite({"watch_limit": 2}, {})), email="sis@example.com")["user"]["id"]
        with db.as_user(uid):
            for i in range(2):
                self.s.create_watch({"name": f"w{i}", "query": "q", "check": False}, {})
            with self.assertRaises(self.s.HTTPError) as cm:
                self.s.create_watch({"name": "w3", "query": "q", "check": False}, {})
            self.assertEqual(cm.exception.status, 403)
            self.assertIn("watch limit reached", str(cm.exception))
        for i in range(5):  # the admin has no limit
            self.s.create_watch({"name": f"a{i}", "query": "q", "check": False}, {})


if __name__ == "__main__":
    unittest.main()
