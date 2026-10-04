"""Two people on one Deal Hunter: each sees and changes only their own things."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402


class IsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        import server
        self.server = server
        self.admin = db.admin_id()
        self.member = db.execute("INSERT INTO users (email, name, password_hash, role, created_at) "
                                 "VALUES ('sis@example.com', 'Sis', ?, 'member', ?)",
                                 (auth.hash_password("sister pass 1"), time.time()))
        with db.as_user(self.admin):
            self.admin_watch = db.create_watch({"name": "Sam 3060", "query": "rtx 3060", "sources": ["ebay"]})
            self.admin_tv = db.save_machine({"name": "Sam's TV", "kind": "tv", "model": "OLED65B2AUA"})
            db.execute("INSERT INTO listings (watch_id, source, source_id, title, first_seen) VALUES (?, 'ebay', '1', 't', ?)",
                       (self.admin_watch, time.time()))
            db.update_settings({"zip_code": "84057", "poll_minutes": 15})

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def as_member(self):
        return db.as_user(self.member)

    def test_member_sees_none_of_the_admins_things(self):
        with self.as_member():
            state = self.server.get_state({}, {})
            self.assertEqual((state["watches"], state["machines"]), ([], []))
            self.assertEqual(state["me"]["name"], "Sis")
            self.assertEqual(self.server.list_listings({}, {"status": "all"})["listings"], [])
            self.assertIsNone(db.get_watch(self.admin_watch))
            self.assertIsNone(db.get_machine(self.admin_tv))

    def test_member_cannot_change_or_delete_the_admins_things(self):
        with self.as_member():
            for fn, args in [(self.server.update_watch, ({"name": "mine now"}, {}, str(self.admin_watch))),
                             (self.server.delete_watch, ({}, {}, str(self.admin_watch))),
                             (self.server.update_machine, ({"name": "x"}, {}, str(self.admin_tv))),
                             (self.server.delete_machine, ({}, {}, str(self.admin_tv)))]:
                with self.assertRaises(self.server.HTTPError) as cm:
                    fn(*args)
                self.assertEqual(cm.exception.status, 404)
            lid = db.query("SELECT id FROM listings")[0]["id"]
            with self.assertRaises(self.server.HTTPError):
                self.server.update_listing({"status": "dismissed"}, {}, str(lid))
            # Can't link a watch to someone else's device either.
            wid = db.create_watch({"name": "w", "query": "q", "machine_id": self.admin_tv})
            self.assertIsNone(db.get_watch(wid)["machine_id"])
        self.assertEqual(db.get_watch(self.admin_watch, any_user=True)["name"], "Sam 3060")
        self.assertEqual(db.query("SELECT status FROM listings")[0]["status"], "new")

    def test_settings_are_personal_and_shared_ones_are_admin_only(self):
        with self.as_member():
            self.assertEqual(db.get_settings()["zip_code"], "")              # not the admin's ZIP
            self.server.put_settings({"zip_code": "90210", "poll_minutes": 5, "ebay_client_id": "stolen"}, {})
            s = db.get_settings()
            self.assertEqual((s["zip_code"], s["poll_minutes"], s["ebay_client_id"]), ("90210", 15, ""))
            with self.assertRaises(self.server.HTTPError):
                self.server.new_api_key({}, {})
        self.assertEqual(db.get_settings(self.admin)["zip_code"], "84057")

    def test_ai_spend_is_per_person(self):
        with db.as_user(self.admin):
            db.execute("INSERT INTO ai_usage (at, provider, model, action, input_tokens, output_tokens, cost, user_id, paid_by) "
                       "VALUES (?, 'claude', 'claude-haiku-4-5', 'judge', 1, 1, 0.5, ?, ?)", (time.time(), self.admin, self.admin))
            self.assertEqual(ai.budget(db.get_settings())["spent"], 0.5)
        with self.as_member():
            self.assertEqual(ai.budget(db.get_settings())["spent"], 0)

    def test_poller_runs_each_watch_with_its_owners_settings(self):
        import poller
        seen = {}
        with self.as_member():
            db.update_settings({"zip_code": "90210"})
            db.create_watch({"name": "sis", "query": "x", "sources": ["ebay"]})
        real = poller.run_watch
        poller.run_watch = lambda w, s: seen.setdefault(w["name"], s["zip_code"]) and {"new": 0, "errors": []}
        try:
            poller.run_all()
        finally:
            poller.run_watch = real
        self.assertEqual(seen, {"Sam 3060": "84057", "sis": "90210"})

    def test_existing_data_moves_into_the_first_admins_account(self):
        import json
        import sqlite3
        db._conn.close()
        db._conn = None
        path = Path(self.tmp.name) / "old.db"
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE watches (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, query TEXT NOT NULL,
              exclude TEXT NOT NULL DEFAULT '[]', min_price REAL, max_price REAL, condition TEXT NOT NULL DEFAULT 'any',
              include_auctions INTEGER NOT NULL DEFAULT 0, sources TEXT NOT NULL DEFAULT '[]',
              enabled INTEGER NOT NULL DEFAULT 1, notes TEXT NOT NULL DEFAULT '', machine_id INTEGER,
              created_at REAL NOT NULL, last_polled REAL, last_error TEXT, polled_sources TEXT NOT NULL DEFAULT '[]');
            INSERT INTO watches (name, query, created_at) VALUES ('old watch', 'x', 0);
            PRAGMA user_version = 4;""")
        old.execute("INSERT INTO settings VALUES ('zip_code', ?), ('ebay_client_id', ?)",
                    (json.dumps("84057"), json.dumps("SamBryan-PRD")))
        old.commit()
        old.close()
        db.DB_PATH = path
        self.assertEqual([w["name"] for w in db.list_watches()], ["old watch"])
        s = db.get_settings()
        self.assertEqual((s["zip_code"], s["ebay_client_id"]), ("84057", "SamBryan-PRD"))
        self.assertTrue(self.server._setup_needed())


if __name__ == "__main__":
    unittest.main()
