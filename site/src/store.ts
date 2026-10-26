// Deal Hunter's data, as the Python app's db.py kept it: settings (shared, and each person's own), watches, their
// finds, and devices, each belonging to a member. Function for function a port of db.py over the deals schema, so
// the API answers as before. Sort orders keep SQLite's: NULL before any value going up and after any going down, text
// by code point, and ties in the order rows were added.
import type { Db } from '@cougarcave/server';
import { sql, type SQL } from 'drizzle-orm';
import { BadValue, pyEq, pyFloat, pyIn, pyInt, pySplit, pyStr, pyStrip, truthy } from './logic/py.js';

type Row = Record<string, unknown>;
const rowsOf = (result: unknown): Row[] => (Array.isArray(result) ? result : (result as { rows?: Row[] }).rows ?? []);

export const DEFAULT_SETTINGS: Record<string, unknown> = {
  poll_minutes: 15,
  check_mode: 'interval',   // interval: every poll_minutes | times: at check_times each day
  check_times: ['07:00', '18:00'],   // "HH:MM", in check_timezone
  check_timezone: 'America/Denver',
  junk_terms: [
    'for parts', 'parts only', 'not working', 'non working', 'broken', 'as is', 'as-is',
    'untested', 'box only', 'empty box', 'bent pin', 'cracked', 'damaged', 'faulty',
    'engineering sample', 'qualification sample', 'replica', 'wtb', 'want to buy',
    'looking for', 'read description',
  ],
  sources_enabled: { ebay: true, ebay_local: true, reddit: true, bestbuy: false, slickdeals: true, buildapcsales: true,
    ksl: true, craigslist: true, offerup: true, ksl_cars: true, poshmark: true },
  ebay_client_id: '',
  ebay_client_secret: '',
  ebay_marketplace: 'EBAY_US',
  zip_code: '',
  local_radius_miles: 50,
  bestbuy_api_key: '',
  reddit_subs: ['hardwareswap', 'homelabsales'],
  deal_max_age_days: 14,   // Slickdeals search reaches back years; older deals are skipped
  discord_enabled: false,
  discord_webhook: '',
  discord_deals_only: true,
  ai_provider: 'off',   // off | ollama | claude
  ollama_url: 'http://localhost:11434',   // Ollama's own default; OLLAMA_URL sets an install's own
  ollama_model: 'qwen3.5:4b',
  api_key: '',   // the retired single API key; kept so old settings read the same
  ha_url: '',   // Home Assistant, for importing devices into My Stuff
  ha_token: '',
  anthropic_api_key: '',
  openai_api_key: '',
  openai_model: 'gpt-6-luna',   // a model is always selected, so picking a Mode is enough
  gemini_api_key: '',
  gemini_model: 'gemini-3.5-flash-lite',
  gemini_free_tier: false,   // key has no billing in Google, so requests cost $0 and skip the monthly limit
  ai_monthly_limit: 5.0,   // US dollars; AI buttons stop working once a request could pass it
  ai_confirm: true,   // show the cost and ask before each paid AI request
  ai_source: 'shared',   // members: "shared" = the admin's AI on their allowance, "own" = their own key
  focus: 'all',   // what this person mostly hunts: all | tech | cars | home | clothes (filters and themes pages)
  sizes: {},   // this person's sizes, to fill in new clothing watches: {"department", "shoe", "top", "pants"}
  claude_model: 'claude-opus-5-5',
};

export const SECRET_KEYS = new Set(['ebay_client_secret', 'bestbuy_api_key', 'discord_webhook', 'anthropic_api_key',
  'openai_api_key', 'gemini_api_key', 'ha_token', 'api_key']);

// Each person's own settings. Everything else (source keys, check interval, Reddit subs...) is shared and only the
// admin can change it.
export const USER_KEYS = new Set(['zip_code', 'local_radius_miles', 'junk_terms', 'discord_enabled', 'discord_webhook',
  'discord_deals_only', 'ai_provider', 'ollama_url', 'ollama_model', 'anthropic_api_key', 'claude_model',
  'openai_api_key', 'openai_model', 'gemini_api_key', 'gemini_model', 'gemini_free_tier',
  'ai_monthly_limit', 'ai_confirm', 'ai_source', 'ha_url', 'ha_token', 'focus', 'sizes']);

