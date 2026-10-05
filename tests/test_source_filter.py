"""Finds filtered by source: one site, or several ("ebay,ebay_local")."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


class SourceFilterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        import server
        self.server = server
        wid = db.create_watch({"name": "x", "query": "x"})
        for i, src in enumerate(("ebay", "ebay_local", "ksl_cars", "craigslist")):
            db.execute("""INSERT INTO listings (watch_id, source, source_id, title, first_seen, status)
                          VALUES (?, ?, ?, 't', ?, 'new')""", (wid, src, str(i), time.time()))

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def sources(self, **params):
        return sorted(l["source"] for l in self.server.list_listings({}, params)["listings"])

    def test_filter(self):
        self.assertEqual(self.sources(), ["craigslist", "ebay", "ebay_local", "ksl_cars"])
        self.assertEqual(self.sources(source="ksl_cars"), ["ksl_cars"])
        self.assertEqual(self.sources(source="ebay,ebay_local"), ["ebay", "ebay_local"])
        self.assertEqual(self.sources(source="nope"), [])


if __name__ == "__main__":
    unittest.main()
