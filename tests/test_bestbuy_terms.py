"""Best Buy API terms: its finds are kept at most 72 hours after a check last saw them."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import poller  # noqa: E402


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        self.wid = db.create_watch({"name": "x", "query": "x", "sources": ["bestbuy", "ebay"]})

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def add(self, source, sid, first_seen, seen_at=None):
        db.execute("""INSERT INTO listings (watch_id, source, source_id, title, first_seen, seen_at)
                      VALUES (?, ?, ?, 't', ?, ?)""", (self.wid, source, sid, first_seen, seen_at))

    def test_old_bestbuy_finds_go_and_others_stay(self):
        now = time.time()
        self.add("bestbuy", "stale", now - 73 * 3600)                      # not seen for 73 h
        self.add("bestbuy", "refreshed", now - 100 * 3600, now - 3600)     # old, but seen an hour ago
        self.add("bestbuy", "fresh", now - 3600)
        self.add("ebay", "old-ebay", now - 500 * 3600)                     # other sources are kept
        self.assertEqual(poller.purge_expired(now), 1)
        left = sorted(r["source_id"] for r in db.query("SELECT source_id FROM listings"))
        self.assertEqual(left, ["fresh", "old-ebay", "refreshed"])

    def test_seeing_a_find_again_refreshes_it(self):
        old = time.time() - 80 * 3600
        self.add("bestbuy", "123-excellent", old)
        item = {"source": "bestbuy", "source_id": "123-excellent", "title": "x", "price": 100.0, "shipping": 0.0,
                "currency": "USD", "url": "u", "image": None, "location": "", "condition": "open box",
                "buying": "best buy", "text": ""}
        import sources
        sources.SOURCES["bestbuy"], real = (lambda w, s: [item]), sources.SOURCES["bestbuy"]
        try:
            settings = {**db.get_settings(), "sources_enabled": {"bestbuy": True}}
            poller.run_watch(db.get_watch(self.wid), settings)
        finally:
            sources.SOURCES["bestbuy"] = real
        self.assertEqual(poller.purge_expired(), 0)  # seen just now, so kept


if __name__ == "__main__":
    unittest.main()