// What a member (not the admin) may see and change: their area, junk words, Discord and their own AI. Everything
// else, including their Home Assistant link, is the admin's.
export const MEMBER_KEYS = new Set(['zip_code', 'local_radius_miles', 'junk_terms', 'discord_enabled', 'discord_webhook',
  'discord_deals_only', 'ai_source', 'ai_provider', 'ollama_url', 'ollama_model', 'anthropic_api_key',
  'claude_model', 'openai_api_key', 'openai_model', 'gemini_api_key', 'gemini_model',
  'gemini_free_tier', 'ai_monthly_limit', 'ai_confirm', 'focus', 'sizes']);
// Shared settings a member's pages still need to read (which sources are switched on, how often checks run).
export const MEMBER_READS = new Set(['sources_enabled', 'poll_minutes', 'check_mode', 'check_times', 'check_timezone']);

export const DEFAULT_WATCH_LIMIT = 10;   // for people who join; the admin has none

const WATCH_FIELDS = ['name', 'query', 'exclude', 'min_price', 'max_price', 'condition', 'include_auctions', 'sources',
  'enabled', 'notes', 'machine_id', 'keep_cheapest', 'color', 'kind', 'year_min', 'year_max', 'max_miles', 'category',
  'size', 'department'];
// Changing any of these makes the next check find a fresh backlog of older listings.
const MATCH_FIELDS = ['query', 'exclude', 'min_price', 'max_price', 'condition', 'include_auctions', 'kind', 'year_min',
  'year_max', 'max_miles', 'size', 'department'];

// Focus categories. Devices belong by kind; a watch by its own choice, else what it's for.
export const CATEGORIES = ['tech', 'cars', 'home', 'clothes'];
export const KIND_CATEGORY: Record<string, string> = {
  pc: 'tech', server: 'tech', monitor: 'tech', phone: 'tech', tablet: 'tech', network: 'tech', console: 'tech',
  printer: 'tech', vehicle: 'cars', tv: 'home', audio: 'home', appliance: 'home', kitchen: 'home', 'smart home': 'home',
  other: 'home', clothing: 'clothes', shoes: 'clothes',
};
// Everything someone owns, not just computers. PCs and servers have a parts list; the rest a make/model.
export const DEVICE_KINDS = ['pc', 'server', 'tv', 'monitor', 'phone', 'tablet', 'audio', 'network', 'console',
  'printer', 'appliance', 'kitchen', 'smart home', 'vehicle', 'clothing', 'shoes', 'other'];
export const PARTS_KINDS = new Set(['pc', 'server']);

/** A member: a person who uses Deal Hunter, with the limits the admin set. */
export interface Member { id: number; subject: string; admin: boolean; disabled: boolean; watch_limit: number | null;
  created_at: number; ai_shared: boolean; ai_allowance: number; ai_daily_cap: number }

/** Python's text for a value bound to a TEXT column: a string as it is, a number or bool as SQLite stores it. */
function textValue(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === 'string') return v;
  if (typeof v === 'boolean') return v ? '1' : '0';
  if (typeof v === 'number') return String(v);
  throw new BadValue('a list or object where text belongs');
}

const json = (v: unknown) => sql`${JSON.stringify(v)}::jsonb`;

/** A device that isn't the member's (db.py's LookupError). */
export class DeviceNotFound extends Error {}

/** Python's dict.get(key, default) on a JSON value; a list item that isn't an object fails as Python's would. */
function getOr(item: unknown, key: string, fallback: unknown): unknown {
  if (!item || typeof item !== 'object' || Array.isArray(item)) throw new BadValue('a list item that is not an object');
  return key in item ? (item as Row)[key] : fallback;
}

/** Iterating a JSON value as Python would: a list's items, a string's characters, an object's keys. */
function pyIter(v: unknown): unknown[] {
  if (Array.isArray(v)) return v;
  if (typeof v === 'string') return [...v];
  if (v && typeof v === 'object') return Object.keys(v);
  throw new BadValue('not a list');
}

/** db.py's _number: a number from what the form sent ("$1,299" too), or null when it isn't one. */
function numberOf(v: unknown, kind: 'int' | 'float'): number | null {
  if (v === null || v === undefined || v === '') return null;
  const s = pyStrip(pyStr(v).replace(/\$/g, '').replace(/,/g, ''));
  try {
    return kind === 'int' ? pyInt(s) : pyFloat(s);
  } catch (e) {
    if (e instanceof BadValue) return null;
    throw e;
  }
}

