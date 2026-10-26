// The sources give the Python app's own answers, ask the same things and wait the same gaps: test/fixtures/
// sources-python.json is what sources.py did for each call, on made-up pages (test/fixtures/make_sources.py), with a
// fake clock. Here the same pages are served to the port, in the same order, on one Sources (so caches, tokens and
// rate gaps carry over between calls as they did in Python). Nothing is fetched.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { type Clock, type Net, NetError, type NetRequest, SourceError, Sources } from '../src/logic/sources.js';
import { DateValueError, htmlUnescape, JSONDecodeError, parsedateTimestamp, pyJsonDumps, quote, quotePlus, rawDecode, urlencode } from '../src/logic/py-text.js';

const SITE = fileURLToPath(new URL('../', import.meta.url));
const REPO = join(SITE, '..');
interface Rule { url: string; has?: string[]; method?: string; status?: number; json?: unknown; body?: string; network?: string }
interface Sent { method: string; url: string; headers: Record<string, string>; body: string | null; timeout: number }
interface Result { out?: unknown; error?: string; message?: string; sent: Sent[]; slept: number[]; clock: number }
const recorded = JSON.parse(readFileSync(join(SITE, 'test/fixtures/sources-python.json'), 'utf8')) as
  { T0: number; rules: Rule[]; calls: { source: string; watch: Record<string, unknown> & { query: string }; settings: Record<string, unknown>; advance: number }[];
    results: Result[] };

/** The fake network and clock make_sources.py used, noting what is sent and slept. */
function fakes(rules: Rule[], T0: number) {
  const state = { now: T0, sent: [] as Sent[], slept: [] as number[] };
  const net: Net = async (req: NetRequest) => {
    state.sent.push({ method: req.method, url: req.url, headers: Object.fromEntries(Object.entries(req.headers).map(([k, v]) => [k.toLowerCase(), v])),
      body: req.body, timeout: req.timeout });
    const rule = rules.find((r) => req.url.startsWith(r.url) && (r.has ?? []).every((h) => req.url.includes(h)) && (r.method ?? req.method) === req.method)
      ?? { url: '', status: 404, body: 'not here' };
    if (rule.network) throw new NetError(rule.network);
    return { status: rule.status ?? 200, body: Buffer.from(rule.json !== undefined ? JSON.stringify(rule.json) : rule.body ?? '') };
  };
  const clock: Clock = { now: () => state.now, sleep: async (s) => { state.slept.push(s); state.now += s; } };
  return { state, net, clock };
}

function errorName(e: unknown): string {
  if (e instanceof SourceError) return 'SourceError';
  if (e instanceof JSONDecodeError || e instanceof SyntaxError) return 'JSONDecodeError';
  return (e as Error).name;
}

/** Expat words its errors its own way ("mismatched tag: line 1, column 15"); the port says what went wrong without
 * the position. */
const withoutPosition = (m: string | undefined) => m?.replace(/: line \d+, column \d+$/, '');

describe('every source matches Python on made-up pages', () => {
  it('answers, requests, waits and errors, call by call', async () => {
    const { state, net, clock } = fakes(recorded.rules, recorded.T0);
    const sources = new Sources(net, clock);
    const differ: unknown[] = [];
    for (const [i, call] of recorded.calls.entries()) {
      state.now += call.advance;
      state.sent = [];
      state.slept = [];
      let mine: Partial<Result>;
      try {
        mine = { out: await sources.all[call.source]!(structuredClone(call.watch), structuredClone(call.settings)) };
      } catch (e) {
        mine = { error: errorName(e), message: (e as Error).message };
      }
      const python = recorded.results[i]!;
      const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
      const problems = [
        !same(mine.out, python.out) && 'listings',
        (mine.error !== python.error || withoutPosition(mine.message) !== withoutPosition(python.message)) && 'error',
        !same(state.sent, python.sent) && 'requests',
        !same(state.slept, python.slept) && 'waits',
        state.now !== python.clock && 'clock',
      ].filter(Boolean);
      if (problems.length) differ.push({ i, call: `${call.source} ${call.watch.query}`, problems, python: { ...python }, port: { ...mine, sent: state.sent, slept: state.slept } });
    }
    if (process.env.DH_SOURCES_DEBUG) (await import('node:fs')).writeFileSync(process.env.DH_SOURCES_DEBUG, JSON.stringify(differ, null, 1));
    expect(differ.map((d) => (d as { i: number; call: string; problems: string[] }).call + ': ' + (d as { problems: string[] }).problems.join(', '))).toEqual([]);
    expect(recorded.calls.length).toBeGreaterThan(40);
  });
});

describe("the Python behaviour the sources rest on", () => {
  const helpers = (recorded as unknown as { helpers: Record<string, { in: unknown[]; out?: unknown; error?: string }[]> }).helpers;
  const check = (name: string, fn: (...a: never[]) => unknown) => {
    const differ = helpers[name]!.filter((c) => {
      let mine: unknown;
      try {
        mine = { out: fn(...(c.in as never[])) };
      } catch (e) {
        mine = { error: e instanceof DateValueError ? 'ValueError' : e instanceof JSONDecodeError ? 'JSONDecodeError' : (e as Error).name };
      }
      return JSON.stringify(mine) !== JSON.stringify('error' in c ? { error: c.error } : { out: c.out });
    });
    expect(differ).toEqual([]);
  };
  it('html.unescape', () => check('unescape', htmlUnescape));
  it('email.utils.parsedate_to_datetime', () => check('parsedate', parsedateTimestamp));
  it('quote, quote_plus and urlencode', () => {
    check('quote', quote);
    check('quote_plus', quotePlus);
    check('urlencode', (pairs: [string, unknown][], doseq?: boolean) => urlencode(pairs, doseq ?? false));
  });
  it("json's raw_decode and dumps", () => {
    check('raw_decode', (text: string, idx: number) => rawDecode(text, idx));
    check('dumps', (v: unknown) => pyJsonDumps(v));
    check('dumps_compact', (v: unknown) => pyJsonDumps(v, [',', ':']));
  });
});

describe('the recording', () => {
  // The fixture is committed, so the comparison needs no Python. When Python is here, check it is still what the
  // Python code gives, so a stale recording can't hide a difference.
  const python = spawnSync('python', ['--version'], { encoding: 'utf8' });
  it.skipIf(python.status !== 0)('is what the Python code gives today', () => {
    const run = spawnSync('python', [join(SITE, 'test/fixtures/make_sources.py')], {
      cwd: REPO, env: { ...process.env, PYTHONPATH: REPO, PYTHONIOENCODING: 'utf-8' }, encoding: 'utf8', maxBuffer: 1 << 26 });
    expect(run.status, run.stderr).toBe(0);
    expect(JSON.parse(run.stdout)).toEqual(recorded);
  });
});
