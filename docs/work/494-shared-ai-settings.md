# #494 Deal Hunter: take its AI key and limit from the shared AI settings file

Tier: **yellow** (an API secret). Priority: medium. Repo: sambena/deal-hunter (**public**: no internal hosts, addresses, paths or ids in code, docs or PR text).
Prerequisite: cougar-cave-ui **#492** (the reader) merged. Design: Relay Library doc #35, "AI keys - one place".

## Outcome
Deal Hunter keeps all its own AI logic: the AI buttons, `ai.py`, its estimates, per-person limits and ledger. Only the admin's AI comes from the shared settings file that the owner console writes for the app `deals`:
- the key for the chosen provider;
- on or off;
- the provider and model;
- the monthly limit.

Everything run on the admin's AI ("shared") uses that key and stays within that limit.

## Today
`ai.py` `settings_for()` copies the admin's settings (`AI_KEYS`: `anthropic_api_key`, `claude_model`, …) from the database. Claude can also fall back to the `ANTHROPIC_API_KEY` environment variable.

## Do
1. **Read the file:** vendor `cougar_web.ai.central` from cougar-cave-ui (the module only, with its version noted), or add a small reader that implements the same contract. Read the path from `COUGAR_AI_SETTINGS`. Do not hard-code a host path.
2. **When `deals` has an entry:**
   - the admin's AI uses the file's provider, model and key, and the file's `monthly_usd` replaces the admin's monthly limit;
   - the admin's key fields in Settings > AI show "Managed in the owner console" and are read-only.
3. **When it doesn't:** with no file or no entry (the fail-closed reader returns nothing), the admin's AI is **off**. Do not fall back to keys in the database or the environment, so there is one source.
4. **People with their own key ("own"):** unchanged in this item. Whether to keep that option is Sam's question; ask in the PR.

## Acceptance (fake keys, temp file, no real calls)
- **Valid:** with a file setting `deals` to anthropic, a key and $1, the admin's AI is on, uses that key, and its monthly limit is $1. A friend on "shared" uses the same key and limit.
- **Off:** no file, `"ai": false` or an empty key turns the admin's AI off, and no provider call is made. A stray `anthropic_api_key` in the database is **not** used (test it).
- **Isolation:** `settings_for()` never contains another provider's key.
- `pytest` passes, the change doesn't add any internal address to the public repo, and a broken case (re-enable the database fallback once) fails the "not used" test.
