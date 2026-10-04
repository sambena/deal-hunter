"""Device details (make, model, year, MSRP, purchase), custom fields, and listings since_id for apps."""

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import db  # noqa: E402


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


class FieldsTest(TempDb):
    def test_details_and_custom_fields_round_trip(self):
        mid = db.save_machine({"name": "Living room TV", "kind": "tv", "make": "LG", "model": "OLED65B2AUA",
                               "year": "2022", "msrp": "$1,999.99", "purchased": "2023-01-15", "price_paid": "1299",
                               "custom": [{"label": "Serial number", "value": "205MAXX1234"},
                                          {"label": "", "value": "dropped: no label"},
                                          {"label": "Warranty until", "value": "2026-01-15"}]})
        m = db.get_machine(mid)
        self.assertEqual((m["make"], m["model"], m["year"], m["msrp"], m["purchased"], m["price_paid"]),
                         ("LG", "OLED65B2AUA", 2022, 1999.99, "2023-01-15", 1299.0))
        self.assertEqual([f["label"] for f in m["custom"]], ["Serial number", "Warranty until"])

    def test_bad_numbers_are_ignored_not_errors(self):
        mid = db.save_machine({"name": "x", "kind": "tv", "year": "around 2019", "msrp": "lots"})
        m = db.get_machine(mid)
        self.assertEqual((m["year"], m["msrp"]), (None, None))
        self.assertIsNone(db.get_machine(db.save_machine({"name": "y", "kind": "tv", "year": 1850}))["year"])

    def test_ai_sees_the_details(self):
        text = ai._describe_machine({"name": "TV", "kind": "tv", "make": "LG", "model": "OLED65B2AUA", "year": 2022,
                                     "msrp": 1999.99, "price_paid": 1299, "purchased": "2023-01-15", "parts": [],
                                     "custom": [{"label": "Size", "value": "65 inch"}], "notes": ""})
        for bit in ("Make/model: LG OLED65B2AUA", "Year: 2022", "MSRP: $2,000", "Paid: $1,299 (2023-01-15)",
                    "Size: 65 inch"):
            self.assertIn(bit, text)


class SplitMigrationTest(TempDb):
    def test_imported_devices_get_make_split_off_and_typed_ones_are_left_alone(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.executescript("""
            CREATE TABLE machines (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
              notes TEXT NOT NULL DEFAULT '', parts TEXT NOT NULL DEFAULT '[]', kind TEXT NOT NULL DEFAULT 'pc',
              model TEXT NOT NULL DEFAULT '', source_ref TEXT);
            INSERT INTO machines (name, kind, model, source_ref) VALUES ('LG', 'tv', 'LG OLED65B2AUA', 'ha1');
            INSERT INTO machines (name, kind, model, source_ref) VALUES ('iPad', 'tablet', 'iPad', 'ha2');
            INSERT INTO machines (name, kind, model, source_ref) VALUES ('Mine', 'tv', 'Sony Bravia XR', NULL);
            PRAGMA user_version = 3;""")
        conn.close()
        got = {m["name"]: (m["make"], m["model"]) for m in db.list_machines()}
        self.assertEqual(got, {"LG": ("LG", "OLED65B2AUA"), "iPad": ("", "iPad"), "Mine": ("", "Sony Bravia XR")})


class SinceIdTest(TempDb):
    def test_only_newer_listings_oldest_first_any_status(self):
        import server
        wid = db.create_watch({"name": "w", "query": "x", "sources": ["ebay"]})
        for i, status in enumerate(["new", "dismissed", "seen", "new"], 1):
            db.execute("INSERT INTO listings (watch_id, source, source_id, title, first_seen, status) "
                       "VALUES (?, 'ebay', ?, 't', ?, ?)", (wid, str(i), time.time(), status))
        got = [r["id"] for r in server.list_listings({}, {"since_id": "1"})["listings"]]
        self.assertEqual(got, [2, 3, 4])  # includes the dismissed one; apps decide what to alert on
        only_new = server.list_listings({}, {"since_id": "1", "status": "new"})["listings"]
        self.assertEqual([r["id"] for r in only_new], [4])
        self.assertEqual(server.list_listings({}, {"since_id": "4"})["listings"], [])


if __name__ == "__main__":
    unittest.main()
