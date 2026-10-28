// Deal Hunter's API: the same answers as the Python app for the same requests (test/fixtures/make_api.py replays them
// through server.py's own routes on a database the real db.py made, which is copied in here), then the sign-in rules,
// each refusal beside a working control, on the web port and the API port. Made-up data only; needs python.
import { spawnSync } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { request as httpRequest, type Server } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { DEV_MEMBER, DEV_OWNER, loadConfig } from '@cougarcave/server';
import { sql } from 'drizzle-orm';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { listenApiPort } from '../src/api-port.js';
import { build } from '../src/app.js';
import { loadShared } from '../src/ai.js';
import { copy, openOld, readOld } from '../src/legacy/copy.js';
import { clearCpscCache, type CpscGet } from '../src/logic/product-recalls.js';
import { type Net, NetError } from '../src/logic/sources.js';
import { clearRecallCache, type Get, LookupFailed } from '../src/logic/vehicles.js';

const ISSUER = 'https://auth.example.test/application/o/deals/';
const REPO = fileURLToPath(new URL('../../', import.meta.url));
const SITE = fileURLToPath(new URL('../', import.meta.url));
const T0 = 1_793_000_000;
const KIM = '00000000-0000-4000-8000-0000000000c3';
const config = (aiFile = join(folder, 'none.json')) => loadConfig({ APP_ORIGIN: 'http://127.0.0.1:8780', DEV_LOGIN: '1', DATA_DIR: ':memory:', AI_SETTINGS_FILE: aiFile });

interface Req { who: 'owner' | 'member'; method: 'GET' | 'POST' | 'PUT' | 'DELETE'; url: string; body?: unknown; at: number }
interface Answer { status: number; body?: unknown; message?: string }
let folder: string;
type Recording = { requests: Req[]; answers: Answer[]; asked: unknown[]; ids: Record<string, number> };
let recorded: Recording;
let devices: Recording;
let homeAssistant: Recording;
let aiRecording: Recording;
const OUTSIDE = JSON.parse(readFileSync(join(SITE, 'test/fixtures/outside.json'), 'utf8')) as
  { nhtsa: Record<string, Record<string, unknown>>; cpsc: Record<string, unknown>; ha: Record<string, { json?: unknown; body?: string; http?: number; network?: string }> };
let asked: unknown[] = [];

/** NHTSA and CPSC as outside.json makes them up, noting each address asked, as make_api.py's stand-ins do. */
const nhtsa: Get = async (url) => {
  asked.push(url);
  const found = OUTSIDE.nhtsa[url] ?? {};
  if ('http' in found) throw new LookupFailed(`NHTSA answered HTTP ${found.http}`);
  return structuredClone(found);
};
const cpsc: CpscGet = async (url) => {
  asked.push(url);
  const found = OUTSIDE.cpsc[new URL(url).searchParams.get('RecallTitle')!] ?? [];
  if (!Array.isArray(found) && 'http' in (found as object)) throw new LookupFailed(`CPSC answered HTTP ${(found as { http: number }).http}`);
  return structuredClone(found);
};
let app: Awaited<ReturnType<typeof build>>;
let clock = T0;

function record(mode: 'core' | 'devices' | 'ha' | 'ai'): Recording {
  const run = spawnSync('python', [join(SITE, 'test/fixtures/make_api.py'), join(folder, `${mode}.db`), ISSUER, mode,
    join(SITE, 'test/fixtures/outside.json')],
  { cwd: REPO, env: { ...process.env, PYTHONPATH: REPO, PYTHONIOENCODING: 'utf-8', COUGAR_AI_SETTINGS: join(folder, 'none.json') },
    encoding: 'utf8', maxBuffer: 1 << 26 });
  if (run.status !== 0) throw new Error(`make_api.py failed: ${run.stderr || run.error}`);
  return JSON.parse(run.stdout) as Recording;
}

