// Cars and trucks someone owns: VIN decoding and safety recalls, from NHTSA's free public APIs. A port of the Python
// app's vehicles.py (test/logic.test.ts checks it against Python, with NHTSA's answers made up; no test calls NHTSA).
//
// vPIC (vpic.nhtsa.dot.gov) turns a VIN into make/model/year/trim/engine; the recalls API lists open campaigns for a
// make/model/year. Neither needs a key. Only these two fixed hosts are ever called.
import { asciiDigits, pyFixed, pyQuote, pyRepr, pyStr, pyStrip, pySplit, pyTitle, pyUrlencode } from './py.js';

const USER_AGENT = 'deal-hunter/0.1 (personal vehicle lookup)';
const VIN_RE = /^[A-HJ-NPR-Z0-9]{17}$/;   // 17 characters, never I, O or Q
export const RECALL_CACHE_SECONDS = 12 * 3600;

export class LookupFailed extends Error {}

/** Fetches JSON from one of NHTSA's two hosts. Tests pass their own. */
export type Get = (url: string) => Promise<Record<string, unknown>>;

export const nhtsaGet: Get = async (url) => {
  let res: Response;
  try {
    res = await fetch(url, { headers: { 'User-Agent': USER_AGENT, Accept: 'application/json' }, signal: AbortSignal.timeout(20_000) });
  } catch {
    throw new LookupFailed("couldn't reach NHTSA; try again later");
  }
  if (!res.ok) throw new LookupFailed(`NHTSA answered HTTP ${res.status}`);
  try {
    return await res.json() as Record<string, unknown>;
  } catch {
    throw new LookupFailed("couldn't reach NHTSA; try again later");
  }
};

/** A VIN with spaces and dashes taken out, upper case; refused unless it is 17 letters and digits (no I, O or Q). */
export function cleanVin(vin: unknown): string {
  const v = String(vin || '').replace(/[\s-]/gu, '').toUpperCase();
  if (!VIN_RE.test(v)) throw new LookupFailed('A VIN is 17 letters and digits (no I, O or Q)');
  return v;
}

/** Python's `value or ""`, as text. */
const text = (v: unknown): string => (v ? String(v) : '');
const isDigits = (s: string) => /^\p{Nd}+$/u.test(s);

export interface DecodedVin { vin: string; make: string; model: string; year: number | null; trim: string; body: string; drive: string; engine: string; warning: string }

/** Make, model, year, trim and a short spec line for a VIN. */
export async function decodeVin(vin: unknown, get: Get = nhtsaGet): Promise<DecodedVin> {
  const v = cleanVin(vin);
  const data = await get(`https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/${v}?format=json`);
  const results = data.Results as Record<string, unknown>[] | undefined;
  const r = (results && results.length ? results[0] : {}) ?? {};
  if (!r.Make) throw new LookupFailed("NHTSA doesn't know this VIN; check it for typos");
  const displacement = text(r.DisplacementL);
  const engine = [
    isDigits(displacement.replace('.', '')) ? `${pyFixed(Number(asciiDigits(displacement)), 1)}L` : '',
    r.EngineCylinders ? `V${text(r.EngineCylinders)}` : '',
    r.FuelTypePrimary === null || r.FuelTypePrimary === undefined || r.FuelTypePrimary === '' || r.FuelTypePrimary === 'Gasoline' ? '' : String(r.FuelTypePrimary),
  ].filter(Boolean).join(' ');
  const year = r.ModelYear;
  return {
    vin: v,
    make: pyTitle(text(r.Make)),
    model: text(r.Model),
    year: isDigits(text(year)) ? Number(asciiDigits(text(year))) : null,
    trim: text(r.Trim),
    body: text(r.BodyClass),
    drive: text(r.DriveType),
    engine,
    // vPIC returns "0 - VIN decoded clean..." or a warning such as a bad check digit.
    warning: ('ErrorCode' in r ? pyStr(r.ErrorCode) : '0').split(',')[0] === '0' ? '' : text(r.ErrorText),
  };
}

export interface Recall { campaign: unknown; date: unknown; component: unknown; summary: unknown; consequence: unknown; remedy: unknown; park_it: boolean; url: string }

const recallCache = new Map<string, [number, Recall[]]>();

/** Safety recalls NHTSA lists for this make/model/year (kept for half a day). */
export async function recalls(make: string, model: string, year: number | null, now?: number, get: Get = nhtsaGet): Promise<Recall[]> {
  if (!(make && model && year)) throw new LookupFailed('needs the make, model and year (look up the VIN, or fill them in)');
  const at = now || Date.now() / 1000;
  const key = JSON.stringify([make.toLowerCase(), model.toLowerCase(), Math.trunc(year)]);
  const hit = recallCache.get(key);
  if (hit && at - hit[0] < RECALL_CACHE_SECONDS) return hit[1];
  const ask = async (m: string) => {
    const q = pyUrlencode({ make, model: m, modelYear: Math.trunc(year) });
    return ((await get(`https://api.nhtsa.gov/recalls/recallsByVehicle?${q}`)).results as Record<string, unknown>[] | undefined) || [];
  };
  let found: Record<string, unknown>[] | null;
  try {
    found = await ask(model);
  } catch (e) {
    if (!(e instanceof LookupFailed)) throw e;
    found = null;   // NHTSA rejects models it doesn't know, like "Tacoma TRD"
  }
  if ((!found || !found.length) && pyStrip(model).includes(' ')) found = await ask(pySplit(model)[0]!);   // "Tacoma TRD Off-Road" -> "Tacoma"
  if (found === null) throw new LookupFailed(`NHTSA doesn't know the model ${pyRepr(model)}`);
  // As r.get(k, "") in Python: a value NHTSA sends comes back as it is.
  const field = (r: Record<string, unknown>, k: string): unknown => (k in r ? r[k] : '');
  const out = found.map((r) => ({
    campaign: field(r, 'NHTSACampaignNumber'),
    date: field(r, 'ReportReceivedDate'),
    component: field(r, 'Component'),
    summary: field(r, 'Summary'),
    consequence: field(r, 'Consequence'),
    remedy: field(r, 'Remedy'),
    park_it: pyStr(field(r, 'parkIt')).toLowerCase() === 'true',   // NHTSA: don't drive it until fixed
    url: `https://www.nhtsa.gov/recalls?nhtsaId=${pyQuote(String(field(r, 'NHTSACampaignNumber')))}`,
  }));
  recallCache.set(key, [at, out]);
  return out;
}

/** For tests: forget the cached recalls. */
export const clearRecallCache = () => recallCache.clear();
