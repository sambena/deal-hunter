"""The AI spend gate. Providers are faked: these tests never call a paid API."""

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


def settings(**kw):
    return {**db.DEFAULT_SETTINGS, "ai_provider": "claude", "claude_model": "claude-haiku-4-5", "anthropic_api_key": "k",
            "ai_monthly_limit": 1.0, **kw}


class FakeClaude:
    """Stands in for the Anthropic call and reports fixed token usage."""
    def __init__(self, tokens_in=500, tokens_out=200, text='{"verdict": "good", "note": "fine"}', problem=None):
        self.result = (text, tokens_in, tokens_out, problem)
        self.calls = 0

    def __call__(self, *a):
        self.calls += 1
        return self.result


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def run_with(self, fake, s):
        with mock.patch.dict(ai.CALLERS, {"claude": fake}):
            return ai.run(s, "judge", PROMPT, SCHEMA)

    def spend(self, amount, at=None):
        db.execute("INSERT INTO ai_usage (at, provider, model, action, input_tokens, output_tokens, cost) "
                   "VALUES (?, 'claude', 'claude-haiku-4-5', 'judge', 0, 0, ?)", (at or time.time(), amount))

    def test_records_the_real_cost(self):
        fake = FakeClaude(tokens_in=500, tokens_out=200)
        self.assertEqual(self.run_with(fake, settings())["verdict"], "good")
        row = db.query("SELECT * FROM ai_usage")[0]
        self.assertAlmostEqual(row["cost"], (500 * 1.0 + 200 * 5.0) / 1e6)
        self.assertEqual(row["model"], "claude-haiku-4-5")

    def test_refuses_when_worst_case_passes_the_limit(self):
        worst = ai.estimate(settings(), "judge", PROMPT, SCHEMA)["max"]
        self.spend(1.0 - worst / 2)  # less than one worst case left
        fake = FakeClaude()
        with self.assertRaises(ai.AIError) as cm:
            self.run_with(fake, settings())
        self.assertIn("Monthly AI limit", str(cm.exception))
        self.assertEqual(fake.calls, 0)  # never reached the provider

    def test_zero_limit_blocks_paid_but_not_ollama(self):
        with self.assertRaises(ai.AIError):
            self.run_with(FakeClaude(), settings(ai_monthly_limit=0))
        fake_ollama = FakeClaude()
        with mock.patch.dict(ai.CALLERS, {"ollama": fake_ollama}):
            ai.run(settings(ai_provider="ollama", ollama_model="qwen3:8b", ai_monthly_limit=0),
                   "judge", PROMPT, SCHEMA)
        self.assertEqual(fake_ollama.calls, 1)
        self.assertEqual(db.query("SELECT cost FROM ai_usage")[0]["cost"], 0)

    def test_last_month_does_not_count(self):
        self.spend(5.0, at=ai.month_start() - 3600)
        self.assertEqual(ai.budget(settings())["spent"], 0)
        self.run_with(FakeClaude(), settings())

    def test_missing_key_is_refused_before_any_call(self):
        fake = FakeClaude()
        with mock.patch.dict(ai.os.environ, {}, clear=True), self.assertRaises(ai.AIError) as cm:
            self.run_with(fake, settings(anthropic_api_key=""))
        self.assertIn("API key", str(cm.exception))
        self.assertEqual(fake.calls, 0)

    def test_unknown_model_is_refused(self):
        with self.assertRaises(ai.AIError) as cm:
            ai.estimate(settings(claude_model="claude-something-new"), "judge", PROMPT, SCHEMA)
        self.assertIn("no known price", str(cm.exception))

    def test_cut_off_answer_is_still_recorded(self):
        with self.assertRaises(ai.AIError):
            self.run_with(FakeClaude(text='{"verd', problem="Claude's answer was cut off; try again"), settings())
        self.assertEqual(len(db.query("SELECT * FROM ai_usage")), 1)

    def test_estimate_scales_with_model_price(self):
        cheap = ai.estimate(settings(), "judge", PROMPT, SCHEMA)
        best = ai.estimate(settings(claude_model="claude-opus-5-5"), "judge", PROMPT, SCHEMA)
        self.assertAlmostEqual(best["typical"] / cheap["typical"], 4.0, places=1)
        self.assertLess(cheap["typical"], cheap["max"])
        self.assertTrue(cheap["allowed"])

    def test_max_out_is_sent_to_the_provider(self):
        seen = {}

        def fake(s, info, prompt, schema, max_out):
            seen["max_out"] = max_out
            return '{"verdict": "ok", "note": "x"}', 1, 1, None
        self.run_with(fake, settings())
        self.assertEqual(seen["max_out"], ai.ACTIONS["judge"]["max_out"])


if __name__ == "__main__":
    unittest.main()
