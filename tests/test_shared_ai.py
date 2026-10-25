"""The admin's AI from the owner console's shared settings file: temp files and fake keys only; providers are
faked, so these tests never call a paid API."""

import json
import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import db  # noqa: E402
import shared_ai  # noqa: E402
import shared_ai_file  # noqa: E402

PROMPT, SCHEMA = ai.judge_prompt({"title": "Ryzen 7 5700X", "total": 120, "source": "ebay"},
                                 {"name": "5700X", "query": "5700x"}, None)
ANSWER = ('{"verdict": "good", "note": "fine"}', 500, 200, None)
KEY, STRAY_DB, STRAY_ENV = "FAKEKEY-console-0001", "FAKEKEY-stray-db-0002", "FAKEKEY-stray-env-0003"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        import server
        self.s = server
        self.admin = db.admin_id()
        db.execute("UPDATE users SET name = 'Sam' WHERE id = ?", (self.admin,))
        self.friend = db.execute("INSERT INTO users (email, name, role, created_at, ai_allowance) "
                                 "VALUES ('f@x.y', 'Friend', 'member', ?, 2)", (time.time(),))
        self.calls = []

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def fake(self, provider):
        def call(settings, info, prompt, schema, max_out):
            self.calls.append((provider, settings.get(ai.PROVIDERS[provider]["key"]), info["id"]))
            return ANSWER
        return call

    def ask(self, uid):
        fakes = {p: self.fake(p) for p in ("claude", "openai", "gemini")}
        with db.as_user(uid), mock.patch.dict(ai.CALLERS, fakes):
            return ai.run(ai.settings_for(), "judge", PROMPT, SCHEMA)


class ReaderTest(Base):
    def broken(self):
        c = shared_ai_file.contract
        unknown = c()
        unknown["apps"]["deals"]["provider"] = "mistral"
        truthy = c()
        truthy["apps"]["deals"]["ai"] = "yes"
        # In an order where neighbours fail for different reasons: an unchanged reason is logged only once.
        return {"bad json": '{"version": 1, "apps": ', "version 2": {**c(key=KEY), "version": 2},
                "no deals entry": c(key=KEY, app="kitchen"), "ai false": c(key=KEY, ai=False),
                "unknown provider": unknown, "ai not exactly true": truthy,
                "bad monthly_usd": c(key=KEY, monthly="lots"), "empty key": c(key=""), "a list": "[1]"}

    def test_fails_closed(self):
        path = shared_ai_file.use(self, key=KEY)
        self.assertEqual(shared_ai.load()["key"], KEY)  # the valid case first, same file and app
        for case, data in self.broken().items():
            with self.subTest(case), self.assertLogs("deal-hunter.ai", logging.WARNING) as logs:
                shared_ai_file.write(path, data)
                self.assertIsNone(shared_ai.load())
                self.assertIn("the admin's AI is off", logs.output[0])
                self.assertNotIn(KEY, "\n".join(logs.output))
        os.remove(path)
        self.assertIsNone(shared_ai.load())  # no file

    def test_rereads_only_when_the_file_changes(self):
        path = shared_ai_file.use(self, key=KEY)
        shared_ai.load()
        before = shared_ai.reads
        for _ in range(5):
            shared_ai.load()
        self.assertEqual(shared_ai.reads, before)
        shared_ai_file.write(path, shared_ai_file.contract(key="FAKEKEY-rotated-0004"))
        self.assertEqual(shared_ai.load()["key"], "FAKEKEY-rotated-0004")
        self.assertEqual(shared_ai.reads, before + 1)


