// The background checks: a port of the Python app's poller.py. Every watch is run against its sources; new matches
// are stored with their deal % and alerted on Discord. One pass checks every enabled watch with its owner's settings
// and sends each identical request once (friends watching the same card don't multiply eBay calls). The pass runs
// every poll_minutes, or at the day's set times; nothing else is scheduled.
import type { Db } from '@cougarcave/server';
import { sql } from 'drizzle-orm';
import * as matching from './logic/matching.js';
import { BlockedURL, checkDiscord } from './logic/netguard.js';
import { pyJsonDumps } from './logic/py-text.js';
import { pyFixed, pyRound, pyStr, truthy } from './logic/py.js';
import { nextCheck } from './logic/schedule.js';
import { type Clock, family, type Listing, type Net, NetError, SourceError, USER_AGENT } from './logic/sources.js';
import type { Store } from './store.js';

type Row = Record<string, unknown>;
type SourceFn = (watch: Row & { query: string }, settings: Row) => Promise<Listing[]>;
const rowsOf = (result: unknown): Row[] => (Array.isArray(result) ? result : (result as { rows?: Row[] }).rows ?? []);

// Everything a source's request depends on. Two watches (anyone's) with the same key get the same results, so during
// one pass the request is sent once and the answer reused. Matching, excludes and alerts still run per watch.
const FETCH_SETTINGS = ['zip_code', 'local_radius_miles', 'ebay_marketplace', 'deal_max_age_days', 'reddit_subs'];
// Sources that send the watch's price/condition/auction choices with the request; the rest only send the query.
const WATCH_FILTERS: Record<string, string[]> = {
  ebay: ['min_price', 'max_price', 'condition', 'include_auctions'], ebay_local: ['min_price', 'max_price', 'condition', 'include_auctions'],
  craigslist: ['min_price', 'max_price'], offerup: ['min_price', 'max_price'], poshmark: ['size', 'department'],
};
// Vehicle watches search sources' car and truck sections, with the year range and mileage.
const VEHICLE_FILTERS = ['kind', 'year_min', 'year_max', 'max_miles'];
// The sources that can search for cars and trucks; vehicle watches skip the rest (Reddit, Best Buy...).
const VEHICLE_SOURCES = new Set(['ebay', 'ebay_local', 'craigslist', 'offerup', 'ksl_cars']);
const VEHICLE_ONLY = new Set(['ksl_cars']);   // and these only search for vehicles
// Clothes and shoes: where they're sold (no Reddit, Best Buy or car sites); Poshmark only sells clothes.
const CLOTHING_SOURCES = new Set(['ebay', 'ebay_local', 'poshmark', 'craigslist', 'offerup', 'ksl', 'slickdeals']);
const CLOTHING_ONLY = new Set(['poshmark']);
// Best Buy's API terms allow keeping its content for at most 72 hours, so its finds are dropped once they haven't
// been seen in a check for that long (a find that's still listed keeps being refreshed).
const RETENTION_SECONDS: Record<string, number> = { bestbuy: 72 * 3600 };

const DROP_REASONS: [string, string][] = [['missing', 'title missing a search word'], ['excluded', 'excluded words'],
  ['junk', 'junk words (parts, broken...)'], ['over max price', 'over the max price'], ['under min price', 'under the min price'],
  ['no model year', 'no model year (parts, ads)'], ['older', 'older than wanted'], ['newer', 'newer than wanted'],
  ['too many miles', 'too many miles']];

/** matching's reason for a rejected listing, grouped the way a person reads them. */
export const dropReason = (why: string) => DROP_REASONS.find(([start]) => why.startsWith(start))?.[1] ?? (why || 'other');

/** What one pass shares: the answers so far by request, and how many were sent or reused. */
interface Shared { answers: Map<string, Listing[] | SourceError>; made: number; shared: number }

export interface PollerState { running: boolean; last_cycle: number | null; next_cycle: number | null; source_errors: Record<string, string>;
  fetches: { made: number; shared: number } }