export class Store {
  constructor(private readonly db: Db, private readonly now: () => number) {}

  async query(q: SQL): Promise<Row[]> {
    return rowsOf(await this.db.execute(q));
  }

  // ---- members ------------------------------------------------------------------------------------------------

  async member(subject: string): Promise<Member | null> {
    const [row] = await this.query(sql`SELECT * FROM deals.members WHERE subject = ${subject}`);
    return (row as unknown as Member) ?? null;
  }

  /** The person's member row, made on their first sign-in with the limits a newly invited friend got. */
  async memberFor(subject: string): Promise<Member> {
    const found = await this.member(subject);
    if (found) return found;
    // Checked first, so the id sequence isn't spent on every request; two first requests at once both land on the row.
    await this.db.execute(sql`INSERT INTO deals.members (subject, created_at, watch_limit) VALUES (${subject}, ${this.now()}, ${DEFAULT_WATCH_LIMIT})
      ON CONFLICT (subject) DO NOTHING`);
    return (await this.member(subject))!;
  }

  /** The first admin, whose name the pages show as the AI's owner. */
  async adminId(): Promise<number | null> {
    const [row] = await this.query(sql`SELECT id FROM deals.members WHERE admin ORDER BY id LIMIT 1`);
    return row ? Number(row.id) : null;
  }

  // ---- settings -----------------------------------------------------------------------------------------------

  /** Shared settings plus one person's own. */
  async getSettings(memberId: number): Promise<Record<string, unknown>> {
    const shared = Object.fromEntries((await this.query(sql`SELECT key, value FROM deals.settings`)).map((r) => [r.key, r.value]));
    const mine = Object.fromEntries((await this.query(sql`SELECT key, value FROM deals.user_settings WHERE member_id = ${memberId}`))
      .map((r) => [r.key, r.value]));
    const merged: Record<string, unknown> = { ...DEFAULT_SETTINGS };
    for (const [k, v] of Object.entries(shared)) if (!USER_KEYS.has(k as string)) merged[k as string] = v;
    Object.assign(merged, mine);
    // Sources added after the settings were saved start at their default on/off.
    merged.sources_enabled = { ...(DEFAULT_SETTINGS.sources_enabled as object), ...((shared.sources_enabled as object) ?? {}) };
    return merged;
  }

  /** Personal keys go to the person's own settings; shared keys only when allowed (the admin). */
  async updateSettings(changes: Record<string, unknown>, memberId: number, sharedAllowed: boolean): Promise<void> {
    for (const [key, value] of Object.entries(changes)) {
      if (!(key in DEFAULT_SETTINGS) || (!sharedAllowed && !MEMBER_KEYS.has(key))) continue;
      if (USER_KEYS.has(key)) {
        await this.db.execute(sql`INSERT INTO deals.user_settings (member_id, key, value) VALUES (${memberId}, ${key}, ${json(value)})
          ON CONFLICT (member_id, key) DO UPDATE SET value = EXCLUDED.value`);
      } else if (sharedAllowed) {
        await this.db.execute(sql`INSERT INTO deals.settings (key, value) VALUES (${key}, ${json(value)})
          ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`);
      }
    }
  }

  /** Settings safe to send to the browser: secrets become booleans; a member only gets their own. */
  async publicSettings(memberId: number, admin: boolean): Promise<Record<string, unknown>> {
    let s = await this.getSettings(memberId);
    if (!admin) s = Object.fromEntries(Object.entries(s).filter(([k]) => MEMBER_KEYS.has(k) || MEMBER_READS.has(k)));
    for (const key of SECRET_KEYS) if (key in s) s[key] = truthy(s[key]);
    return s;
  }

  // ---- watches ------------------------------------------------------------------------------------------------

  private async watchRow(r: Row): Promise<Row> {
    const out: Row = { ...r, user_id: r.member_id };
    delete out.member_id;
    out.category_choice = CATEGORIES.includes(r.category as string) ? r.category : null;   // null = automatic
    out.category = await this.watchCategory(r);
    out.last_check = r.last_check ?? {};
    return out;
  }

