"""Shared fetches: identical searches (anyone's) are requested once per pass; matching stays per watch."""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import poller  # noqa: E402
import sources  # noqa: E402

ITEMS = [
    {"source": "ksl", "source_id": "1", "title": "Zotac RTX 3060 12GB", "price": 230.0, "shipping": 0.0,
     "currency": "USD", "url": "u1", "image": None, "location": "Orem, UT", "condition": "used", "buying": "KSL",
     "text": ""},
    {"source": "ksl", "source_id": "2", "title": "EVGA RTX 3060 12GB", "price": 300.0, "shipping": 0.0,
     "currency": "USD", "url": "u2", "image": None, "location": "Provo, UT", "condition": "used", "buying": "KSL",
     "text": ""},
]


class SharedFetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        self.calls = []

        def fake_ksl(watch, settings):
            self.calls.append(watch["query"])
            return [dict(i) for i in ITEMS]
        patch = mock.patch.dict(sources.SOURCES, {"ksl": fake_ksl})
        patch.start()
        self.addCleanup(patch.stop)
        self.friend = db.execute("INSERT INTO users (email, name, role, created_at) VALUES ('f@x.y', 'F', 'member', ?)",
                                 (time.time(),))
        for uid in (db.admin_id(), self.friend):
            db.update_settings({"zip_code": "84057"}, user_id=uid)

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def watch(self, uid, **kw):
        with db.as_user(uid):
            return db.create_watch({"name": "3060", "query": "rtx 3060 12gb", "sources": ["ksl"], **kw})

    def test_same_search_requested_once(self):
        mine = self.watch(db.admin_id(), max_price=250)
        theirs = self.watch(self.friend)
        poller.run_all()
        self.assertEqual(self.calls, ["rtx 3060 12gb"])
        self.assertEqual(poller.state["fetches"], {"made": 1, "shared": 1})
        # Each watch still applies its own limits: the $300 card is over my cap only.
        got = {w: len(db.query("SELECT id FROM listings WHERE watch_id = ?", (w,))) for w in (mine, theirs)}
        self.assertEqual(got, {mine: 1, theirs: 2})

    def test_different_area_is_a_different_request(self):
        self.watch(db.admin_id())
        self.watch(self.friend)
        db.update_settings({"zip_code": "84101"}, user_id=self.friend)
        poller.run_all()
        self.assertEqual(len(self.calls), 2)

    def test_errors_are_shared_and_next_pass_asks_again(self):
        self.watch(db.admin_id())
        self.watch(self.friend)

        def blocked(watch, settings):
            self.calls.append(watch["query"])
            raise sources.SourceError("blocked")
        with mock.patch.dict(sources.SOURCES, {"ksl": blocked}):
            poller.run_all()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([w["last_error"] for w in db.list_watches(all_users=True)], ["ksl: blocked"] * 2)
        poller.run_all()  # a new pass doesn't reuse the old answer
        self.assertEqual(len(self.calls), 2)

    def test_ebay_price_filters_split_requests(self):
        a, b = {"query": "q", "max_price": 250}, {"query": "q", "max_price": 300}
        self.assertNotEqual(poller.fetch_key("ebay", a, {}), poller.fetch_key("ebay", b, {}))
        self.assertEqual(poller.fetch_key("ksl", a, {}), poller.fetch_key("ksl", b, {}))

    def test_reused_items_are_copies(self):
        shared = {}
        w = {"query": "q"}
        a = poller.fetch("ksl", w, {}, shared)
        a[0]["title"] = "changed"
        self.assertEqual(poller.fetch("ksl", w, {}, shared)[0]["title"], "Zotac RTX 3060 12GB")


if __name__ == "__main__":
    unittest.main()