/** One lock for checks, as Python's _run_lock: a full pass, one watch and one person's watches never overlap. */
class Lock {
  private tail = Promise.resolve();
  run<T>(fn: () => Promise<T>): Promise<T> {
    const result = this.tail.then(fn);
    this.tail = result.then(() => undefined, () => undefined);
    return result;
  }
}

export class Poller {
  readonly state: PollerState = { running: false, last_cycle: null, next_cycle: null, source_errors: {}, fetches: { made: 0, shared: 0 } };
  private readonly lock = new Lock();
  private wake: (() => void) | null = null;
  private stopped = false;

  constructor(private readonly db: Db, private readonly store: Store, private readonly sources: Record<string, SourceFn>,
    private readonly net: Net, private readonly clock: Clock, private readonly log: (line: string) => void = () => {}) {}

  private async query(q: ReturnType<typeof sql>): Promise<Row[]> {
    return rowsOf(await this.db.execute(q));
  }

  fetchKey(name: string, watch: Row, settings: Row): string {
    const vehicle = watch.kind === 'vehicle' ? VEHICLE_FILTERS.map((k) => watch[k] ?? null) : null;
    return JSON.stringify([name, watch.query, (WATCH_FILTERS[name] ?? []).map((k) => watch[k] ?? null), vehicle, watch.fits ?? null,
      FETCH_SETTINGS.map((k) => pyStr(settings[k]))]);
  }

  /** A watch linked to one of the owner's vehicles searches for parts that fit it (eBay's fitment check). */
  private async vehicleFit(watch: Row): Promise<Row | null> {
    if (!truthy(watch.machine_id) || watch.kind === 'vehicle') return null;
    const m = await this.store.getMachine(Number(watch.machine_id), watch.user_id as number);
    if (!m || m.kind !== 'vehicle' || !(truthy(m.make) && truthy(m.model) && truthy(m.year))) return null;
    return { year: m.year, make: m.make, model: m.model };
  }

  /** Runs one source for a watch, reusing an identical request made earlier in this pass (errors too). */
  private async fetch(name: string, watch: Row & { query: string }, settings: Row, shared: Shared | null): Promise<Listing[]> {
    if (!shared) return this.sources[name]!(watch, settings);
    const key = this.fetchKey(name, watch, settings);
    const hit = shared.answers.get(key);
    if (hit !== undefined) {
      shared.shared++;
      if (hit instanceof SourceError) throw hit;
      return hit.map((it) => ({ ...it }));
    }
    shared.made++;
    try {
      shared.answers.set(key, await this.sources[name]!(watch, settings));
    } catch (e) {
      if (e instanceof SourceError) shared.answers.set(key, e);
      throw e;
    }
    return (shared.answers.get(key) as Listing[]).map((it) => ({ ...it }));
  }

