# Deal Hunter

Used gear, cars and clothes, watched for you.

Deal Hunter as a Cougar Cave app, made with `create-cougar-app`. Sign-in, the bar, phone pairing, the security policy
and the AI settings come from `@cougarcave/server` and `@cougarcave/ui`; this folder is only Deal Hunter's own pages,
API and tables.

It is being moved here from the Python app at the top of this repository in steps: the plan is
`../docs/work/457-deal-hunter-on-the-framework-and-cave-db.md`. So far: the app and its tables (`src/db/schema.ts`),
which keep the Python app's rows column for column, the one-time copy of its data, and the rules that decide
which listings match and how good a deal is (`src/logic/`: matching, hardware upgrades, pasted specs, VIN and recalls).
The API (`src/api.ts`): watches, finds, settings, People, My Stuff (devices, VINs, recalls from NHTSA and CPSC, upgrade
rules, pasted specs, the deal radar) and the Android download, with the same answers as the Python app, on the web port
and on the API port (`src/api-port.ts`, paired phones only). The sources (`src/logic/sources.ts`: eBay, eBay local
pickup, KSL, KSL Cars, Craigslist, OfferUp, Poshmark, Reddit, r/buildapcsales, Slickdeals, Best Buy), with the Python
app's rate gaps, caches and retries. The checks (`src/poller.ts`): every watch against its sources on the schedule
(every poll_minutes, or at set times in a time zone), shared requests, deal %, keep-the-cheapest, Discord alerts and
the Best Buy purge; "Check now" and the Discord test. The Home Assistant import (`src/logic/homeassistant.ts`). The AI (`src/ai.ts`,
prompts in `src/logic/ai-prompts.ts`): Claude, OpenAI, Gemini and Ollama, priced and capped before each request, with
the admin's AI from the owner console's settings file (`AI_SETTINGS_FILE`). Until the cutover, the Python
app is what runs.

## Run it

```bash
cp .env.example .env
npm install
npm run dev
```

`npm run dev` builds the pages, starts the server, and rebuilds or restarts each when you change `client/` or `src/`.

Open http://127.0.0.1:8780 and sign in with the dev login (local only).

## Work on it

- **API:** `src/routes.ts`. Mark each route `signedIn` (and `browserOnly` if a phone must not use it); check bodies with zod.
- **Pages:** `client/`. Components are Lit from `/cave/cave-ui.js` (the kit's copy, one Lit per page).
- **Data:** `src/db/schema.ts` (Drizzle), in Deal Hunter's own schema. Store people as their id and join to `people`
  (from `@cougarcave/server`) for names; never copy them. After changing the schema: `npm run db:generate`, delete
  the new file's `CREATE SCHEMA` line (in cave-db the schema exists; a test checks), and commit the file in `drizzle/`;
  the app applies it when it starts. Locally (no `DATABASE_URL`) the data is in `data/pg`.
- **Tests:** `npm test` (Vitest). Add one beside every route and component. They run on an in-memory database.
  The copy's tests build an old database with the real Python code, so they need `python` on the path.
  `test/logic.test.ts` compares the rules with the Python app's own answers, recorded in `test/fixtures/logic-python.json`
  by `test/fixtures/make_logic.py` (run it from the repository root when the cases change); with Python on the path,
  a test also checks the recording is current.
  `test/api.test.ts` replays requests through the Python app's own routes (`test/fixtures/make_api.py`, needs `python`)
  and compares every answer, then checks each sign-in rule beside a working control.
  `test/sources.test.ts` runs every source on made-up pages and compares its listings, errors, requests and waits with
  the Python app's (`test/fixtures/make_sources.py` records them, with a fake network and clock; nothing is fetched).
  `test/poller.test.ts` replays the Python app's checks (`test/fixtures/make_poller.py`, with made-up answers in
  `poller-spec.json`) and compares every find, alert, request and the poller's state after each step.
- **Checks before a PR:** `npm run typecheck && npm test && npm run build`.

## The one-time copy

At the cutover, the Python app's database (`data/deal-hunter.db`, stopped and backed up first) comes over once, inside
the container:

```bash
node dist/legacy-cli.js copy  --db <old deal-hunter.db> [--leave-out 5,6]
node dist/legacy-cli.js check --db <old deal-hunter.db> [--leave-out 5,6] --self-check
```

- The old file is only read. Everything is written in one transaction, so a refusal or a failure writes nothing.
- People come over by their active central link for this site's issuer (`OIDC_ISSUER`, or `--issuer`). Any account
  without one stops the copy, listed by id: link it in the old app first, or name it in `--leave-out` (none of its
  rows are copied).
- Tables that already hold rows are refused; `--replace` empties them first.
- The check compares every table's row count, a checksum over every column, and the next id. `--self-check` plants
  a changed value, a missing row and an extra row in a transaction that is rolled back, and fails unless the checker
  reports each one. Exit codes: 0 matching, 1 a difference, 2 refused or failed.
- Sessions, phone pairings and the old app's own sign-in are not copied (the framework replaces them); their counts
  are printed. The old file, kept with the backup, still holds them and the central audit.

## Ship it

1. In the owner console, **Add app** registers Deal Hunter with Cougar Cave sign-in and gives its settings.
2. Build the image: `docker build -t deals .`
3. Run it on the docker network `cave` with `/data` on a volume, the settings as environment variables, the client
   secret as a file (`OIDC_CLIENT_SECRET_FILE`), `DATABASE_URL` from the `.db.env` file Add app wrote, and the AI
   settings folder mounted read-only at `/run/cougar-ai`.
4. Point a tunnel name (`deals.cougarcave.dev`) at the container's port 8780.

## Update the shared packages

The packages are in `vendor/`. From the cougar-cave-ui repository: `npm run new-app -- --update <path to this app>`,
then `npm install`, test, and commit.
