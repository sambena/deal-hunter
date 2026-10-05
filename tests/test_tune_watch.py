"""A new watch is searched at once; each check records what every site returned; AI suggests a better watch."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import db  # noqa: E402
import poller  # noqa: E402
import sources  # noqa: E402


def item(sid, title, price):
    return {"source": "ebay", "source_id": sid, "title": title, "price": price, "shipping": 0.0, "currency": "USD",
            "url": "u", "image": None, "location": "", "condition": "used", "buying": "", "text": ""}


class TuneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        import server
        self.server = server

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_new_watch_is_searched_straight_away(self):
        with mock.patch.object(poller, "run_one", return_value={"new": 3, "errors": []}) as run:
            out = self.server.create_watch({"name": "x", "query": "rtx 3060", "sources": ["ebay"]}, {})
        run.assert_called_once_with(out["id"])
        self.assertEqual(out["new"], 3)

    def test_each_check_records_what_sites_returned(self):
        items = [item("1", "RTX 3060 12GB", 250), item("2", "RTX 3060 12GB", 400), item("3", "GTX 1060", 90),
                 item("4", "RTX 3060 for parts", 50)]

        def ksl(w, s):
            raise sources.SourceError("KSL's bot protection blocked the request")
        with mock.patch.dict(sources.SOURCES, {"ebay": lambda w, s: [dict(i) for i in items], "ksl": ksl}):
            wid = db.create_watch({"name": "3060", "query": "rtx 3060", "max_price": 300, "sources": ["ebay", "ksl"]})
            poller.run_watch(db.get_watch(wid), db.get_settings())
        lc = db.get_watch(wid)["last_check"]
        self.assertEqual(lc["ebay"], {"raw": 4, "kept": 1, "new": 1, "dropped": {
            "over the max price": 1, "title missing a search word": 1, "junk words (parts, broken...)": 1}})
        self.assertIn("bot protection", lc["ksl"]["error"])
        self.assertEqual(self.server.diagnosis(db.get_watch(wid))[0]["source"], "ebay")

    def test_tune_suggests_and_cleans(self):
        wid = db.create_watch({"name": "Pickup", "query": "pickup", "kind": "vehicle", "sources": ["ksl_cars"]})
        db.execute("""UPDATE watches SET last_check = '{"ebay": {"raw": 40, "kept": 0, "new": 0,
                      "dropped": {"over the max price": 40}}}' WHERE id = ?""", (wid,))
        seen = {}

        def fake_run(settings, action, prompt, schema):
            seen["prompt"] = prompt
            return {"query": "pickup 4x4", "exclude": ["parts", " ", "salvage"], "min_price": None, "max_price": 6000,
                    "year_min": 2005, "year_max": None, "max_miles": -5, "size": None,
                    "sources": ["ksl_cars", "poshmark", "made_up"], "reason": "Wider price."}
        watch = {**db.get_watch(wid), "max_price": 3000}
        with mock.patch.object(ai, "run", fake_run):
            s = self.server.ai_tune_watch({"watch": watch, "watch_id": wid, "instructions": "finds nothing"}, {})
        self.assertIn("40 returned, 0 kept (dropped: 40 over the max price)", seen["prompt"])
        self.assertIn("finds nothing", seen["prompt"])
        self.assertEqual((s["query"], s["exclude"], s["max_price"], s["year_min"], s["max_miles"]),
                         ("pickup 4x4", ["parts", "salvage"], 6000.0, 2005, None))
        self.assertEqual(s["sources"], ["ksl_cars"])  # Poshmark isn't for cars; made-up keys dropped
        self.assertEqual(s["diagnosis"][0]["raw"], 40)

    def test_tune_needs_a_query(self):
        with self.assertRaises(self.server.HTTPError):
            self.server._ai_prompt("tune", {"watch": {"query": " "}})


if __name__ == "__main__":
    unittest.main()
