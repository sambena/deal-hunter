// Python's own behaviour, where the ported rules depend on it, so Deal Hunter decides exactly as the Python app did:
// shlex.split, round() and "%.1f" (ties to even), str.title(), str.splitlines(), urllib's quoting, int() of any
// decimal digits, and the re module's Unicode-aware \b, \d and \w.

/** Python's \w: letters, numbers and the underscore, in any script. */
const W = '[\\p{L}\\p{N}_]';

/** A regular expression written in Python's syntax, with \b, \d, \w and $ meaning what they mean in Python's re for
 * text: Unicode-aware, and $ also just before a final newline. re.I is the i flag. Always Unicode mode. */
export function pyRe(pattern: string, flags = ''): RegExp {
  let out = '';
  let inClass = false;
  const chars = [...pattern];
  for (let i = 0; i < chars.length; i++) {
    const ch = chars[i]!;
    if (ch === '\\') {
      const next = chars[++i] ?? '';
      if (next === 'd') out += '\\p{Nd}';
      else if (next === 'w') out += inClass ? '\\p{L}\\p{N}_' : W;
      else if (next === 'b' && !inClass) out += `(?:(?<=${W})(?!${W})|(?<!${W})(?=${W}))`;
      else out += '\\' + next;
    } else if (inClass) {
      if (ch === ']') inClass = false;
      out += ch;
    } else if (ch === '[') {
      inClass = true;
      out += ch;
      if (chars[i + 1] === '^') out += chars[++i];
      if (chars[i + 1] === ']') out += '\\' + chars[++i];   // a ] first in a class is a literal one in Python
    } else if (ch === '$' && !flags.includes('m')) {
      out += '(?=\\n?$)';
    } else {
      out += ch;
    }
  }
  return new RegExp(out, flags.includes('u') ? flags : flags + 'u');
}

/** re.fullmatch(pattern, s, flags): the whole text, to its very end. */
export function pyFullmatch(pattern: string, s: string, flags = ''): RegExpExecArray | null {
  const re = pyRe(`^(?:${pattern})`, flags);
  return new RegExp(`${re.source}(?![\\s\\S])`, re.flags).exec(s);
}

/** Python's int() and float() read decimal digits of any script; JavaScript's Number reads only 0-9. */
export function asciiDigits(s: string): string {
  return s.replace(/\p{Nd}/gu, (d) => {
    // Unicode keeps each script's digits as a run starting at its zero, so the value is the distance from the run's
    // start, modulo ten (a few runs hold several sets of 0-9).
    let start = d.codePointAt(0)!;
    while (/\p{Nd}/u.test(String.fromCodePoint(start - 1))) start--;
    return String((d.codePointAt(0)! - start) % 10);
  });
}

/** shlex.split(s) (POSIX mode, no comments). Throws on an unclosed quote or a trailing backslash, as shlex does. */
export function shlexSplit(s: string): string[] {
  const WS = ' \t\r\n';
  const tokens: string[] = [];
  let token = '';
  let inToken = false;
  let i = 0;
  const chars = [...s];
  while (i < chars.length) {
    const ch = chars[i]!;
    if (WS.includes(ch)) {
      if (inToken) { tokens.push(token); token = ''; inToken = false; }
      i++;
    } else if (ch === "'") {
      inToken = true;
      const end = chars.indexOf("'", i + 1);
      if (end < 0) throw new Error('No closing quotation');
      token += chars.slice(i + 1, end).join('');
      i = end + 1;
    } else if (ch === '"') {
      inToken = true;
      i++;
      for (;;) {
        if (i >= chars.length) throw new Error('No closing quotation');
        const c = chars[i]!;
        if (c === '"') { i++; break; }
        // Inside double quotes only \\ and \" are escapes (shlex's escapedquotes); any other backslash stays.
        if (c === '\\' && i + 1 < chars.length && (chars[i + 1] === '\\' || chars[i + 1] === '"')) {
          token += chars[i + 1];
          i += 2;
        } else if (c === '\\' && i + 1 >= chars.length) {
          throw new Error('No closing quotation');
        } else {
          token += c;
          i++;
        }
      }
    } else if (ch === '\\') {
      if (i + 1 >= chars.length) throw new Error('No escaped character');
      inToken = true;
      token += chars[i + 1];
      i += 2;
    } else {
      inToken = true;
      token += ch;
      i++;
    }
  }
  if (inToken) tokens.push(token);
  return tokens;
}

/** Python's str.split() with no argument: runs of whitespace, none empty. */
export const pySplit = (s: string): string[] => s.split(/[\s\x1c-\x1f]+/u).filter(Boolean);

