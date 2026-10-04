"""OfferUp: reading listings out of the search page's Next.js data (made-up page, no network)."""

import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import sources  # noqa: E402

SETTINGS = {"zip_code": "84057", "local_radius_miles": 25}
PLACE = {"lat": 40.3134, "lon": -111.6953, "city": "Orem", "state": "UT"}


def listing(lid, title, price, place="Orem, UT", flags=("LOCAL_PICKUP",), cond=None):
    return {"__typename": "ModularFeedTileListing", "tileType": "LISTING",
            "listing": {"__typename": "ModularFeedListing", "listingId": lid, "conditionText": cond,
                        "flags": list(flags), "image": {"url": f"https://images.offerup.com/{lid}.jpg"},
                        "locationName": place, "price": price, "title": title}}


def page(feed=None):
    feed = feed if feed is not None else {"looseTiles": [
        {"__typename": "ModularFeedTileGoogleDisplayAd", "tileType": "AD_3P_GOOGLE_DISPLAY"},
        listing("a-1", "EVGA RTX 3060 12GB", "240", cond="Used - good"),
        listing("b-2", "RTX 3060 12GB make offer", "1"),
        listing("a-1", "EVGA RTX 3060 12GB", "240", cond="Used - good"),  # the page repeats tiles
    ], "modules": [{"tiles": [listing("c-3", "Zotac RTX 3060", "260", "Provo, UT", ("LOCAL_PICKUP", "SHIPPING"))]}]}
    data = {"props": {"pageProps": {"searchFeedResponse": feed}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'.encode()


class Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self.body


@mock.patch.object(sources, "OFFERUP_GAP_SECONDS", 0)
@mock.patch.dict(sources._places, {"84057": PLACE})
class OfferUpTest(unittest.TestCase):
    def fetch(self, body=None, **watch):
        seen = []

        def opener(req, timeout):
            seen.append(req)
            return Resp(body or page())
        with mock.patch.object(sources.urllib.request, "urlopen", opener):
            return sources.offerup({"query": "rtx 3060 12gb", **watch}, SETTINGS), seen

    def test_reads_listings_and_sets_the_area(self):
        items, seen = self.fetch(max_price=249.5)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0].full_url).query)
        self.assertEqual(q, {"q": ["rtx 3060 12gb"], "radius": ["30"], "sort": ["-posted"], "price_max": ["250"]})
        cookie = json.loads(urllib.parse.unquote(seen[0].get_header("Cookie").removeprefix("ou.location=")))
        self.assertEqual((cookie["zipCode"], cookie["latitude"], cookie["city"]), ("84057", 40.3134, "Orem"))
        self.assertEqual([i["source_id"] for i in items], ["a-1", "b-2", "c-3"])
        first = items[0]
        self.assertEqual((first["price"], first["shipping"], first["condition"]), (240.0, 0.0, "used - good"))
        self.assertEqual(first["url"], "https://offerup.com/item/detail/a-1")
        self.assertIsNone(items[1]["price"])  # $1 means "make an offer"
        self.assertEqual(items[2]["buying"], "OfferUp · ships")

    def test_radius_caps_at_50(self):
        with mock.patch.dict(SETTINGS, {"local_radius_miles": 120}):
            _, seen = self.fetch()
        self.assertIn("radius=50", seen[0].full_url)

    def test_errors(self):
        with self.assertRaises(sources.SourceError):
            sources.offerup({"query": "x"}, {**SETTINGS, "zip_code": ""})
        with self.assertRaises(sources.SourceError) as cm:
            self.fetch(b"<html>new design</html>")
        self.assertIn("site change", str(cm.exception))

    def test_existing_ksl_watches_get_offerup(self):
        import sqlite3
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "old.db"
        old = sqlite3.connect(path)
        old.executescript(db.SCHEMA)
        old.execute("""INSERT INTO watches (name, query, sources, created_at) VALUES
                       ('a', 'x', '["ebay","ksl","reddit"]', 0), ('b', 'y', '["ebay"]', 0)""")
        old.execute("PRAGMA user_version = 6")
        old.commit()
        old.close()
        db._conn, db.DB_PATH = None, path
        try:
            got = [json.loads(r["sources"]) for r in db.query("SELECT sources FROM watches ORDER BY id")]
            self.assertEqual(got, [["ebay", "ksl", "craigslist", "offerup", "reddit"], ["ebay"]])
        finally:
            db._conn.close()
            db._conn = None
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
