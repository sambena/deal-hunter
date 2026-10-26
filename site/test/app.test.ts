// Deal Hunter's server, in-process (no ports): it starts, sends the strict page policy, signs in with the dev login,
// and its migration makes the deals tables, which keep the Python app's rows exactly. Made-up data only.
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { loadConfig } from '@cougarcave/server';
import { eq, sql } from 'drizzle-orm';
import { afterEach, describe, expect, it } from 'vitest';
import { build } from '../src/app.js';
import { aiUsage, DEFAULT_SOURCES, listings, machines, members, SCHEMA, settings, userSettings, watches } from '../src/db/schema.js';

const config = () => loadConfig({ APP_ORIGIN: 'http://127.0.0.1:8780', DEV_LOGIN: '1', DATA_DIR: ':memory:' });
let app: Awaited<ReturnType<typeof build>>;
afterEach(async () => app?.close());

const PAT = '00000000-0000-4000-8000-0000000000a1';
const SAM = '00000000-0000-4000-8000-0000000000a2';
const AT = 1767323045.123456;  // an epoch-seconds float, as Python's time.time() gives

/** The database error's own message, whether the driver wraps it or not. */
async function refusal(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (e) {
    const err = e as Error & { cause?: Error };
    return `${err.message} ${err.cause?.message ?? ''}`;
  }
  return 'accepted';
}

async function member(id: number, subject = PAT) {
  await app.cave.db.insert(members).values({ id, subject, createdAt: AT });
}

async function watch(id: number, memberId: number) {
  await app.cave.db.insert(watches).values({ id, memberId, name: 'RTX 3080', query: 'rtx 3080', createdAt: AT });
}

