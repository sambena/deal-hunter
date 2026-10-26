// Deal Hunter's server. @cougarcave/server gives it sign-in, sessions, the security policy, phone pairing, the AI
// settings and /healthz; this file only names the app and adds its routes (routes.ts).
import { fileURLToPath } from 'node:url';
import { createApp, type Config } from '@cougarcave/server';
import { SCHEMA } from './db/schema.js';
import { routes } from './routes.js';

export const NAME = 'Deal Hunter';
export const SLUG = 'deals';

export async function build(config: Config, options: { logger?: false } = {}) {
  // The built page files: vite build writes them to dist/public. The same URL works from src/ (dev) and dist/.
  const app = await createApp({ name: NAME, slug: SLUG, config, publicDir: new URL('../dist/public/', import.meta.url), routes, ...options });
  // Bring the app's tables up to date (drizzle/, written by npm run db:generate). Running it again changes nothing.
  await app.cave.database.migrate(fileURLToPath(new URL('../drizzle/', import.meta.url)), SCHEMA);
  return app;
}
