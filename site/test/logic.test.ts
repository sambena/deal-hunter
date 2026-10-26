// The ported rules give the Python app's own answers: test/fixtures/logic-python.json is what matching.py, hardware.py,
// specs.py and vehicles.py (and the Python behaviour they rest on) returned for the same made-up inputs, recorded by
// test/fixtures/make_logic.py. NHTSA's answers are made up; nothing is fetched.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import * as hardware from '../src/logic/hardware.js';
import * as matching from '../src/logic/matching.js';
import { pyCapitalize, pyFixed, pyQuote, pyRound, pyTitle, pyUrlencode, shlexSplit, splitlines } from '../src/logic/py.js';
import * as specs from '../src/logic/specs.js';
import * as vehicles from '../src/logic/vehicles.js';

const SITE = fileURLToPath(new URL('../', import.meta.url));
const REPO = join(SITE, '..');
const FIXTURE = join(SITE, 'test/fixtures/logic-python.json');
type Case = { in: unknown[]; out?: unknown; error?: string; message?: string };
const recorded = JSON.parse(readFileSync(FIXTURE, 'utf8')) as Record<string, Case[]>;

/** Python's exception type for an error the port raised. */
function errorName(e: unknown): string {
  if (e instanceof vehicles.LookupFailed) return 'LookupFailed';
  const message = (e as Error).message ?? '';
  return /^KeyError/.test(message) ? 'KeyError' : /quotation|escaped/.test(message) ? 'ValueError' : 'Error';
}

/** Runs the port on every recorded input; returns the cases whose answer differs, with both answers. */
async function differences(name: string, fn: (...args: any[]) => unknown, opts: { message?: boolean } = {}) {
  const cases = recorded[name];
  expect(cases?.length, `${name} has recorded cases`).toBeGreaterThan(0);
  const bad: unknown[] = [];
  for (const c of cases!) {
    let mine: Case;
    try {
      mine = { in: c.in, out: await fn(...structuredClone(c.in)) };
    } catch (e) {
      mine = { in: c.in, error: errorName(e), ...(opts.message ? { message: (e as Error).message } : {}) };
    }
    const theirs = opts.message || !c.error ? c : { in: c.in, error: c.error };
    try {
      expect(mine).toEqual(theirs);
    } catch {
      bad.push({ in: c.in, python: c.error ? `${c.error}: ${c.message}` : c.out, port: mine.error ?? mine.out });
    }
  }
  return bad;
}

const T = <A extends unknown[], R>(fn: (...a: A) => R) => (...a: A) => fn(...a);

describe('matching matches Python', () => {
  const pairs: [string, (...a: any[]) => unknown][] = [
    ['normalize', matching.normalize], ['compact', matching.compact], ['parse_query', matching.parseQuery],
    ['search_terms', matching.searchTerms], ['check', matching.check], ['still_wanted', matching.stillWanted],
    ['body_style', matching.bodyStyle], ['vehicle_make', matching.vehicleMake], ['vehicle_year', matching.vehicleYear],
    ['vehicle_miles', matching.vehicleMiles], ['title_status', matching.titleStatus], ['check_vehicle', matching.checkVehicle],
    ['norm_size', matching.normSize], ['title_size', matching.titleSize], ['check_size', matching.checkSize],
    ['vehicle_history', matching.vehicleHistory], ['typical_price', matching.typicalPrice], ['deal_pct', matching.dealPct],
    ['deal_label', matching.dealLabel],
  ];
  for (const [name, fn] of pairs) it(name, async () => expect(await differences(name, fn)).toEqual([]));
});

describe('hardware matches Python', () => {
  it('cpu_socket', async () => expect(await differences('cpu_socket', hardware.cpuSocket)).toEqual([]));
  it('detect_platform', async () => expect(await differences('detect_platform', hardware.detectPlatform)).toEqual([]));
  it('suggest', async () => expect(await differences('suggest', hardware.suggest)).toEqual([]));
});