  /** Checks one watch: {new, errors}. `shared` holds this pass's requests (see fetch). */
  async runWatch(original: Row, settings: Row, shared: Shared | null = null): Promise<{ new: number; errors: string[] }> {
    let watch = original as Row & { query: string };
    const fits = await this.vehicleFit(watch);
    if (fits) watch = { ...watch, fits };
    const vehicle = watch.kind === 'vehicle';
    // (price, model year) of everything found so far; vehicles compare with similar model years.
    const history: [number, number | null][] = (await this.query(sql`SELECT total, year FROM deals.listings WHERE watch_id = ${watch.id}
      AND total IS NOT NULL ORDER BY id`)).map((r) => [r.total as number, r.year as number | null]);
    const polled = new Set(watch.polled_sources as string[]);
    const enabled = settings.sources_enabled as Record<string, unknown>;
    let newRows: (Listing & { id: number; backlog: boolean })[] = [];
    const errors: string[] = [];
    // What each site returned this check and why listings were dropped, for "why does it find nothing?"
    const diagnosis: Record<string, unknown> = {};
    // With a "keep the cheapest N" limit, new finds are stored hidden and only the ones that make the cut become
    // 'new' (so nothing reading the database alerts on one that's about to be hidden).
    const limited = truthy(watch.keep_cheapest);
    // Local searches go first so an item that is both local and national is stored as local.
    const order = [...(watch.sources as string[])].sort((a, b) => Number(a !== 'ebay_local') - Number(b !== 'ebay_local'));
    for (const name of order) {
      const clothing = watch.kind === 'clothing';
      if (!truthy(enabled[name]) || !Object.hasOwn(this.sources, name) || (vehicle && !VEHICLE_SOURCES.has(name)) || (!vehicle && VEHICLE_ONLY.has(name))
        || (clothing && !CLOTHING_SOURCES.has(name)) || (!clothing && CLOTHING_ONLY.has(name))) continue;
      let items: Listing[];
      try {
        items = await this.fetch(name, watch, settings, shared);
        delete this.state.source_errors[name];
      } catch (e) {
        if (!(e instanceof SourceError)) throw e;
        errors.push(`${name}: ${e.message}`);
        this.state.source_errors[name] = e.message;
        diagnosis[name] = { error: [...e.message].slice(0, 300).join('') };
        continue;
      }
      const diag = { raw: items.length, kept: 0, new: 0, dropped: {} as Record<string, number> };
      diagnosis[name] = diag;
      const drop = (reason: string) => { diag.dropped[reason] = (diag.dropped[reason] ?? 0) + 1; };
      // A source's first successful check finds everything already listed; store those but don't send them to Discord.
      const firstFromSource = !polled.has(name);
      polled.add(name);
      for (const it of items) {
        const total = it.price !== null && it.price !== undefined ? pyRound((it.price as number) + ((it.shipping as number) || 0), 2) : null;
        let [ok, why] = matching.check((it.match_text as string) || (it.title as string), watch as unknown as matching.WatchRules,
          settings.junk_terms as string[], total, ('text' in it ? it.text : '') as string);
        if (!ok) { drop(dropReason(why)); continue; }
        let year: number | null = null, miles: number | null = null, status: string | null = null, size: string | null = null;
        if (watch.kind === 'clothing') {
          size = (it.size as string) || matching.titleSize(it.title as string);
          if (!matching.checkSize(size, watch as unknown as matching.WatchRules)[0]) { drop('other sizes'); continue; }
        }
        if (vehicle) {
          const text = `${pyStr(it.title)} ${pyStr('text' in it ? it.text : '')}`;
          year = (it.year as number) || matching.vehicleYear(it.title as string);
          miles = it.miles !== null && it.miles !== undefined ? it.miles as number : matching.vehicleMiles(text);
          status = (it.title_status as string) || matching.titleStatus(text);
          [ok, why] = matching.checkVehicle(year, miles, watch as unknown as matching.WatchRules);
          if (!ok) { drop(dropReason(why)); continue; }
        }
        const fam = family(it.source as string);   // e.g. an eBay item found by both the national and local search
        const [exists] = await this.query(sql`SELECT id FROM deals.listings WHERE watch_id = ${watch.id} AND source_id = ${it.source_id as string}
          AND source IN (${sql.join(fam.map((f) => sql`${f}`), sql`, `)}) ORDER BY id LIMIT 1`);
        diag.kept++;
        if (exists) {
          await this.db.execute(sql`UPDATE deals.listings SET seen_at = ${this.clock.now()} WHERE id = ${exists.id}`);
          continue;
        }
        diag.new++;
        const compare = vehicle ? matching.vehicleHistory(year, history) : history.map(([t]) => t);
        const pct = matching.dealPct(total, compare);
        const make = vehicle ? ((it.make as string) || matching.vehicleMake(it.title as string) || null) : null;
        const [row] = await this.query(sql`INSERT INTO deals.listings (watch_id, source, source_id, title, price, shipping, total, currency, url, image,
          location, condition, buying, first_seen, status, deal_pct, year, miles, title_status, size, make)
          VALUES (${watch.id}, ${it.source as string}, ${pyStr(it.source_id)}, ${it.title as string}, ${it.price ?? null}, ${it.shipping ?? null}, ${total},
          ${it.currency ?? null}, ${it.url ?? null}, ${it.image ?? null}, ${it.location ?? null}, ${it.condition ?? null}, ${it.buying ?? null},
          ${this.clock.now()}, ${limited ? 'pruned' : 'new'}, ${pct}, ${year}, ${miles}, ${status || null}, ${size || null}, ${make}) RETURNING id`);
        if (total !== null) history.push([total, year]);
        newRows.push({ ...it, id: Number(row!.id), total, deal_pct: pct, backlog: firstFromSource });
      }
    }
    await this.db.execute(sql`UPDATE deals.watches SET last_polled = ${this.clock.now()}, last_error = ${errors.join('; ') || null},
      polled_sources = ${JSON.stringify([...polled].sort())}::jsonb, last_check = ${JSON.stringify(diagnosis)}::json WHERE id = ${watch.id}`);
    if (limited && newRows.length) {
      // Only the cheapest N stay; a new find that didn't make the cut stays hidden and never alerts.
      await this.store.pruneCheapest(watch, new Set(newRows.map((r) => r.id)));
      const kept = new Set((await this.query(sql`SELECT id FROM deals.listings WHERE watch_id = ${watch.id} AND status = 'new'`)).map((r) => Number(r.id)));
      newRows = newRows.filter((r) => kept.has(r.id));
    }
    let toSend = newRows.filter((r) => !r.backlog);
    if (truthy(settings.discord_deals_only)) toSend = toSend.filter((r) => matching.dealLabel(r.deal_pct as number | null));
    if (toSend.length && truthy(settings.discord_enabled) && truthy(settings.discord_webhook)) {
      try {
        await this.notifyDiscord(settings, watch, toSend);
      } catch (e) {   // a notification failure must not stop polling
        errors.push(`discord: ${(e as Error).message}`);
      }
    }
    return { new: newRows.length, errors };
  }

