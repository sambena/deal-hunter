"""Car and truck watches: year/miles from listings, vehicle filters, vehicle-only sources, year-band deals."""

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

VEHICLE = {"name": "Tacoma", "query": "toyota tacoma", "kind": "vehicle", "year_min": 2015, "year_max": 2020,
           "max_miles": 150000, "max_price": 30000}


def car(sid, title, price, miles=None, text=""):
    return {"source": "craigslist", "source_id": sid, "title": title, "price": price, "shipping": 0.0,
            "currency": "USD", "url": "u", "image": None, "location": "Orem", "condition": "used",
            "buying": "Craigslist", "text": text, "miles": miles}


class Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self.body


class ParsingTest(unittest.TestCase):
    def test_year_miles_status(self):
        self.assertEqual(matching.vehicle_year("2018 Toyota Tacoma TRD"), 2018)
        self.assertIsNone(matching.vehicle_year("Tacoma floor mats"))
        self.assertEqual(matching.vehicle_miles("1999 F-150, 145k miles"), 145000)
        self.assertEqual(matching.vehicle_miles("98,500 miles"), 98500)
        self.assertEqual(matching.vehicle_miles("only 98.5k mi"), 98500)
        self.assertIsNone(matching.vehicle_miles("5 miles from campus"))
        self.assertEqual(matching.title_status("2012 Civic SALVAGE title"), "salvage")
        self.assertEqual(matching.title_status("rebuilt title, runs great"), "rebuilt")
        self.assertEqual(matching.title_status("clean title"), "")

    def test_check_vehicle(self):
        self.assertEqual(matching.check_vehicle(2018, 90000, VEHICLE), (True, ""))
        self.assertFalse(matching.check_vehicle(2012, 90000, VEHICLE)[0])
        self.assertFalse(matching.check_vehicle(2022, 90000, VEHICLE)[0])
        self.assertFalse(matching.check_vehicle(2018, 200000, VEHICLE)[0])
        self.assertTrue(matching.check_vehicle(2018, None, VEHICLE)[0])  # miles not stated
        self.assertFalse(matching.check_vehicle(None, 90000, VEHICLE)[0])  # no year: parts, wanted ads

    def test_vehicle_junk_list(self):
        w = {**VEHICLE, "exclude": []}
        self.assertTrue(matching.check("2018 Toyota Tacoma sold as is", w, ["as is"], 20000)[0])
        self.assertFalse(matching.check("2018 Toyota Tacoma parting out", w, ["as is"], 20000)[0])

    def test_year_band_history(self):
        hist = [(20000, 2016), (21000, 2017), (22000, 2018), (23000, 2018), (24000, 2017), (9000, 2005)]
        self.assertEqual(len(matching.vehicle_history(2017, hist)), 5)
        self.assertEqual(len(matching.vehicle_history(2010, hist)), 6)  # too few nearby: all of them


