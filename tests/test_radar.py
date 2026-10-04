"""Deal radar: a proposed watch per device, rules for PCs and one AI request for everything else."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import db  # noqa: E402


class RadarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        import server
        self.server = server
        self.pc = db.save_machine({"name": "Dexter", "kind": "pc",
                                   "parts": [{"category": "cpu", "model": "AMD Ryzen 7 3700X"},
                                             {"category": "motherboard", "model": "MSI MPG B550 GAMING PLUS"}]})
        self.empty_pc = db.save_machine({"name": "Rosie", "kind": "pc", "parts": []})
        self.tv = db.save_machine({"name": "Living room TV", "kind": "tv", "model": "LG OLED65B2AUA"})
        self.plug = db.save_machine({"name": "Microwave plug", "kind": "smart home", "model": "Third Reality 3RSP02064Z"})

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_rules_only_without_ai(self):
        res = self.server.deal_radar({}, {})
        pc_items = [i for i in res["items"] if i["machine_id"] == self.pc]
        self.assertEqual([i["category"] for i in pc_items], ["cpu", "ram"])  # best CPU + one RAM kit
        self.assertEqual(pc_items[0]["query"], "5800x3d")
        why = {s["name"]: s["why"] for s in res["skipped"]}
        self.assertIn("Get specs", why["Rosie"])
        self.assertIn("AI", why["Living room TV"])
        self.assertNotIn("Microwave plug", why)  # smart-home gadgets aren't radar material
        self.assertEqual(res["gear_count"], 1)

    def test_one_ai_request_covers_the_gear(self):
        settings = {**db.get_settings(), "ai_provider": "gemini", "gemini_api_key": "k", "gemini_free_tier": True}
        answer = json.dumps({"suggestions": [
            {"device_id": self.tv, "name": "LG C4 65\"", "category": "tv", "query": "oled65c4", "exclude": [],
             "max_price": 1300, "reason": "brighter panel, 144 Hz"},
            {"device_id": 999, "name": "made up", "category": "tv", "query": "x", "exclude": [], "max_price": None,
             "reason": "not a real device"}]})
        calls = []
        with mock.patch.object(db, "get_settings", lambda: settings), \
             mock.patch.dict(ai.CALLERS, {"gemini": lambda *a: calls.append(a[2]) or (answer, 500, 200, None)}):
            res = self.server.deal_radar({"use_ai": True}, {})
        self.assertEqual(len(calls), 1)
        self.assertIn("LG OLED65B2AUA", calls[0])
        self.assertNotIn("Third Reality", calls[0])
        tv = [i for i in res["items"] if i["from"] == "ai"]
        self.assertEqual([(i["machine_id"], i["query"], i["machine_name"]) for i in tv],
                         [(self.tv, "oled65c4", "Living room TV")])  # the unknown device_id is dropped

    def test_no_watch_without_a_search(self):
        # LGA1700 boards take DDR4 or DDR5, so the rules can't name a RAM kit.
        db.save_machine({"name": "Rosie", "kind": "pc", "parts": [{"category": "cpu", "model": "Intel Core i7-12700"}]},
                        self.empty_pc)
        res = self.server.deal_radar({}, {})
        rosie = [i for i in res["items"] if i["machine_id"] == self.empty_pc]
        self.assertEqual([i["category"] for i in rosie], ["cpu"])
        self.assertTrue(all(i["query"] for i in res["items"]))

    def test_already_watched_is_flagged(self):
        db.create_watch({"name": "x", "query": "5800X3D ", "sources": ["ebay"]})
        res = self.server.deal_radar({}, {})
        cpu = next(i for i in res["items"] if i["category"] == "cpu")
        self.assertTrue(cpu["already"])

    def test_create_without_checking_returns_at_once(self):
        with mock.patch.object(self.server.poller, "run_one", side_effect=AssertionError("should not check")):
            res = self.server.create_watch({"name": "n", "query": "q", "check": False}, {})
        self.assertEqual((res["new"], res["errors"]), (0, []))


if __name__ == "__main__":
    unittest.main()
