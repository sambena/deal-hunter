// More of Python's own behaviour, for reading the shops' pages exactly as the Python app's sources.py did:
// html.unescape, json's raw_decode, email.utils.parsedate_to_datetime, and urllib's quote, quote_plus and urlencode.
import { HTML5, INVALID_CHARREFS, INVALID_CODEPOINTS } from './py-data.js';
import { pyInt, pySplit } from './py.js';

/** urllib.parse.quote(s, safe): letters, digits, _.-~ and the safe characters stay; the rest is UTF-8 %XX. */
export function quote(s: string, safe = '/'): string {
  let out = encodeURIComponent(s).replace(/[!'()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());
  for (const ch of new Set(safe)) {
    const code = '%' + ch.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0');
    if (ch.charCodeAt(0) < 128 && code !== '%' && !/[A-Za-z0-9_.~-]/.test(ch)) out = out.split(code).join(ch);
  }
  return out;
}

/** urllib.parse.quote_plus(s, safe=""): as quote, with spaces as +. */
export const quotePlus = (s: string, safe = ''): string => (s.includes(' ') ? quote(s, safe + ' ').replace(/ /g, '+') : quote(s, safe));

/** urllib.parse.urlencode(pairs, doseq): key=value pairs joined by &, each quote_plus'd; with doseq, a list value
 * gives one pair per item. */
export function urlencode(pairs: [string, unknown][] | Record<string, unknown>, doseq = false): string {
  const list = Array.isArray(pairs) ? pairs : Object.entries(pairs);
  const out: string[] = [];
  for (const [k, v] of list) {
    if (doseq && Array.isArray(v)) for (const item of v) out.push(`${quotePlus(k)}=${quotePlus(String(item))}`);
    else out.push(`${quotePlus(k)}=${quotePlus(String(v))}`);
  }
  return out.join('&');
}

// ---- html.unescape -------------------------------------------------------------------------------------------

const CHARREF = /&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)/gu;

function replaceCharref(s: string): string {
  if (s[0] === '#') {
    const digits = s[1] === 'x' || s[1] === 'X' ? s.slice(2).replace(/;+$/, '') : s.slice(1).replace(/;+$/, '');
    const big = BigInt(s[1] === 'x' || s[1] === 'X' ? '0x' + digits : digits);
    const num = big > 0x10ffffn ? 0x110000 : Number(big);
    if (num in INVALID_CHARREFS) return INVALID_CHARREFS[num]!;
    if ((num >= 0xd800 && num <= 0xdfff) || num > 0x10ffff) return '�';
    if (INVALID_CODEPOINTS.has(num)) return '';
    return String.fromCodePoint(num);
  }
  if (Object.hasOwn(HTML5, s)) return HTML5[s]!;
  // The longest name that matches (as the HTML standard says).
  const chars = [...s];
  for (let x = chars.length - 1; x > 1; x--) {
    const name = chars.slice(0, x).join('');
    if (Object.hasOwn(HTML5, name)) return HTML5[name]! + chars.slice(x).join('');
  }
  return '&' + s;
}

/** html.unescape(s): every named and numeric character reference, by the HTML5 rules, as Python decodes them. */
export function htmlUnescape(s: string): string {
  return s.includes('&') ? s.replace(CHARREF, (_m, ref: string) => replaceCharref(ref)) : s;
}

// ---- json raw_decode -------------------------------------------------------------------------------------------

export class JSONDecodeError extends Error {}

/** json.JSONDecoder().raw_decode(text, idx): the JSON value that starts exactly at idx (no whitespace skipped), and
 * where it ends. */
export function rawDecode(text: string, idx: number): [unknown, number] {
  let i = idx;
  const ch = text[i];
  if (ch === undefined) throw new JSONDecodeError('Expecting value');
  const skipString = () => {
    i++;
    while (i < text.length && text[i] !== '"') i += text[i] === '\\' ? 2 : 1;
    if (i >= text.length) throw new JSONDecodeError('Unterminated string');
    i++;
  };
  if (ch === '{' || ch === '[') {
    let depth = 0;
    while (i < text.length) {
      const c = text[i]!;
      if (c === '"') { skipString(); continue; }
      if (c === '{' || c === '[') depth++;
      else if (c === '}' || c === ']') {
        depth--;
        if (depth === 0) { i++; break; }
      }
      i++;
    }
    if (depth !== 0) throw new JSONDecodeError('Expecting value');
  } else if (ch === '"') {
    skipString();
  } else {
    const m = /^(-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?|true|false|null)/.exec(text.slice(i));
    if (!m) throw new JSONDecodeError('Expecting value');
    i += m[0].length;
  }
  try {
    return [JSON.parse(text.slice(idx, i)), i];
  } catch (e) {
    throw new JSONDecodeError((e as Error).message);
  }
}

// ---- email.utils.parsedate_to_datetime --------------------------------------------------------------------------

const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec', 'january', 'february',
  'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const ZONES: Record<string, number> = { UT: 0, UTC: 0, GMT: 0, Z: 0, AST: -400, ADT: -300, EST: -500, EDT: -400, CST: -600,
  CDT: -500, MST: -700, MDT: -600, PST: -800, PDT: -700 };

