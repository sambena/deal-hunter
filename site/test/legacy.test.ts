// The one-time copy and the checker, against an old database made by the real Python code (test/fixtures/
// make_legacy.py: made-up people and listings only). Needs python. Every refusal sits beside a working control.
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFileSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import { openDatabase, type Database } from '@cougarcave/server';
import { eq, sql } from 'drizzle-orm';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { listings, members, SCHEMA, settings, userSettings, watches } from '../src/db/schema.js';
import { check, copy, openOld, readOld, Refused, selfCheck, type Old } from '../src/legacy/copy.js';

const ISSUER = 'https://auth.example.test/application/o/deals/';
const REPO = fileURLToPath(new URL('../../', import.meta.url));
const SITE = fileURLToPath(new URL('../', import.meta.url));
const UNLINKED = [5, 6, 7];   // never linked, linked to another login service, link disabled

let folder: string;
let file: string;
let accounts: Record<string, number>;
let database: Database;

function makeLegacy(into: string) {
  const run = spawnSync('python', [join(SITE, 'test/fixtures/make_legacy.py'), into, ISSUER],
    { cwd: REPO, env: { ...process.env, PYTHONPATH: REPO, PYTHONIOENCODING: 'utf-8' }, encoding: 'utf8' });
  if (run.status !== 0) throw new Error(`make_legacy.py failed: ${run.stderr || run.error}`);
  return JSON.parse(run.stdout).accounts;
}

/** The old file read as the copy reads it, closed again at once. */
function old(path = file, leaveOut = UNLINKED): Old {
  const src = openOld(path);
  try {
    return readOld(src, ISSUER, leaveOut);
  } finally {
    src.close();
  }
}

/** A copy of the old file to change, for the refusals. */
function altered(change: string): string {
  const path = join(folder, `altered-${Math.random().toString(36).slice(2)}.db`);
  copyFileSync(file, path);
  const w = new DatabaseSync(path);
  w.exec(change);
  w.close();
  return path;
}

const db = () => database.db;
const differences = async (o = old()) => (await check(db(), o)).flatMap((r) => r.differences.map((d) => `${r.table}: ${d}`));
const count = async (table: string) => Number((await db().execute(sql.raw(`SELECT count(*)::int AS n FROM ${SCHEMA}.${table}`)) as unknown as { rows: { n: number }[] }).rows[0]!.n);

beforeAll(() => {
  folder = mkdtempSync(join(tmpdir(), 'dh-legacy-'));
  file = join(folder, 'deal-hunter.db');
  accounts = makeLegacy(file);
});
afterAll(() => rmSync(folder, { recursive: true, force: true }));
beforeEach(async () => {
  database = await openDatabase({ dataDir: ':memory:' });
  await database.migrate(fileURLToPath(new URL('../drizzle/', import.meta.url)), SCHEMA);
});
afterEach(async () => database.close());

