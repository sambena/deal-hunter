"""Craigslist: decoding the search API's packed rows (made-up answer in the real format, no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import poller  # noqa: E402
import sources  # noqa: E402

SETTINGS = {"zip_code": "84057", "local_radius_miles": 50}


def answer():
    return {"apiVersion": 8, "data": {
        "location": {"areaId": 292, "city": "Orem", "lat": 40.3134, "lon": -111.6953, "postal": "84057",
                     "radius": 50, "region": "UT"},
        "decode": {"minPostingId": 7941080729, "minPostedDate": 1788894299,
                   "locations": [0, [292, "provo"], [197, "wyoming"]],
                   "locationDescriptions": [0, "Lehi, Utah", "Rock Springs", "WE BUY GPUS! CALL NOW 555-0100 CASH TODAY"]},
        "items": [
            [10, 500, 7, 230, "1:1~40.3909~-111.8504", "0t20CI", [13, "pUpxr3Twi7VobkGmQdzqYX"],
             [4, "3:00O0O_g9xDt4kO3XG_0t20CI", "3:00k0k_6kcn91ZiXdM_0t20CI"], [6, "lehi-rtx-3060-12gb"],
             [10, "$230"], "EVGA RTX 3060 12GB"],
            [11, 600, 7, 200, "2:2~41.5967~-109.2419", "09G06K", [13, "aDhLg9mZ9kWUyez4ZBehXx"],
             [6, "rock-springs-rtx-3060"], [10, "$200"], "RTX 3060 12GB"],  # ~150 miles away
            [12, 700, 7, -1, "1:3~40.39~-111.85", "0t20CI", [13, "zzz"], [6, "provo-wtb"], "RTX 3060 12GB no price"],
            "garbage row",
        ]}}


class Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self.body


@mock.patch.object(sources, "CRAIGSLIST_GAP_SECONDS", 0)
class CraigslistTest(unittest.TestCase):
    def fetch(self, body=None, **watch):
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            return Resp(json.dumps(body or answer()).encode())
        with mock.patch.object(sources.urllib.request, "urlopen", opener):
            return sources.craigslist({"query": "rtx 3060 12gb", **watch}, SETTINGS), seen

    def test_decodes_rows_and_drops_far_ones(self):
        items, seen = self.fetch(max_price=249.99)
        self.assertIn("postal=84057", seen[0])
        self.assertIn("search_distance=50", seen[0])
        self.assertIn("max_price=250", seen[0])
        self.assertIn("query=rtx+3060+12gb", seen[0])
        self.assertEqual([i["title"] for i in items], ["EVGA RTX 3060 12GB", "RTX 3060 12GB no price"])
        first = items[0]
        self.assertEqual(first["source_id"], "7941080739")
        self.assertEqual(first["price"], 230.0)
        self.assertEqual(first["location"], "Lehi, Utah")
        self.assertEqual(first["url"], "https://www.craigslist.org/view/d/lehi-rtx-3060-12gb/pUpxr3Twi7VobkGmQdzqYX")
        self.assertEqual(first["image"], "https://images.craigslist.org/00O0O_g9xDt4kO3XG_0t20CI_300x300.jpg")
        self.assertIsNone(items[1]["price"])
        self.assertEqual(items[1]["location"], "")  # an ad in the place field isn't shown as a place
        self.assertIn("84057", sources._places)

    def test_needs_zip(self):
        with self.assertRaises(sources.SourceError):
            sources.craigslist({"query": "x"}, {**SETTINGS, "zip_code": ""})

    def test_changed_format_is_an_error(self):
        with self.assertRaises(sources.SourceError):
            self.fetch({"data": {"items": [[1, 2, 3]], "decode": {}}})

    def test_poller_stores_and_shares_by_price(self):
        tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(tmp.name) / "t.db"
        try:
            db.update_settings(SETTINGS)
            wid = db.create_watch({"name": "3060", "query": "rtx 3060 12gb", "max_price": 250,
                                   "sources": ["craigslist"]})
            with mock.patch.object(sources.urllib.request, "urlopen",
                                   lambda req, timeout: Resp(json.dumps(answer()).encode())):
                r = poller.run_watch(db.get_watch(wid), db.get_settings())
            self.assertEqual(r["errors"], [])
            self.assertEqual(db.query("SELECT total, location, source FROM listings WHERE total IS NOT NULL"),
                             [{"total": 230.0, "location": "Lehi, Utah", "source": "craigslist"}])
            a, b = {"query": "q", "max_price": 250}, {"query": "q", "max_price": 300}
            self.assertNotEqual(poller.fetch_key("craigslist", a, {}), poller.fetch_key("craigslist", b, {}))
        finally:
            db._conn.close()
            db._conn = None
            tmp.cleanup()

    def test_existing_ksl_watches_get_craigslist(self):
        import sqlite3
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "old.db"
        old = sqlite3.connect(path)
        old.executescript(db.SCHEMA)
        old.execute("""INSERT INTO watches (name, query, sources, created_at) VALUES
                       ('a', 'x', '["ebay","ebay_local","ksl","reddit"]', 0), ('b', 'y', '["ebay"]', 0)""")
        old.execute("PRAGMA user_version = 6")
        old.commit()
        old.close()
        db._conn, db.DB_PATH = None, path
        try:
            got = [json.loads(r["sources"]) for r in db.query("SELECT sources FROM watches ORDER BY id")]
            self.assertEqual(got, [["ebay", "ebay_local", "ksl", "craigslist", "reddit"], ["ebay"]])
        finally:
            db._conn.close()
            db._conn = None
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
