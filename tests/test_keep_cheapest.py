"""Keep only the cheapest N finds per watch: pruning on check and on save, starred kept, no alerts for the cut."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import poller  # noqa: E402
import sources  # noqa: E402


def item(sid, price):
    return {"source": "ksl", "source_id": sid, "title": f"RTX 3060 12GB #{sid}", "price": price, "shipping": 0.0,
            "currency": "USD", "url": "u", "image": None, "location": "", "condition": "used", "buying": "KSL",
            "text": ""}


class KeepCheapestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        self.items = []
        patch = mock.patch.dict(sources.SOURCES, {"ksl": lambda w, s: [dict(i) for i in self.items]})
        patch.start()
        self.addCleanup(patch.stop)
        self.wid = db.create_watch({"name": "3060", "query": "rtx 3060", "sources": ["ksl"], "keep_cheapest": 2})

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def statuses(self):
        return {r["source_id"]: r["status"] for r in db.query("SELECT source_id, status FROM listings")}

    def check(self):
        return poller.run_watch(db.get_watch(self.wid), db.get_settings())

    def test_field_is_saved_and_cleaned(self):
        self.assertEqual(db.get_watch(self.wid)["keep_cheapest"], 2)
        for bad in ("", 0, "0", -3, "x", None):
            db.update_watch(self.wid, {"keep_cheapest": bad})
            self.assertIsNone(db.get_watch(self.wid)["keep_cheapest"], bad)
        db.update_watch(self.wid, {"keep_cheapest": "5"})
        self.assertEqual(db.get_watch(self.wid)["keep_cheapest"], 5)

    def test_check_keeps_cheapest_and_only_they_alert(self):
        self.items = [item("a", 300), item("b", 200), item("c", 250), item("d", None)]
        r = self.check()
        self.assertEqual(r["new"], 2)
        self.assertEqual(self.statuses(), {"a": "pruned", "b": "new", "c": "new", "d": "pruned"})
        # A cheaper find arrives later: it's new, and the dearest shown one is hidden.
        db.execute("UPDATE watches SET polled_sources = '[\"ksl\"]' WHERE id = ?", (self.wid,))
        self.items.append(item("e", 100))
        with mock.patch.object(poller, "notify_discord") as discord:
            db.update_settings({"discord_enabled": True, "discord_deals_only": False,
                                "discord_webhook": "https://discord.com/api/webhooks/1/x"})
            r = self.check()
        self.assertEqual(r["new"], 1)
        self.assertEqual([l["source_id"] for l in discord.call_args[0][2]], ["e"])
        self.assertEqual(self.statuses(), {"a": "pruned", "b": "new", "c": "pruned", "d": "pruned", "e": "new"})
        # A pricier one arriving doesn't make the cut and doesn't alert.
        self.items.append(item("f", 400))
        with mock.patch.object(poller, "notify_discord") as discord:
            self.assertEqual(self.check()["new"], 0)
        discord.assert_not_called()
        self.assertEqual(self.statuses()["f"], "pruned")

    def test_starred_stay_and_dont_count(self):
        self.items = [item("a", 300), item("b", 200), item("c", 250)]
        self.check()
        db.execute("UPDATE listings SET status = 'starred' WHERE source_id = 'a'")
        db.prune_cheapest(db.get_watch(self.wid))
        self.assertEqual(self.statuses(), {"a": "starred", "b": "new", "c": "new"})

    def test_saving_prunes_and_clearing_restores(self):
        db.update_watch(self.wid, {"keep_cheapest": None})
        self.items = [item("a", 300), item("b", 200), item("c", 250)]
        self.check()
        db.update_watch(self.wid, {"keep_cheapest": 1})
        self.assertEqual(db.prune_cheapest(db.get_watch(self.wid)), 2)
        self.assertEqual(self.statuses(), {"a": "pruned", "b": "new", "c": "pruned"})
        # Dismissing the cheapest frees its place for the next one.
        db.execute("UPDATE listings SET status = 'dismissed' WHERE source_id = 'b'")
        db.prune_cheapest(db.get_watch(self.wid))
        self.assertEqual(self.statuses(), {"a": "pruned", "b": "dismissed", "c": "seen"})
        db.update_watch(self.wid, {"keep_cheapest": None})
        self.assertEqual(db.prune_cheapest(db.get_watch(self.wid)), 0)
        self.assertEqual(self.statuses(), {"a": "seen", "b": "dismissed", "c": "seen"})

    def test_pruned_are_not_found_again(self):
        self.items = [item("a", 300), item("b", 200), item("c", 250)]
        self.check()
        self.assertEqual(self.check()["new"], 0)
        self.assertEqual(len(db.query("SELECT id FROM listings")), 3)


if __name__ == "__main__":
    unittest.main()
