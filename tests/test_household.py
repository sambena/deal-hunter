"""Household things: kitchen/appliance kinds, and CPSC recalls by brand + model or kind of product."""

import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import homeassistant  # noqa: E402
import product_recalls  # noqa: E402

CPSC = [
    {"RecallNumber": "24-300", "RecallDate": "2024-08-08T00:00:00", "Title": "Samsung Recalls Slide-In Electric Ranges",
     "Description": "", "URL": "https://www.cpsc.gov/r1", "Products": [{"Name": "Samsung Slide-In Electric Ranges"}],
     "Hazards": [{"Name": "Fire hazard"}], "Remedies": [{"Name": "Get free knob locks"}]},
    {"RecallNumber": "23-074", "RecallDate": "2022-12-22T00:00:00", "Title": "Samsung Recalls Top-Load Washing Machines",
     "Description": "Sold in a range of colors.", "URL": "https://www.cpsc.gov/r2",
     "Products": [{"Name": "Top-load washers", "Model": "WA49B5205AW"}],
     "Hazards": [{"Name": "Fire"}], "Remedies": [{"Name": "Stop using and unplug"}]},
    {"RecallNumber": "10-001", "RecallDate": "2010-01-01T00:00:00", "Title": "Samsung Recalls Chargers", "Description": "",
     "Products": [{"Name": "Chargers"}]},
]


class RecallMatchTest(unittest.TestCase):
    def setUp(self):
        product_recalls._cache.clear()
        p = mock.patch.object(product_recalls, "_fetch", lambda make, now: CPSC)
        p.start()
        self.addCleanup(p.stop)

    def test_kind_of_product_from_title_only(self):
        r = product_recalls.recalls({"make": "Samsung", "name": "Kitchen stove", "kind": "appliance"})
        self.assertEqual([(x["campaign"], x["match"]) for x in r], [("24-300", "type")])  # not the washer's "range"

    def test_model_number_wins(self):
        for model in ("WA49B5205AW", "WA49B5205AW/US"):  # a region suffix still matches
            r = product_recalls.recalls({"make": "Samsung", "name": "Laundry", "model": model, "kind": "appliance"})
            self.assertEqual([(x["campaign"], x["match"], x["park_it"]) for x in r], [("23-074", "model", True)])

    def test_needs_a_make(self):
        with self.assertRaises(product_recalls.LookupFailed):
            product_recalls.recalls({"make": "", "name": "fridge"})

    def test_product_words(self):
        self.assertIn("refrigerator", product_recalls.product_words({"name": "Garage fridge"}))
        self.assertEqual(product_recalls.product_words({"name": "Thing", "kind": "tv"}), ["television", "tv"])


class KindsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_kitchen_is_a_home_kind(self):
        mid = db.save_machine({"name": "Blender", "kind": "kitchen", "make": "Ninja"})
        self.assertEqual((db.get_machine(mid)["kind"], db.get_machine(mid)["category"]), ("kitchen", "home"))

    def test_home_assistant_guesses(self):
        guess = lambda t: next((k for p, k, _ in homeassistant.KIND_RULES if re.search(p, t)), None)  # noqa: E731
        self.assertEqual([guess(t) for t in ("lg refrigerator", "ninja blender", "ge range", "orange lamp")],
                         ["appliance", "kitchen", "appliance", None])

    def test_recalls_route_picks_the_database(self):
        import server
        pc = db.save_machine({"name": "PC", "kind": "pc"})
        stove = db.save_machine({"name": "Stove", "kind": "appliance", "make": "Samsung"})
        with self.assertRaises(server.HTTPError):
            server.machine_recalls({}, {}, str(pc))
        with mock.patch.object(product_recalls, "_fetch", lambda make, now: CPSC):
            out = server.machine_recalls({}, {}, str(stove))
        self.assertEqual((out["source"], out["recalls"][0]["campaign"]), ("cpsc", "24-300"))


if __name__ == "__main__":
    unittest.main()