describe('Deal Hunter', () => {
  it('is healthy and sends the strict page policy', async () => {
    app = await build(config(), { logger: false });
    const res = await app.inject({ url: '/healthz' });
    expect([res.statusCode, res.json()]).toEqual([200, { ok: true }]);
    expect(res.headers['content-security-policy']).toContain("script-src 'self'");
  });

  it('knows who is signed in only after the dev login', async () => {
    app = await build(config(), { logger: false });
    const anonymous = await app.inject({ url: '/auth/me' });
    expect(anonymous.json().signedIn).toBe(false);
    const login = await app.inject({ url: '/auth/dev?who=owner' });
    const cookie = login.cookies.map((c) => `${c.name}=${c.value}`).join('; ');
    const me = await app.inject({ url: '/auth/me', headers: { cookie } });
    expect([me.json().signedIn, me.json().name]).toEqual([true, 'Owner (dev)']);
  });

  it('makes the deals tables once; starting again changes nothing', async () => {
    app = await build(config(), { logger: false });
    const rows = await app.cave.db.execute(sql`SELECT table_name FROM information_schema.tables WHERE table_schema = ${SCHEMA} ORDER BY table_name`);
    const names = ((rows as unknown as { rows?: { table_name: string }[] }).rows ?? (rows as unknown as { table_name: string }[])).map((r) => r.table_name);
    expect(names).toEqual(['__drizzle_migrations', 'ai_usage', 'listings', 'machines', 'members', 'settings', 'user_settings', 'watches']);
    const again = await app.cave.database.migrate(fileURLToPath(new URL('../drizzle/', import.meta.url)), SCHEMA);
    expect(again).toBe(0);
  });

  it('never creates a schema: in cave-db it exists and the app may not make one', () => {
    const folder = fileURLToPath(new URL('../drizzle/', import.meta.url));
    const files = readdirSync(folder).filter((f) => f.endsWith('.sql'));
    expect(files.length).toBeGreaterThan(0);
    const creating = files.filter((f) => /CREATE\s+SCHEMA/i.test(readFileSync(folder + f, 'utf8')));
    expect(creating).toEqual([]);
  });

  it('starts a watch, a device and a person with the Python app\'s defaults', async () => {
    app = await build(config(), { logger: false });
    const db = app.cave.db;
    await member(1);
    await watch(1, 1);
    await db.insert(machines).values({ id: 1, memberId: 1, name: 'Desk PC' });
    expect((await db.select().from(members))[0]).toEqual({ id: 1, subject: PAT, admin: false, disabled: false, watchLimit: null,
      createdAt: AT, aiShared: true, aiAllowance: 0, aiDailyCap: 20 });
    expect((await db.select().from(watches))[0]).toMatchObject({ exclude: [], minPrice: null, condition: 'any', includeAuctions: false,
      sources: DEFAULT_SOURCES, enabled: true, notes: '', machineId: null, polledSources: [], keepCheapest: null, color: null,
      lastCheck: null, kind: 'item', category: null });
    expect((await db.select().from(machines))[0]).toMatchObject({ notes: '', parts: [], kind: 'pc', model: '', sourceRef: null, make: '',
      purchased: '', custom: [], vin: '', odometer: null });
  });

  it('keeps every value of a row exactly: old ids, float times, JSON in order', async () => {
    app = await build(config(), { logger: false });
    const db = app.cave.db;
    await member(7);
    const lastCheck = { ebay: { returned: 40, kept: 2, dropped: { over_max: 38 } } };
    await db.insert(watches).values({ id: 42, memberId: 7, name: 'Tacoma', query: 'toyota tacoma', exclude: ['salvage', 'parts'],
      maxPrice: 23999.99, sources: ['ksl_cars', 'ebay', 'craigslist'], createdAt: AT, lastPolled: AT + 0.5,
      polledSources: ['ebay'], keepCheapest: 5, color: '#a1b2c3', lastCheck, kind: 'vehicle', yearMin: 2016, yearMax: 2020, maxMiles: 90000 });
    await db.insert(listings).values({ id: 9001, watchId: 42, source: 'ksl_cars', sourceId: 'abc-1', title: '2018 Toyota Tacoma TRD',
      price: 27500, shipping: 0, total: 27500, currency: 'USD', firstSeen: AT, status: 'starred', dealPct: 0.123456789,
      seenAt: AT + 900, year: 2018, miles: 61000, titleStatus: 'clean', make: 'Toyota' });
    await db.insert(settings).values([{ key: 'poll_minutes', value: 15 }, { key: 'check_times', value: ['07:00', '18:00'] },
      { key: 'sources_enabled', value: { ebay: true, bestbuy: false } }, { key: 'ebay_marketplace', value: 'EBAY_US' }]);
    await db.insert(userSettings).values({ memberId: 7, key: 'ai_monthly_limit', value: 5.5 });
    await db.insert(aiUsage).values({ id: 3, memberId: 7, at: AT, provider: 'claude', model: 'claude-haiku-5-5', action: 'judge',
      inputTokens: 812, outputTokens: 96, cost: 0.000123, paidBy: 7 });

    const [w] = await db.select().from(watches);
    expect([w!.id, w!.createdAt, w!.lastPolled, w!.maxPrice, w!.exclude, w!.sources, w!.lastCheck])
      .toEqual([42, AT, AT + 0.5, 23999.99, ['salvage', 'parts'], ['ksl_cars', 'ebay', 'craigslist'], lastCheck]);
    const [l] = await db.select().from(listings);
    expect([l!.id, l!.firstSeen, l!.dealPct, l!.status, l!.make]).toEqual([9001, AT, 0.123456789, 'starred', 'Toyota']);
    const stored = Object.fromEntries((await db.select().from(settings)).map((s) => [s.key, s.value]));
    expect(stored).toEqual({ poll_minutes: 15, check_times: ['07:00', '18:00'], sources_enabled: { ebay: true, bestbuy: false }, ebay_marketplace: 'EBAY_US' });
    expect((await db.select().from(userSettings))[0]!.value).toBe(5.5);
    expect((await db.select().from(aiUsage))[0]).toMatchObject({ id: 3, at: AT, cost: 0.000123, paidBy: 7 });
  });

  it('refuses a second find with the same source id on one watch, beside another watch and another source', async () => {
    app = await build(config(), { logger: false });
    const db = app.cave.db;
    await member(1);
    await watch(1, 1);
    await watch(2, 1);
    const find = (watchId: number, source: string) =>
      refusal(db.insert(listings).values({ watchId, source, sourceId: 'x1', title: 'GPU', firstSeen: AT }));
    expect(await find(1, 'ebay')).toBe('accepted');
    expect(await find(2, 'ebay')).toBe('accepted');
    expect(await find(1, 'ksl')).toBe('accepted');
    expect(await find(1, 'ebay')).toMatch(/listings_watch_source_id/);
  });

  it('refuses rows for a person or watch that does not exist, beside valid ones', async () => {
    app = await build(config(), { logger: false });
    const db = app.cave.db;
    await member(1);
    expect(await refusal(db.insert(watches).values({ memberId: 1, name: 'a', query: 'a', createdAt: AT }))).toBe('accepted');
    expect(await refusal(db.insert(watches).values({ memberId: 99, name: 'a', query: 'a', createdAt: AT }))).toMatch(/foreign key/i);
    expect(await refusal(db.insert(listings).values({ watchId: 99, source: 'ebay', sourceId: 'x', title: 't', firstSeen: AT }))).toMatch(/foreign key/i);
    expect(await refusal(db.insert(machines).values({ memberId: 99, name: 'PC' }))).toMatch(/foreign key/i);
    expect(await refusal(db.insert(userSettings).values({ memberId: 99, key: 'zip_code', value: '00000' }))).toMatch(/foreign key/i);
    expect(await refusal(db.insert(aiUsage).values({ memberId: 1, at: AT, provider: 'ollama', model: 'm', action: 'judge', inputTokens: 1, outputTokens: 1, cost: 0, paidBy: 99 }))).toMatch(/foreign key/i);
  });

  it('has one member per person, beside a second person', async () => {
    app = await build(config(), { logger: false });
    expect(await refusal(member(1, PAT))).toBe('accepted');
    expect(await refusal(member(2, SAM))).toBe('accepted');
    expect(await refusal(member(3, PAT))).toMatch(/duplicate key|unique/i);
  });

  it('removes a watch with its finds, and only its own', async () => {
    app = await build(config(), { logger: false });
    const db = app.cave.db;
    await member(1);
    await watch(1, 1);
    await watch(2, 1);
    await db.insert(listings).values([{ watchId: 1, source: 'ebay', sourceId: 'a', title: 't', firstSeen: AT },
      { watchId: 2, source: 'ebay', sourceId: 'b', title: 't', firstSeen: AT }]);
    await db.delete(watches).where(eq(watches.id, 1));
    expect((await db.select().from(listings)).map((l) => l.watchId)).toEqual([2]);
  });
});
