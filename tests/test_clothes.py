"""Clothes and shoes: sizes, the Poshmark source, clothing watches in the poller, My sizes."""

import json
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import matching  # noqa: E402
import poller  # noqa: E402
import sources  # noqa: E402

SHOES = {"name": "Air Max", "query": "nike air max", "kind": "clothing", "size": "10 1/2", "department": "men"}


class SizeTest(unittest.TestCase):
    def test_norm_size(self):
        self.assertEqual([matching.norm_size(s) for s in ("10 1/2", "10.5", "10½", "Medium", "xl", "W32 L30", "32 x 30",
                                                          "", None)],
                         ["10.5", "10.5", "10.5", "M", "XL", "32x30", "32x30", "", ""])

    def test_title_size(self):
        cases = {"Nike Air Max 90 Size 10.5": "10.5", "Jordan 1 sz 10 1/2": "10.5", "Air Max 270 10.5M": "10.5",
                 "Levis 501 W32 L30": "32x30", "Levis 501 32x30": "32x30", "Patagonia jacket size M": "M",
                 "Nike shoes": "", "Small dog sweater": ""}
        self.assertEqual({t: matching.title_size(t) for t in cases}, cases)

    def test_check_size(self):
        self.assertFalse(matching.check_size("11", SHOES)[0])
        self.assertTrue(matching.check_size("10.5", SHOES)[0])
        self.assertTrue(matching.check_size("", SHOES)[0])  # doesn't say: kept, shown as "size not listed"
        self.assertTrue(matching.check_size("11", {**SHOES, "size": None})[0])  # any size


class Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self.body


def posh_page(rows):
    state = {"common": {}, "$_search": {"gridData": {"data": rows}}}
    return f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>".encode()


ROWS = [
    {"id": "a1", "title": "Nike Air Max 90 Men's 10.5", "price_amount": {"val": "35.0"}, "size": "10.5",
     "condition": "uln", "brand": "Nike", "department": {"display": "Men"}, "picture_url": "https://img/a1.jpg",
     "inventory": {"status": "available"}},
    {"id": "b2", "title": "Nike Air Max sold", "price_amount": {"val": "20.0"}, "size": "10.5",
     "inventory": {"status": "sold_out"}},
]


@mock.patch.object(sources, "POSHMARK_GAP_SECONDS", 0)
class PoshmarkTest(unittest.TestCase):
    def test_request_and_fields(self):
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            return Resp(posh_page(ROWS))
        with mock.patch.object(sources.urllib.request, "urlopen", opener):
            items = sources.poshmark(SHOES, {})
        q = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
        self.assertEqual((q["query"], q["department"], q["size[]"], q["sort_by"]),
                         (["nike air max"], ["Men"], ["10.5"], ["added_desc"]))
        self.assertEqual([i["source_id"] for i in items], ["a1"])  # sold one dropped
        a = items[0]
        self.assertEqual((a["price"], a["size"], a["condition"], a["buying"], a["url"]),
                         (35.0, "10.5", "like new", "Poshmark · Nike · Men", "https://poshmark.com/listing/a1"))

    def test_pants_size_is_the_waist(self):
        seen = []
        with mock.patch.object(sources.urllib.request, "urlopen",
                               lambda req, timeout: seen.append(req.full_url) or Resp(posh_page([]))):
            sources.poshmark({**SHOES, "size": "W32 L30"}, {})
        self.assertIn("size%5B%5D=32&", seen[0] + "&")

    def test_changed_page(self):
        with mock.patch.object(sources.urllib.request, "urlopen", lambda req, timeout: Resp(b"<html></html>")):
            with self.assertRaises(sources.SourceError):
                sources.poshmark(SHOES, {})


class ClothingWatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_poller_sizes_and_sources(self):
        calls = []
        ebay_items = [{"source": "ebay", "source_id": s, "title": t, "price": 50.0, "shipping": 0.0, "currency": "USD",
                       "url": "u", "image": None, "location": "", "condition": "used", "buying": "", "text": ""}
                      for s, t in (("1", "Nike Air Max 90 size 10.5"), ("2", "Nike Air Max 90 size 11"),
                                   ("3", "Nike Air Max 90 sneakers"))]

        def fake(name):
            def run(w, s):
                calls.append(name)
                return [dict(i) for i in ebay_items] if name == "ebay" else []
            return run
        with mock.patch.dict(sources.SOURCES, {n: fake(n) for n in list(sources.SOURCES)}):
            wid = db.create_watch({**SHOES, "sources": ["ebay", "poshmark", "reddit", "ksl_cars", "bestbuy"]})
            other = db.create_watch({"name": "x", "query": "nike air max", "sources": ["poshmark"]})
            r = poller.run_watch(db.get_watch(wid), db.get_settings())
            poller.run_watch(db.get_watch(other), db.get_settings())
        self.assertEqual(sorted(calls), ["ebay", "poshmark"])  # poshmark not for the non-clothing watch
        self.assertEqual(r["new"], 2)
        self.assertEqual(db.query("SELECT source_id, size FROM listings ORDER BY source_id"),
                         [{"source_id": "1", "size": "10.5"}, {"source_id": "3", "size": None}])

    def test_fields_category_and_sizes_setting(self):
        wid = db.create_watch({**SHOES, "department": "aliens", "size": "  10   1/2 "})
        w = db.get_watch(wid)
        self.assertEqual((w["kind"], w["department"], w["size"], w["category"]), ("clothing", None, "10 1/2", "clothes"))
        mid = db.save_machine({"name": "Boots", "kind": "shoes"})
        self.assertEqual(db.get_machine(mid)["category"], "clothes")
        import server
        friend = db.execute("INSERT INTO users (email, name, role, created_at) VALUES ('f@x.y', 'F', 'member', ?)",
                            (time.time(),))
        with db.as_user(friend):
            out = server.put_settings({"sizes": {"shoe": " 10.5 ", "top": "M", "evil": "x" * 99}}, {})
        self.assertEqual(out["sizes"], {"shoe": "10.5", "top": "M"})


if __name__ == "__main__":
    unittest.main()
