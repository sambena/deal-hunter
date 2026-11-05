# #927 Deal Hunter uses the model and prices the owner console publishes, and the console can count its spend

Epic: cougar-auth #921, "One place manages all AI use". Same rules as the shared package (cougar-cave-ui #923), in
Deal Hunter's own AI code, because Deal Hunter is still on server 1.0.9 (the move to 1.1.1 is cougar-cave-ui #944).

## Brief

**Outcome:** on the admin's AI (the owner console's key), Deal Hunter calls only the model the console published for
`deals`, and prices and caps every call with the console's prices. The console can read Deal Hunter's monthly AI
totals the same way it reads Kitchen's.

**Acceptance**
- **Reads `use`:** model, whether it takes effort, effort, prices and the long-prompt row, checked as the shared
  package checks them.
- **Fail closed:** no `use`, or a malformed one (a negative or fractional price, a missing price, no effort flag, a
  bad model name, a bad long-prompt row), and the admin's AI is off, with a one-line reason in the log.
- **Only the published model:** choices list only that model; asking for another is refused before anything is sent.
  A published model Deal Hunter's own list doesn't know still works, named by its id.
- **Published prices:** estimates, the monthly limit check and the recorded cost use the published price; the long
  price applies only above its size.
- **Effort:** sent only when `takes_effort` is true; the console's published effort comes first, then the entry's,
  then `low`.
- **Counting:** a view, `deals.cave_ai_month` (app, month, billed_micros, calls), over the calls run on the admin's
  AI, granted to cave-db's `cave_spend` role on every start when that role exists. It never shows a person, a model
  or a row. People's own keys are not counted.

**Out of scope:** people's own keys keep Deal Hunter's own price list; the move to server 1.1.1 (#944); deploying.

**Known limit:** "the admin's AI" is the calls paid by whoever is admin now. Calls aren't tagged with the key that
paid, so if the admin changed, the totals would follow the new admin. Deal Hunter has one admin.

**Risk:** once deployed, the admin's AI is off until the owner console has published `use` for `deals` (save its AI
settings there once). The console reads the new view on its next look.

## Evidence

- `npm run typecheck`: clean. `npx vitest run`: 6 files, 87 tests passed (78 before).
- The replay of every AI button against the Python recording still gives the same answers and the same provider
  requests, with `use` added to its settings file.
- **New tests:** the reader maps `use` and refuses 13 malformed shapes beside a working one; prices come from `use`
  (twice the list price shows twice the estimate); the long row applies above its size only; only the published model
  is offered and another is refused beside a working control; an unknown model works by its id; no `use` is off beside
  the same file with one; effort follows `takes_effort` and the console's choice; the view counts only the admin's AI
  by UTC month.
- **Break checks:** pricing from Deal Hunter's list fails 2 tests; accepting no `use` fails 2; always sending effort
  fails 1. Removing the model check inside `modelInfo` fails none, because the request is already refused when the
  choice is checked; the check stays as a second guard.
- **Review (fresh agent):** people on the admin's shared AI got `low` effort instead of the console's; fixed, and
  one recorded request in the AI replay now carries the console's `medium`. Added tests for the effort fallback
  (published, then the entry's, then `low`) and for the recorded cost at the published price; breaking either fallback
  now fails a test. The grant now runs on every start, with a test that a role made after the view gets the view and
  no table.
- **Not done:** no real AI calls, no deploy.
