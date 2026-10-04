"""OpenAI and Gemini request/response handling, with canned replies (no network, no cost)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import db  # noqa: E402

SCHEMA = ai.VERDICT_SCHEMA
ANSWER = '{"verdict": "good", "note": "fair price"}'


def info(provider, model):
    s = {**db.DEFAULT_SETTINGS, "ai_provider": provider, f"{provider}_model": model}
    return ai.model_info(s)


class Capture:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, url, headers, body, name):
        self.calls.append((url, headers, body))
        return self.reply


class OpenAITest(unittest.TestCase):
    settings = {**db.DEFAULT_SETTINGS, "openai_api_key": "sk-test"}

    def test_request_and_usage(self):
        cap = Capture({"status": "completed", "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": ANSWER}]}],
            "usage": {"input_tokens": 300, "output_tokens": 120, "output_tokens_details": {"reasoning_tokens": 80}}})
        with mock.patch.object(ai, "_post_json", cap):
            text, tin, tout, problem = ai._openai(self.settings, info("openai", "gpt-6-luna"), "p", SCHEMA, 3000)
        url, headers, body = cap.calls[0]
        self.assertTrue(url.endswith("/v1/responses"))
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(body["max_output_tokens"], 3000)
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual((text, tin, tout, problem), (ANSWER, 300, 120, None))

    def test_cut_off_and_refusal(self):
        cap = Capture({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                       "output": [], "usage": {"input_tokens": 10, "output_tokens": 3000}})
        with mock.patch.object(ai, "_post_json", cap):
            *_, tout, problem = ai._openai(self.settings, info("openai", "gpt-6-luna"), "p", SCHEMA, 3000)
        self.assertEqual(tout, 3000)
        self.assertIn("cut off", problem)
        cap = Capture({"status": "completed", "output": [
            {"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}], "usage": {}})
        with mock.patch.object(ai, "_post_json", cap):
            *_, problem = ai._openai(self.settings, info("openai", "gpt-6-luna"), "p", SCHEMA, 3000)
        self.assertIn("declined", problem)

    def test_needs_key(self):
        with self.assertRaises(ai.AIError):
            ai._openai(db.DEFAULT_SETTINGS, info("openai", "gpt-6-luna"), "p", SCHEMA, 3000)


class GeminiTest(unittest.TestCase):
    settings = {**db.DEFAULT_SETTINGS, "gemini_api_key": "g-test"}

    def test_request_and_thinking_counts_as_output(self):
        cap = Capture({"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "thinking...", "thought": True}, {"text": ANSWER}]}}],
            "usageMetadata": {"promptTokenCount": 400, "candidatesTokenCount": 50, "thoughtsTokenCount": 150}})
        with mock.patch.object(ai, "_post_json", cap):
            text, tin, tout, problem = ai._gemini(self.settings, info("gemini", "gemini-3.5-flash-lite"),
                                                  "p", SCHEMA, 3000)
        url, headers, body = cap.calls[0]
        self.assertIn("gemini-3.5-flash-lite:generateContent", url)
        self.assertEqual(headers["x-goog-api-key"], "g-test")
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 3000)
        self.assertEqual(body["generationConfig"]["responseJsonSchema"], SCHEMA)
        self.assertEqual((text, tin, tout, problem), (ANSWER, 400, 200, None))

    def test_blocked_and_cut_off(self):
        cap = Capture({"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 9}})
        with mock.patch.object(ai, "_post_json", cap):
            *_, problem = ai._gemini(self.settings, info("gemini", "gemini-3.8-flash"), "p", SCHEMA, 3000)
        self.assertIn("SAFETY", problem)
        cap = Capture({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "{"}]}}],
                       "usageMetadata": {}})
        with mock.patch.object(ai, "_post_json", cap):
            *_, problem = ai._gemini(self.settings, info("gemini", "gemini-3.8-flash"), "p", SCHEMA, 3000)
        self.assertIn("cut off", problem)


class PriceTest(unittest.TestCase):
    def test_promo_price_ends(self):
        m = next(m for m in ai.MODELS if m["id"] == "gemini-3.8-flash")
        with mock.patch.object(ai.time, "strftime", return_value="2026-12-31"):
            self.assertEqual(ai._price_today(m), {"in": 0.75, "out": 3.75})
        with mock.patch.object(ai.time, "strftime", return_value="2027-01-01"):
            self.assertEqual(ai._price_today(m), {"in": 1.50, "out": 7.50})

    def test_every_paid_provider_has_models(self):
        for p in ai.PROVIDERS:
            if p != "ollama":
                self.assertTrue(any(m["provider"] == p for m in ai.MODELS), p)


if __name__ == "__main__":
    unittest.main()
