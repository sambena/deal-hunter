"""Focus: a per-person setting, and the category every watch and device falls in."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


class FocusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_watch_categories(self):
        truck = db.save_machine({"name": "Truck", "kind": "vehicle"})
        tv = db.save_machine({"name": "TV", "kind": "tv"})
        pc = db.save_machine({"name": "PC", "kind": "pc"})
        w = {
            "car": db.create_watch({"name": "a", "query": "tacoma", "kind": "vehicle"}),
            "parts": db.create_watch({"name": "b", "query": "brake pads", "machine_id": truck}),
            "tv": db.create_watch({"name": "c", "query": "soundbar", "machine_id": tv}),
            "pc": db.create_watch({"name": "d", "query": "5800x3d", "machine_id": pc}),
            "plain": db.create_watch({"name": "e", "query": "rtx 3060"}),
            "chosen": db.create_watch({"name": "f", "query": "klipsch", "category": "home"}),
            "junk": db.create_watch({"name": "g", "query": "x", "category": "boats"}),
        }
        got = {k: (db.get_watch(v)["category"], db.get_watch(v)["category_choice"]) for k, v in w.items()}
        self.assertEqual(got, {"car": ("cars", None), "parts": ("cars", None), "tv": ("home", None),
                               "pc": ("tech", None), "plain": ("tech", None), "chosen": ("home", "home"),
                               "junk": ("tech", None)})
        db.update_watch(w["chosen"], {"category": None})  # back to automatic
        self.assertEqual(db.get_watch(w["chosen"])["category"], "tech")

    def test_device_categories(self):
        for kind, cat in (("vehicle", "cars"), ("server", "tech"), ("audio", "home"), ("appliance", "home")):
            mid = db.save_machine({"name": kind, "kind": kind})
            self.assertEqual(db.get_machine(mid)["category"], cat)

    def test_focus_is_personal_and_members_can_set_it(self):
        friend = db.execute("INSERT INTO users (email, name, role, created_at) VALUES ('f@x.y', 'F', 'member', ?)",
                            (time.time(),))
        with db.as_user(friend):
            db.update_settings({"focus": "cars"}, shared_allowed=False)
            self.assertEqual(db.public_settings(admin=False)["focus"], "cars")
        self.assertEqual(db.get_settings()["focus"], "all")


if __name__ == "__main__":
    unittest.main()
