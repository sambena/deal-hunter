// The checks give the Python app's own results: test/fixtures/make_poller.py runs poller.py on a database the real
// db.py made, with every source answering from test/fixtures/poller-spec.json (made up) and Discord replaced by a
// stand-in. Here the same database is copied in and the same steps run on the port, with the same answers and clock;
// after each step the watches, every find, the Discord posts, the sources asked and the poller's state must match.
// Needs python. Nothing is fetched.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { openDatabase, type Database } from '@cougarcave/server';
import { sql } from 'drizzle-orm';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { SCHEMA } from '../src/db/schema.js';
import { copy, openOld, readOld } from '../src/legacy/copy.js';
import { nextCheck, wallToEpoch } from '../src/logic/schedule.js';
import { type Listing, type Net, SourceError } from '../src/logic/sources.js';
import { Poller } from '../src/poller.js';
import { Store } from '../src/store.js';

const ISSUER = 'https://auth.example.test/application/o/deals/';
const SITE = fileURLToPath(new URL('../', import.meta.url));
const REPO = join(SITE, '..');
const SPEC_FILE = join(SITE, 'test/fixtures/poller-spec.json');
const SPEC = JSON.parse(readFileSync(SPEC_FILE, 'utf8')) as Record<string, Record<string, unknown[]>>;
const SOURCE_NAMES = ['ebay', 'ebay_local', 'reddit', 'bestbuy', 'slickdeals', 'buildapcsales', 'ksl', 'craigslist', 'offerup', 'ksl_cars', 'poshmark'];

type Row = Record<string, unknown>;
interface Step { name: string; at: number; out?: unknown; error?: string; asked: Row[]; posts: Row[]; state: Row; watches: Row[]; listings: Row[];
  cases?: { settings: Row; now: number; out: number }[] }
let folder: string;
let recorded: { steps: Step[]; ids: Record<string, number> };
let database: Database;

beforeAll(async () => {
  folder = mkdtempSync(join(tmpdir(), 'dh-poller-'));
  const run = spawnSync('python', [join(SITE, 'test/fixtures/make_poller.py'), join(folder, 'deal-hunter.db'), ISSUER, SPEC_FILE],
    { cwd: REPO, env: { ...process.env, PYTHONPATH: REPO, PYTHONIOENCODING: 'utf-8' }, encoding: 'utf8', maxBuffer: 1 << 26 });
  if (run.status !== 0) throw new Error(`make_poller.py failed: ${run.stderr || run.error}`);
  recorded = JSON.parse(run.stdout);
  database = await openDatabase({ dataDir: ':memory:' });
  await database.migrate(fileURLToPath(new URL('../drizzle/', import.meta.url)), SCHEMA);
  const src = openOld(join(folder, 'deal-hunter.db'));
  try {
    await copy(database.db, readOld(src, ISSUER));
  } finally {
    src.close();
  }
});
afterAll(async () => {
  await database?.close();
  rmSync(folder, { recursive: true, force: true });
});

const rowsOf = (result: unknown): Row[] => (Array.isArray(result) ? result : (result as { rows?: Row[] }).rows ?? []);
/** Keys sorted, so two JSON values compare by content (jsonb keeps its own key order). */
function sorted(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sorted);
  if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sorted((v as Row)[k])]));
  return v;
}

