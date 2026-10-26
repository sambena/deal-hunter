// When the checks run: poller.py's clean_times, _zone and next_check. Set times are wall-clock times in a time zone,
// read as Python's zoneinfo reads them: a time that happens twice (clocks back) is the first one, and a time that
// doesn't happen (clocks forward) counts with the offset from before the change.
import { asciiDigits, pyFullmatch, pyInt, pyStr, truthy } from './py.js';

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

const validZone = (name: unknown): name is string => {
  if (typeof name !== 'string' || !name) return false;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: name });
    return true;
  } catch {
    return false;
  }
};

/** The time zone to keep set times in: the chosen one, else Mountain, else this machine's own. */
export function zoneOf(name: unknown): string {
  for (const key of [name, 'America/Denver']) if (validZone(key)) return key;
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** The zone's wall-clock date and time at an instant. */
function wall(zone: string, epoch: number) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).formatToParts(new Date(epoch * 1000)).map((p) => [p.type, p.value]));
  return { y: Number(parts.year), mo: Number(parts.month), d: Number(parts.day), h: Number(parts.hour), mi: Number(parts.minute), s: Number(parts.second) };
}

/** The zone's offset from UTC, in seconds, at an instant. */
function offsetAt(zone: string, epoch: number): number {
  const w = wall(zone, Math.floor(epoch));
  return Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi, w.s) / 1000 - Math.floor(epoch);
}

/** The instant a wall-clock time in the zone stands for, as zoneinfo with fold=0 reads it. */
export function wallToEpoch(zone: string, y: number, mo: number, d: number, h: number, mi: number): number {
  const asUtc = Date.UTC(y, mo - 1, d, h, mi, 0) / 1000;
  const before = offsetAt(zone, asUtc - 86400), after = offsetAt(zone, asUtc + 86400);
  const valid = [...new Set([before, after])].map((off) => asUtc - off).filter((t) => offsetAt(zone, t) === asUtc - t);
  if (valid.length) return Math.min(...valid);   // happens twice: the first time
  return asUtc - before;   // doesn't happen: read with the offset from before the change
}

/** When the next full check is due: poll_minutes from now, or the next of the day's set times. */
export function nextCheck(settings: Record<string, unknown>, now: number): number {
  if (settings.check_mode === 'times') {
    const times = cleanTimes(settings.check_times);
    if (times.length) {
      const zone = zoneOf(settings.check_timezone);
      const today = wall(zone, now);
      for (const day of [0, 1]) {
        const date = new Date(Date.UTC(today.y, today.mo - 1, today.d + day));
        for (const t of times) {
          const [h, m] = t.split(':').map(Number) as [number, number];
          const at = wallToEpoch(zone, date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate(), h, m);
          if (at > now + 30) return at;
        }
      }
    }
  }
  return now + Math.max(5, pyInt(truthy(settings.poll_minutes) ? settings.poll_minutes : 15)) * 60;
}
