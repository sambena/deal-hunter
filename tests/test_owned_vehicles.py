"""Vehicles someone owns: VIN decoding and recalls (NHTSA answers made up, no network), VIN/odometer fields."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import vehicles  # noqa: E402

VPIC = {"Results": [{"Make": "TOYOTA", "Model": "Tacoma", "ModelYear": "2020", "Trim": "SR5", "BodyClass": "Pickup",
                     "DriveType": "4WD/4-Wheel Drive/4x4", "EngineCylinders": "6", "DisplacementL": "3.5",
                     "FuelTypePrimary": "Gasoline", "ErrorCode": "0", "ErrorText": "0 - VIN decoded clean."}]}
RECALLS = {"Count": 1, "results": [{"NHTSACampaignNumber": "20V682000", "ReportReceivedDate": "04/11/2020",
                                    "Component": "FUEL SYSTEM", "Summary": "Fuel pump may fail.",
                                    "Consequence": "Engine can stall.", "Remedy": "Replace pump.", "parkIt": "False"}]}


class VinTest(unittest.TestCase):
    def test_clean_vin(self):
        self.assertEqual(vehicles.clean_vin(" 3tmcz5an6-lm327174 "), "3TMCZ5AN6LM327174")
        for bad in ("123", "3TMCZ5AN6LM32717O", "", None):  # too short, has an O
            with self.assertRaises(vehicles.LookupFailed):
                vehicles.clean_vin(bad)

    def test_decode(self):
        urls = []
        with mock.patch.object(vehicles, "_get", lambda url: urls.append(url) or VPIC):
            v = vehicles.decode_vin("3TMCZ5AN6LM327174")
        self.assertEqual(urls, ["https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/3TMCZ5AN6LM327174?format=json"])
        self.assertEqual((v["make"], v["model"], v["year"], v["trim"], v["engine"], v["warning"]),
                         ("Toyota", "Tacoma", 2020, "SR5", "3.5L V6", ""))

    def test_unknown_vin(self):
        with mock.patch.object(vehicles, "_get", lambda url: {"Results": [{"Make": "", "ErrorCode": "11"}]}):
            with self.assertRaises(vehicles.LookupFailed):
                vehicles.decode_vin("3TMCZ5AN6LM327174")

    def test_recalls_cached(self):
        vehicles._recall_cache.clear()
        urls = []
        with mock.patch.object(vehicles, "_get", lambda url: urls.append(url) or RECALLS):
            r = vehicles.recalls("Toyota", "Tacoma", 2020, now=1000)
            vehicles.recalls("toyota", "TACOMA", 2020, now=2000)  # same vehicle, from the cache
            vehicles.recalls("Toyota", "Tacoma", 2020, now=1000 + vehicles.RECALL_CACHE_SECONDS + 1)
        self.assertEqual(len(urls), 2)
        self.assertIn("make=Toyota&model=Tacoma&modelYear=2020", urls[0])
        self.assertEqual((r[0]["campaign"], r[0]["park_it"]), ("20V682000", False))
        with self.assertRaises(vehicles.LookupFailed):
            vehicles.recalls("Toyota", "", 2020)

    def test_recalls_fall_back_to_base_model(self):
        vehicles._recall_cache.clear()
        urls = []

        def nhtsa(url):
            urls.append(url)
            if "Tacoma+TRD" in url:
                raise vehicles.LookupFailed("NHTSA answered HTTP 400")
            return RECALLS
        with mock.patch.object(vehicles, "_get", nhtsa):
            r = vehicles.recalls("Toyota", "Tacoma TRD Off-Road", 2020)
        self.assertEqual(len(r), 1)
        self.assertIn("model=Tacoma&", urls[1])


class FieldsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_vin_and_odometer_kept_when_not_sent(self):
        mid = db.save_machine({"name": "Truck", "kind": "vehicle", "vin": "3tmcz5an6 lm327174", "odometer": "108,642"})
        m = db.get_machine(mid)
        self.assertEqual((m["vin"], m["odometer"]), ("3TMCZ5AN6LM327174", 108642))
        db.save_machine({"name": "Truck", "kind": "vehicle", "make": "Toyota"}, mid)  # an app that doesn't send them
        m = db.get_machine(mid)
        self.assertEqual((m["vin"], m["odometer"], m["make"]), ("3TMCZ5AN6LM327174", 108642, "Toyota"))
        db.save_machine({"name": "Truck", "kind": "vehicle", "vin": "", "odometer": ""}, mid)
        m = db.get_machine(mid)
        self.assertEqual((m["vin"], m["odometer"]), ("", None))


if __name__ == "__main__":
    unittest.main()