  async notifyDiscord(settings: Row, watch: Row, listings: Row[]): Promise<void> {
    if (!listings.length) return;
    const embeds = listings.slice(0, 10).map((l) => {   // Discord's per-message embed limit
      const price = l.total !== null && l.total !== undefined ? `$${pyFixed(l.total as number, 2)}` : 'price not stated';
      const label = matching.dealLabel(l.deal_pct as number | null);
      const deal = label ? ` · ${pyRound((l.deal_pct as number) * 100, 0)}% under typical (${label})` : '';
      const embed: Row = {
        title: [...(l.title as string)].slice(0, 250).join(''),
        url: l.url,
        description: `**${price}**${deal}\n${pyStr(l.source)} · ${(l.condition as string) || ''} ${(l.location as string) || ''}`.trim(),
        footer: { text: `Watch: ${pyStr(watch.name)}` },
      };
      if (truthy(l.image)) embed.thumbnail = { url: l.image };
      return embed;
    });
    await this.sendDiscord(settings.discord_webhook as string, { content: `${listings.length} new match(es) for **${pyStr(watch.name)}**`, embeds });
  }

  /** Posts to a Discord webhook (Discord's own addresses only). Errors read as Python's urllib wrote them. */
  async sendDiscord(webhook: string, payload: Row): Promise<void> {
    checkDiscord(webhook);
    let res;
    try {
      res = await this.net({ url: webhook, method: 'POST', headers: { 'Content-Type': 'application/json', 'User-Agent': USER_AGENT },
        body: pyJsonDumps(payload), timeout: 15 });
    } catch (e) {
      if (e instanceof NetError) throw new DiscordRefused(`<urlopen error ${e.reason}>`);
      throw e;
    }
    if (res.status >= 400) throw new DiscordRefused(`HTTP Error ${res.status}: ${res.reason ?? ''}`);
  }