describe('the checks match Python, step by step', () => {
  it('passes, one watch, one person, the purge: finds, alerts, requests and state', async () => {
    let clock = 0;
    let pass = 0;
    const asked: Row[] = [];
    const posts: Row[] = [];
    const sources = Object.fromEntries(SOURCE_NAMES.map((name) => [name, async (watch: Row & { query: string }): Promise<Listing[]> => {
      asked.push({ source: name, query: watch.query, fits: watch.fits ?? null });
      const answers = SPEC[name]?.[watch.query] ?? [[]];
      const answer = answers[Math.min(pass, answers.length - 1)] as Listing[] | { error: string };
      if (!Array.isArray(answer)) throw new SourceError(answer.error);
      return structuredClone(answer);
    }]));
    const net: Net = async (req) => {
      posts.push({ url: req.url, body: req.body, headers: Object.fromEntries(Object.entries(req.headers).map(([k, v]) => [k.toLowerCase(), v])), timeout: req.timeout });
      return req.url.includes('made-up-member') ? { status: 404, body: Buffer.from(''), reason: 'made up' } : { status: 204, body: Buffer.from('') };
    };
    const store = new Store(database.db, () => clock);
    const poller = new Poller(database.db, store, sources, net, { now: () => clock, sleep: async () => {} });
    const { admin, lee, gpu, same } = recorded.ids;
    const steps: Record<string, () => Promise<unknown>> = {
      'first pass': async () => { await poller.runAll(); return null; },
      'second pass': async () => { pass = 1; await poller.runAll(); return null; },
      'one watch': () => poller.runOne(gpu!, admin!),
      "someone else's watch": () => poller.runOne(same!, admin!),
      "one person's watches": async () => { pass = 2; return poller.pollUser(lee!); },
      'a pass three days on': async () => { await poller.runAll(); return null; },
    };
    const differ: unknown[] = [];
    for (const step of recorded.steps) {
      if (step.cases) {
        for (const c of step.cases) if (nextCheck(c.settings, c.now) !== c.out) differ.push({ step: step.name, settings: c.settings });
        continue;
      }
      clock = step.at;
      asked.length = 0;
      posts.length = 0;
      const out = await steps[step.name]!();
      const watches = rowsOf(await database.db.execute(sql`SELECT id, last_polled, last_error, polled_sources, last_check FROM deals.watches ORDER BY id`));
      const listings = rowsOf(await database.db.execute(sql`SELECT * FROM deals.listings ORDER BY id`));
      const mine = { out, asked, posts, state: { source_errors: poller.state.source_errors, fetches: poller.state.fetches }, watches, listings };
      for (const key of Object.keys(mine) as (keyof typeof mine)[]) {
        if (JSON.stringify(sorted(mine[key])) !== JSON.stringify(sorted(step[key as keyof Step]))) {
          differ.push({ step: step.name, key, python: step[key as keyof Step], port: mine[key] });
        }
      }
    }
    if (process.env.DH_POLLER_DEBUG) (await import('node:fs')).writeFileSync(process.env.DH_POLLER_DEBUG, JSON.stringify(differ, null, 1));
    expect(differ.map((d) => `${(d as Row).step}: ${(d as Row).key ?? 'next check'}`)).toEqual([]);
    expect(recorded.steps.some((s) => s.posts?.length)).toBe(true);
  });
});

describe('set times, as zoneinfo reads them', () => {
  // This computer's Python has no zone data, so these are worked out from zoneinfo's rules: a wall-clock time that
  // happens twice is the first one (fold=0), and one that never happens takes the offset from before the change.
  it('reads ordinary, repeated and missing wall-clock times', () => {
    expect(wallToEpoch('America/Denver', 2026, 10, 26, 7, 0)).toBe(Date.UTC(2026, 9, 26, 13, 0) / 1000);   // MDT, UTC-6
    expect(wallToEpoch('America/Denver', 2026, 12, 1, 7, 0)).toBe(Date.UTC(2026, 11, 1, 14, 0) / 1000);   // MST, UTC-7
    expect(wallToEpoch('America/Denver', 2026, 11, 1, 1, 30)).toBe(Date.UTC(2026, 10, 1, 7, 30) / 1000);   // twice: the MDT one
    expect(wallToEpoch('America/Denver', 2026, 3, 8, 2, 30)).toBe(Date.UTC(2026, 2, 8, 9, 30) / 1000);   // never: read as MST
  });

  it("gives the day's next set time, tomorrow's first when today's have passed, and never one within 30 seconds", () => {
    const zone = { check_mode: 'times', check_timezone: 'America/Denver', check_times: ['18:00', '7:00', 'junk'] };
    const at = (d: number, h: number, m: number) => Date.UTC(2026, 9, d, h + 6, m) / 1000;   // MDT
    expect(nextCheck(zone, at(26, 6, 0))).toBe(at(26, 7, 0));
    expect(nextCheck(zone, at(26, 7, 0) - 30)).toBe(at(26, 18, 0));
    expect(nextCheck(zone, at(26, 7, 0) - 31)).toBe(at(26, 7, 0));
    expect(nextCheck(zone, at(26, 19, 0))).toBe(at(27, 7, 0));
    expect(nextCheck({ ...zone, check_timezone: 'Not/AZone' }, at(26, 6, 0))).toBe(at(26, 7, 0));   // falls back to Mountain
    expect(nextCheck({ ...zone, check_times: [] }, at(26, 6, 0))).toBe(at(26, 6, 0) + 900);
  });
});
