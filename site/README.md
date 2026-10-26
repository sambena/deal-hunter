# Deal Hunter

Used gear, cars and clothes, watched for you.

Deal Hunter as a Cougar Cave app, made with `create-cougar-app`. Sign-in, the bar, phone pairing, the security policy
and the AI settings come from `@cougarcave/server` and `@cougarcave/ui`; this folder is only Deal Hunter's own pages,
API and tables.

It is being moved here from the Python app at the top of this repository in steps: the plan is
`../docs/work/457-deal-hunter-on-the-framework-and-cave-db.md`. So far: the app and its tables (`src/db/schema.ts`),
which keep the Python app's rows column for column. Until the cutover, the Python app is what runs.

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
