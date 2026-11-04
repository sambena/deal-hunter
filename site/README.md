# Deal Hunter

Used gear, cars and clothes, watched for you.

Deal Hunter as a Cougar Cave app, made with `create-cougar-app`. Sign-in, the bar, phone pairing, the security policy
and the AI settings come from `@cougarcave/server` and `@cougarcave/ui`; this folder is only Deal Hunter's own pages,
API and tables.

Its data is in its own schema in cave-db. What it does: watches and finds (`src/api.ts`, also on the API port in
`src/api-port.ts` for paired phones), the rules that decide which listings match and how good a deal is
(`src/logic/`), the sources (`src/logic/sources.ts`: eBay, eBay local pickup, KSL, KSL Cars, Craigslist, OfferUp,
Poshmark, Reddit, r/buildapcsales, Slickdeals, Best Buy), the scheduled checks with Discord alerts (`src/poller.ts`),
the Home Assistant import, and the AI buttons (`src/ai.ts`), with the admin's AI from the owner console.

It replaced an older Python app, which is gone. Its behaviour was recorded once as test fixtures, so the tests still
check the same answers.

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
  the app applies it when it starts.
- **Tests:** `npm test` (Vitest). Add one beside every route and component. They run on an in-memory database.
  The `*-python.json` and `api-*.json` fixtures are the old Python app's answers to the same made-up inputs,
  recorded once: `logic`, `sources`, `api` (every answer, then each sign-in rule beside a working control) and
  `poller` (every find, alert, request and the poller's state after each step) must give the same. Nothing is fetched.
- **Checks before a PR:** `npm run typecheck && npm test && npm run build`.

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
