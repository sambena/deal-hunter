"""KSL Classifieds: reading listings out of the search page (made-up page in the real format, no network)."""

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

LISTINGS = [[
    {"id": 82110834, "title": "Gigabyte GeForce RTX 3060 12GB", "price": 230, "marketType": "Sale",
     "location": {"city": "Spanish Fork", "state": "UT", "zip": "84660"}, "sellerType": "Private",
     "primaryImage": {"url": "https://image.ksldigital.com/a.jpg"}},
    {"id": 82027328, "title": "Zotac Gaming GeForce RTX 3060 12GB", "price": 300, "marketType": "Sale",
     "location": {"city": "Herriman", "state": "UT"}, "sellerType": "Private", "primaryImage": None},
    {"id": 1, "title": "WTB RTX 3060 12GB", "price": 0, "marketType": "Wanted", "location": {}},
]]


def page(listings=LISTINGS):
    payload = '7:["$","SearchStoreProvider",null,{"initialState":{"results":' + json.dumps(listings) + ',"total":3}}]'
    return ('<html><script>self.__next_f.push([1,' + json.dumps(payload) + '])</script></html>').encode()


class Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self.body


SETTINGS = {**db.DEFAULT_SETTINGS, "zip_code": "84057", "local_radius_miles": 50}


@mock.patch.object(sources, "KSL_GAP_SECONDS", 0)
class KslTest(unittest.TestCase):
    def fetch(self, body, query="rtx 3060 12gb -laptop", settings=SETTINGS):
        seen = {}

        def fake(req, timeout):
            seen["url"], seen["ua"] = req.full_url, req.get_header("User-agent")
            return Resp(body)
        with mock.patch.object(sources.urllib.request, "urlopen", fake):
            return sources.ksl({"query": query}, settings), seen

    def test_reads_sale_listings_and_builds_the_url(self):
        items, seen = self.fetch(page())
        self.assertEqual(seen["url"],
                         "https://classifieds.ksl.com/search/keyword/rtx%203060%2012gb/zip/84057/miles/50")
        self.assertIn("Mozilla", seen["ua"])
        self.assertEqual([i["source_id"] for i in items], ["82110834", "82027328"])  # the Wanted post is skipped
        first = items[0]
        self.assertEqual((first["price"], first["shipping"], first["location"]), (230.0, 0.0, "Spanish Fork, UT"))
        self.assertEqual(first["url"], "https://classifieds.ksl.com/listing/82110834")
        self.assertEqual(first["buying"], "KSL · private seller")

    def test_needs_zip(self):
        with self.assertRaises(sources.SourceError):
            sources.ksl({"query": "x"}, {**SETTINGS, "zip_code": ""})

    def test_block_page_and_layout_change_are_clear_errors(self):
        with self.assertRaises(sources.SourceError) as cm:
            self.fetch(b"<title>Access to this page has been denied.</title>")
        self.assertIn("bot protection", str(cm.exception))
        with self.assertRaises(sources.SourceError) as cm:
            self.fetch(b"<html>new design</html>")
        self.assertIn("site change", str(cm.exception))

    def test_poller_stores_with_price_and_matching(self):
        tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(tmp.name) / "t.db"
        try:
            wid = db.create_watch({"name": "3060", "query": "rtx 3060 12gb", "max_price": 250, "sources": ["ksl"]})
            with mock.patch.object(sources.urllib.request, "urlopen", lambda req, timeout: Resp(page())):
                r = poller.run_watch(db.get_watch(wid), {**db.get_settings(), **SETTINGS})
            self.assertEqual(r, {"new": 1, "errors": []})  # $300 Zotac is over the cap
            self.assertEqual(db.query("SELECT total, location FROM listings")[0],
                             {"total": 230.0, "location": "Spanish Fork, UT"})
        finally:
            db._conn.close()
            db._conn = None
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
