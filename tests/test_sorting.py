"""Sorting finds: price both ways, and for cars miles, year and make; for clothes size."""

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import matching  # noqa: E402
import poller  # noqa: E402
import sources  # noqa: E402


class SortTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        import server
        self.server = server
        self.wid = db.create_watch({"name": "trucks", "query": "pickup", "kind": "vehicle"})
        rows = [("a", 5000, 2005, 150000, "Ford", None), ("b", 3500, 2012, None, "Chevrolet", None),
                ("c", 4200, 1999, 90000, "Dodge", None), ("d", None, None, None, None, "10.5")]
        for i, (sid, total, year, miles, make, size) in enumerate(rows):
            db.execute("""INSERT INTO listings (watch_id, source, source_id, title, total, first_seen, status,
                          year, miles, make, size) VALUES (?, 'ksl_cars', ?, 't', ?, ?, 'new', ?, ?, ?, ?)""",
                       (self.wid, sid, total, time.time() + i, year, miles, make, size))

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def order(self, sort):
        return [l["source_id"] for l in self.server.list_listings({}, {"sort": sort})["listings"]]

    def test_orders(self):
        self.assertEqual(self.order("price"), ["b", "c", "a", "d"])
        self.assertEqual(self.order("price_desc"), ["a", "c", "b", "d"])
        self.assertEqual(self.order("miles"), ["c", "a", "b", "d"])  # no miles stated: last
        self.assertEqual(self.order("year_desc"), ["b", "a", "c", "d"])
        self.assertEqual(self.order("year_asc"), ["c", "a", "b", "d"])
        self.assertEqual(self.order("make"), ["b", "c", "a", "d"])  # Chevrolet, Dodge, Ford
        self.assertEqual(self.order("size")[0], "d")
        self.assertEqual(self.order("whatever"), ["d", "c", "b", "a"])  # newest

    def test_make_stored_on_new_finds(self):
        items = [{"source": "craigslist", "source_id": "z", "title": "2005 chevy silverado 1500", "price": 4000.0,
                  "shipping": 0.0, "currency": "USD", "url": "u", "image": None, "location": "", "condition": "used",
                  "buying": "", "text": ""}]
        with mock.patch.dict(sources.SOURCES, {"craigslist": lambda w, s: [dict(i) for i in items]}):
            db.update_watch(self.wid, {"sources": ["craigslist"]})
            poller.run_watch(db.get_watch(self.wid), db.get_settings())
        self.assertEqual(db.query("SELECT make FROM listings WHERE source_id = 'z'")[0]["make"], "Chevrolet")


class MakeTest(unittest.TestCase):
    def test_vehicle_make(self):
        cases = {"2005 Chevy Silverado": "Chevrolet", "1997 Ford F-150": "Ford", "2012 GMC 2500": "GMC",
                 "2015 land rover range rover": "Land Rover", "2019 mercedes-benz c300": "Mercedes-Benz",
                 "2010 VW Jetta": "Volkswagen", "1999 FORD RANGER": "Ford", "Toyota truck": ""}
        self.assertEqual({t: matching.vehicle_make(t) for t in cases}, cases)

    def test_existing_finds_get_a_make(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "old.db"
        old = sqlite3.connect(path)
        old.executescript(db.SCHEMA.replace("    make TEXT,                       -- vehicles: Ford, Chevrolet... (for sorting)\n", ""))
        old.execute("INSERT INTO watches (name, query, created_at) VALUES ('w', 'q', 0)")
        old.execute("""INSERT INTO listings (watch_id, source, source_id, title, first_seen, year)
                       VALUES (1, 'ksl_cars', '1', '2008 Dodge Ram 1500', 0, 2008)""")
        old.execute("PRAGMA user_version = 8")
        old.commit()
        old.close()
        db._conn, db.DB_PATH = None, path
        try:
            self.assertEqual(db.query("SELECT make FROM listings")[0]["make"], "Dodge")
        finally:
            db._conn.close()
            db._conn = None
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