  async purgeExpired(): Promise<number> {
    let removed = 0;
    for (const [source, seconds] of Object.entries(RETENTION_SECONDS)) {
      removed += (await this.query(sql`DELETE FROM deals.listings WHERE source = ${source} AND COALESCE(seen_at, first_seen) < ${this.clock.now() - seconds}
        RETURNING id`)).length;
    }
    return removed;
  }

  /** Each watch runs with its owner's settings (ZIP, junk words, Discord...). A watch that fails is noted on it. */
  private async runAsOwner(w: Row, shared: Shared | null): Promise<{ new: number; errors: string[] }> {
    try {
      return await this.runWatch(w, await this.store.getSettings(w.user_id as number), shared);
    } catch (e) {
      this.log(`checking watch ${pyStr(w.id)} failed: ${(e as Error).stack ?? e}`);
      await this.db.execute(sql`UPDATE deals.watches SET last_error = 'internal error, see console' WHERE id = ${w.id}`);
      return { new: 0, errors: ['internal error'] };
    }
  }

  /** One full pass: every enabled watch, then the Best Buy purge. */
  runAll(): Promise<void> {
    return this.lock.run(async () => {
      this.state.running = true;
      const shared: Shared = { answers: new Map(), made: 0, shared: 0 };
      try {
        for (const w of await this.store.listWatches(null)) if (w.enabled) await this.runAsOwner(w, shared);
        await this.purgeExpired();
        this.state.fetches = { made: shared.made, shared: shared.shared };
      } finally {
        this.state.running = false;
        this.state.last_cycle = this.clock.now();
      }
    });
  }

  /** Checks one of a member's watches now. */
  runOne(watchId: number, memberId: number): Promise<{ new: number; errors: string[] }> {
    return this.lock.run(async () => {
      const w = await this.store.getWatch(watchId, memberId);
      if (!w) return { new: 0, errors: ['watch not found'] };
      return this.runWatch(w, await this.store.getSettings(memberId));
    });
  }

  /** Checks one person's watches in the background (a member's "Check now" shouldn't check everyone's). */
  pollUser(memberId: number): Promise<{ new: number; errors: string[] }[]> {
    const work = this.lock.run(async () => {
      const shared: Shared = { answers: new Map(), made: 0, shared: 0 };
      const results: { new: number; errors: string[] }[] = [];
      for (const w of await this.store.listWatches(memberId)) if (w.enabled) results.push(await this.runAsOwner(w, shared));
      return results;
    });
    work.catch((e) => this.log(`checking member ${memberId}'s watches failed: ${(e as Error).message}`));
    return work;
  }

  /** The schedule's settings are shared ones, the same for everyone. */
  private async scheduleSettings(): Promise<Row> {
    return this.store.getSettings((await this.store.adminId()) ?? 0);
  }

  /** The loop: a pass, then wait until the next is due (or someone asks for one now). On set times, a restart waits
   * for the next one instead of checking straight away. */
  async loop(): Promise<void> {
    let first = true;
    while (!this.stopped) {
      let due: number;
      try {
        const settings = await this.scheduleSettings();
        if (!(first && settings.check_mode === 'times')) await this.runAll();
        due = nextCheck(await this.scheduleSettings(), this.clock.now());
      } catch (e) {   // one bad cycle must not stop the poller for good
        this.log(`a check pass failed: ${(e as Error).stack ?? e}`);
        due = this.clock.now() + 15 * 60;
      }
      first = false;
      this.state.next_cycle = due;
      await new Promise<void>((done) => {
        const timer = setTimeout(() => { this.wake = null; done(); }, Math.max(1, due - this.clock.now()) * 1000);
        this.wake = () => { clearTimeout(timer); this.wake = null; done(); };
      });
    }
  }

  start(): void {
    void this.loop();
  }

  /** Asks for a full pass now. */
  pollNow(): void {
    this.wake?.();
  }

  stop(): void {
    this.stopped = true;
    this.wake?.();
  }
}

/** Discord answered with an error (urllib's HTTPError, an OSError). */
export class DiscordRefused extends Error {}
export { BlockedURL };