  /** A member's watches, newest first (everyone's only for the poller). */
  async listWatches(memberId: number | null): Promise<Row[]> {
    const where = memberId === null ? sql`` : sql`WHERE w.member_id = ${memberId}`;
    const rows = await this.query(sql`
      SELECT w.*,
        (SELECT COUNT(*)::int FROM deals.listings l WHERE l.watch_id = w.id AND l.status = 'new') AS new_count,
        (SELECT COUNT(*)::int FROM deals.listings l WHERE l.watch_id = w.id) AS total_count,
        (SELECT MIN(total) FROM deals.listings l WHERE l.watch_id = w.id AND l.status != 'dismissed') AS best_price
      FROM deals.watches w ${where} ORDER BY w.created_at DESC, w.id`);
    return Promise.all(rows.map((r) => this.watchRow(r)));
  }

  async getWatch(watchId: number, memberId: number | null): Promise<Row | null> {
    const [row] = memberId === null
      ? await this.query(sql`SELECT * FROM deals.watches WHERE id = ${watchId}`)
      : await this.query(sql`SELECT * FROM deals.watches WHERE id = ${watchId} AND member_id = ${memberId}`);
    return row ? this.watchRow(row) : null;
  }

  /** tech, cars, home or clothes: as chosen, else a car watch is cars, a watch for one of your things takes that
   * thing's category (parts for your truck are cars), and anything else is tech. */
  async watchCategory(watch: Row): Promise<string> {
    if (CATEGORIES.includes(watch.category as string)) return watch.category as string;
    if (watch.kind === 'vehicle') return 'cars';
    if (watch.kind === 'clothing') return 'clothes';
    if (truthy(watch.machine_id)) {
      const m = await this.getMachine(pyInt(watch.machine_id), null);
      if (m) return KIND_CATEGORY[(m.kind as string) || 'pc'] ?? 'tech';
    }
    return 'tech';
  }

  /** db.py's _watch_values: the watch fields sent, cleaned as the Python app cleaned them. */
  private async watchValues(data: Row, memberId: number): Promise<Row> {
    const out: Row = {};
    for (const f of WATCH_FIELDS) {
      if (!(f in data)) continue;
      let v = data[f];
      if (f === 'exclude' || f === 'sources') {
        v = Array.isArray(v) ? v : [];
      } else if (f === 'enabled' || f === 'include_auctions') {
        v = truthy(v);
      } else if (f === 'min_price' || f === 'max_price') {
        v = pyIn(v, null, '') || v === undefined ? null : pyFloat(v);
      } else if (f === 'keep_cheapest') {
        try {
          v = pyIn(v, null, '', 0, '0') ? null : pyInt(v);
        } catch (e) {
          if (!(e instanceof BadValue)) throw e;
          v = null;
        }
        v = v === null || (v as number) > 0 ? v : null;
      } else if (f === 'kind') {
        v = ['item', 'vehicle', 'clothing'].includes(v as string) ? v : 'item';
      } else if (f === 'size') {
        // str(v or ""): True reads "True"; then single spaces, at most 20 characters.
        v = [...pySplit(!truthy(v) ? '' : v === true ? 'True' : textValue(v)!).join(' ')].slice(0, 20).join('') || null;
      } else if (f === 'department') {
        v = ['men', 'women', 'kids'].includes(v as string) ? v : null;
      } else if (f === 'category') {
        v = CATEGORIES.includes(v as string) ? v : null;
      } else if (f === 'year_min' || f === 'year_max' || f === 'max_miles') {
        try {
          v = pyIn(v, null, '') ? null : Math.trunc(pyFloat(v));
          if (v !== null && !Number.isFinite(v)) v = null;   // Python's int(float('inf')) fails, and so gives None
        } catch (e) {
          if (!(e instanceof BadValue)) throw e;
          v = null;
        }
        if (v !== null && ((v as number) < 0 || (f !== 'max_miles' && !((v as number) >= 1900 && (v as number) <= 2100)))) v = null;
      } else if (f === 'color') {
        v = typeof v === 'string' && /^#[0-9a-fA-F]{6}$/.test(v) ? v.toLowerCase() : null;
      } else if (f === 'machine_id' && v !== null && v !== undefined && !(await this.getMachine(pyInt(v), memberId))) {
        v = null;   // only link to your own devices
      } else if (f === 'name' || f === 'query' || f === 'notes' || f === 'condition') {
        v = textValue(v);
      }
      out[f] = v;
    }
    return out;
  }

