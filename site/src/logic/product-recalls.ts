// Safety recalls for things in My Stuff that aren't cars: the US Consumer Product Safety Commission's free recall
// database (saferproducts.gov). No key. CPSC can't search by model number, so this asks for the brand's recalls and
// keeps the ones that name the model, or failing that the same kind of product. A port of product_recalls.py.
import { pyRe, pyStr, pyUrlencode, truthy } from './py.js';
import { LookupFailed } from './vehicles.js';

export const CPSC_URL = 'https://www.saferproducts.gov/RestWebServices/Recall';
const USER_AGENT = 'deal-hunter/0.1 (personal vehicle lookup)';
const CACHE_SECONDS = 12 * 3600;
const LOOKBACK_YEARS = 20;

// Words for the kind of product, so "Samsung fridge" finds "Samsung Recalls Refrigerators".
const PRODUCT_WORDS: [string, string[]][] = [
  ['fridge', ['refrigerator', 'fridge', 'freezer']], ['refrigerator', ['refrigerator', 'fridge']],
  ['freezer', ['freezer']], ['stove', ['range', 'stove', 'oven', 'cooktop']], ['range', ['range', 'stove', 'oven']],
  ['oven', ['oven', 'range', 'stove']], ['cooktop', ['cooktop', 'range']], ['microwave', ['microwave']],
  ['dishwasher', ['dishwasher']], ['washer', ['washer', 'washing machine']], ['dryer', ['dryer']],
  ['blender', ['blender']], ['toaster', ['toaster']], ['air fryer', ['air fryer', 'fryer']],
  ['coffee', ['coffee', 'espresso']], ['kettle', ['kettle']], ['mixer', ['mixer']], ['pressure cooker', ['pressure cooker']],
  ['instant pot', ['pressure cooker', 'multi-cooker']], ['vacuum', ['vacuum']], ['heater', ['heater']],
  ['dehumidifier', ['dehumidifier']], ['humidifier', ['humidifier']], ['tv', ['television', 'tv']],
  ['phone', ['phone']], ['laptop', ['laptop', 'notebook computer']], ['tablet', ['tablet']], ['charger', ['charger']],
  ['battery', ['battery', 'batteries', 'power bank']], ['speaker', ['speaker']], ['router', ['router']],
];
const KIND_WORDS: Record<string, string[]> = { tv: ['television', 'tv'], phone: ['phone'], tablet: ['tablet'],
  audio: ['speaker', 'soundbar'], network: ['router'], printer: ['printer'], monitor: ['monitor'] };

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const compact = (s: unknown) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
/** Python's `d.get(key, "")` inside an f-string: missing is "", None is "None". */
const field = (d: Record<string, unknown>, key: string): string => (key in d ? pyStr(d[key]) : '');

export function productWords(device: Record<string, unknown>): string[] {
  const text = ['name', 'model', 'notes'].map((k) => (device[k] ? pyStr(device[k]) : '')).join(' ').toLowerCase();
  const words = PRODUCT_WORDS.filter(([key]) => pyRe(`\\b${escape(key)}s?\\b`).test(text)).flatMap(([, ws]) => ws);
  return [...new Set(words.length ? words : KIND_WORDS[(device.kind as string) || ''] ?? [])];
}

/** Fetches CPSC's JSON. Tests pass their own. */
export type CpscGet = (url: string) => Promise<unknown>;

export const cpscGet: CpscGet = async (url) => {
  let res: Response;
  try {
    res = await fetch(url, { headers: { 'User-Agent': USER_AGENT, Accept: 'application/json' }, signal: AbortSignal.timeout(25_000) });
  } catch {
    throw new LookupFailed("couldn't reach CPSC; try again later");
  }
  if (!res.ok) throw new LookupFailed(`CPSC answered HTTP ${res.status}`);
  try {
    return await res.json();
  } catch {
    throw new LookupFailed("couldn't reach CPSC; try again later");
  }
};

const cache = new Map<string, [number, Record<string, unknown>[]]>();
export const clearCpscCache = () => cache.clear();

async function fetchRecalls(make: string, now: number, get: CpscGet): Promise<Record<string, unknown>[]> {
  const key = make.toLowerCase();
  const hit = cache.get(key);
  if (hit && now - hit[0] < CACHE_SECONDS) return hit[1];
  const start = new Date((now - LOOKBACK_YEARS * 365 * 86400) * 1000).toISOString().slice(0, 10);
  const data = await get(`${CPSC_URL}?${pyUrlencode({ format: 'json', RecallTitle: make, RecallDateStart: start })}`);
  cache.set(key, [now, Array.isArray(data) ? data as Record<string, unknown>[] : []]);
  return cache.get(key)![1];
}

export interface ProductRecall { campaign: unknown; date: string; component: unknown; summary: string; consequence: string;
  remedy: string; park_it: boolean; url: unknown; match: 'model' | 'type' }

/** CPSC recalls for this device: 'model' ones name its model number, 'type' ones are the same brand and kind of
 * product (worth checking the model on the recall page). Newest first, model matches first. */
export async function productRecalls(device: Record<string, unknown>, now: number, get: CpscGet = cpscGet): Promise<ProductRecall[]> {
  const make = String(device.make || '').trim();
  if (!make) throw new LookupFailed('needs the make (brand), e.g. Samsung, GE, Ninja');
  const model = compact(device.model);
  const words = productWords(device);
  const out: ProductRecall[] = [];
  for (const r of await fetchRecalls(make, now, get)) {
    const products = (r.Products || []) as Record<string, unknown>[];
    const text = [String(r.Title || ''), String(r.Description || ''),
      ...products.map((p) => `${field(p, 'Name')} ${field(p, 'Model')} ${field(p, 'Description')}`)].join(' ');
    const listed = products.map((p) => compact('Model' in p ? p.Model : ''));
    let match: 'model' | 'type';
    if (model.length >= 4 && (compact(text).includes(model) || listed.some((m) => m.length >= 5 && (model.includes(m) || m.includes(model))))) {
      match = 'model';   // also "WA49B5205AW/US" for a recall listing WA49B5205AW
    } else if (words.length && words.some((w) => pyRe(`\\b${escape(w)}`, 'i').test(
      [String(r.Title || ''), ...products.map((p) => ('Name' in p ? p.Name : ''))].join(' ')))) {
      // The kind of product only counts in the title and product names: descriptions say things like "a range of models".
      match = 'type';
    } else {
      continue;
    }
    const names = (list: unknown) => ((list || []) as Record<string, unknown>[]).map((x) => ('Name' in x ? x.Name : '')).join('; ');
    out.push({
      campaign: 'RecallNumber' in r ? r.RecallNumber : '',
      date: String(r.RecallDate || '').slice(0, 10),
      component: 'Title' in r ? r.Title : '',
      summary: String((((truthy(r.Products) ? r.Products : [{}]) as Record<string, unknown>[])[0] ?? {}).Name || ''),
      consequence: names(r.Hazards),
      remedy: names(r.Remedies),
      park_it: pyRe('stop using|unplug', 'i').test(names(r.Remedies)),
      url: r.URL || 'https://www.cpsc.gov/Recalls',
      match,
    });
  }
  out.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  out.sort((a, b) => Number(a.match !== 'model') - Number(b.match !== 'model'));   // stable: model matches first, each newest first
  return out;
}
