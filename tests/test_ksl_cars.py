"""KSL Cars: search URL from a vehicle watch and reading cars out of the page (made-up page, no network)."""

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

CARS = [[
    {"id": 10697438, "listingType": "CAR", "title": "2015 Toyota Tacoma V6", "price": 21250, "mileage": 96774,
     "makeYear": 2015, "make": "Toyota", "model": "Tacoma", "trim": "V6", "newUsed": "Used",
     "sellerType": "Dealership", "dealer": {"name": "Generous Auto"},
     "location": {"city": "Sandy", "state": "UT", "zip": "84070"},
     "primaryImage": {"url": "https://image.ksldigital.com/a.jpg"}},
    {"id": 10841453, "listingType": "CAR", "title": "2020 Toyota Tacoma Limited", "price": 26900, "mileage": 0,
     "makeYear": 2020, "newUsed": "Used", "sellerType": "For Sale By Owner",
     "location": {"city": "Salt Lake City", "state": "UT"}},
], [
    {"id": 10697438, "listingType": "CAR", "title": "2015 Toyota Tacoma V6", "price": 21250},  # featured repeat
    {"id": 5, "listingType": "CAR", "title": "2009 Toyota Tacoma", "price": 9000, "mileage": 210000,
     "makeYear": 2009, "sellerType": "For Sale By Owner", "location": {"city": "Provo", "state": "UT"}},
]]
WATCH = {"name": "Tacoma", "query": "toyota tacoma", "kind": "vehicle", "year_min": 2015, "year_max": 2020,
         "max_miles": 150000, "max_price": 29999.5}
SETTINGS = {"zip_code": "84057", "local_radius_miles": 50}


def page(cars=CARS):
    payload = '7:["$","SearchStoreProvider",null,{"initialState":{"results":' + json.dumps(cars) + '}}]'
    return ('<html><script>self.__next_f.push([1,' + json.dumps(payload) + '])</script></html>').encode()


class Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self.body


@mock.patch.object(sources, "KSL_GAP_SECONDS", 0)
class KslCarsTest(unittest.TestCase):
    def fetch(self, body=None, watch=WATCH):
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            return Resp(body or page())
        with mock.patch.object(sources.urllib.request, "urlopen", opener):
            return sources.ksl_cars(watch, SETTINGS), seen

    def test_url_and_fields(self):
        items, seen = self.fetch()
        self.assertEqual(seen[0], "https://cars.ksl.com/search/keyword/toyota%20tacoma/yearFrom/2015/yearTo/2020/"
                                  "mileageTo/150000/priceTo/30000/zip/84057/miles/50")
        self.assertEqual([i["source_id"] for i in items], ["10697438", "10841453", "5"])  # repeat dropped
        first, second = items[0], items[1]
        self.assertEqual((first["year"], first["miles"], first["price"]), (2015, 96774, 21250.0))
        self.assertEqual((first["buying"], first["location"]), ("KSL Cars · Generous Auto", "Sandy, UT"))
        self.assertEqual(first["url"], "https://cars.ksl.com/listing/10697438")
        self.assertIsNone(second["miles"])  # 0 = not stated
        self.assertEqual(second["buying"], "KSL Cars · private seller")

    def test_errors(self):
        with self.assertRaises(sources.SourceError):
            sources.ksl_cars(WATCH, {**SETTINGS, "zip_code": ""})
        with self.assertRaises(sources.SourceError):
            self.fetch(b"<html>new design</html>")

    def test_only_vehicle_watches_use_it(self):
        tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(tmp.name) / "t.db"
        try:
            db.update_settings(SETTINGS)
            car = db.create_watch({**WATCH, "sources": ["ksl_cars"]})
            thing = db.create_watch({"name": "x", "query": "toyota tacoma", "sources": ["ksl_cars"]})
            with mock.patch.object(sources.urllib.request, "urlopen", lambda req, timeout: Resp(page())):
                r = poller.run_watch(db.get_watch(car), db.get_settings())
                self.assertEqual(poller.run_watch(db.get_watch(thing), db.get_settings())["new"], 0)
            self.assertEqual(r["new"], 2)  # the 2009 is too old
            self.assertEqual(db.query("SELECT year, miles FROM listings ORDER BY year"),
                             [{"year": 2015, "miles": 96774}, {"year": 2020, "miles": None}])
        finally:
            db._conn.close()
            db._conn = None
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