  private static dbValue(f: string, v: unknown): SQL {
    if (f === 'exclude' || f === 'sources') return json(v);
    if (f === 'machine_id' && v !== null && v !== undefined) return sql`${pyInt(v)}`;
    return sql`${v ?? null}`;
  }

  async createWatch(data: Row, memberId: number): Promise<number> {
    const vals = await this.watchValues(data, memberId);
    const cols = Object.keys(vals);
    const [row] = await this.query(sql`INSERT INTO deals.watches (${sql.raw([...cols, 'created_at', 'member_id'].join(', '))})
      VALUES (${sql.join([...cols.map((f) => Store.dbValue(f, vals[f])), sql`${this.now()}`, sql`${memberId}`], sql`, `)}) RETURNING id`);
    return Number(row!.id);
  }

  /** null if there's no such watch, else how many new finds turning it off marked seen. */
  async updateWatch(watchId: number, data: Row, memberId: number): Promise<number | null> {
    const old = await this.getWatch(watchId, memberId);
    if (!old) return null;
    const vals = await this.watchValues(data, memberId);
    const sets: SQL[] = Object.keys(vals).map((f) => sql`${sql.raw(f)} = ${Store.dbValue(f, vals[f])}`);
    for (const f of MATCH_FIELDS) {
      if (!(f in vals)) continue;
      const before = (await this.watchValues({ [f]: old[f] }, memberId))[f];
      if (JSON.stringify(before) !== JSON.stringify(vals[f]) && !pyEq(before, vals[f])) {
        // New criteria find a new backlog; treat it like a fresh watch so it isn't sent to Discord.
        sets.push(sql`polled_sources = '[]'::jsonb`);
        break;
      }
    }
    if (sets.length) await this.db.execute(sql`UPDATE deals.watches SET ${sql.join(sets, sql`, `)} WHERE id = ${watchId} AND member_id = ${memberId}`);
    if (old.enabled && vals.enabled === false) {
      // Turning a watch off: its unread finds count as viewed, so they stop showing as new everywhere.
      const done = await this.query(sql`UPDATE deals.listings SET status = 'seen' WHERE watch_id = ${watchId} AND status = 'new' RETURNING id`);
      return done.length;
    }
    return 0;
  }

  /** Keep only the watch's N cheapest finds showing; the rest become 'pruned' (hidden, not deleted, so the next check
   * doesn't find them again as new). Starred finds always stay and don't use up a place. Pruned finds come back if
   * cheaper ones go or the limit is raised or cleared. `fresh` are finds this check just stored (hidden until now):
   * the ones that make the cut become 'new'. Returns how many finds were newly hidden. */
  async pruneCheapest(watch: Row, fresh: Set<number> = new Set()): Promise<number> {
    const n = watch.keep_cheapest as number | null;
    const rows = await this.query(sql`SELECT id, status FROM deals.listings WHERE watch_id = ${watch.id} AND status IN ('new', 'seen', 'pruned')
      ORDER BY total IS NULL, total, first_seen DESC, id`);
    const keep = n ? rows.slice(0, n) : rows;
    const hide = n ? rows.slice(n) : [];
    for (const r of keep) {
      if (r.status === 'pruned') await this.db.execute(sql`UPDATE deals.listings SET status = ${fresh.has(Number(r.id)) ? 'new' : 'seen'} WHERE id = ${r.id}`);
    }
    const newly = hide.filter((r) => r.status !== 'pruned' && !fresh.has(Number(r.id))).map((r) => Number(r.id));
    for (const id of newly) await this.db.execute(sql`UPDATE deals.listings SET status = 'pruned' WHERE id = ${id}`);
    return newly.length;
  }

  async deleteWatch(watchId: number, memberId: number): Promise<boolean> {
    return (await this.query(sql`DELETE FROM deals.watches WHERE id = ${watchId} AND member_id = ${memberId} RETURNING id`)).length > 0;
  }

  // ---- machines -----------------------------------------------------------------------------------------------

  private static machineRow(r: Row): Row {
    const out: Row = { ...r, user_id: r.member_id, category: KIND_CATEGORY[(r.kind as string) || 'pc'] ?? 'tech' };
    delete out.member_id;
    return out;
  }

  /** A member's devices, by name. */
  async listMachines(memberId: number): Promise<Row[]> {
    return (await this.query(sql`SELECT * FROM deals.machines WHERE member_id = ${memberId} ORDER BY name COLLATE "C", id`)).map(Store.machineRow);
  }

