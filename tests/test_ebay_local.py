"""eBay local pickup: filters sent, dedupe with the national search, settings and migration."""

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

ITEM = {"itemId": "v1|123|0", "title": "AMD Ryzen 7 5700X", "price": {"value": "120.00", "currency": "USD"},
        "itemWebUrl": "https://www.ebay.com/itm/123", "itemLocation": {"city": "Provo", "stateOrProvince": "UT"},
        "condition": "Used", "buyingOptions": ["FIXED_PRICE"]}
SETTINGS = {**db.DEFAULT_SETTINGS, "ebay_client_id": "id", "ebay_client_secret": "s",
            "zip_code": "84057", "local_radius_miles": 30}


class FakeEbay:
    def __init__(self):
        self.urls = []

    def __call__(self, url, **kw):
        self.urls.append(url)
        return {"itemSummaries": [ITEM]}


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()


@mock.patch.object(sources, "_ebay_access_token", lambda s: "tok")
class EbayLocalTest(TempDb):
    def test_local_sends_all_pickup_filters(self):
        fake = FakeEbay()
        with mock.patch.object(sources, "_http", fake):
            items = sources.ebay_local({"query": "5700x"}, SETTINGS)
        url = sources.urllib.parse.unquote(fake.urls[0])
        for f in ("deliveryOptions:{SELLER_ARRANGED_LOCAL_PICKUP}", "pickupCountry:US",
                  "pickupPostalCode:84057", "pickupRadius:30", "pickupRadiusUnit:mi"):
            self.assertIn(f, url)
        self.assertEqual(items[0]["source"], "ebay_local")
        self.assertTrue(items[0]["buying"].startswith("local pickup"))

    def test_national_has_no_pickup_filters(self):
        fake = FakeEbay()
        with mock.patch.object(sources, "_http", fake):
            sources.ebay({"query": "5700x"}, SETTINGS)
        self.assertNotIn("pickup", fake.urls[0])

    def test_local_needs_zip(self):
        with self.assertRaises(sources.SourceError):
            sources.ebay_local({"query": "5700x"}, {**SETTINGS, "zip_code": ""})

    def test_same_item_from_both_searches_is_stored_once_as_local(self):
        wid = db.create_watch({"name": "x", "query": "5700x", "sources": ["ebay", "ebay_local"]})
        with mock.patch.object(sources, "_http", FakeEbay()):
            r = poller.run_watch(db.get_watch(wid), SETTINGS)
        self.assertEqual(r["new"], 1)
        rows = db.query("SELECT source FROM listings WHERE watch_id = ?", (wid,))
        self.assertEqual([x["source"] for x in rows], ["ebay_local"])


class SettingsAndMigrationTest(TempDb):
    def test_new_source_defaults_on_with_old_saved_settings(self):
        db.update_settings({"sources_enabled": {"ebay": True, "reddit": False, "bestbuy": False}})
        s = db.get_settings()["sources_enabled"]
        self.assertTrue(s["ebay_local"])
        self.assertFalse(s["reddit"])

    def test_existing_ebay_watches_get_local(self):
        conn = db.connect()
        conn.executescript(db.SCHEMA)
        conn.execute("INSERT INTO watches (name, query, sources, created_at) VALUES ('a', 'x', ?, 0)",
                     (json.dumps(["ebay", "reddit"]),))
        conn.execute("INSERT INTO watches (name, query, sources, created_at) VALUES ('b', 'y', ?, 0)",
                     (json.dumps(["reddit"]),))
        conn.commit()
        conn.close()
        got = {w["name"]: w["sources"] for w in db.list_watches()}
        # Later migrations append more sources; this one only inserts ebay_local after ebay.
        self.assertEqual(got["a"][:3], ["ebay", "ebay_local", "reddit"])
        self.assertNotIn("ebay_local", got["b"])



@mock.patch.object(sources, "_ebay_access_token", lambda s: "tok")
class EbayConditionJunkTest(TempDb):
    def test_for_parts_condition_is_filtered(self):
        broken = {**ITEM, "itemId": "v1|999|0", "title": "Nvidia GeForce RTX 3060 12GB Dell OEM",
                  "condition": "For parts or not working"}
        wid = db.create_watch({"name": "x", "query": "rtx 3060 12gb", "sources": ["ebay"]})
        with mock.patch.object(sources, "_http", lambda url, **kw: {"itemSummaries": [broken]}):
            r = poller.run_watch(db.get_watch(wid), SETTINGS)
        self.assertEqual(r["new"], 0)


if __name__ == "__main__":
    unittest.main()