describe('the copy', () => {
  it('stops and lists accounts with no active link, writing nothing; leaving them out lets it through', async () => {
    const all = old(file, []);
    expect(all.unlinked).toEqual([
      'account 5 (member): no active link for this issuer',
      'account 6 (member): no active link for this issuer',
      'account 7 (member): no active link for this issuer']);
    await expect(copy(db(), all)).rejects.toThrow(/account 5 .*account 6 .*account 7/);
    expect(await count('members')).toBe(0);
    await copy(db(), old());
    expect(await count('members')).toBe(4);
  });

  it('copies everything, and the checker finds every table the same, next ids included', async () => {
    const o = old();
    const counts = await copy(db(), o);
    expect(counts).toEqual({ members: 4, settings: 6, user_settings: 6, machines: 2, watches: 4, listings: 4, ai_usage: 3 });
    const results = await check(db(), o);
    expect(results.filter((r) => r.differences.length)).toEqual([]);
    expect(results.every((r) => r.checksum)).toBe(true);
    // The highest listing id given (6) was deleted; the next one still skips it, as SQLite would have.
    expect(Object.fromEntries(results.filter((r) => r.nextId !== '-').map((r) => [r.table, r.nextId])))
      .toEqual({ members: '8', machines: '3', watches: '7', listings: '7', ai_usage: '4' });
  });

  it('keeps people by their central subject, with their limits; email, name and password stay behind', async () => {
    await copy(db(), old());
    const rows = await db().select().from(members).orderBy(members.id);
    expect(rows.map((m) => [m.id, m.subject, m.admin, m.disabled, m.watchLimit, m.aiShared, m.aiAllowance, m.aiDailyCap])).toEqual([
      [accounts.pat, '00000000-0000-4000-8000-0000000000a1', true, false, null, true, 0, 20],
      [accounts.lee, '00000000-0000-4000-8000-0000000000a2', false, false, 10, true, 1.25, 5],
      [accounts.kim, '00000000-0000-4000-8000-0000000000a3', false, false, 3, false, 0, 20],
      [accounts.gone, '00000000-0000-4000-8000-0000000000a4', false, true, null, true, 0, 20]]);
    expect(JSON.stringify(rows)).not.toMatch(/example\.test|Pat|password/);
  });

  it('keeps values exactly: floats, JSON in order, text with any letters, the settings as stored', async () => {
    await copy(db(), old());
    const [gpu] = await db().select().from(watches).where(eq(watches.name, 'RTX 3080'));
    expect(gpu).toMatchObject({ maxPrice: 450.5, exclude: ['mining'], keepCheapest: 5, color: '#a1b2c3', includeAuctions: true,
      polledSources: ['ebay', 'ksl'], lastCheck: { ebay: { returned: 40, kept: 2 } } });
    const finds = await db().select().from(listings).orderBy(listings.id);
    expect(finds.map((l) => [l.title, l.total, l.dealPct])).toEqual([
      ['RTX 3080 FE', 439.99, 0.1234567891], ['RTX 3080 — local “pickup”', 400, null],
      ['2018 Toyota Tacoma TRD', 27500, null], ['Running shoes 10.5', 35, null]]);
    const shared = Object.fromEntries((await db().select().from(settings)).map((s) => [s.key, s.value]));
    expect(shared).toMatchObject({ poll_minutes: 30, check_times: ['07:00', '18:30'], sources_enabled: { ebay: true, bestbuy: false, ksl: true } });
    const lee = Object.fromEntries((await db().select().from(userSettings).where(eq(userSettings.memberId, accounts.lee!))).map((s) => [s.key, s.value]));
    expect(lee).toEqual({ zip_code: '00000', local_radius_miles: 25, junk_terms: ['for parts', 'naïve ünïcode'], ai_monthly_limit: 5, sizes: { shoe: '10.5', top: 'M' } });
  });

  it('leaves out every row of an account left out, and says so', async () => {
    const o = old();
    expect(o.leftOut).toEqual(['user_settings: 1 row(s) of the accounts left out', 'watches: 1 row(s) of the accounts left out']);
    await copy(db(), o);
    expect((await db().select().from(watches)).map((w) => w.name)).not.toContain('Lamp');
  });

  it('counts what the framework replaces, and copies none of it', () => {
    expect(old().retired).toMatchObject({ tokens: 1, central_identities: 6, invites: 0, paired_devices: 0 });
  });

  it('gives new rows ids after the old ones', async () => {
    await copy(db(), old());
    const [w] = await db().insert(watches).values({ memberId: accounts.lee!, name: 'New', query: 'new', createdAt: 1 }).returning();
    const [l] = await db().insert(listings).values({ watchId: w!.id, source: 'ebay', sourceId: 'n', title: 'n', firstSeen: 1 }).returning();
    expect([w!.id, l!.id]).toEqual([7, 7]);
  });

  it('refuses tables that already hold rows, beside replacing them on purpose', async () => {
    const o = old();
    await copy(db(), o);
    await expect(copy(db(), o)).rejects.toThrow(/already hold rows/);
    await db().insert(settings).values({ key: 'stray', value: 1 });
    await copy(db(), o, { replace: true });
    expect(await differences(o)).toEqual([]);
  });

  it('never writes to the old file, and opens it read-only', async () => {
    const before = createHash('sha256').update(readFileSync(file)).digest('hex');
    await copy(db(), old());
    expect(createHash('sha256').update(readFileSync(file)).digest('hex')).toBe(before);
    const src = openOld(file);
    expect(() => src.exec("UPDATE settings SET value = '1'")).toThrow(/readonly|read-only/i);
    src.close();
  });

  it('refuses a value of the wrong kind before writing anything, beside the file as it is', async () => {
    const path = altered("UPDATE listings SET year = 'soon' WHERE source_id = 'c1'");
    await expect(copy(db(), old(path))).rejects.toThrow(/listings id 4: year holds string/);
    expect(await count('members')).toBe(0);
    const json = altered("UPDATE watches SET sources = 'not json' WHERE name = 'Tacoma'");
    await expect(copy(db(), old(json))).rejects.toThrow(/watches id 3: sources is not valid JSON/);
    expect(await count('members')).toBe(0);
    await copy(db(), old());
    expect(await count('members')).toBe(4);
  });

  it('refuses a database at another version or with other columns', () => {
    expect(() => old(altered('PRAGMA user_version = 10'))).toThrow(/version 10; this copy is for version 9/);
    expect(() => old(altered('ALTER TABLE watches ADD COLUMN surprise TEXT'))).toThrow(/watches: columns differ \(only in the old database: surprise/);
    expect(() => old(file, [99])).toThrow(Refused);
    expect(old().rows.get('members')!.length).toBe(4);
  });
});

describe('the checker', () => {
  it('reports a changed value, a missing row, an extra row and a wrong next id, each by table and key', async () => {
    const o = old();
    await copy(db(), o);
    expect(await differences(o)).toEqual([]);
    await db().update(watches).set({ sources: ['ksl', 'ebay'] }).where(eq(watches.name, 'Tacoma'));   // was ebay first
    await db().update(members).set({ disabled: false }).where(eq(members.id, accounts.gone!));
    await db().update(listings).set({ dealPct: 0.1234567892 }).where(eq(listings.sourceId, 'e1'));
    await db().delete(userSettings).where(eq(userSettings.key, 'focus'));
    await db().insert(settings).values({ key: 'extra', value: true });
    await db().execute(sql.raw(`SELECT setval(pg_get_serial_sequence('${SCHEMA}.machines', 'id'), 1)`));
    expect(await differences(o)).toEqual([
      'members: changed ["4"]: disabled',
      'settings: 6 old rows, 7 new',
      'settings: extra ["extra"]',
      'user_settings: 6 old rows, 5 new',
      'user_settings: missing ["3","focus"]',
      'machines: next id 2, expected 3',
      'watches: changed ["3"]: sources',
      'listings: changed ["1"]: deal_pct']);
  });

  it('catches every planted difference in its self-check, and rolls them back', async () => {
    const o = old();
    await copy(db(), o);
    const s = await selfCheck(db(), o);
    expect(s.planted).toHaveLength(3);
    expect(s.caught).toEqual(expect.arrayContaining(['listings: changed ["1"]: title', 'ai_usage: missing ["3"]', 'settings: extra ["planted"]']));
    expect(await differences(o)).toEqual([]);
  });
});