beforeAll(() => {
  folder = mkdtempSync(join(tmpdir(), 'dh-api-'));
  recorded = record('core');
  devices = record('devices');
  homeAssistant = record('ha');
  aiRecording = record('ai');
}, 120_000);   // four Python recordings: well past the default 10 s on a busy machine

type Answer2 = { error?: string; status?: number; body?: unknown; text?: string; usage?: unknown; stop_reason?: string; json?: unknown; http?: number };
const AI_ANSWERS = (JSON.parse(readFileSync(join(SITE, 'test/fixtures/outside.json'), 'utf8')) as { ai: Record<string, Answer2[]> }).ai;

/** The AI providers as outside.json makes them up: one queue each, in order. A request is noted the way make_api.py's
 * stand-ins note it (Claude's as the SDK's arguments). A busy Claude (429, no connection) answers every retry the
 * same, and the retries count once, as the Python SDK stand-in doesn't retry. */
function aiNet() {
  const queues = Object.fromEntries(Object.entries(AI_ANSWERS).map(([k, v]) => [k, [...v]]));
  let tries = 0;
  let retrying: Answer2 | null = null;
  const net: Net = async (req) => {
    const headers = Object.fromEntries(Object.entries(req.headers).map(([k, v]) => [k.toLowerCase(), v]));
    const host = new URL(req.url).host;
    if (host === 'api.anthropic.com') {
      const body = JSON.parse(req.body!) as Record<string, unknown>;
      const entry = { provider: 'claude', api_key: headers['x-api-key'], timeout: req.timeout, ...body };
      const a = queues.claude![0]!;
      // A retry asks again while the same busy answer is still being given; anything else is a new request.
      if (!(a === retrying && tries > 0)) { asked.push(entry); retrying = a; tries = 0; }
      if (a.error === 'rate' || a.error === 'connection') {
        if (++tries >= 3) queues.claude!.shift();
        if (a.error === 'connection') throw new NetError('made-up connection failure');
        return { status: 429, body: Buffer.from('{}') };
      }
      queues.claude!.shift();
      if (a.error === 'auth') return { status: 401, body: Buffer.from('{}') };
      if (a.error === 'status') return { status: a.status!, body: Buffer.from(JSON.stringify(a.body)) };
      return { status: 200, body: Buffer.from(JSON.stringify({ content: [{ type: 'text', text: a.text }], usage: a.usage, stop_reason: a.stop_reason })) };
    }
    asked.push({ url: req.url, body: JSON.parse(req.body!), headers, timeout: req.timeout });
    const provider = host.includes('openai') ? 'openai' : 'gemini';
    const a = queues[provider]!.shift()!;
    if (a.http) return { status: a.http, body: Buffer.from(typeof a.body === 'string' ? a.body : JSON.stringify(a.body ?? '')), reason: 'made up' };
    return { status: 200, body: Buffer.from(JSON.stringify(a.json)) };
  };
  return net;
}

/** Home Assistant as outside.json makes it up, by the address's port, noting each address asked. */
const homeAssistantNet: Net = async (req) => {
  asked.push(req.url);
  const found = OUTSIDE.ha[new URL(req.url).port] ?? { http: 404 };
  if (found.network) throw new NetError(found.network);
  if (found.http) return { status: found.http, body: Buffer.from(''), reason: 'made up' };
  return { status: 200, body: Buffer.from(found.json !== undefined ? JSON.stringify(found.json) : found.body ?? '') };
};
afterAll(() => rmSync(folder, { recursive: true, force: true }));
afterEach(async () => app?.close());