/** Python's str.strip() with no argument. */
export const pyStrip = (s: string): string => s.replace(/^[\s\x1c-\x1f]+|[\s\x1c-\x1f]+$/gu, '');

/** Python's str.splitlines(): the line breaks Python knows, with no empty last line for a final break. */
export function splitlines(s: string): string[] {
  const lines = s.split(/\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/);
  if (lines.length && lines.at(-1) === '' && s.length) lines.pop();
  return s.length ? lines : [];
}

/** The characters whose title case isn't their upper case (ß -> Ss, ǆ -> ǅ), from Python's own Unicode data:
 * str.title() and str.capitalize() start a word with the title case. */
const TITLE_CASE: Record<string, string> = {
  "\u00df":"Ss","\u01c4":"\u01c5","\u01c5":"\u01c5","\u01c6":"\u01c5","\u01c7":"\u01c8","\u01c8":"\u01c8",
  "\u01c9":"\u01c8","\u01ca":"\u01cb","\u01cb":"\u01cb","\u01cc":"\u01cb","\u01f1":"\u01f2","\u01f2":"\u01f2",
  "\u01f3":"\u01f2","\u0587":"\u0535\u0582","\u10d0":"\u10d0","\u10d1":"\u10d1","\u10d2":"\u10d2",
  "\u10d3":"\u10d3","\u10d4":"\u10d4","\u10d5":"\u10d5","\u10d6":"\u10d6","\u10d7":"\u10d7","\u10d8":"\u10d8",
  "\u10d9":"\u10d9","\u10da":"\u10da","\u10db":"\u10db","\u10dc":"\u10dc","\u10dd":"\u10dd","\u10de":"\u10de",
  "\u10df":"\u10df","\u10e0":"\u10e0","\u10e1":"\u10e1","\u10e2":"\u10e2","\u10e3":"\u10e3","\u10e4":"\u10e4",
  "\u10e5":"\u10e5","\u10e6":"\u10e6","\u10e7":"\u10e7","\u10e8":"\u10e8","\u10e9":"\u10e9","\u10ea":"\u10ea",
  "\u10eb":"\u10eb","\u10ec":"\u10ec","\u10ed":"\u10ed","\u10ee":"\u10ee","\u10ef":"\u10ef","\u10f0":"\u10f0",
  "\u10f1":"\u10f1","\u10f2":"\u10f2","\u10f3":"\u10f3","\u10f4":"\u10f4","\u10f5":"\u10f5","\u10f6":"\u10f6",
  "\u10f7":"\u10f7","\u10f8":"\u10f8","\u10f9":"\u10f9","\u10fa":"\u10fa","\u10fd":"\u10fd","\u10fe":"\u10fe",
  "\u10ff":"\u10ff","\u1f80":"\u1f88","\u1f81":"\u1f89","\u1f82":"\u1f8a","\u1f83":"\u1f8b","\u1f84":"\u1f8c",
  "\u1f85":"\u1f8d","\u1f86":"\u1f8e","\u1f87":"\u1f8f","\u1f88":"\u1f88","\u1f89":"\u1f89","\u1f8a":"\u1f8a",
  "\u1f8b":"\u1f8b","\u1f8c":"\u1f8c","\u1f8d":"\u1f8d","\u1f8e":"\u1f8e","\u1f8f":"\u1f8f","\u1f90":"\u1f98",
  "\u1f91":"\u1f99","\u1f92":"\u1f9a","\u1f93":"\u1f9b","\u1f94":"\u1f9c","\u1f95":"\u1f9d","\u1f96":"\u1f9e",
  "\u1f97":"\u1f9f","\u1f98":"\u1f98","\u1f99":"\u1f99","\u1f9a":"\u1f9a","\u1f9b":"\u1f9b","\u1f9c":"\u1f9c",
  "\u1f9d":"\u1f9d","\u1f9e":"\u1f9e","\u1f9f":"\u1f9f","\u1fa0":"\u1fa8","\u1fa1":"\u1fa9","\u1fa2":"\u1faa",
  "\u1fa3":"\u1fab","\u1fa4":"\u1fac","\u1fa5":"\u1fad","\u1fa6":"\u1fae","\u1fa7":"\u1faf","\u1fa8":"\u1fa8",
  "\u1fa9":"\u1fa9","\u1faa":"\u1faa","\u1fab":"\u1fab","\u1fac":"\u1fac","\u1fad":"\u1fad","\u1fae":"\u1fae",
  "\u1faf":"\u1faf","\u1fb2":"\u1fba\u0345","\u1fb3":"\u1fbc","\u1fb4":"\u0386\u0345",
  "\u1fb7":"\u0391\u0342\u0345","\u1fbc":"\u1fbc","\u1fc2":"\u1fca\u0345","\u1fc3":"\u1fcc",
  "\u1fc4":"\u0389\u0345","\u1fc7":"\u0397\u0342\u0345","\u1fcc":"\u1fcc","\u1ff2":"\u1ffa\u0345",
  "\u1ff3":"\u1ffc","\u1ff4":"\u038f\u0345","\u1ff7":"\u03a9\u0342\u0345","\u1ffc":"\u1ffc","\ufb00":"Ff",
  "\ufb01":"Fi","\ufb02":"Fl","\ufb03":"Ffi","\ufb04":"Ffl","\ufb05":"St","\ufb06":"St","\ufb13":"\u0544\u0576",
  "\ufb14":"\u0544\u0565","\ufb15":"\u0544\u056b","\ufb16":"\u054e\u0576","\ufb17":"\u0544\u056d"
};
const titleCase = (ch: string): string => TITLE_CASE[ch] ?? ch.toUpperCase();

