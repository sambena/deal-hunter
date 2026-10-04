"""Slickdeals and r/buildapcsales parsing, with made-up feeds in the real formats (no network)."""

import datetime
import email.utils
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

SLICK = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>ZOTAC RTX 3060 Twin Edge 12GB GDDR6 $239.99 + Free Shipping</title>
  <link>https://slickdeals.net/f/1001-zotac</link><guid>thread-1001</guid><pubDate>NOW</pubDate>
  <content:encoded><![CDATA[<img src="https://static.example/zotac.jpg"> at Newegg]]></content:encoded></item>
<item><title>Open Box: MSI RTX 3060 Ventus 2X 12GB $1,019.50 bundle</title>
  <link>https://slickdeals.net/f/1002-msi</link><guid>thread-1002</guid><pubDate>NOW</pubDate></item>
<item><title>Renewed RTX 3060 12GB card</title>
  <link>https://slickdeals.net/f/1003-x</link><guid>thread-1003</guid><pubDate>NOW</pubDate></item>
<item><title>Old deal RTX 3060 12GB $199</title><link>https://slickdeals.net/f/900-old</link><guid>thread-900</guid><pubDate>Sun, 14 Jan 2024 10:00:00 +0000</pubDate></item>
</channel></rss>"""

POSTS = [{"id": "abc1", "title": "[GPU] Gigabyte RTX 3060 12GB Gaming OC - $229.99 ($259.99 - $30 code) (Newegg)",
          "url": "https://reddit.com/r/buildapcsales/comments/abc1", "body": "", "image": None, "_sub": "buildapcsales"},
         {"id": "abc2", "title": "[PSU] Corsair RM850x - $89.99", "url": "u", "body": "", "image": None,
          "_sub": "buildapcsales"}]


class Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self.body


class ParseTest(unittest.TestCase):
    def test_slickdeals_items(self):
        seen = {}

        now = email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc)).encode()

        def fake_urlopen(req, timeout):
            seen["url"] = req.full_url
            return Resp(SLICK.replace(b"NOW", now))
        with mock.patch.object(sources.urllib.request, "urlopen", fake_urlopen):
            items = sources.slickdeals({"query": "rtx 3060|3060ti 12gb -laptop"}, {})
        self.assertIn("q=rtx+3060+12gb", seen["url"])  # first alternative of each group, no excludes
        first, second, third = items  # the 2024 deal is skipped as too old
        self.assertEqual((first["source_id"], first["price"], first["condition"]), ("1001", 239.99, "new"))
        self.assertEqual(first["image"], "https://static.example/zotac.jpg")
        self.assertEqual((second["price"], second["condition"]), (1019.50, "open box"))
        self.assertEqual((third["price"], third["condition"]), (None, "refurbished"))

    def test_buildapcsales_first_price_is_the_deal_price(self):
        with mock.patch.object(sources, "_cached_feed", lambda sub: POSTS):
            items = sources.buildapcsales({"query": "x"}, {})
        self.assertEqual(items[0]["price"], 229.99)
        self.assertEqual(items[0]["buying"], "r/buildapcsales")
        self.assertEqual(items[1]["price"], 89.99)

    def test_feed_is_fetched_once_per_cycle(self):
        calls = []
        sources._feed_cache.clear()
        with mock.patch.object(sources, "_reddit_feed", lambda sub: calls.append(sub) or POSTS):
            sources.buildapcsales({"query": "a"}, {})
            sources.buildapcsales({"query": "b"}, {})
        self.assertEqual(calls, ["buildapcsales"])


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_matching_and_price_cap_apply(self):
        wid = db.create_watch({"name": "3060", "query": "rtx 3060 12gb", "max_price": 300,
                               "sources": ["buildapcsales"]})
        with mock.patch.object(sources, "_cached_feed", lambda sub: POSTS):
            r = poller.run_watch(db.get_watch(wid), db.get_settings())
        self.assertEqual(r["new"], 1)  # the PSU doesn't match the query
        self.assertEqual(db.query("SELECT total FROM listings")[0]["total"], 229.99)

    def test_existing_watches_get_the_deal_feeds(self):
        conn = db.connect()
        conn.executescript(db.SCHEMA)
        conn.execute("INSERT INTO watches (name, query, sources, created_at) VALUES ('a', 'x', ?, 0)",
                     (json.dumps(["ebay", "reddit"]),))
        conn.commit()
        conn.close()
        srcs = db.list_watches()[0]["sources"]
        self.assertEqual(srcs, ["ebay", "ebay_local", "reddit", "slickdeals", "buildapcsales"])
        self.assertTrue(db.get_settings()["sources_enabled"]["slickdeals"])


if __name__ == "__main__":
    unittest.main()