/** The app, with the old database copied in. */
async function start(extra: Parameters<typeof build>[1] = {}, database = 'core.db', aiFile?: string) {
  clock = T0;
  asked = [];
  clearRecallCache();
  clearCpscCache();
  app = await build(config(aiFile), { logger: false, now: () => clock, downloadsDir: join(folder, 'downloads'), nhtsa, cpsc,
    net: async () => { throw new NetError('no network in tests'); }, clock: { now: () => clock, sleep: async (s) => { clock += s; } }, ...extra });
  const src = openOld(join(folder, database));
  try {
    await copy(app.cave.db, readOld(src, ISSUER));
  } finally {
    src.close();
  }
  // Kim never signs in here; in cave-db core.people is the login service's own list, which knows everyone.
  await app.cave.db.execute(sql`INSERT INTO core.people (id, name, email, is_active) VALUES (${KIM}, 'Kim', 'kim@example.test', true)`);
}

const cookieOf = (res: { cookies: { name: string; value: string }[] }) => res.cookies.map((c) => `${c.name}=${c.value}`).join('; ');
async function devCookie(who: 'owner' | 'member') {
  return cookieOf(await app.inject({ url: `/auth/dev?who=${who}` }));
}

/** A browser session made directly in the framework's store: whoever, signed in however and whenever. */
function session(subject: string, amr: string[] | null, authTime = clock): string {
  const token = randomBytes(24).toString('base64url');
  const person = JSON.stringify({ subject, name: 'Made up', email: 'made-up@example.test', role: 'member' });
  app.cave.store.run('INSERT INTO sessions (hash, sid, subject, person, auth_time, amr, created, seen, expires) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
    createHash('sha256').update(token).digest('hex'), 'sid-' + token.slice(0, 6), subject, person, authTime, amr ? JSON.stringify(amr) : null,
    clock, clock, clock + 30 * 86400);
  return `cave_session=${token}`;
}

function call(cookie: string, method: string, url: string, body?: unknown) {
  return app.inject({ method: method as 'GET', url, headers: { cookie, ...(method !== 'GET' ? { 'x-cave-request': '1' } : {}) },
    ...(body !== undefined ? { payload: body as object } : {}) });
}

// ---- the same answers as the Python app ------------------------------------------------------------------------

/** People's has_password is retired, and last_active now comes from the framework's sign-in records. */
function comparable(r: Req, body: unknown): unknown {
  const b = structuredClone(body) as Record<string, unknown>;
  if (r.url === '/api/admin/users' && Array.isArray(b.users)) {
    for (const u of b.users as Record<string, unknown>[]) { delete u.has_password; delete u.last_active; }
  }
  return b;
}

describe('the same answers as the Python app', () => {
  it('for watches, finds, settings and People, in order', async () => {
    await start();
    expect(await replay(recorded)).toEqual([]);
  }, 60_000);

  it('for every AI button, asking each AI provider the same things', async () => {
    await start({ net: aiNet() }, 'ai.db', join(folder, 'ai.ai.json'));
    expect(await replay(aiRecording)).toEqual([]);
    expect(asked).toEqual(aiRecording.asked);
    expect(asked.length).toBeGreaterThan(20);
  }, 60_000);

  it('for the Home Assistant import, asking Home Assistant the same things', async () => {
    await start({ net: homeAssistantNet }, 'ha.db');
    expect(await replay(homeAssistant)).toEqual([]);
    expect(asked).toEqual(homeAssistant.asked);
    expect(asked.length).toBeGreaterThan(5);
  }, 60_000);

  it('for My Stuff, VINs and recalls, asking NHTSA and CPSC the same things', async () => {
    await start({}, 'devices.db');
    expect(await replay(devices)).toEqual([]);
    expect(asked).toEqual(devices.asked);
    expect(asked.length).toBeGreaterThan(5);
  }, 60_000);
});