/** The date was missing or unreadable: Python's ValueError (or TypeError, for no text at all). */
export class DateValueError extends Error {}

/** email.utils.parsedate_to_datetime(data).timestamp(): the epoch seconds of an RFC 2822 date. A date without a time
 * zone (or with -0000) is read as this computer's local time, as Python reads a naive datetime. */
export function parsedateTimestamp(data: string): number {
  const fail = () => new DateValueError(`Invalid date value or format "${data}"`);
  if (!data) throw fail();
  let parts = pySplit(data);
  if (!parts.length) throw fail();
  if (parts[0]!.endsWith(',') || DAYS.includes(parts[0]!.toLowerCase())) parts.shift();
  else {
    const i = parts[0]!.lastIndexOf(',');
    if (i >= 0) parts[0] = parts[0]!.slice(i + 1);
  }
  if (parts.length === 3) {   // RFC 850 date, deprecated
    const stuff = parts[0]!.split('-');
    if (stuff.length === 3) parts = [...stuff, ...parts.slice(1)];
  }
  if (parts.length === 4) {
    const s = parts[3]!;
    let i = s.indexOf('+');
    if (i === -1) i = s.indexOf('-');
    if (i > 0) parts.splice(3, 1, s.slice(0, i), s.slice(i));
    else parts.push('');   // dummy time zone
  }
  if (parts.length < 5) throw fail();
  let [dd, mm, yy, tm, tz] = parts.slice(0, 5) as [string, string, string, string, string];
  if (!(dd && mm && yy)) throw fail();
  mm = mm.toLowerCase();
  if (!MONTHS.includes(mm)) {
    [dd, mm] = [mm, dd.toLowerCase()];
    if (!MONTHS.includes(mm)) throw fail();
  }
  let month = MONTHS.indexOf(mm) + 1;
  if (month > 12) month -= 12;
  if (dd.endsWith(',')) dd = dd.slice(0, -1);
  if (yy.indexOf(':') > 0) [yy, tm] = [tm, yy];
  if (yy.endsWith(',')) {
    yy = yy.slice(0, -1);
    if (!yy) throw fail();
  }
  if (!yy) throw new Error('IndexError: string index out of range');   // Python's yy[0] on an empty string
  if (!/^\p{Nd}/u.test(yy)) [yy, tz] = [tz, yy];
  if (!tm) throw new Error('IndexError: string index out of range');
  if (tm.endsWith(',')) tm = tm.slice(0, -1);
  let pieces = tm.split(':');
  let thh: string, tmm: string, tss: string;
  if (pieces.length === 2) [thh, tmm, tss] = [pieces[0]!, pieces[1]!, '0'];
  else if (pieces.length === 3) [thh, tmm, tss] = pieces as [string, string, string];
  else if (pieces.length === 1 && pieces[0]!.includes('.')) {   // some mailers use '.' between time parts
    pieces = pieces[0]!.split('.');
    if (pieces.length === 2) [thh, tmm, tss] = [pieces[0]!, pieces[1]!, '0'];
    else if (pieces.length === 3) [thh, tmm, tss] = pieces as [string, string, string];
    else throw fail();
  } else throw fail();
  let year: number, day: number, hour: number, minute: number, second: number;
  try {
    [year, day, hour, minute, second] = [pyInt(yy), pyInt(dd), pyInt(thh), pyInt(tmm), pyInt(tss)];
  } catch {
    throw fail();
  }
  if (year < 100) year += year > 68 ? 1900 : 2000;
  let offset: number | null = null;
  const zone = tz.toUpperCase();
  if (Object.hasOwn(ZONES, zone)) offset = ZONES[zone]!;
  else {
    try { offset = pyInt(zone); } catch { /* an unknown zone: none */ }
    if (offset === 0 && zone.startsWith('-')) offset = null;
  }
  if (offset) offset = Math.sign(offset) * (Math.floor(Math.abs(offset) / 100) * 3600 + (Math.abs(offset) % 100) * 60);
  // datetime() refuses impossible dates and times.
  const daysIn = new Date(Date.UTC(year, month, 0)).getUTCDate();
  if (year < 1 || year > 9999 || day < 1 || day > daysIn || hour < 0 || hour > 23 || minute < 0 || minute > 59 || second < 0 || second > 59) throw fail();
  if (offset === null) {
    const local = new Date(2000, 0, 1);
    local.setFullYear(year, month - 1, day);
    local.setHours(hour, minute, second, 0);
    return local.getTime() / 1000;
  }
  const utc = new Date(0);
  utc.setUTCFullYear(year, month - 1, day);
  utc.setUTCHours(hour, minute, second, 0);
  return utc.getTime() / 1000 - offset;
}

