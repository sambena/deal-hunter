"""Editing a watch tidies its existing finds: newly excluded or out-of-price ones go, starred ones stay,
and "under typical" is worked out again from what's left."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402

FINDS = [  # title, total, status
    ("Dell Precision 5820 Tower i9-10980XE 64GB", 2444.99, "new"),
    ("Dell Precision 5820 Tower i9-10980XE 32GB", 1948.99, "starred"),
    ("Intel Core i9-10980XE 18 core CPU", 620.0, "new"),
    ("Intel i9-10980XE Extreme Edition", 491.19, "seen"),
    ("Intel Core i9-10980XE SRGSG", 349.99, "new"),
    ("Intel Core i9-10980XE Processor", 340.57, "new"),
    ("i9-10980XE CPU tray", 399.0, "new"),
]


class WatchEditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        import server
        self.s = server
        self.wid = db.create_watch({"name": "i9-10980XE for Proxmox", "query": "10980xe", "sources": ["ebay"]})
        for i, (title, total, status) in enumerate(FINDS):
            db.execute("INSERT INTO listings (watch_id, source, source_id, title, total, first_seen, status) "
                       "VALUES (?, 'ebay', ?, ?, ?, ?, ?)", (self.wid, str(i), title, total, time.time(), status))

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def titles(self):
        return {r["title"]: r for r in db.query("SELECT title, deal_pct FROM listings WHERE watch_id = ?", (self.wid,))}

    def test_exclude_from_a_find_removes_the_matching_finds_but_not_starred(self):
        out = self.s.update_watch({"exclude": ["tower"]}, {}, str(self.wid))
        self.assertEqual(out, {"ok": True, "removed": 1, "marked_seen": 0})
        left = self.titles()
        self.assertNotIn("Dell Precision 5820 Tower i9-10980XE 64GB", left)
        self.assertIn("Dell Precision 5820 Tower i9-10980XE 32GB", left)  # starred
        self.assertEqual(db.get_watch(self.wid)["exclude"], ["tower"])
        # "Typical" no longer includes the $2,445 tower: the $340 CPU is a smaller discount than before.
        self.assertIsNotNone(left["Intel Core i9-10980XE Processor"]["deal_pct"])

    def test_max_price_and_query_excludes(self):
        self.assertEqual(self.s.update_watch({"max_price": 500}, {}, str(self.wid))["removed"], 2)
        self.assertEqual(self.s.update_watch({"query": "10980xe -tray"}, {}, str(self.wid))["removed"], 1)
        self.assertNotIn("i9-10980XE CPU tray", self.titles())

    def test_renaming_removes_nothing(self):
        self.assertEqual(self.s.update_watch({"name": "Proxmox CPU"}, {}, str(self.wid))["removed"], 0)
        self.assertEqual(len(self.titles()), len(FINDS))


if __name__ == "__main__":
    unittest.main()
