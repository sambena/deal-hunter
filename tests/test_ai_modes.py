"""Whose AI a request runs on: a member's own key, or the admin's AI within the limits the admin set.
Providers are faked: these tests never call a paid API."""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import db  # noqa: E402

PROMPT, SCHEMA = ai.judge_prompt({"title": "Ryzen 7 5700X", "total": 120, "source": "ebay"},
                                 {"name": "5700X", "query": "5700x"}, None)
ANSWER = ('{"verdict": "good", "note": "fine"}', 500, 200, None)
PAID = {"ai_provider": "claude", "claude_model": "claude-haiku-4-5", "anthropic_api_key": "sam-key"}
FREE = {"ai_provider": "gemini", "gemini_model": "gemini-3.5-flash-lite", "gemini_api_key": "sam-key",
        "gemini_free_tier": True}


class AiModesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        import server
        self.s = server
        self.admin = db.admin_id()
        db.execute("UPDATE users SET name = 'Sam' WHERE id = ?", (self.admin,))
        self.sis = db.execute("INSERT INTO users (email, name, role, created_at) VALUES ('sis@x.y', 'Sis', 'member', ?)",
                              (time.time(),))

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def admin_ai(self, **kw):
        db.update_settings({**kw}, user_id=self.admin)

    def ask(self, uid):
        with db.as_user(uid), mock.patch.dict(ai.CALLERS, {"claude": lambda *a: ANSWER, "gemini": lambda *a: ANSWER}):
            return ai.run(ai.settings_for(), "judge", PROMPT, SCHEMA)

    def estimate(self, uid):
        with db.as_user(uid):
            return ai.estimate(ai.settings_for(), "judge", PROMPT, SCHEMA)

    def test_members_use_the_admins_free_ai_without_seeing_the_key(self):
        self.admin_ai(**FREE)
        with db.as_user(self.sis):
            s = ai.settings_for()
            self.assertEqual((s["ai_provider"], s["_ai"]["source"], s["_ai"]["owner"]), ("gemini", "shared", "Sam"))
            self.assertIs(self.s.get_state({}, {})["settings"]["gemini_api_key"], False)  # their own (empty) key
        self.ask(self.sis)
        row = db.query("SELECT user_id, paid_by, cost FROM ai_usage")[0]
        self.assertEqual((row["user_id"], row["paid_by"], row["cost"]), (self.sis, self.admin, 0))

    def test_daily_cap(self):
        self.admin_ai(**FREE)
        db.execute("UPDATE users SET ai_daily_cap = 2 WHERE id = ?", (self.sis,))
        self.ask(self.sis)
        self.ask(self.sis)
        est = self.estimate(self.sis)
        self.assertFalse(est["allowed"])
        self.assertIn("today's 2 AI requests on Sam's AI", est["reason"])
        self.assertTrue(self.estimate(self.admin)["allowed"])  # the admin has no daily cap

    def test_paid_models_need_an_allowance(self):
        self.admin_ai(**PAID, ai_monthly_limit=5.0)
        est = self.estimate(self.sis)  # allowance starts at $0
        self.assertFalse(est["allowed"])
        self.assertIn("Sam gave you $0.00 a month", est["reason"])
        db.execute("UPDATE users SET ai_allowance = 1 WHERE id = ?", (self.sis,))
        self.ask(self.sis)
        with db.as_user(self.sis):
            b = ai.budget(ai.settings_for())
        self.assertEqual((b["source"], b["limit"], b["today"]), ("shared", 1.0, 1))
        self.assertGreater(b["spent"], 0)
        with db.as_user(self.admin):  # the admin's limit counts what Sis ran on Sam's key
            self.assertEqual(ai.budget(ai.settings_for())["spent"], b["spent"])

    def test_the_admins_limit_covers_everyone_on_the_key(self):
        self.admin_ai(**PAID, ai_monthly_limit=1.0)
        db.execute("UPDATE users SET ai_allowance = 5 WHERE id = ?", (self.sis,))
        db.execute("INSERT INTO ai_usage (at, provider, model, action, input_tokens, output_tokens, cost, user_id, paid_by)"
                   " VALUES (?, 'claude', 'claude-haiku-4-5', 'judge', 0, 0, 0.999, ?, ?)",
                   (time.time(), self.admin, self.admin))
        est = self.estimate(self.sis)
        self.assertFalse(est["allowed"])
        self.assertIn("Sam's AI budget for this month is used up", est["reason"])

    def test_not_shared_or_admin_ai_off(self):
        self.admin_ai(**FREE)
        db.execute("UPDATE users SET ai_shared = 0 WHERE id = ?", (self.sis,))
        with self.assertRaises(ai.AIError) as cm:
            self.estimate(self.sis)
        self.assertIn("hasn't shared their AI", str(cm.exception))
        db.execute("UPDATE users SET ai_shared = 1 WHERE id = ?", (self.sis,))
        self.admin_ai(ai_provider="off")
        with self.assertRaises(ai.AIError) as cm:
            self.estimate(self.sis)
        self.assertIn("Sam's AI is off", str(cm.exception))

    def test_own_key_is_separate(self):
        self.admin_ai(**PAID, ai_monthly_limit=5.0)
        with db.as_user(self.sis):
            self.s.put_settings({"ai_source": "own", **PAID, "anthropic_api_key": "sis-key", "ai_monthly_limit": 2.0}, {})
            s = ai.settings_for()
            self.assertEqual((s["_ai"]["source"], s["anthropic_api_key"], s["ai_monthly_limit"]), ("own", "sis-key", 2.0))
        self.ask(self.sis)
        self.assertEqual(db.query("SELECT paid_by FROM ai_usage")[0]["paid_by"], self.sis)
        with db.as_user(self.admin):
            self.assertEqual(ai.budget(ai.settings_for())["spent"], 0)  # not on Sam's key

    def test_admin_sets_limits_and_sees_use_of_their_ai(self):
        self.admin_ai(**FREE)
        self.s.admin_update_user({"ai_daily_cap": 5, "ai_allowance": "2.50", "ai_shared": True}, {}, str(self.sis))
        self.ask(self.sis)
        sis = {u["name"]: u for u in self.s.admin_users({}, {})["users"]}["Sis"]
        self.assertEqual((sis["ai_daily_cap"], sis["ai_allowance"], sis["ai_shared"], sis["ai_today"], sis["ai_source"]),
                         (5, 2.5, 1, 1, "shared"))
        with db.as_user(self.sis):
            with self.assertRaises(self.s.HTTPError):
                self.s.admin_update_user({"ai_allowance": 100}, {}, str(self.sis))  # not their own allowance


if __name__ == "__main__":
    unittest.main()
