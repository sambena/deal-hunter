// When the checks run: the parts of the Python app's poller.py that settings use. The loop itself comes with the
// scheduler's own PR.
import { asciiDigits, pyFullmatch, pyStr } from './py.js';

/** ["7:00", "18:00", "junk"] -> ["07:00", "18:00"]: valid HH:MM, sorted, no repeats. */
export function cleanTimes(times: unknown): string[] {
  const items = Array.isArray(times) ? times : (times ? pyStr(times) : '').replace(/;/g, ',').split(',');
  const out = new Set<string>();
  for (const t of items) {
    const m = pyFullmatch('\\s*([01]?\\d|2[0-3]):([0-5]\\d)\\s*', typeof t === 'string' ? t : pyStr(t));
    if (m) out.add(`${String(Number(asciiDigits(m[1]!))).padStart(2, '0')}:${m[2]}`);
  }
  return [...out].sort();
}
