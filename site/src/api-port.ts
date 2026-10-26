// The API port (API_PORT, 8781 on the server): a second door for apps on the network, as the Python app had. Only
// /api, only a paired phone's credential (Authorization: Bearer, or X-API-Key as before), never a browser's cookie,
// and never the settings, the old key or the Discord test. Requests go through the same app, its rules and its routes.
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import type { FastifyInstance } from 'fastify';

const BLOCKED = ['/api/settings', '/api/api-key', '/api/test-discord'];

function refuse(res: ServerResponse, status: number, error: string, message: string): void {
  const body = JSON.stringify({ error, message });
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
  res.end(body);
}

/** Hands one request on the API port to the app, or refuses it. */
export function apiPortHandler(app: FastifyInstance) {
  return (req: IncomingMessage, res: ServerResponse): void => {
    const path = (req.url ?? '/').split('?')[0]!;
    if (!path.startsWith('/api/')) return refuse(res, 404, 'not_found', 'API only on this port; see /api.html on the web address');
    if (BLOCKED.some((p) => path.startsWith(p))) return refuse(res, 403, 'forbidden', 'not available through the API port');
    delete req.headers.cookie;   // a browser's session never counts here
    const key = req.headers['x-api-key'];
    if (!req.headers.authorization && typeof key === 'string' && key.trim()) req.headers.authorization = `Bearer ${key.trim()}`;
    app.routing(req, res);
  };
}

/** Starts the API port; close the returned server when the app stops. */
export function listenApiPort(app: FastifyInstance, port: number, host: string): Promise<Server> {
  const server = createServer(apiPortHandler(app));
  return new Promise((done, failed) => {
    server.once('error', failed);
    server.listen(port, host, () => done(server));
  });
}
