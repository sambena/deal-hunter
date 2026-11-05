# #944 Deal Hunter on server 1.2.0, with its side data in cave-db

## Brief
Outcome: Deal Hunter keeps the framework's side data (sign-in sessions, paired phones, used sign-out tokens, rate
limits, the audit log) in its own cave-db schema, not in `DATA_DIR/cave.db` (Library 132), as Kitchen (#935) and
RaidRoster (#944, raidroster 337ec81) do.

Acceptance:
- `@cougarcave/server` 1.2.0 (built from cougar-cave-ui main 82fe178; byte-for-byte the copy Kitchen vendors).
- `createApp({ sideData: 'database' })`; the existing `cave.db` is copied in once on the first start and left in place.
- People's "last active" still comes from the framework's sessions and phones, now read from cave-db.
- Live Deal Hunter refuses to start without `DATABASE_URL`. Only local work with `DEV_LOGIN=1` and tests run without it.

Out of scope: Deal Hunter's own AI (it keeps its own AI code and spend view, #927); phone app releases (`phoneApp`).

Risk: sign-in and paired phones. Rollback is the previous image; it reads `cave.db` as it was before the move, so
sign-ins and pairings made after the move are lost. Upgrading again after a rollback copies nothing unless the
`copied_from_file` row is deleted from `deals.cave_meta` first.

## Evidence
- 1.0.9 to 1.2.0: Deal Hunter used `app.cave.store` in one place, the People "last active" query
  (`src/api.ts`); it now awaits `app.cave.side` with the `cave_` table names. No other changed API is used.
- New tests: "last active" follows a session made in the side store; side data lands in the database with no
  `cave.db` made; a `cave.db` seeded the old way is copied in once (the old cookie still signs in, a sign-out after a
  restart sticks); the cave-db check refuses a live start without `DATABASE_URL`.
- `npm run typecheck` clean; `npm test`: 91 passed.
- Live: `deals-web` already has `DATABASE_URL`, and its `cave.db` is there to copy.