class SourceParamsTest(unittest.TestCase):
    def test_craigslist_cars_and_trucks(self):
        seen = []
        body = {"data": {"location": {"lat": 40.31, "lon": -111.69}, "decode": {
            "minPostingId": 1000, "minPostedDate": 0, "locations": [0, [292, "provo"]],
            "locationDescriptions": [0, "Orem"]},
            "items": [[5, 1, 145, 22000, "1:1~40.30~-111.70", "x", [13, "uuid1"], [6, "orem-2018-tacoma"],
                       [9, 125000], [10, "$22,000"], "2018 toyota tacoma"]]}}

        def opener(req, timeout):
            seen.append(req.full_url)
            return Resp(json.dumps(body).encode())
        with mock.patch.object(sources, "CRAIGSLIST_GAP_SECONDS", 0), \
                mock.patch.object(sources.urllib.request, "urlopen", opener):
            items = sources.craigslist(VEHICLE, {"zip_code": "84057", "local_radius_miles": 50})
        q = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
        self.assertEqual((q["searchPath"], q["min_auto_year"], q["max_auto_year"], q["max_auto_miles"]),
                         (["cta"], ["2015"], ["2020"], ["150000"]))
        self.assertEqual((items[0]["miles"], items[0]["buying"]), (125000, "Craigslist · owner"))

    def test_offerup_vehicle_params(self):
        seen = []
        data = {"props": {"pageProps": {"searchFeedResponse": {"looseTiles": [{"listing": {
            "__typename": "ModularFeedListing", "listingId": "a", "title": "2020 Toyota Tacoma", "price": "31800",
            "vehicleMiles": 21813, "locationName": "Ogden, UT", "flags": ["LOCAL_PICKUP"]}}]}}}}
        page = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'

        def opener(req, timeout):
            seen.append(req.full_url)
            return Resp(page.encode())
        with mock.patch.object(sources, "OFFERUP_GAP_SECONDS", 0), \
                mock.patch.dict(sources._places, {"84057": {"lat": 40.3, "lon": -111.7, "city": "Orem", "state": "UT"}}), \
                mock.patch.object(sources.urllib.request, "urlopen", opener):
            items = sources.offerup(VEHICLE, {"zip_code": "84057", "local_radius_miles": 50})
        q = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
        self.assertEqual((q["radius"], q["veh_year_min"], q["veh_year_max"], q["veh_mileage"]),
                         (["80"], ["2015"], ["2020"], ["150000"]))
        self.assertEqual(items[0]["miles"], 21813)


class PollerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        self.calls = []

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_vehicle_watch_end_to_end(self):
        items = [car("1", "2018 Toyota Tacoma TRD", 24000, 98000), car("2", "2012 Toyota Tacoma", 12000, 150000),
                 car("3", "Toyota Tacoma floor mats", 80), car("4", "2017 Toyota Tacoma 160k miles", 20000),
                 car("5", "2016 toyota tacoma rebuilt title", 15000)]

        def fake(name):
            def run(w, s):
                self.calls.append(name)
                return [dict(i) for i in items] if name == "craigslist" else []
            return run
        with mock.patch.dict(sources.SOURCES, {n: fake(n) for n in list(sources.SOURCES)}):
            wid = db.create_watch({**VEHICLE, "sources": ["craigslist", "reddit", "slickdeals", "offerup"]})
            r = poller.run_watch(db.get_watch(wid), db.get_settings())
        self.assertEqual(sorted(self.calls), ["craigslist", "offerup"])  # no hardware sources
        rows = db.query("SELECT source_id, year, miles, title_status FROM listings ORDER BY source_id")
        self.assertEqual(rows, [{"source_id": "1", "year": 2018, "miles": 98000, "title_status": None},
                                {"source_id": "5", "year": 2016, "miles": None, "title_status": "rebuilt"}])
        self.assertEqual(r["new"], 2)

    def test_watch_fields_and_edit_prunes(self):
        wid = db.create_watch({**VEHICLE, "year_min": "1800", "max_miles": "abc", "kind": "boat"})
        w = db.get_watch(wid)
        self.assertEqual((w["kind"], w["year_min"], w["max_miles"]), ("item", None, None))
        db.update_watch(wid, {"kind": "vehicle", "year_min": 2015, "max_miles": 150000})
        for sid, year, miles in (("a", 2016, 90000), ("b", 2019, 140000)):
            db.execute("""INSERT INTO listings (watch_id, source, source_id, title, total, first_seen, status, year, miles)
                          VALUES (?, 'craigslist', ?, 'toyota tacoma', 20000, ?, 'new', ?, ?)""",
                       (wid, sid, time.time(), year, miles))
        import server
        out = server.update_watch({"max_miles": 100000}, {}, str(wid))
        self.assertEqual(out["removed"], 1)
        self.assertEqual([r["source_id"] for r in db.query("SELECT source_id FROM listings")], ["a"])


if __name__ == "__main__":
    unittest.main()
