// Starts Deal Hunter: `npm run dev` (watching src/) or `npm start` (the build). Settings come from the environment
// or .env (see .env.example).
import { loadConfig } from '@cougarcave/server';
import { build } from './app.js';

const config = loadConfig();
const app = await build(config);
await app.listen({ host: config.HOST, port: config.PORT });

// docker stop and Ctrl+C: finish what's in flight, close the database, then exit.
for (const signal of ['SIGTERM', 'SIGINT'] as const) {
  process.once(signal, () => {
    app.close().then(() => process.exit(0), () => process.exit(1));
  });
}
