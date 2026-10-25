# #517 Deal Hunter: honour the console's AI tier and effort

Tier: **yellow**. Priority: medium. Repo: sambena/deal-hunter (**public**: no internal hosts, addresses, paths or ids). Prerequisite: #494 merged (it is).

## Outcome
When the owner console sets the `deals` entry's tier or effort, the admin's managed AI uses that model and effort. Deal Hunter keeps its own AI code; `shared_ai.py` and `ai.py` apply the rule below against Deal Hunter's own priced `MODELS` list.

## The rule (the same in every app; agreed with the owner console, cougar-cave-ui #499)
- The settings entry may have `tier`: `cheapest` (the default), `balanced` or `best`, and `effort`: `low`, `medium` or `high`. A pinned `model` wins over `tier`.
- **cheapest:** the lowest combined input + output price among the models this app has a price for, for that provider (newest wins a tie).
- **balanced / best:** the newest priced model of the family:
  - Claude: sonnet / opus;
  - Gemini: flash / pro;
  - OpenAI: mini / the plain model.
- **Fallback:** an unknown tier or no match uses cheapest. Never pick an unpriced model, so every call can be capped. With nothing priced, AI is off.
- **Effort:** pass it only to models that support it (for example Claude Haiku 5.5); otherwise ignore it. Never error on it.
- **No paid calls:** fake keys, temp files, stubbed or fake providers only.

## Acceptance
With a temp settings file and the fake or mocked provider:
- each tier resolves to the expected model from `MODELS`;
- cheapest for Gemini is the lowest-priced flash-lite, not the newest;
- an unpriced model is never chosen;
- effort reaches the request only for models marked as supporting it;
- a pinned model wins over the tier.

`pytest` passes. A broken case (let the tier pick an unpriced model) makes a test fail.
