"""Parts that fit: a watch linked to an owned vehicle searches eBay Motors parts with eBay's fitment filter."""

import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import poller  # noqa: E402
import sources  # noqa: E402

ITEMS = {"itemSummaries": [
    {"itemId": "v1|1|0", "title": "Brake pads front Tacoma", "price": {"value": "45.00", "currency": "USD"},
     "buyingOptions": ["FIXED_PRICE"], "compatibilityMatch": "EXACT", "itemWebUrl": "https://ebay.com/1"},
    {"itemId": "v1|2|0", "title": "Brake pads front universal", "price": {"value": "30.00", "currency": "USD"},
     "buyingOptions": ["FIXED_PRICE"], "compatibilityMatch": "POSSIBLE", "itemWebUrl": "https://ebay.com/2"},
]}


class PartsFitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        self.urls = []

        def http(url, **kw):
            self.urls.append(url)
            return ITEMS
        for p in (mock.patch.object(sources, "_ebay_access_token", lambda s: "tok"),
                  mock.patch.object(sources, "_http", http)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def params(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.urls[-1]).query)

    def test_linked_vehicle_adds_fitment(self):
        truck = db.save_machine({"name": "Truck", "kind": "vehicle", "make": "Toyota", "model": "Tacoma", "year": 2020})
        wid = db.create_watch({"name": "pads", "query": "brake pads", "sources": ["ebay"], "machine_id": truck})
        r = poller.run_watch(db.get_watch(wid), db.get_settings())
        q = self.params()
        self.assertEqual(q["category_ids"], ["6028"])
        self.assertEqual(q["compatibility_filter"], ["Year:2020;Make:Toyota;Model:Tacoma"])
        self.assertEqual(r["new"], 2)
        buying = {row["source_id"]: row["buying"] for row in db.query("SELECT source_id, buying FROM listings")}
        self.assertTrue(buying["v1|1|0"].endswith("fits your Tacoma"))
        self.assertTrue(buying["v1|2|0"].endswith("may fit"))

    def test_other_watches_have_no_fitment(self):
        pc = db.save_machine({"name": "PC", "kind": "pc"})
        half = db.save_machine({"name": "Car", "kind": "vehicle", "make": "Toyota", "model": ""})  # no model/year
        for mid in (pc, half, None):
            wid = db.create_watch({"name": "x", "query": "brake pads", "sources": ["ebay"], "machine_id": mid})
            poller.run_watch(db.get_watch(wid), db.get_settings())
            self.assertNotIn("compatibility_filter", self.params())

    def test_fit_is_part_of_the_shared_fetch_key(self):
        a = {"query": "brake pads", "fits": {"year": 2020, "make": "Toyota", "model": "Tacoma"}}
        b = {"query": "brake pads", "fits": {"year": 2018, "make": "Toyota", "model": "Tacoma"}}
        self.assertNotEqual(poller.fetch_key("ebay", a, {}), poller.fetch_key("ebay", b, {}))


if __name__ == "__main__":
    unittest.main()