/** Sends the recorded requests in order, each at its recorded time; the ones whose answer differs from Python's. */
async function replay(rec: Recording): Promise<unknown[]> {
  {
    const differ: unknown[] = [];
    rec.requests.forEach((r, i) => expect(r.at, `request ${i}`).toBe(T0 + i * 60));
    for (const [i, r] of rec.requests.entries()) {
      clock = r.at;
      // Signed in just now, as the Python recording's sessions were, so privileged changes count as fresh.
      const res = await call(await devCookie(r.who), r.method, r.url, r.body);
      const python = rec.answers[i]!;
      const port: Answer = res.statusCode === 200 ? { status: 200, body: res.json() } : { status: res.statusCode, message: res.json().message };
      const same = python.status === 200
        ? port.status === 200 && JSON.stringify(sorted(comparable(r, port.body))) === JSON.stringify(sorted(comparable(r, python.body)))
        : port.status === python.status && port.message === python.message;
      if (!same) differ.push({ i, request: `${r.who} ${r.method} ${r.url}`, python, port });
    }
    if (process.env.DH_API_DEBUG) writeFileSync(process.env.DH_API_DEBUG, JSON.stringify(differ, null, 1));
    return differ;
  }
}

/** Keys sorted, so two JSON values compare by content; numbers as JSON writes them. */
function sorted(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sorted);
  if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sorted((v as Record<string, unknown>)[k])]));
  return v;
}

// ---- who may do what -------------------------------------------------------------------------------------------

