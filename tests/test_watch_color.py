"""A watch's colour: '#rrggbb' or null (then the app and web page pick from the palette by id)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


class WatchColorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_color_is_checked(self):
        wid = db.create_watch({"name": "a", "query": "x", "color": "#A855F7"})
        self.assertEqual(db.get_watch(wid)["color"], "#a855f7")
        for bad in ("red", "#fff", "#12345g", "url(x)", 5, None, ""):
            db.update_watch(wid, {"color": bad})
            self.assertIsNone(db.get_watch(wid)["color"], bad)
        db.update_watch(wid, {"color": "#22c55e"})
        db.update_watch(wid, {"name": "b"})  # other edits leave it alone
        self.assertEqual(db.list_watches()[0]["color"], "#22c55e")


if __name__ == "__main__":
    unittest.main()