class AdminAiTest(Base):
    def test_valid_admin_and_friend_use_the_files_key_and_limit(self):
        shared_ai_file.use(self, provider="anthropic", key=KEY, monthly=1.0)
        with db.as_user(self.admin):
            s = ai.settings_for()
            b = ai.budget(s)
        self.assertEqual((s["ai_provider"], s["claude_model"], s["anthropic_api_key"], s["ai_monthly_limit"]),
                         ("claude", "claude-haiku-5-5", KEY, 1.0))  # the cheapest Claude by default
        self.assertEqual(b["limit"], 1.0)
        self.ask(self.admin)
        self.ask(self.friend)  # a friend on "shared"
        self.assertEqual(self.calls, [("claude", KEY, "claude-haiku-5-5")] * 2)
        with db.as_user(self.friend):
            self.assertEqual(ai.settings_for()["_ai"]["pool_limit"], 1.0)
        self.assertEqual({r["paid_by"] for r in db.query("SELECT paid_by FROM ai_usage")}, {self.admin})

    def test_haiku_5_5_price_effort_and_long_prompts(self):
        info = ai.model_info({"ai_provider": "claude", "claude_model": "claude-haiku-5-5"})
        self.assertEqual((info["in"], info["out"], info["effort"]), (0.10, 0.50, True))  # effort is sent as "low"
        self.assertAlmostEqual(ai.cost(info, 50_000, 1_000_000), (50_000 * 0.10 + 1_000_000 * 0.50) / 1e6)  # short
        self.assertAlmostEqual(ai.cost(info, 200_000, 1_000), (200_000 * 0.50 + 1_000 * 2.50) / 1e6)  # over 100K

    def test_model_from_the_file(self):
        shared_ai_file.use(self, provider="anthropic", key=KEY, model="claude-sonnet-5-5")
        self.ask(self.admin)
        self.assertEqual(self.calls[0][2], "claude-sonnet-5-5")

    def test_unpriced_model_is_off(self):
        shared_ai_file.use(self, provider="anthropic", key=KEY, model="claude-made-up-9")
        with self.assertRaises(ai.AIError) as cm:
            self.ask(self.admin)
        self.assertIn("no price for", str(cm.exception))
        self.assertEqual(self.calls, [])

    def test_off_means_no_call_and_no_stray_key(self):
        db.update_settings({"ai_provider": "claude", "claude_model": "claude-haiku-4-5", "anthropic_api_key": STRAY_DB,
                            "ai_monthly_limit": 50}, user_id=self.admin)
        cases = {"no file": None, "ai false": {"ai": False}, "empty key": {"key": ""}}
        for case, over in cases.items():
            with self.subTest(case), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": STRAY_ENV}):
                if over is None:
                    os.environ["COUGAR_AI_SETTINGS"] = str(Path(self.tmp.name) / "missing.json")
                else:
                    shared_ai_file.use(self, provider="anthropic", **{"key": KEY, **over})
                for uid in (self.admin, self.friend):
                    with db.as_user(uid):
                        s = ai.settings_for()
                        self.assertEqual((s["ai_provider"], s["anthropic_api_key"]), ("off", ""))
                        self.assertEqual(ai.choices(s), [])
                    with self.assertRaises(ai.AIError):
                        self.ask(uid)
                self.assertEqual(self.calls, [])
                self.assertEqual(db.query("SELECT COUNT(*) AS n FROM ai_usage")[0]["n"], 0)
        # The valid case beside them: the file on, the same stray keys around, and only the file's key is used.
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": STRAY_ENV}):
            shared_ai_file.use(self, provider="anthropic", key=KEY)
            self.ask(self.admin)
        self.assertEqual(self.calls, [("claude", KEY, "claude-haiku-5-5")])

    def test_settings_for_holds_only_its_own_providers_key(self):
        shared_ai_file.use(self, provider="gemini", key=KEY,
                           keys={"anthropic": "FAKEKEY-anthropic-other", "openai": "FAKEKEY-openai-other"})
        db.update_settings({"openai_api_key": STRAY_DB}, user_id=self.admin)
        for uid in (self.admin, self.friend):
            with db.as_user(uid):
                s = ai.settings_for()
            dumped = json.dumps(s)
            self.assertEqual((s["ai_provider"], s["gemini_api_key"], s["anthropic_api_key"], s["openai_api_key"]),
                             ("gemini", KEY, "", ""))
            for other in ("FAKEKEY-anthropic-other", "FAKEKEY-openai-other", STRAY_DB):
                self.assertNotIn(other, dumped)

    def test_a_rotated_key_is_used_without_a_restart(self):
        path = shared_ai_file.use(self, provider="anthropic", key=KEY)
        self.ask(self.admin)
        shared_ai_file.write(path, shared_ai_file.contract(key="FAKEKEY-rotated-0004", monthly=2.0))
        self.ask(self.admin)
        self.assertEqual([c[1] for c in self.calls], [KEY, "FAKEKEY-rotated-0004"])


class SettingsPageTest(Base):
    def test_admin_sees_the_consoles_ai_without_the_key_and_cannot_change_it(self):
        shared_ai_file.use(self, provider="anthropic", key=KEY, monthly=1.0)
        with db.as_user(self.admin):
            page = self.s.get_state({}, {})["settings"]
            self.assertNotIn(KEY, json.dumps(page))
            self.assertEqual((page["ai_provider"], page["anthropic_api_key"], page["openai_api_key"],
                              page["ai_monthly_limit"], page["claude_model"]), ("claude", True, False, 1.0, "claude-haiku-5-5"))
            self.assertEqual(page["ai_managed"], {"on": True, "provider": "claude", "model": "claude-haiku-5-5",
                                                  "monthly_usd": 1.0, "reason": ""})
            self.s.put_settings({"anthropic_api_key": STRAY_DB, "ai_monthly_limit": 99, "ai_provider": "openai",
                                 "ai_confirm": False}, {})
            stored = db.get_settings(self.admin)
            self.assertEqual((stored["anthropic_api_key"], stored["ai_monthly_limit"], stored["ai_provider"]),
                             ("", 5.0, "off"))   # the defaults: nothing the form sent about the admin's AI was kept
            self.assertIs(stored["ai_confirm"], False)  # other settings still save
            s = ai.settings_for()
            self.assertEqual((s["anthropic_api_key"], s["ai_monthly_limit"]), (KEY, 1.0))

    def test_admin_page_says_off(self):
        os.environ.pop("COUGAR_AI_SETTINGS", None)
        with mock.patch.dict(os.environ, {"COUGAR_AI_SETTINGS": str(Path(self.tmp.name) / "none.json")}), \
                db.as_user(self.admin):
            page = self.s.get_state({}, {})["settings"]
        self.assertEqual((page["ai_provider"], page["ai_managed"]["on"]), ("off", False))
        self.assertIn("owner console", page["ai_managed"]["reason"])

    def test_members_settings_are_unchanged(self):
        shared_ai_file.use(self, provider="anthropic", key=KEY)
        with db.as_user(self.friend):
            page = self.s.get_state({}, {})["settings"]
            self.assertNotIn("ai_managed", page)
            self.s.put_settings({"ai_source": "own", "ai_provider": "claude", "claude_model": "claude-haiku-4-5",
                                 "anthropic_api_key": "FAKEKEY-friend-own"}, {})
            self.assertEqual(ai.settings_for()["anthropic_api_key"], "FAKEKEY-friend-own")  # "own" unchanged


if __name__ == "__main__":
    unittest.main()