describe('signing in', () => {
  it('answers nothing signed out but 401, beside the same requests signed in', async () => {
    await start();
    const owner = await devCookie('owner');
    for (const [method, url] of [['GET', '/api/state'], ['GET', '/api/listings'], ['POST', '/api/watches'], ['GET', '/api/nothing-here'],
      ['GET', '/api/admin/users'], ['GET', '/api/app/android']] as const) {
      const out = await call('', method, url, method === 'POST' ? { name: 'x', query: 'x', check: false } : undefined);
      expect([url, out.statusCode, out.json().error]).toEqual([url, 401, 'signed_out']);
      const ok = await call(owner, method, url, method === 'POST' ? { name: 'x', query: 'x', check: false } : undefined);
      expect([url, ok.statusCode]).toEqual([url, url === '/api/nothing-here' ? 404 : 200]);
    }
    expect((await call('', 'GET', '/download/android')).statusCode).toBe(401);
  });

  it('refuses an admin whose sign-in lacked both factors on every route; a member signed in that way still works', async () => {
    await start();
    const pwdOnly = session(DEV_OWNER.subject, ['pwd']);
    const codeOnly = session(DEV_OWNER.subject, ['mfa']);
    const both = session(DEV_OWNER.subject, ['pwd', 'mfa']);
    const member = session(DEV_MEMBER.subject, ['pwd']);
    for (const [method, url] of [['GET', '/api/state'], ['GET', '/api/listings'], ['GET', '/api/me'], ['POST', '/api/listings/mark-seen'],
      ['GET', '/download/android']] as const) {
      for (const cookie of [pwdOnly, codeOnly]) {
        const res = await call(cookie, method, url, method === 'POST' ? {} : undefined);
        expect([url, res.statusCode, res.json().error]).toEqual([url, 403, 'needs_both_factors']);
        expect(res.json().message).toMatch(/password and your authenticator code in the same sign-in/);
      }
      if (url !== '/download/android') {
        expect([url, (await call(both, method, url, method === 'POST' ? {} : undefined)).statusCode]).toEqual([url, 200]);
        expect([url, (await call(member, method, url, method === 'POST' ? {} : undefined)).statusCode]).toEqual([url, 200]);
      }
    }
  });

  it('refuses a change to People or the shared settings unless the sign-in is under an hour old; reading is fine', async () => {
    await start();
    const lee = recorded.ids.lee!;
    const old = session(DEV_OWNER.subject, ['pwd', 'mfa'], T0 - 3601);
    const fresh = session(DEV_OWNER.subject, ['pwd', 'mfa'], T0 - 3599);
    const stale = await call(old, 'PUT', '/api/settings', { poll_minutes: 25 });
    expect([stale.statusCode, stale.json().error]).toEqual([403, 'needs_recent_signin']);
    expect(stale.json().message).toMatch(/your last Cougar Cave sign-in was 60 minutes ago/);
    expect((await call(old, 'PUT', `/api/admin/users/${lee}`, { watch_limit: 4 })).json().error).toBe('needs_recent_signin');
    expect((await call(old, 'GET', '/api/admin/users')).statusCode).toBe(200);
    expect((await call(old, 'GET', '/api/state')).statusCode).toBe(200);
    expect((await call(fresh, 'PUT', '/api/settings', { poll_minutes: 25 })).statusCode).toBe(200);
    expect((await call(fresh, 'PUT', `/api/admin/users/${lee}`, { watch_limit: 4 })).statusCode).toBe(200);
    // A member's own settings aren't privileged: an old sign-in may change them.
    const memberOld = session(DEV_MEMBER.subject, ['pwd'], T0 - 3600);
    expect((await call(memberOld, 'PUT', '/api/settings', { zip_code: '00003' })).statusCode).toBe(200);
  });

  it('keeps People to the admin, and asks a member for both factors first, as the Python app did', async () => {
    await start();
    const memberBoth = session(DEV_MEMBER.subject, ['pwd', 'mfa']);
    const memberPwd = session(DEV_MEMBER.subject, ['pwd']);
    const res = await call(memberBoth, 'GET', '/api/admin/users');
    expect([res.statusCode, res.json().message]).toEqual([403, 'only the admin can do that']);
    expect((await call(memberPwd, 'GET', '/api/admin/users')).json().error).toBe('needs_both_factors');
    expect((await call(await devCookie('owner'), 'GET', '/api/admin/users')).statusCode).toBe(200);
  });

  it('turns a disabled person away at once, and lets them back when re-enabled', async () => {
    await start();
    const owner = await devCookie('owner');
    const member = await devCookie('member');
    const lee = recorded.ids.lee!;
    expect((await call(member, 'GET', '/api/state')).statusCode).toBe(200);
    expect((await call(owner, 'PUT', `/api/admin/users/${lee}`, { disabled: true })).statusCode).toBe(200);
    const res = await call(member, 'GET', '/api/state');
    expect([res.statusCode, res.json().error]).toEqual([401, 'signed_out']);
    expect((await call(owner, 'GET', '/api/state')).statusCode).toBe(200);
    expect((await call(owner, 'PUT', `/api/admin/users/${lee}`, { disabled: false })).statusCode).toBe(200);
    expect((await call(member, 'GET', '/api/state')).statusCode).toBe(200);
  });

  it('makes a member row on a first sign-in, with a new friend\'s limits', async () => {
    await start();
    const someone = '00000000-0000-4000-8000-0000000000d4';
    const res = await call(session(someone, ['pwd']), 'GET', '/api/state');
    expect(res.statusCode).toBe(200);
    expect(res.json().watches).toEqual([]);
    const [row] = (await app.cave.db.execute(sql`SELECT * FROM deals.members WHERE subject = ${someone}`) as unknown as { rows: Record<string, unknown>[] }).rows;
    expect(row).toMatchObject({ admin: false, disabled: false, watch_limit: 10, ai_shared: true, ai_daily_cap: 20 });
  });

  it('says the old sign-in is gone, signed in or not', async () => {
    await start();
    for (const cookie of ['', await devCookie('owner')]) {
      for (const [method, url] of [['POST', '/api/auth/login'], ['POST', '/api/auth/setup'], ['GET', '/api/tokens'], ['POST', '/api/api-key']] as const) {
        const res = await call(cookie, method, url, method === 'POST' ? {} : undefined);
        expect([url, res.statusCode, res.json().error]).toEqual([url, 410, 'gone']);
      }
    }
  });
});

// ---- phones, on both ports -------------------------------------------------------------------------------------

