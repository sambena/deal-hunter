"""Parts that fit: a watch linked to an owned vehicle searches eBay's leaf parts category with fitment."""

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
SUGGESTIONS = {"categorySuggestions": [
    {"category": {"categoryId": "11111", "categoryName": "Brake Pad Toys"},
     "categoryTreeNodeAncestors": [{"categoryId": "220"}]},  # not under eBay Motors parts
    {"category": {"categoryId": "33564", "categoryName": "Brake Pads"},
     "categoryTreeNodeAncestors": [{"categoryId": "33559"}, {"categoryId": "6030"}, {"categoryId": "6028"}]},
]}
NO_FITMENT = sources.SourceError('HTTP 400 from api.ebay.com: {"errors":[{"errorId":12506,"message":'
                                 '"The category ID submitted does not support fitment."}]}')


class PartsFitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        sources._parts_category_cache.clear()
        self.urls, self.suggest, self.refuse_fitment = [], SUGGESTIONS, False

        def http(url, **kw):
            self.urls.append(url)
            if "get_category_suggestions" in url:
                return self.suggest
            if self.refuse_fitment and "compatibility_filter" in url:
                raise NO_FITMENT
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

    def searches(self):
        return [urllib.parse.parse_qs(urllib.parse.urlparse(u).query) for u in self.urls if "item_summary" in u]

    def truck_watch(self):
        truck = db.save_machine({"name": "Truck", "kind": "vehicle", "make": "Toyota", "model": "Tacoma", "year": 2020})
        return db.create_watch({"name": "pads", "query": "brake pads", "sources": ["ebay"], "machine_id": truck})

    def test_leaf_category_with_fitment(self):
        r = poller.run_watch(db.get_watch(self.truck_watch()), db.get_settings())
        q = self.searches()[-1]
        self.assertEqual(q["category_ids"], ["33564"])  # the suggestion under Parts & Accessories
        self.assertEqual(q["compatibility_filter"], ["Year:2020;Make:Toyota;Model:Tacoma"])
        self.assertEqual(r, {"new": 2, "errors": []})
        buying = {row["source_id"]: row["buying"] for row in db.query("SELECT source_id, buying FROM listings")}
        self.assertTrue(buying["v1|1|0"].endswith("fits your Tacoma"))
        self.assertTrue(buying["v1|2|0"].endswith("may fit"))

    def test_category_without_fitment_searches_again_without_it(self):
        self.refuse_fitment = True
        r = poller.run_watch(db.get_watch(self.truck_watch()), db.get_settings())
        self.assertEqual(r["errors"], [])  # the live bug: this used to fail the whole eBay search
        first, second = self.searches()
        self.assertIn("compatibility_filter", first)
        self.assertNotIn("compatibility_filter", second)
        self.assertEqual(r["new"], 2)

    def test_no_parts_suggestion_means_no_fitment(self):
        self.suggest = {"categorySuggestions": []}
        poller.run_watch(db.get_watch(self.truck_watch()), db.get_settings())
        q = self.searches()[-1]
        self.assertEqual(q["category_ids"], ["6028"])
        self.assertNotIn("compatibility_filter", q)

    def test_other_watches_have_no_fitment(self):
        pc = db.save_machine({"name": "PC", "kind": "pc"})
        half = db.save_machine({"name": "Car", "kind": "vehicle", "make": "Toyota", "model": ""})  # no model/year
        for mid in (pc, half, None):
            wid = db.create_watch({"name": "x", "query": "brake pads", "sources": ["ebay"], "machine_id": mid})
            poller.run_watch(db.get_watch(wid), db.get_settings())
            self.assertNotIn("compatibility_filter", self.searches()[-1])

    def test_fit_is_part_of_the_shared_fetch_key(self):
        a = {"query": "brake pads", "fits": {"year": 2020, "make": "Toyota", "model": "Tacoma"}}
        b = {"query": "brake pads", "fits": {"year": 2018, "make": "Toyota", "model": "Tacoma"}}
        self.assertNotEqual(poller.fetch_key("ebay", a, {}), poller.fetch_key("ebay", b, {}))


if __name__ == "__main__":
    unittest.main()
