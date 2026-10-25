"""#517: the admin's AI honours the owner console's tier and effort (the rule agreed in cougar-cave-ui #499).
Temp settings files with fake keys; each vendor's request is captured before it would leave the machine, so these
tests never call a paid API."""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import db  # noqa: E402
import shared_ai_file  # noqa: E402
from test_shared_ai import KEY, PROMPT, SCHEMA, Base  # noqa: E402

ANSWER = '{"verdict": "good", "note": "fine"}'
PROVIDER = {"claude": "anthropic", "openai": "openai", "gemini": "gemini"}


def admin_model(test, provider="claude", **kw):
    """The model the admin's AI resolves to with this settings-file entry, or None when it's off."""
    shared_ai_file.use(test, provider=PROVIDER[provider], key=KEY, **kw)
    with db.as_user(test.admin):
        s = ai.settings_for()
    return s.get(ai.PROVIDERS[provider]["model"]) if s["ai_provider"] == provider else None


class TierTest(Base):
    def test_each_tier_resolves_to_the_expected_model(self):
        expected = {
            "claude": {None: "claude-haiku-5-5", "cheapest": "claude-haiku-5-5", "balanced": "claude-sonnet-5-5",
                       "best": "claude-opus-5-5", "turbo": "claude-haiku-5-5"},
            # MODELS has no Gemini model newer than these in each family.
            "gemini": {None: "gemini-3.5-flash-lite", "balanced": "gemini-3.8-flash", "best": "gemini-3.1-pro-preview"},
            # MODELS has no mini or plain GPT model, so balanced and best fall back to cheapest.
            "openai": {None: "gpt-6-luna", "balanced": "gpt-6-luna", "best": "gpt-6-luna"},
        }
        for provider, tiers in expected.items():
            for tier, model in tiers.items():
                with self.subTest(provider=provider, tier=tier):
                    self.assertEqual(admin_model(self, provider, tier=tier), model)

    def test_newest_in_the_family_wins(self):
        # Opus 5.5 over the older (and pricier) Opus 5.
        self.assertEqual(ai.resolve("claude", "best"), "claude-opus-5-5")

    def test_gemini_cheapest_is_the_lowest_priced_not_the_newest(self):
        newer = {"provider": "gemini", "id": "gemini-9.9-flash-lite", "family": "flash-lite", "gen": (9, 9),
                 "label": "newer, pricier flash-lite", "in": 1.00, "out": 5.00, "effort": True}
        with mock.patch.object(ai, "MODELS", ai.MODELS + [newer]):
            self.assertEqual(ai.resolve("gemini", "cheapest"), "gemini-3.5-flash-lite")
            self.assertEqual(admin_model(self, "gemini"), "gemini-3.5-flash-lite")

    def test_newest_wins_a_price_tie(self):
        twin = {**next(m for m in ai.MODELS if m["id"] == "claude-haiku-5-5"), "id": "claude-haiku-4-9", "gen": (4, 9)}
        twin.pop("long")
        self.assertEqual(ai.resolve("claude", "cheapest", ai.MODELS + [twin]), "claude-haiku-5-5")

    def test_an_unpriced_model_is_never_chosen(self):
        unpriced = {"provider": "claude", "id": "claude-opus-9-9", "family": "opus", "gen": (9, 9),
                    "label": "no price yet", "in": None, "out": None, "effort": True}
        models = ai.MODELS + [unpriced]
        self.assertEqual(ai.resolve("claude", "best", models), "claude-opus-5-5")   # the newest *priced* opus
        self.assertEqual(ai.resolve("claude", "cheapest", models), "claude-haiku-5-5")
        self.assertIsNone(ai.resolve("claude", "best", [unpriced]))                  # nothing priced: off

    def test_nothing_priced_turns_the_admins_ai_off(self):
        with mock.patch.object(ai, "MODELS", [m for m in ai.MODELS if m["provider"] != "openai"]):
            self.assertIsNone(admin_model(self, "openai"))
            self.assertIn("no price for (none)", ai.managed()["off"])
        self.assertEqual(admin_model(self, "openai"), "gpt-6-luna")                 # the valid case beside it

    def test_a_pinned_model_wins_over_the_tier(self):
        self.assertEqual(admin_model(self, tier="best", model="claude-haiku-4-5"), "claude-haiku-4-5")
        self.assertIsNone(admin_model(self, tier="cheapest", model="claude-made-up-9"))   # pinned and unpriced: off