async function pairPhone(cookie: string): Promise<string> {
  const code = await call(cookie, 'POST', '/auth/devices/code', {});
  expect(code.statusCode).toBe(201);
  const paired = await app.inject({ method: 'POST', url: '/auth/devices/pair', headers: { 'x-cave-request': '1' },
    payload: { pin: code.json().pin, name: 'Test phone' } });
  expect(paired.statusCode).toBe(200);
  return paired.json().credential as string;
}

/** One request to the API port, over real HTTP. */
function onApiPort(server: Server, method: string, path: string, headers: Record<string, string> = {}, body?: unknown) {
  const { port } = server.address() as { port: number };
  return new Promise<{ status: number; json: Record<string, unknown> }>((done, failed) => {
    const payload = body === undefined ? undefined : JSON.stringify(body);
    const r = httpRequest({ host: '127.0.0.1', port, method, path, headers: { ...headers, ...(payload ? { 'content-type': 'application/json' } : {}) } }, (res) => {
      let text = '';
      res.on('data', (c) => { text += c; });
      res.on('end', () => done({ status: res.statusCode!, json: text ? JSON.parse(text) : {} }));
    });
    r.on('error', failed);
    r.end(payload);
  });
}

describe('phones', () => {
  it('pairs only from a sign-in that used a code, as before', async () => {
    await start();
    const pwdOnly = session(DEV_MEMBER.subject, ['pwd']);
    const res = await call(pwdOnly, 'POST', '/auth/devices/code', {});
    expect([res.statusCode, res.json().error]).toEqual([403, 'needs_recent_signin']);
    expect((await call(session(DEV_MEMBER.subject, ['mfa']), 'POST', '/auth/devices/code', {})).statusCode).toBe(201);
  });

  it("an admin's phone works on ordinary routes but not privileged ones", async () => {
    await start();
    const phone = await pairPhone(await devCookie('owner'));
    const bearer = { authorization: `Bearer ${phone}` };
    expect((await app.inject({ url: '/api/state', headers: bearer })).statusCode).toBe(200);
    const people = await app.inject({ url: '/api/admin/users', headers: bearer });
    expect([people.statusCode, people.json().error]).toEqual([403, 'needs_both_factors']);
    const settings = await app.inject({ method: 'PUT', url: '/api/settings', headers: { ...bearer, 'x-cave-request': '1' }, payload: { poll_minutes: 9 } });
    expect([settings.statusCode, settings.json().error]).toEqual([403, 'needs_both_factors']);
  });

  it("a disabled person's phone gets no_access (it stays paired), beside their working phone before", async () => {
    await start();
    const phone = await pairPhone(await devCookie('member'));
    const bearer = { authorization: `Bearer ${phone}` };
    expect((await app.inject({ url: '/api/state', headers: bearer })).statusCode).toBe(200);
    await call(await devCookie('owner'), 'PUT', `/api/admin/users/${recorded.ids.lee}`, { disabled: true });
    const res = await app.inject({ url: '/api/state', headers: bearer });
    expect([res.statusCode, res.json().error]).toEqual([403, 'no_access']);
  });

  describe('on the API port', () => {
    let server: Server;
    afterEach(async () => { await new Promise((done) => server?.close(done)); });

    it("takes a phone's credential, and nothing else", async () => {
      await start({ apiPort: 0 });
      const owner = await devCookie('owner');
      const phone = await pairPhone(owner);
      server = await listenApiPort(app, 0, '127.0.0.1');
      expect((await onApiPort(server, 'GET', '/api/state', { authorization: `Bearer ${phone}` })).status).toBe(200);
      expect((await onApiPort(server, 'GET', '/api/state', { 'x-api-key': phone })).status).toBe(200);   // the old header still works
      const cookie = await onApiPort(server, 'GET', '/api/state', { cookie: owner });
      expect([cookie.status, cookie.json.error]).toEqual([401, 'signed_out']);
      const none = await onApiPort(server, 'GET', '/api/state');
      expect([none.status, none.json.error]).toEqual([401, 'signed_out']);
      const wrong = await onApiPort(server, 'GET', '/api/state', { authorization: 'Bearer dh_made-up-old-key' });
      expect([wrong.status, wrong.json.error]).toEqual([401, 'signed_out']);
    });

    it('serves only the API, never the settings or the Discord test, beside the same on the web port', async () => {
      await start({ apiPort: 0 });
      const phone = await pairPhone(await devCookie('member'));
      server = await listenApiPort(app, 0, '127.0.0.1');
      const auth = { authorization: `Bearer ${phone}`, 'x-cave-request': '1' };
      const page = await onApiPort(server, 'GET', '/', auth);
      expect([page.status, page.json.error]).toEqual([404, 'not_found']);
      for (const path of ['/api/settings', '/api/test-discord', '/api/api-key']) {
        const res = await onApiPort(server, 'PUT', path, auth, { zip_code: '00004' });
        expect([path, res.status, res.json.error]).toEqual([path, 403, 'forbidden']);
      }
      const web = await app.inject({ method: 'PUT', url: '/api/settings', headers: auth, payload: { zip_code: '00004' } });
      expect(web.statusCode).toBe(200);
      const write = await onApiPort(server, 'POST', '/api/listings/mark-seen', auth, {});
      expect(write.status).toBe(200);
    });
  });
});

