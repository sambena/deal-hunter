"""Turning a watch off marks its new finds as viewed; other watches and other edits are untouched."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


class WatchOffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        self.a = db.create_watch({"name": "a", "query": "x"})
        self.b = db.create_watch({"name": "b", "query": "y"})
        for wid, sid, status in ((self.a, "1", "new"), (self.a, "2", "starred"), (self.a, "3", "dismissed"),
                                 (self.b, "4", "new")):
            db.execute("""INSERT INTO listings (watch_id, source, source_id, title, first_seen, status)
                          VALUES (?, 'ebay', ?, 't', ?, ?)""", (wid, sid, time.time(), status))

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def statuses(self):
        return {r["source_id"]: r["status"] for r in db.query("SELECT source_id, status FROM listings")}

    def test_turning_off_marks_new_as_seen(self):
        db.update_watch(self.a, {"name": "renamed"})
        self.assertEqual(self.statuses()["1"], "new")
        self.assertEqual(db.update_watch(self.a, {"enabled": False}), 1)
        self.assertEqual(self.statuses(), {"1": "seen", "2": "starred", "3": "dismissed", "4": "new"})

    def test_already_off_or_turning_on_changes_nothing(self):
        db.execute("UPDATE watches SET enabled = 0 WHERE id = ?", (self.b,))
        self.assertEqual(db.update_watch(self.b, {"enabled": False}), 0)
        self.assertEqual(db.update_watch(self.b, {"enabled": True}), 0)
        self.assertIsNone(db.update_watch(999, {"enabled": False}))
        self.assertEqual(self.statuses()["4"], "new")


if __name__ == "__main__":
    unittest.main()