class Captured:
    """What each vendor was sent: Claude through a stand-in SDK module, OpenAI and Gemini through _post_json."""

    def __init__(self, test):
        self.sent = []
        sdk = types.ModuleType("anthropic")
        sdk.AuthenticationError = sdk.RateLimitError = sdk.APIStatusError = sdk.APIConnectionError = type(
            "Never", (Exception,), {})
        sdk.Anthropic = lambda **kw: types.SimpleNamespace(messages=types.SimpleNamespace(create=self.claude))
        for patcher in (mock.patch.dict(sys.modules, {"anthropic": sdk}),
                        mock.patch.object(ai, "_post_json", self.post)):
            patcher.start()
            test.addCleanup(patcher.stop)

    def claude(self, **kw):
        self.sent.append(kw)
        usage = types.SimpleNamespace(input_tokens=500, output_tokens=200, cache_creation_input_tokens=0,
                                      cache_read_input_tokens=0)
        return types.SimpleNamespace(usage=usage, stop_reason="end_turn",
                                     content=[types.SimpleNamespace(type="text", text=ANSWER)])

    def post(self, url, headers, body, name):
        self.sent.append(body)
        if name == "OpenAI":
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": ANSWER}]}],
                    "usage": {"input_tokens": 500, "output_tokens": 200}, "status": "completed"}
        return {"candidates": [{"content": {"parts": [{"text": ANSWER}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 200}}


def effort_sent(provider, body):
    if provider == "claude":
        return body["output_config"].get("effort")
    if provider == "openai":
        return (body.get("reasoning") or {}).get("effort")
    return (body["generationConfig"].get("thinkingConfig") or {}).get("thinkingLevel")


class EffortTest(Base):
    def ask_admin(self, provider="claude", **kw):
        captured = Captured(self)
        shared_ai_file.use(self, provider=PROVIDER[provider], key=KEY, **kw)
        with db.as_user(self.admin):
            ai.run(ai.settings_for(), "judge", PROMPT, SCHEMA)
        body = captured.sent[-1]
        return body.get("model"), effort_sent(provider, body)

    def test_the_consoles_effort_reaches_the_request(self):
        for provider in ("claude", "openai", "gemini"):
            for effort in ("low", "medium", "high"):
                with self.subTest(provider=provider, effort=effort):
                    self.assertEqual(self.ask_admin(provider, effort=effort)[1], effort)

    def test_no_or_unknown_effort_is_the_old_low(self):
        self.assertEqual(self.ask_admin()[1], "low")
        self.assertEqual(self.ask_admin(effort="max")[1], "low")                  # ignored, never an error

    def test_a_model_without_effort_gets_none(self):
        # Claude Haiku 4.5 rejects an effort setting, so none is sent even when the console sets one.
        self.assertEqual(self.ask_admin(model="claude-haiku-4-5", effort="high"), ("claude-haiku-4-5", None))

    def test_tier_and_effort_together_reach_the_request(self):
        self.assertEqual(self.ask_admin(tier="balanced", effort="medium"), ("claude-sonnet-5-5", "medium"))

    def test_ai_that_isnt_the_admins_keeps_low(self):
        # The console's effort is for the admin's AI only; a person's own AI (no managed scope) still sends "low".
        info = ai.model_info({"ai_provider": "openai", "openai_model": "gpt-6-luna"})
        self.assertEqual(ai.effort_for({"ai_provider": "openai", "_ai": {"source": "own"}}, info), "low")

if __name__ == "__main__":
    unittest.main()
