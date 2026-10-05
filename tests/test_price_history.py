"""Price history per watch: daily lowest/median, typical line, cheapest now."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        import server
        self.server = server

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_points_typical_and_best(self):
        wid = db.create_watch({"name": "3060", "query": "rtx 3060"})
        day = 86400
        now = time.time()
        rows = [(now - 2 * day, 300, "seen"), (now - 2 * day, 260, "dismissed"), (now - day, 280, "seen"),
                (now - day, 250, "new"), (now, 240, "new"), (now - 200 * day, 100, "seen")]
        for i, (t, total, status) in enumerate(rows):
            db.execute("""INSERT INTO listings (watch_id, source, source_id, title, total, first_seen, status)
                          VALUES (?, 'ebay', ?, 't', ?, ?, ?)""", (wid, str(i), total, t, status))
        h = self.server.watch_history({}, {"days": "90"}, str(wid))
        self.assertEqual([p["count"] for p in h["points"]], [2, 2, 1])  # the 200-day-old one is outside
        self.assertEqual((h["points"][0]["min"], h["points"][0]["median"]), (260, 280))
        self.assertEqual(h["typical"], 260)
        self.assertEqual(h["best_now"], 240)  # dismissed $260 doesn't count as available
        self.assertEqual(h["best_label"], "")  # 7.7% under typical
        with self.assertRaises(self.server.HTTPError):
            self.server.watch_history({}, {}, "999")

    def test_needs_five_prices_for_typical(self):
        wid = db.create_watch({"name": "x", "query": "x"})
        db.execute("INSERT INTO listings (watch_id, source, source_id, title, total, first_seen) VALUES (?, 'e', '1', 't', 5, ?)",
                   (wid, time.time()))
        h = self.server.watch_history({}, {"days": "bad"}, str(wid))
        self.assertEqual((h["days"], h["typical"], len(h["points"])), (90, None, 1))


if __name__ == "__main__":
    unittest.main()
