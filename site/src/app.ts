// Deal Hunter's server. @cougarcave/server gives it sign-in, sessions, the security policy, phone pairing, the AI
// settings and /healthz; this file names the app and adds its API (api.ts).
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createApp, type Config } from '@cougarcave/server';
import { api, type ApiOptions, pinNeedsCode } from './api.js';
import { SCHEMA } from './db/schema.js';

export const NAME = 'Deal Hunter';
export const SLUG = 'deals';

export interface BuildOptions extends Partial<ApiOptions> {
  logger?: false;
}

export async function build(config: Config, options: BuildOptions = {}) {
  const { logger, now = () => Date.now() / 1000, apiPort = null, downloadsDir = join(config.DATA_DIR, 'downloads'), resolve } = options;
  // The built page files: vite build writes them to dist/public. The same URL works from src/ (dev) and dist/.
  const app = await createApp({
    name: NAME, slug: SLUG, config, publicDir: new URL('../dist/public/', import.meta.url), now,
    routes: (scope) => api(scope, { now, apiPort, downloadsDir, ...(resolve ? { resolve } : {}) }),
    ...(logger === false ? { logger } : {}),
  });
  app.addHook('onRequest', pinNeedsCode);
  // Bring the app's tables up to date (drizzle/, written by npm run db:generate). Running it again changes nothing.
  await app.cave.database.migrate(fileURLToPath(new URL('../drizzle/', import.meta.url)), SCHEMA);
  return app;
}
