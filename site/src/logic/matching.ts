// Which listings belong to a watch, and how good a deal each is: a port of the Python app's matching.py, rule for rule
// (test/logic.test.ts checks it against the Python code's own answers).
//
// Query syntax, typed into a watch:
//     10980xe                 every plain term must appear
//     9980xe|10980xe          any one of the alternatives
//     "ddr4 32gb"             quoted phrase
//     -ecc                    must NOT appear (same as adding it to Exclude)
//
// Matching ignores case, punctuation and spacing, so "10980xe" matches "i9-10980 XE" and "4x32gb" matches "4 x 32GB".
import { asciiDigits, median, pyCapitalize, pyFullmatch, pyRe, pyRound, pySplit, pyTitle, shlexSplit } from './py.js';

/** The parts of a watch the rules read. */
export interface WatchRules {
  query: string;
  kind?: string | null;
  exclude?: string[];
  min_price?: number | null;
  max_price?: number | null;
  year_min?: number | null;
  year_max?: number | null;
  max_miles?: number | null;
  size?: string | null;
}

export const normalize = (text: string): string => text.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

export const compact = (text: string): string => text.toLowerCase().replace(/[^a-z0-9]+/g, '');

/** [include groups, excludes]. Each group is a list of alternatives. */
export function parseQuery(query: string): [string[][], string[]] {
  let tokens: string[];
  try {
    tokens = shlexSplit(query);
  } catch {   // unbalanced quote
    tokens = pySplit(query.replace(/"/g, ' '));
  }
  const groups: string[][] = [];
  const excludes: string[] = [];
  for (const tok of tokens) {
    if (tok.startsWith('-') && [...tok].length > 1) {
      excludes.push(tok.slice(1));
    } else {
      const alts = tok.split('|').filter(Boolean);
      if (alts.length) groups.push(alts);
    }
  }
  return [groups, excludes];
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function contains(haystackNorm: string, haystackCompact: string, term: string): boolean {
  const tn = normalize(term);
  if (!tn) return true;
  if (new RegExp(`(?<![a-z0-9])${escape(tn)}(?![a-z0-9])`).test(haystackNorm)) return true;
  // Fall back to spacing-insensitive match only for terms with digits (model numbers), so "rtx" doesn't match inside
  // "vertx" but "10980xe" matches "10980 xe".
  const tc = compact(term);
  if (!(/[0-9]/.test(tc) && tc.length >= 4 && haystackCompact.includes(tc))) return false;
  // The joined-up term must start and end on word edges: "10980 xe" is "10980xe", but "RX 5700 XT" is not "5700x".
  const words = pySplit(haystackNorm);
  for (let i = 0; i < words.length; i++) {
    let joined = '';
    for (const w of words.slice(i)) {
      joined += w;
      if (joined === tc) return true;
      if (!tc.startsWith(joined)) break;
    }
  }
  return false;
}

/** Plain keywords for sites' own search boxes (the first alternative of each group). */
export const searchTerms = (query: string): string => parseQuery(query)[0].map((g) => g[0]).join(' ');

// ---- vehicles -------------------------------------------------------------------

// For car and truck watches the hardware junk list doesn't fit ("as is" is how most dealers sell); these are what make
// a vehicle listing not a vehicle for sale. Salvage/rebuilt titles are shown, not hidden.
export const VEHICLE_JUNK = ['parts only', 'parting out', 'part out', 'for parts', 'not running', "doesn't run",
  'does not run', 'non running', 'wtb', 'want to buy', 'looking for', 'iso'];
const YEAR_RE = pyRe('(?<!\\d)(19[5-9]\\d|20[0-4]\\d)(?!\\d)');
const MILES_RE = pyRe('(\\d{1,3}(?:,\\d{3})+|\\d+(?:\\.\\d+)?)\\s*(k)?\\s*(?:miles|mi\\b|odometer)', 'i');
const TITLE_STATUS: [string, string][] = [['salvage', 'salvage'], ['rebuilt', 'rebuilt'], ['rebuild title', 'rebuilt'],
  ['branded title', 'branded'], ['lemon', 'branded'], ['flood', 'salvage']];

// Body styles aren't in listing titles ("2005 Ford F-150 XLT"), so for a vehicle watch a body-style word matches the
// common models of that style, and sources that can filter by body style do so themselves.
export const BODY_STYLES: Record<string, string> = {
  pickup: 'pickup', pickups: 'pickup', truck: 'pickup', trucks: 'pickup', suv: 'suv', suvs: 'suv', van: 'van',
  vans: 'van', minivan: 'minivan', sedan: 'sedan', coupe: 'coupe', convertible: 'convertible', hatchback: 'hatchback',
  wagon: 'wagon',
};
export const BODY_MODELS: Record<string, string[]> = {
  pickup: ['pickup', 'truck', 'f-150', 'f150', 'f-250', 'f250', 'f-350', 'f350', 'silverado', 'sierra', 'ram',
    '1500', '2500', '3500', 'tacoma', 'tundra', 'ranger', 'colorado', 'canyon', 'frontier', 'titan',
    'ridgeline', 'gladiator', 's-10', 's10', 'dakota', 'maverick', 'santa cruz', 'avalanche', 'f-100',
    'c10', 'k10', 'cybertruck', 'lightning', 'rivian'],
  suv: ['suv', '4runner', 'tahoe', 'suburban', 'explorer', 'expedition', 'highlander', 'pilot', 'cr-v', 'crv',
    'rav4', 'equinox', 'traverse', 'durango', 'grand cherokee', 'cherokee', 'wrangler', 'bronco', 'forester',
    'outback', 'pathfinder', 'rogue', 'cx-5', 'cx-9', 'tucson', 'santa fe', 'sorento', 'telluride', 'yukon',
    'escalade', 'land cruiser', 'sequoia', 'edge', 'escape', 'acadia', 'x5', 'q5', 'model y', 'model x'],
  van: ['van', 'sprinter', 'transit', 'express', 'savana', 'promaster', 'econoline', 'e-150', 'e-250'],
  minivan: ['minivan', 'odyssey', 'sienna', 'pacifica', 'caravan', 'grand caravan', 'carnival', 'sedona', 'quest'],
  sedan: ['sedan'], coupe: ['coupe'], convertible: ['convertible'], hatchback: ['hatchback'],
  wagon: ['wagon'],
};

const style = (word: string): string | undefined => (Object.hasOwn(BODY_STYLES, word.toLowerCase()) ? BODY_STYLES[word.toLowerCase()] : undefined);

/** ['pickup', 'ford'] for "ford pickup": the body style asked for, and the rest of the search words. When one group
 * names two different styles ("truck|suv"), Python took either (set order); this takes the first. */
export function bodyStyle(query: string): [string | null, string] {
  const [groups] = parseQuery(query);
  let body: string | null = null;
  const rest: string[] = [];
  for (const g of groups) {
    const styles = g.map(style).filter((x): x is string => Boolean(x));
    if (styles.length && body === null) body = styles[0]!;
    else rest.push(g.length === 1 ? g[0]! : g.join('|'));
  }
  return [body, rest.join(' ')];
}

// Makes as people write them in titles -> how they're shown and sorted.
const MAKE_ALIASES: Record<string, string> = {
  chevy: 'Chevrolet', chev: 'Chevrolet', vw: 'Volkswagen', mercedes: 'Mercedes-Benz', benz: 'Mercedes-Benz',
  mb: 'Mercedes-Benz', gmc: 'GMC', bmw: 'BMW', ram: 'Ram', dodge: 'Dodge', landrover: 'Land Rover', alfa: 'Alfa Romeo',
  mini: 'MINI', kia: 'Kia',
};
const TWO_WORD_MAKES = new Set(['land rover', 'alfa romeo', 'aston martin', 'rolls royce', 'mercedes benz', 'am general']);
const alias = (word: string) => (Object.hasOwn(MAKE_ALIASES, word) ? MAKE_ALIASES[word] : undefined);

/** The make, from the words after the model year: "2005 chevy silverado" -> "Chevrolet". */
export function vehicleMake(title: string | null): string {
  const text = title || '';
  const m = YEAR_RE.exec(text);
  const words = m ? text.slice(m.index + m[0].length).match(/[A-Za-z][A-Za-z-]*/g) ?? [] : [];
  if (!words.length) return '';
  const two = words.slice(0, 2).join(' ').toLowerCase().replace(/-/g, ' ');
  if (TWO_WORD_MAKES.has(two)) return alias(two.replace(/ /g, '')) ?? pyTitle(two).replace('Mercedes Benz', 'Mercedes-Benz');
  const first = words[0]!.toLowerCase();
  const known = alias(first);
  if (known) return known;
  const w = words[0]!;
  if (w !== w.toLowerCase() && w !== w.toUpperCase()) return w;   // written with care already: "Mercedes-Benz", "McLaren"
  return w.length > 3 ? w.split('-').map(pyCapitalize).join('-') : w.toUpperCase();
}

/** The model year, which listings put first: "2018 Toyota Tacoma TRD". */
export function vehicleYear(title: string | null): number | null {
  const m = YEAR_RE.exec(title || '');
  return m ? Number(asciiDigits(m[1]!)) : null;
}

export function vehicleMiles(text: string | null): number | null {
  const m = MILES_RE.exec(text || '');
  if (!m) return null;
  const digits = asciiDigits(m[1]!);
  const n = Number(digits.replace(/,/g, ''));
  const miles = m[2] || (n < 1000 && digits.includes('.')) ? Math.trunc(n * 1000) : Math.trunc(n);
  return miles >= 100 ? miles : null;   // "5 miles from campus" is a distance, not an odometer
}

export function titleStatus(text: string | null): string {
  const low = (text || '').toLowerCase();
  return TITLE_STATUS.find(([word]) => low.includes(word))?.[1] ?? '';
}

/** A vehicle watch's year range and mileage. A vehicle for sale names its model year, so a listing without one (floor
 * mats, a wanted ad) isn't one; unknown miles pass (not every seller states them). */
export function checkVehicle(year: number | null, miles: number | null, watch: WatchRules): [boolean, string] {
  if (year === null) return [false, 'no model year'];
  if (watch.year_min && year < watch.year_min) return [false, 'older than wanted'];
  if (watch.year_max && year > watch.year_max) return [false, 'newer than wanted'];
  if (miles !== null && watch.max_miles && miles > watch.max_miles) return [false, 'too many miles'];
  return [true, ''];
}

// ---- clothes and shoes ------------------------------------------------------------

const LETTER_SIZES: Record<string, string> = {
  xxs: 'XXS', xs: 'XS', 'extra small': 'XS', s: 'S', small: 'S', m: 'M', medium: 'M', l: 'L', large: 'L', xl: 'XL',
  'x-large': 'XL', 'extra large': 'XL', xxl: 'XXL', '2xl': 'XXL', 'xx-large': 'XXL', xxxl: 'XXXL', '3xl': 'XXXL',
};
// "Size 10.5", "sz 10 1/2", "US 10.5", "10.5M", "W32 L30", "32x30", "size M"
const SIZE_RE = pyRe(
  "\\b(?:size|sz|us|uk|eu)\\s*[:#]?\\s*(\\d{1,2}(?:\\.5| 1/2|½)?|xxs|xs|s|m|l|xl|xxl|xxxl|[23]xl)\\b"
  + "|\\b(\\d{1,2}(?:\\.5)?)\\s*(?:m|w|men'?s|women'?s)\\b"
  + '|\\bw\\s?(\\d{2})\\s*l\\s?(\\d{2})\\b|\\b(\\d{2})\\s*x\\s*(\\d{2})\\b', 'i');

/** One spelling per size: "10 1/2" -> "10.5", "medium" -> "M", "W32 L30" -> "32x30". */
export function normSize(size: string | null | undefined): string {
  let s = pySplit(String(size || '').toLowerCase().replace(/½/g, '.5')).join(' ');
  if (!s) return '';
  s = s.replace(pyRe('(\\d+) 1/2', 'g'), '$1.5');
  let m = pyFullmatch('w?\\s?(\\d{2})\\s*(?:x|l|/)\\s*l?\\s?(\\d{2})', s);
  if (m) return `${m[1]}x${m[2]}`;
  if (Object.hasOwn(LETTER_SIZES, s)) return LETTER_SIZES[s]!;
  m = pyFullmatch('(\\d{1,2}(?:\\.5)?)(?:\\.0)?\\s*(?:m|w|us)?', s);
  return m ? m[1]! : s.toUpperCase();
}

/** The size a listing title states, if any ("" when it doesn't say). */
export function titleSize(text: string | null): string {
  const m = SIZE_RE.exec(text || '');
  if (!m) return '';
  if (m[1]) return normSize(m[1]);
  if (m[2]) return normSize(m[2]);
  const [a, b] = m[3] ? [m[3], m[4]] : [m[5], m[6]];
  return `${a}x${b}`;
}

/** A clothing watch's size: a listing in another size is out; one that doesn't say stays. */
export function checkSize(listingSize: string | null, watch: WatchRules): [boolean, string] {
  const want = normSize(watch.size);
  const have = normSize(listingSize);
  if (want && have && want !== have) return [false, `size ${have}, not ${want}`];
  return [true, ''];
}

/** Whether a listing belongs to a watch: [ok, the reason when it doesn't]. */
export function check(title: string, watch: WatchRules, junkTerms: string[], total: number | null, extraText = ''): [boolean, string] {
  if (watch.kind === 'vehicle') junkTerms = VEHICLE_JUNK;
  let [groups] = parseQuery(watch.query);
  const [, qExcludes] = parseQuery(watch.query);
  if (watch.kind === 'vehicle') {
    // "pickup" also matches an F-150 or a Silverado. As in Python, the models added are those of the group's first
    // word, so a group whose first word isn't a body style but another is ("ford|truck") fails there (a KeyError).
    groups = groups.map((g) => {
      if (!g.some((a) => style(a))) return g;
      const first = style(g[0]!);
      if (!first) throw new Error(`KeyError: '${g[0]!.toLowerCase()}'`);
      return [...g, ...BODY_MODELS[first]!];
    });
  }
  const hn = normalize(title), hc = compact(title);
  for (const group of groups) {
    if (!group.some((alt) => contains(hn, hc, alt))) return [false, `missing '${group.slice(0, 3).join('|')}'`];
  }
  const full = `${title} ${extraText}`;
  const fn = normalize(full), fc = compact(full);
  for (const term of [...qExcludes, ...(watch.exclude ?? [])]) {
    if (contains(hn, hc, term)) return [false, `excluded '${term}'`];
  }
  for (const term of junkTerms) {
    // Junk phrases are checked against title + description.
    if (contains(fn, fc, term)) return [false, `junk '${term}'`];
  }
  if (total !== null) {
    if (watch.max_price !== null && watch.max_price !== undefined && total > watch.max_price) return [false, 'over max price'];
    if (watch.min_price !== null && watch.min_price !== undefined && total < watch.min_price) return [false, 'under min price'];
  }
  return [true, ''];
}

/** For finds already stored when a watch is edited: do its excludes and price limits still allow it? (The query itself
 * isn't re-checked: some sources match on text that isn't stored.) */
export function stillWanted(title: string, total: number | null, watch: WatchRules): boolean {
  const [, qExcludes] = parseQuery(watch.query);
  const hn = normalize(title), hc = compact(title);
  if ([...qExcludes, ...(watch.exclude ?? [])].some((t) => contains(hn, hc, t))) return false;
  if (total !== null) {
    if (watch.max_price !== null && watch.max_price !== undefined && total > watch.max_price) return false;
    if (watch.min_price !== null && watch.min_price !== undefined && total < watch.min_price) return false;
  }
  return true;
}

/** Prices to compare a vehicle with: the watch's finds within two model years, when there are enough. */
export function vehicleHistory(year: number | null, history: [number, number | null][]): number[] {
  const near = history.filter(([, y]) => year !== null && y !== null && Math.abs(y - year) <= 2).map(([t]) => t);
  return near.length >= 5 ? near : history.map(([t]) => t);
}

/** The usual asking price: the median of a watch's prices with the extremes trimmed (needs 5+). */
export function typicalPrice(history: (number | null)[]): number | null {
  const prices = history.filter((p): p is number => Boolean(p) && (p as number) > 0).sort((a, b) => a - b);
  if (prices.length < 5) return null;
  // Trim the extremes so a $1 auction or a $9999 troll listing doesn't skew it.
  const cut = Math.floor(prices.length / 10);
  return median(cut ? prices.slice(cut, prices.length - cut) : prices);
}

/** How far below the typical asking price this is, as a fraction (0.2 = 20% under). Needs a few prior prices for the
 * same watch; null until then. */
export function dealPct(total: number | null, history: (number | null)[]): number | null {
  if (total === null) return null;
  const typical = typicalPrice(history);
  return typical ? pyRound((typical - total) / typical, 3) : null;
}

export function dealLabel(pct: number | null): string {
  if (pct === null) return '';
  if (pct >= 0.25) return 'great';
  if (pct >= 0.10) return 'good';
  return '';
}