  async getMachine(machineId: number, memberId: number | null): Promise<Row | null> {
    const [row] = memberId === null
      ? await this.query(sql`SELECT * FROM deals.machines WHERE id = ${machineId}`)
      : await this.query(sql`SELECT * FROM deals.machines WHERE id = ${machineId} AND member_id = ${memberId}`);
    return row ? Store.machineRow(row) : null;
  }

  /** Adds a device (machineId null) or changes one of the member's; a LookupError's 404 when it isn't theirs. */
  async saveMachine(data: Row, memberId: number, machineId: number | null = null): Promise<number> {
    const parts = (Array.isArray(data.parts) ? data.parts : 'parts' in data ? pyIter(data.parts) : [])
      .filter((p) => pyStrip(pyStr(getOr(p, 'model', ''))));
    const kind = DEVICE_KINDS.includes(data.kind as string) ? data.kind as string : 'pc';
    const year = numberOf(data.year, 'int');
    const custom = ((truthy(data.custom) ? pyIter(data.custom) : []) as Row[])
      .filter((f) => pyStrip(pyStr(getOr(f, 'label', ''))))
      .map((f) => ({ label: [...pyStrip(pyStr(getOr(f, 'label', '')))].slice(0, 60).join(''),
                     value: [...pyStrip(pyStr(getOr(f, 'value', '')))].slice(0, 500).join('') }));
    const vals = {
      name: textValue('name' in data ? data.name : 'Untitled'), notes: textValue('notes' in data ? data.notes : ''),
      parts: json(parts), kind,
      make: pyStrip(truthy(data.make) ? pyStr(data.make) : ''), model: pyStrip(truthy(data.model) ? pyStr(data.model) : ''),
      year: year && year >= 1950 && year <= 2100 ? year : null, msrp: numberOf(data.msrp, 'float'),
      purchased: pyStrip(truthy(data.purchased) ? pyStr(data.purchased) : ''), price_paid: numberOf(data.price_paid, 'float'),
      custom: json(custom), source_ref: truthy(data.source_ref) ? textValue(data.source_ref) : null,
    };
    let id: number;
    if (machineId === null) {
      const [row] = await this.query(sql`INSERT INTO deals.machines (name, notes, parts, kind, make, model, year, msrp, purchased, price_paid,
        custom, source_ref, member_id) VALUES (${vals.name}, ${vals.notes}, ${vals.parts}, ${vals.kind}, ${vals.make}, ${vals.model},
        ${vals.year}, ${vals.msrp}, ${vals.purchased}, ${vals.price_paid}, ${vals.custom}, ${vals.source_ref}, ${memberId}) RETURNING id`);
      id = Number(row!.id);
    } else {
      const changed = await this.query(sql`UPDATE deals.machines SET name = ${vals.name}, notes = ${vals.notes}, parts = ${vals.parts},
        kind = ${vals.kind}, make = ${vals.make}, model = ${vals.model}, year = ${vals.year}, msrp = ${vals.msrp},
        purchased = ${vals.purchased}, price_paid = ${vals.price_paid}, custom = ${vals.custom},
        source_ref = COALESCE(${vals.source_ref}, source_ref) WHERE id = ${machineId} AND member_id = ${memberId} RETURNING id`);
      if (!changed.length) throw new DeviceNotFound();
      id = machineId;
    }
    // VIN and odometer change only when sent, so apps that don't know them yet don't wipe them.
    if ('vin' in data) {
      const vin = [...pySplit(truthy(data.vin) ? pyStr(data.vin) : '').join('').toUpperCase()].slice(0, 17).join('');
      await this.db.execute(sql`UPDATE deals.machines SET vin = ${vin} WHERE id = ${id}`);
    }
    if ('odometer' in data) {
      const miles = numberOf(data.odometer, 'int');
      await this.db.execute(sql`UPDATE deals.machines SET odometer = ${miles && miles >= 0 ? miles : null} WHERE id = ${id}`);
    }
    return id;
  }

  async deleteMachine(machineId: number, memberId: number): Promise<boolean> {
    await this.db.execute(sql`UPDATE deals.watches SET machine_id = NULL WHERE machine_id = ${machineId} AND member_id = ${memberId}`);
    return (await this.query(sql`DELETE FROM deals.machines WHERE id = ${machineId} AND member_id = ${memberId} RETURNING id`)).length > 0;
  }
}