// ---- the Android app -------------------------------------------------------------------------------------------

describe('the Android download', () => {
  it('says what is published, and hands it to a signed-in person or phone only', async () => {
    const downloads = join(folder, 'downloads');
    await start();
    const owner = await devCookie('owner');
    expect((await call(owner, 'GET', '/api/app/android')).json()).toEqual({ available: false });
    expect((await call(owner, 'GET', '/download/android')).statusCode).toBe(404);
    mkdirSync(downloads, { recursive: true });
    writeFileSync(join(downloads, 'deal-hunter.apk'), 'made-up apk');
    writeFileSync(join(downloads, 'android.json'), JSON.stringify({ versionName: '1.2.3', versionCode: 45, sha256: 'ab' }));
    const info = (await call(owner, 'GET', '/api/app/android')).json();
    expect(info).toMatchObject({ available: true, url: '/download/android', version: '1.2.3', version_code: 45, sha256: 'ab', size: 11 });
    const got = await call(owner, 'GET', '/download/android');
    expect([got.statusCode, got.body, got.headers['content-disposition']]).toEqual([200, 'made-up apk', 'attachment; filename="deal-hunter.apk"']);
    const phone = await pairPhone(owner);
    expect((await app.inject({ url: '/download/android', headers: { authorization: `Bearer ${phone}` } })).body).toBe('made-up apk');
    const out = await app.inject({ url: '/download/android' });
    expect([out.statusCode, out.body]).toEqual([401, 'Sign in to Deal Hunter to get the app']);
    rmSync(downloads, { recursive: true, force: true });
  });
});


