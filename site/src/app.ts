// Deal Hunter's server. @cougarcave/server gives it sign-in, sessions, the security policy, phone pairing, the AI
// settings and /healthz; this file names the app and adds its API (api.ts).
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createApp, type Config, type Db } from '@cougarcave/server';
import { sql } from 'drizzle-orm';
import { api, type ApiOptions, pinNeedsCode } from './api.js';
import { type Clock, type Net, realClock, realNet, Sources } from './logic/sources.js';
import { Poller } from './poller.js';
import { Store } from './store.js';
import { SCHEMA } from './db/schema.js';

declare module 'fastify' {
  interface FastifyInstance { poller: Poller }
}

export const NAME = 'Deal Hunter';
export const SLUG = 'deals';

export interface BuildOptions extends Partial<Omit<ApiOptions, 'poller' | 'net' | 'aiSettingsFile'>> {
  logger?: false;
  /** The shops' network and the clock the checks wait on; tests pass fakes. */
  net?: Net;
  clock?: Clock;
}

/** Lets cave-db's cave_spend role (the owner console) read deals.cave_ai_month and nothing else of Deal Hunter's. */
export async function grantSpend(db: Db): Promise<void> {
  await db.execute(sql.raw(`DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cave_spend') THEN
    GRANT USAGE ON SCHEMA "deals" TO cave_spend;
    GRANT SELECT ON "deals"."cave_ai_month" TO cave_spend;
  END IF;
END $$`));
}

/** Live Deal Hunter keeps every row in cave-db (Library 132). Without its address it would quietly start a private
 * PGlite in DATA_DIR, so it refuses to start instead. Only local work (the dev login, 127.0.0.1 only) and tests run
 * on PGlite. */
export function requireCaveDb(config: Config): void {
  if (!config.DATABASE_URL && !config.DEV_LOGIN) {
    throw new Error('Deal Hunter needs cave-db: set DATABASE_URL (from its db.env, written by the owner console Add app). ' +
                    'Only local work with DEV_LOGIN=1 runs without it.');
  }
}

export async function build(config: Config, options: BuildOptions = {}) {
  const { logger, now = () => Date.now() / 1000, apiPort = null, downloadsDir = join(config.DATA_DIR, 'downloads'), resolve, nhtsa, cpsc, net = realNet } = options;
  const clock = options.clock ?? (options.now ? { now, sleep: realClock.sleep } : realClock);
  const holder: { poller: Poller | null } = { poller: null };
  // The built page files: vite build writes them to dist/public. The same URL works from src/ (dev) and dist/.
  const app = await createApp({
    name: NAME, slug: SLUG, config, publicDir: new URL('../dist/public/', import.meta.url), now,
    // The framework's side data (sessions, paired phones, rate limits, the audit log) goes in Deal Hunter's cave-db
    // schema with the rest of its data, not in a cave.db file (Library 132). An existing cave.db is copied in once.
    sideData: 'database',
    // Finds show the shops' own photos (and Best Buy's logo, as its API terms ask), from wherever each shop keeps them.
    policy: { extend: { 'img-src': ['*'] } },
    routes: (scope) => api(scope, { now, apiPort, downloadsDir, ...(resolve ? { resolve } : {}), ...(nhtsa ? { nhtsa } : {}), ...(cpsc ? { cpsc } : {}), net, aiSettingsFile: config.AI_SETTINGS_FILE, sleep: (s) => clock.sleep(s), poller: () => holder.poller! }),
    ...(logger === false ? { logger } : {}),
  });
  app.addHook('onRequest', pinNeedsCode);
  holder.poller = new Poller(app.cave.db, new Store(app.cave.db, now), new Sources(net, clock).all, net, clock, (line) => app.log.error(line));
  app.decorate('poller', holder.poller);
  app.addHook('onClose', async () => holder.poller?.stop());
  // Bring the app's tables up to date (drizzle/, written by npm run db:generate). Running it again changes nothing.
  await app.cave.database.migrate(fileURLToPath(new URL('../drizzle/', import.meta.url)), SCHEMA);
  // The owner console reads the AI totals (deals.cave_ai_month, drizzle/0002) through cave-db's cave_spend role. Granted
  // on every start, as the framework grants its own view, so a role made after the view still gets it. Without the role
  // (local development) nothing is granted.
  await grantSpend(app.cave.db);
  return app;
}