describe('specs matches Python', () => {
  it('looks_like_report', async () => expect(await differences('looks_like_report', specs.looksLikeReport)).toEqual([]));
  it('parse_report', async () => expect(await differences('parse_report', specs.parseReport)).toEqual([]));
  it('tidy_cpu', async () => expect(await differences('tidy_cpu', specs.tidyCpu)).toEqual([]));
  it('merge_parts', async () => expect(await differences('merge_parts', specs.mergeParts)).toEqual([]));
  it('the commands people paste, character for character', async () =>
    expect(await differences('commands', () => [specs.WINDOWS_COMMAND, specs.LINUX_COMMAND])).toEqual([]));
});

describe('vehicles matches Python, with NHTSA made up', () => {
  it('clean_vin', async () => expect(await differences('clean_vin', vehicles.cleanVin, { message: true })).toEqual([]));

  it('decode_vin', async () => {
    const decode = (vin: string, result: unknown) => vehicles.decodeVin(vin, async () => (result === null ? {} : { Results: [result] }));
    expect(await differences('decode_vin', decode, { message: true })).toEqual([]);
    let asked = '';
    await vehicles.decodeVin(' 1abcd-23efgh456789 ', async (url) => { asked = url; return { Results: [{ Make: 'X' }] }; });
    expect(asked).toBe(recorded.decode_vin_url![0]!.out);
  });

  it('recalls, the fallback to the base model and the refusals', async () => {
    const run = async (make: string, model: string, year: number | null, answers: Record<string, unknown>) => {
      vehicles.clearRecallCache();
      const asked: string[] = [];
      const get: vehicles.Get = async (url) => {
        asked.push(url);
        const found = answers[new URL(url).searchParams.get('model')!];
        if (found === 'fail') throw new vehicles.LookupFailed('NHTSA answered HTTP 400');
        return found === undefined ? {} : { results: found };
      };
      try {
        return { recalls: await vehicles.recalls(make, model, year, 1000, get), asked };
      } catch (e) {
        if (!(e instanceof vehicles.LookupFailed)) throw e;
        return { error: e.message, asked };
      }
    };
    expect(await differences('recalls', run, { message: true })).toEqual([]);
  });

  it('keeps recalls for half a day, then asks again', async () => {
    vehicles.clearRecallCache();
    let calls = 0;
    const get: vehicles.Get = async () => { calls++; return { results: [] }; };
    await vehicles.recalls('Toyota', 'Tacoma', 2018, 1000, get);
    await vehicles.recalls('toyota', 'TACOMA', 2018, 1000 + vehicles.RECALL_CACHE_SECONDS - 1, get);
    expect(calls).toBe(1);
    await vehicles.recalls('Toyota', 'Tacoma', 2018, 1000 + vehicles.RECALL_CACHE_SECONDS, get);
    expect(calls).toBe(2);
  });
});

describe('the Python behaviour the rules rest on', () => {
  it("round() and '%.1f' break exact ties to even", async () => {
    expect(await differences('round3', (x: number) => pyRound(x, 3))).toEqual([]);
    expect(await differences('fixed1', (x: number) => pyFixed(x, 1))).toEqual([]);
  });
  it('shlex.split', async () => expect(await differences('shlex', shlexSplit)).toEqual([]));
  it('str.title, str.capitalize and str.splitlines', async () => {
    expect(await differences('title', pyTitle)).toEqual([]);
    expect(await differences('capitalize', pyCapitalize)).toEqual([]);
    expect(await differences('splitlines', splitlines)).toEqual([]);
  });
  it('urllib quoting', async () => {
    expect(await differences('quote', pyQuote)).toEqual([]);
    expect(await differences('urlencode', T(pyUrlencode))).toEqual([]);
  });
});

describe('the recording', () => {
  // The fixture is committed, so these tests need no Python. When Python is here, check it is still what the Python
  // code says, so a stale recording can't hide a difference.
  const python = spawnSync('python', ['--version'], { encoding: 'utf8' });
  it.skipIf(python.status !== 0)('is what the Python code gives today', () => {
    const run = spawnSync('python', [join(SITE, 'test/fixtures/make_logic.py')], {
      cwd: REPO, env: { ...process.env, PYTHONPATH: REPO, PYTHONIOENCODING: 'utf-8', PYTHONHASHSEED: '0' }, encoding: 'utf8', maxBuffer: 1 << 26,
    });
    expect(run.status, run.stderr).toBe(0);
    expect(JSON.parse(run.stdout)).toEqual(recorded);
  });
});