describe('checking now, and the Discord test', () => {
  it("runs a member's own watch now, never someone else's, and starts checks from Check now", async () => {
    await start();
    const owner = await devCookie('owner');
    const member = await devCookie('member');
    const mine = await call(owner, 'POST', '/api/watches/1/run', {});
    expect(mine.statusCode).toBe(200);
    expect(mine.json().errors).toEqual(expect.arrayContaining([expect.stringMatching(/^ebay: /)]));   // no network here: each source says why
    expect((await call(member, 'POST', '/api/watches/1/run', {})).json()).toEqual({ new: 0, errors: ['watch not found'] });
    const made = await call(owner, 'POST', '/api/watches', { name: 'Checked at once', query: 'lamp' });
    expect(made.json()).toMatchObject({ id: expect.any(Number), new: 0 });
    expect(made.json().errors.length).toBeGreaterThan(0);
    expect((await call(owner, 'POST', '/api/poll', {})).json()).toEqual({ ok: true });
    expect((await call(member, 'POST', '/api/poll', {})).json()).toEqual({ ok: true });
    expect((await call(owner, 'GET', '/api/state')).json().poller).toMatchObject({ running: false, source_errors: expect.any(Object) });
  });

  it("says there's no webhook, or that Discord didn't take the message, beside a webhook that works", async () => {
    await start();
    const member = await devCookie('member');
    await call(member, 'PUT', '/api/settings', { discord_webhook: '' });
    const lee = recorded.ids.lee!;
    await app.cave.db.execute(sql`DELETE FROM deals.user_settings WHERE member_id = ${lee} AND key = 'discord_webhook'`);
    const none = await call(member, 'POST', '/api/test-discord', {});
    expect([none.statusCode, none.json().message]).toEqual([400, 'No Discord webhook saved']);
    expect((await call(member, 'PUT', '/api/settings', { discord_webhook: 'https://discord.com/api/webhooks/9/made-up' })).statusCode).toBe(200);
    const refused = await call(member, 'POST', '/api/test-discord', {});   // no network in these tests: nothing reaches Discord
    expect([refused.statusCode, refused.json().message]).toEqual([502, "Discord didn't accept the message; check the webhook address"]);
    await app.close();
    const posts: string[] = [];
    await start({ net: async (req) => { posts.push(`${req.method} ${req.url} ${req.body}`); return { status: 204, body: Buffer.from('') }; } });
    const again = await devCookie('member');
    await call(again, 'PUT', '/api/settings', { discord_webhook: 'https://discord.com/api/webhooks/9/made-up' });
    expect((await call(again, 'POST', '/api/test-discord', {})).json()).toEqual({ ok: true });
    expect(posts).toEqual(['POST https://discord.com/api/webhooks/9/made-up {"content": "Deal Hunter test message: notifications work."}']);
  });
});

describe("the owner console's AI settings file", () => {
  // shared_ai.py's rules: version 1, an entry with ai: true, a known provider, a monthly limit of 0-1000 and that
  // provider's key; an unknown tier or effort is ignored, never an error. Each refusal beside the file that works.
  const file = join(tmpdir(), `dh-ai-${process.pid}.json`);
  const ok = { version: 1, keys: { anthropic: ' made-up ' }, apps: { deals: { ai: true, provider: 'anthropic', monthly_usd: 1000, tier: 'turbo', effort: 'max' } } };
  const read = (data: unknown) => { writeFileSync(file, typeof data === 'string' ? data : JSON.stringify(data)); return loadShared(file); };
  afterAll(() => rmSync(file, { force: true }));

  it('turns the admin AI on only for a complete entry', () => {
    expect(read(ok)).toEqual({ provider: 'claude', model: null, tier: 'turbo', effort: null, monthly_usd: 1000, key: 'made-up' });
    expect(loadShared(join(tmpdir(), 'dh-no-such-file.json'))).toBeNull();
    expect(read('{not json')).toBeNull();
    expect(read({ ...ok, version: 2 })).toBeNull();
    expect(read({ ...ok, apps: {} })).toBeNull();
    expect(read({ ...ok, apps: { deals: { ...ok.apps.deals, ai: 1 } } })).toBeNull();
    expect(read({ ...ok, apps: { deals: { ...ok.apps.deals, provider: 'claude' } } })).toBeNull();
    expect(read({ ...ok, apps: { deals: { ...ok.apps.deals, monthly_usd: 1000.01 } } })).toBeNull();
    expect(read({ ...ok, apps: { deals: { ...ok.apps.deals, monthly_usd: '5' } } })).toBeNull();
    expect(read({ ...ok, keys: { anthropic: '  ' } })).toBeNull();
    expect(read({ ...ok, apps: { deals: { ...ok.apps.deals, model: 'claude-haiku-5-5', effort: 'high' } } })).toMatchObject({ model: 'claude-haiku-5-5', effort: 'high' });
  });
});
