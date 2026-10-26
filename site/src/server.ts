// Starts Deal Hunter: `npm run dev` (watching src/) or `npm start` (the build). Settings come from the environment
// or .env (see .env.example). Deal Hunter's own: API_PORT (and API_HOST) for the API port, DOWNLOADS_DIR for the
// Android app's folder.
import type { Server } from 'node:http';
import { loadConfig } from '@cougarcave/server';
import { listenApiPort } from './api-port.js';
import { build } from './app.js';

const config = loadConfig();
const apiPort = process.env.API_PORT ? Number(process.env.API_PORT) : null;
if (apiPort !== null && !(Number.isInteger(apiPort) && apiPort > 0 && apiPort < 65536)) throw new Error('API_PORT must be a port number');
const app = await build(config, { apiPort, ...(process.env.DOWNLOADS_DIR ? { downloadsDir: process.env.DOWNLOADS_DIR } : {}) });
await app.listen({ host: config.HOST, port: config.PORT });
let apiServer: Server | null = null;
if (apiPort !== null) {
  apiServer = await listenApiPort(app, apiPort, process.env.API_HOST || config.HOST);
  app.log.info(`Deal Hunter API (paired phones only) on port ${apiPort}`);
}

// docker stop and Ctrl+C: finish what's in flight, close the database, then exit.
for (const signal of ['SIGTERM', 'SIGINT'] as const) {
  process.once(signal, () => {
    apiServer?.close();
    app.close().then(() => process.exit(0), () => process.exit(1));
  });
}