// ---- json.dumps --------------------------------------------------------------------------------------------------

/** A string as json.dumps writes it (ensure_ascii): anything outside printable ASCII as \uXXXX. */
function jsonString(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]!;
    const c = s.charCodeAt(i);
    if (ch === '"') out += '\\"';
    else if (ch === '\\') out += '\\\\';
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else if (ch === '\b') out += '\\b';
    else if (ch === '\f') out += '\\f';
    else if (c < 0x20 || c > 0x7e) out += '\\u' + c.toString(16).padStart(4, '0');
    else out += ch;
  }
  return out + '"';
}

/** json.dumps(value, indent=n): one item per line, "," and ": " between them, nothing outside ASCII. */
export function pyJsonIndent(value: unknown, indent: number, level = 0): string {
  if (value === null || value === undefined || typeof value !== 'object') return pyJsonDumps(value);
  const pad = ' '.repeat(indent * (level + 1));
  const end = ' '.repeat(indent * level);
  if (Array.isArray(value)) {
    if (!value.length) return '[]';
    return '[\n' + value.map((v) => pad + pyJsonIndent(v, indent, level + 1)).join(',\n') + '\n' + end + ']';
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) return '{}';
  return '{\n' + entries.map(([k, v]) => pad + jsonString(k) + ': ' + pyJsonIndent(v, indent, level + 1)).join(',\n') + '\n' + end + '}';
}

/** json.dumps(value, separators=...): Python's default ", " and ": " unless given. A number prints as JavaScript has
 * it, which matches Python for every value read from JSON except a whole float written with ".0". */
export function pyJsonDumps(value: unknown, separators: [string, string] = [', ', ': ']): string {
  const [item, key] = separators;
  if (value === null || value === undefined) return 'null';
  if (value === true) return 'true';
  if (value === false) return 'false';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : value > 0 ? 'Infinity' : value < 0 ? '-Infinity' : 'NaN';
  if (typeof value === 'string') return jsonString(value);
  if (Array.isArray(value)) return '[' + value.map((v) => pyJsonDumps(v, separators)).join(item) + ']';
  return '{' + Object.entries(value as Record<string, unknown>).map(([k, v]) => jsonString(k) + key + pyJsonDumps(v, separators)).join(item) + '}';
}
