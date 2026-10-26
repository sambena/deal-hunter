// Deal Hunter's server. @cougarcave/server gives it sign-in, sessions, the security policy, phone pairing, the AI
// settings and /healthz; this file names the app and adds its API (api.ts).
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createApp, type Config } from '@cougarcave/server';
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

export interface BuildOptions extends Partial<Omit<ApiOptions, 'poller'>> {
  logger?: false;
  /** The shops' network and the clock the checks wait on; tests pass fakes. */
  net?: Net;
  clock?: Clock;
}

export async function build(config: Config, options: BuildOptions = {}) {
  const { logger, now = () => Date.now() / 1000, apiPort = null, downloadsDir = join(config.DATA_DIR, 'downloads'), resolve, nhtsa, cpsc, net = realNet } = options;
  const clock = options.clock ?? (options.now ? { now, sleep: realClock.sleep } : realClock);
  const holder: { poller: Poller | null } = { poller: null };
  // The built page files: vite build writes them to dist/public. The same URL works from src/ (dev) and dist/.
  const app = await createApp({
    name: NAME, slug: SLUG, config, publicDir: new URL('../dist/public/', import.meta.url), now,
    routes: (scope) => api(scope, { now, apiPort, downloadsDir, ...(resolve ? { resolve } : {}), ...(nhtsa ? { nhtsa } : {}), ...(cpsc ? { cpsc } : {}), poller: () => holder.poller! }),
    ...(logger === false ? { logger } : {}),
  });
  app.addHook('onRequest', pinNeedsCode);
  holder.poller = new Poller(app.cave.db, new Store(app.cave.db, now), new Sources(net, clock).all, net, clock, (line) => app.log.error(line));
  app.decorate('poller', holder.poller);
  app.addHook('onClose', async () => holder.poller?.stop());
  // Bring the app's tables up to date (drizzle/, written by npm run db:generate). Running it again changes nothing.
  await app.cave.database.migrate(fileURLToPath(new URL('../drizzle/', import.meta.url)), SCHEMA);
  return app;
}
