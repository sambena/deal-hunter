# #932 Remove the old Python app

## Outcome
Deal Hunter is only the Cougar Cave app in `site/`, with its data in cave-db. Nothing from the old Python app is
left: no SQLite database code, no own sign-in, no one-time copy tool. Sam's decision (2026-11-03): no app keeps its own
database, and there was no old Deal Hunter data to keep (none was found on the server or on Sam's PC).

## What changed
- Deleted the Python app at the top of the repository (`server.py`, `db.py`, its sign-in, sources, poller, AI,
  `static/`, `tests/`, `start.bat` and the rest).
- Deleted the one-time copy tool (`site/src/legacy/`, `site/src/legacy-cli.ts`) and its test.
- The tests that compared the new app with the Python app now read the Python app's answers from fixtures recorded
  once before it was removed (`site/test/fixtures/api-*.json`, `poller-python.json`), and load their starting data
  straight into the tables (`site/test/seed.ts`). The live-Python re-checks and the recorder scripts are gone.
- READMEs and the schema comment describe the app as it is now.

## Evidence
- `npm run typecheck`: clean. `npm test`: 78 passed (6 files). `npm run build`: builds.
  (Before: 93, the difference is the copy tool's test and the two "recording is still current" checks.)
- Break check: dropping one seeded watch from `api-core.json` fails 16 API tests; restored.

## Not changed
The live app (`deals-web`) already runs this `site/` app on cave-db, so nothing live changes. No deploy is needed.
