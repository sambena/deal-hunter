"""My hardware beyond PCs: device kinds, the Home Assistant import, and AI prompts for any device."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import db  # noqa: E402
import homeassistant as ha  # noqa: E402

# Shapes as Home Assistant's template endpoint returns them (from a real install).
HA_DEVICES = [
    {"id": "tv1", "name": "LG", "make": "LGE", "model": "OLED65B2AUA", "area": "Living Room",
     "integrations": ["cast"], "domains": ["media_player"], "entry_type": None},
    {"id": "ph1", "name": "Pixel 10", "make": "Google", "model": "Pixel 10", "area": None,
     "integrations": ["mobile_app"], "domains": ["sensor"], "entry_type": None},
    {"id": "ip1", "name": "iPad", "make": None, "model": None, "area": None,
     "integrations": ["mobile_app"], "domains": ["sensor"], "entry_type": None},
    {"id": "plug1", "name": "Microwave", "make": "Third Reality, Inc", "model": "3RSP02064Z", "area": "Kitchen",
     "integrations": ["zha"], "domains": ["switch", "sensor"], "entry_type": None},
    {"id": "wash", "name": "Washer", "make": "LGE", "model": "T1789EFH_F (DEVICE_WASHER)", "area": None,
     "integrations": ["lg_thinq"], "domains": ["sensor"], "entry_type": None},
    {"id": "ap1", "name": "AP Garage", "make": "Ubiquiti Networks", "model": "U7NHD", "area": None,
     "integrations": [], "domains": ["sensor"], "entry_type": None},
    {"id": "wlan", "name": "Bryanfamily", "make": "Ubiquiti Networks", "model": "UniFi WLAN", "area": None,
     "integrations": ["unifi"], "domains": ["switch"], "entry_type": None},
    {"id": "vm1", "name": "plex", "make": "Proxmox Server Solutions GmbH", "model": None, "area": None,
     "integrations": ["proxmoxve"], "domains": ["sensor"], "entry_type": None},
    {"id": "sun", "name": "Sun", "make": None, "model": None, "area": None,
     "integrations": ["sun"], "domains": ["sensor"], "entry_type": "service"},
    {"id": "pc1", "name": "Dexter", "make": "Micro-Star INTL CO., LTD.", "model": None, "area": None,
     "integrations": [], "domains": ["switch"], "entry_type": None},
]


class ClassifyTest(unittest.TestCase):
    def cands(self, known_names=(), known_refs=()):
        with mock.patch.object(ha, "fetch_devices", lambda url, token: HA_DEVICES):
            return {c["ref"]: c for c in ha.candidates("http://ha", "t", set(known_names), set(known_refs))}

    def test_kinds_and_ticks(self):
        c = self.cands(known_names={"dexter"})
        self.assertEqual((c["tv1"]["kind"], c["tv1"]["checked"]), ("tv", True))
        self.assertEqual(c["ph1"]["kind"], "phone")
        self.assertEqual(c["ip1"]["kind"], "tablet")
        self.assertEqual((c["plug1"]["kind"], c["plug1"]["checked"]), ("smart home", False))  # named Microwave
        self.assertEqual((c["wash"]["kind"], c["wash"]["model"]), ("appliance", "T1789EFH_F"))
        self.assertEqual(c["ap1"]["kind"], "network")
        self.assertTrue(c["pc1"]["already"])
        self.assertFalse(c["pc1"]["checked"])

    def test_virtual_devices_are_left_out(self):
        c = self.cands()
        for ref in ("wlan", "vm1", "sun"):
            self.assertNotIn(ref, c)

    def test_already_imported_refs_are_left_out(self):
        self.assertNotIn("tv1", self.cands(known_refs={"tv1"}))

    def test_full_model(self):
        self.assertEqual(ha.full_model("LGE", "OLED65B2AUA"), "LG OLED65B2AUA")
        self.assertEqual(ha.full_model("Google Inc.", "Google Nest Hub"), "Google Nest Hub")
        self.assertEqual(ha.full_model("Ubiquiti Networks", "U7NHD"), "Ubiquiti U7NHD")

    def test_needs_address_and_token(self):
        with self.assertRaises(ha.HAError):
            ha.fetch_devices("", "")


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        import server
        self.server = server

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_import_adds_once_with_kind_and_model(self):
        picked = [{"ref": "tv1", "name": "Living room TV", "kind": "tv", "make": "LGE", "model": "OLED65B2AUA",
                   "area": "Living Room"}]
        self.assertEqual(self.server.ha_import({"devices": picked}, {}), {"added": 1})
        self.assertEqual(self.server.ha_import({"devices": picked}, {}), {"added": 0})  # same ref again
        m = db.list_machines()[0]
        self.assertEqual((m["name"], m["kind"], m["model"], m["source_ref"]),
                         ("Living room TV", "tv", "LG OLED65B2AUA", "tv1"))
        self.assertIn("Living Room", m["notes"])

    def test_rules_only_for_pcs(self):
        tv = db.save_machine({"name": "TV", "kind": "tv", "model": "LG OLED65B2AUA"})
        res = self.server.suggest_upgrades({}, {}, str(tv))
        self.assertEqual(res["suggestions"], [])
        self.assertIn("AI", res["explanation"])
        pc = db.save_machine({"name": "Box", "kind": "pc", "parts": [{"category": "cpu", "model": "Ryzen 7 3700X"}]})
        self.assertEqual(self.server.suggest_upgrades({}, {}, str(pc))["platform"], "AMD AM4")

    def test_old_databases_get_the_new_columns(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        path = Path(self.tmp.name) / "old.db"
        import sqlite3
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE machines (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                    "notes TEXT NOT NULL DEFAULT '', parts TEXT NOT NULL DEFAULT '[]')")
        old.execute("INSERT INTO machines (name, parts) VALUES ('Old box', ?)", (json.dumps([]),))
        old.commit()
        old.close()
        db.DB_PATH = path
        m = db.list_machines()[0]
        self.assertEqual((m["kind"], m["model"], m["source_ref"]), ("pc", "", None))


class PromptTest(unittest.TestCase):
    def test_gear_gets_the_gear_prompt(self):
        prompt, schema = ai.upgrades_prompt({"name": "Living room TV", "kind": "tv", "model": "LG OLED65B2AUA",
                                             "parts": [], "notes": ""})
        self.assertIn("Tv: Living room TV", prompt)
        self.assertIn("Make/model: LG OLED65B2AUA", prompt)
        self.assertIn("upgrades or replacements", prompt)
        self.assertIs(schema, ai.SUGGESTION_SCHEMA)

    def test_pcs_keep_the_parts_prompt(self):
        prompt, _ = ai.upgrades_prompt({"name": "Box", "kind": "pc", "parts": [{"category": "cpu", "model": "x"}]})
        self.assertIn("drop-in upgrades", prompt)


if __name__ == "__main__":
    unittest.main()