/** Python's str.title(): each run of letters starts upper case and goes on lower case. */
export const pyTitle = (s: string): string =>
  s.replace(/\p{L}+/gu, (w) => { const [first = '', ...rest] = [...w]; return titleCase(first) + rest.join('').toLowerCase(); });

/** Python's str.capitalize(): the first character upper case, the rest lower. */
export const pyCapitalize = (s: string): string => { const [first = '', ...rest] = [...s]; return titleCase(first) + rest.join('').toLowerCase(); };

/** x to `digits` decimals, correctly rounded from the float's exact value, ties to even: Python's round(x, n) and
 * f"{x:.nf}". JavaScript's toFixed breaks exact ties away from zero instead. */
function roundedText(x: number, digits: number): string {
  const fixed = Math.abs(x).toFixed(digits);
  // An exact tie shows as "...5" then nothing but zeros in the exact expansion; toFixed(100) is exact for any float
  // that could be one (a tie has only digits + 1 decimals).
  const exact = Math.abs(x).toFixed(100);
  const point = exact.indexOf('.');
  const tail = exact.slice(point + 1 + digits);
  if (tail[0] === '5' && /^0*$/.test(tail.slice(1))) {
    const down = exact.slice(0, point + 1 + digits).replace(/\.$/, '');
    const lastDigit = Number(down.at(-1));
    if (lastDigit % 2 === 0) return down;   // to even: keep the lower one
  }
  return fixed;
}

/** Python's round(x, digits) for a float, -0.0 included. */
export function pyRound(x: number, digits: number): number {
  const v = Number(roundedText(x, digits));
  return x < 0 || Object.is(x, -0) ? -v : v;
}

/** Python's f"{x:.{digits}f}". */
export function pyFixed(x: number, digits: number): string {
  const text = roundedText(x, digits);
  return (x < 0 || Object.is(x, -0)) ? `-${text}` : text;
}

/** urllib.parse.quote(s) with Python's default safe "/": letters, digits, _.-~ and / stay; the rest is UTF-8 %XX. */
export const pyQuote = (s: string): string =>
  encodeURIComponent(s).replace(/%2F/g, '/').replace(/[!'()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());

/** urllib.parse.urlencode(pairs): quote_plus on each key and value (space as +, nothing else safe). */
export const pyUrlencode = (pairs: Record<string, string | number>): string =>
  Object.entries(pairs).map(([k, v]) => [k, String(v)].map((x) => pyQuote(x).replace(/\//g, '%2F').replace(/%20/g, '+')).join('=')).join('&');

/** statistics.median of numbers. */
export function median(values: number[]): number {
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

/** Python's str() of a JSON value: None, True, False, numbers and strings as Python writes them. */
export const pyStr = (v: unknown): string =>
  v === null || v === undefined ? 'None' : v === true ? 'True' : v === false ? 'False' : String(v);

const NON_PRINTABLE = /[\p{C}\p{Z}]/u;

/** Python's repr() of a str: single quotes unless the text has ' and no ". */
export function pyRepr(s: string): string {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = q;
  for (const ch of s) {
    const c = ch.codePointAt(0)!;
    if (ch === '\\') out += '\\\\';
    else if (ch === q) out += '\\' + q;
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else if (ch !== ' ' && (c < 0x20 || c === 0x7f || NON_PRINTABLE.test(ch))) {
      out += c <= 0xff ? '\\x' + c.toString(16).padStart(2, '0')
        : c <= 0xffff ? '\\u' + c.toString(16).padStart(4, '0') : '\\U' + c.toString(16).padStart(8, '0');
    } else out += ch;
  }
  return out + q;
}
