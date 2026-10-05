"""AI review of finds: which AIs can be picked, the prompt, and applying the verdicts (AI mocked)."""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import db  # noqa: E402


class AIReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        import server
        self.server = server
        db.update_settings({"ai_provider": "gemini", "gemini_api_key": "k", "gemini_model": "gemini-3.5-flash-lite",
                            "gemini_free_tier": True})
        self.wid = db.create_watch({"name": "Pickup", "query": "pickup", "kind": "vehicle", "max_price": 5000})
        self.ids = []
        for i, (title, status) in enumerate((("2005 Ford F-150", "new"), ("2003 Ram 1500 salvage", "starred"),
                                              ("Tacoma floor mats", "seen"), ("2001 Silverado", "dismissed"))):
            self.ids.append(db.execute("""INSERT INTO listings (watch_id, source, source_id, title, total, first_seen,
                status, year, miles) VALUES (?, 'ksl_cars', ?, ?, 4000, ?, ?, 2005, 150000)""",
                (self.wid, str(i), title, time.time(), status)))

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_choices(self):
        cs = ai.choices(ai.settings_for())
        self.assertEqual({c["provider"] for c in cs}, {"gemini", "ollama"})  # a key (Ollama: an address)
        self.assertTrue(any(c["current"] and c["model"] == "gemini-3.5-flash-lite" for c in cs))
        self.assertTrue(all(c["free"] for c in cs))
        with self.assertRaises(ai.AIError):
            ai.with_choice(ai.settings_for(), "claude", "claude-opus-5-5")  # no Claude key
        s = ai.with_choice(ai.settings_for(), "gemini", "gemini-3.8-flash")
        self.assertEqual((s["ai_provider"], s["gemini_model"]), ("gemini", "gemini-3.8-flash"))

    def test_shared_ai_offers_only_the_admins_pick(self):
        friend = db.execute("INSERT INTO users (email, name, role, created_at) VALUES ('f@x.y', 'F', 'member', ?)",
                            (time.time(),))
        with db.as_user(friend):
            cs = ai.choices(ai.settings_for())
        self.assertEqual([(c["provider"], c["model"]) for c in cs], [("gemini", "gemini-3.5-flash-lite")])

    def test_prompt(self):
        prompt, schema, extra = self.server._ai_prompt("review", {"listing_ids": self.ids, "instructions": "4x4 only"})
        self.assertIn("4x4 only", prompt)
        self.assertIn(f"id {self.ids[0]}: 2005 Ford F-150 | $4000", prompt)
        self.assertNotIn("Silverado", prompt)  # dismissed finds aren't reviewed
        self.assertIn(f'Watch id {self.wid} "Pickup" (query "pickup", max $5000)', prompt)
        self.assertEqual(len(extra["rows"]), 3)

    def test_review_applies_verdicts(self):
        answer = {"reviews": [
            {"id": self.ids[0], "verdict": "good", "reason": "Fair price."},
            {"id": self.ids[1], "verdict": "skip", "reason": "Salvage title."},
            {"id": self.ids[2], "verdict": "skip", "reason": "Floor mats, not a truck."},
            {"id": 9999, "verdict": "skip", "reason": "made up"}]}
        with mock.patch.object(ai, "run", lambda *a, **k: answer):
            r = self.server.ai_review({"listing_ids": self.ids, "hide": True}, {})
        self.assertEqual({k: r[k] for k in ("reviewed", "good", "ok", "skip", "hidden", "unanswered")},
                         {"reviewed": 3, "good": 1, "ok": 0, "skip": 2, "hidden": 1, "unanswered": 0})
        self.assertEqual(r["hidden_ids"], [self.ids[2]])  # the starred one stays
        rows = {x["id"]: x for x in db.query("SELECT id, status, ai_note FROM listings")}
        self.assertEqual((rows[self.ids[2]]["status"], rows[self.ids[2]]["ai_note"]), ("dismissed", "SKIP: Floor mats, not a truck."))
        self.assertEqual(rows[self.ids[1]]["status"], "starred")
        self.assertEqual(rows[self.ids[0]]["ai_note"], "GOOD: Fair price.")

    def test_watch_changes_only_tighten_your_own_watches(self):
        answer = {"reviews": [], "watch_changes": [
            {"watch_id": self.wid, "add_excludes": ["floor mats", "Floor Mats", "salvage"], "max_price": 4500,
             "min_price": None, "year_min": 2000, "max_miles": 200000, "reason": "Mats and salvage titles keep coming."},
            {"watch_id": self.wid, "add_excludes": [], "max_price": 9000, "min_price": None, "year_min": None,
             "max_miles": None, "reason": "looser: dropped"},
            {"watch_id": 999, "add_excludes": ["x"], "max_price": None, "min_price": None, "year_min": None,
             "max_miles": None, "reason": "not theirs"}]}
        with mock.patch.object(ai, "run", lambda *a, **k: answer):
            r = self.server.ai_review({"listing_ids": self.ids}, {})
        self.assertEqual(r["watch_changes"], [{"watch_id": self.wid, "watch_name": "Pickup", "changes": {
            "exclude": ["floor mats", "salvage"], "max_price": 4500.0, "year_min": 2000, "max_miles": 200000},
            "reason": "Mats and salvage titles keep coming."}])
        out = self.server.update_watch(r["watch_changes"][0]["changes"], {}, str(self.wid))  # what Apply sends
        w = db.get_watch(self.wid)
        self.assertEqual((w["exclude"], w["max_price"], w["year_min"]), (["floor mats", "salvage"], 4500.0, 2000))
        self.assertGreaterEqual(out["removed"], 1)  # the floor mats find goes

    def test_without_hide_only_notes(self):
        with mock.patch.object(ai, "run", lambda *a, **k: {"reviews": [{"id": self.ids[2], "verdict": "skip", "reason": "x"}]}):
            r = self.server.ai_review({"listing_ids": self.ids}, {})
        self.assertEqual((r["hidden"], r["unanswered"]), (0, 2))
        self.assertEqual(db.query("SELECT status FROM listings WHERE id = ?", (self.ids[2],))[0]["status"], "seen")

    def test_someone_elses_finds_are_not_reviewed(self):
        friend = db.execute("INSERT INTO users (email, name, role, created_at) VALUES ('f@x.y', 'F', 'member', ?)",
                            (time.time(),))
        with db.as_user(friend):
            with self.assertRaises(self.server.HTTPError):
                self.server._ai_prompt("review", {"listing_ids": self.ids})


if __name__ == "__main__":
    unittest.main()
