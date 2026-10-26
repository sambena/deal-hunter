// Deal Hunter's API: the Python app's routes (../server.py), with the same answers, on the framework's sign-in.
// - Everyone is a member, by their Cougar Cave subject. Someone with access to Deal Hunter who signs in for the first
//   time gets a member row with the limits a newly invited friend had.
// - An admin's browser session that didn't use both a password and a code is refused on every route, as the Python app
//   refused such an admin's sign-in. A phone can only be paired from a sign-in that used a code (as before), so an
//   admin's phone works on ordinary routes; privileged ones need a browser.
// - Privileged routes (People, the shared settings) need both factors, and for a change a sign-in from the last
//   five minutes.
// - Errors are the framework's {error, message}; the message is the Python app's own text.
// - Not here yet, each in its own PR: checking watches now (run, poll), the Discord test and the Home Assistant import
//   come with the sources and scheduler; the AI routes, the AI parts of state and settings, and the "with AI" choice of
//   the device routes (suggest, specs, radar), with the AI. Until the scheduler lands, a new watch answers as if created
//   with check: false; until the AI lands, asking for AI answers 501 not_yet.
import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { bothFactors, people, Refusal, type Session } from '@cougarcave/server';
import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import { sql } from 'drizzle-orm';
import * as hardware from './logic/hardware.js';
import * as matching from './logic/matching.js';
import { BlockedURL, check as checkAddress, checkDiscord, type Resolve, resolveHost } from './logic/netguard.js';
import { BadValue, median, pyFloat, pyIn, pyInt, pyRound, pySplit, pyStr, pyStrip, truthy } from './logic/py.js';
import { cpscGet, type CpscGet, productRecalls } from './logic/product-recalls.js';
import { cleanTimes } from './logic/schedule.js';
import * as specs from './logic/specs.js';
import { decodeVin, type Get, LookupFailed, nhtsaGet, recalls as vehicleRecalls } from './logic/vehicles.js';
import { DEFAULT_WATCH_LIMIT, DeviceNotFound, type Member, PARTS_KINDS, SECRET_KEYS, Store } from './store.js';

/** The admin's AI is managed in the owner console, so the admin can't set it here (ai.py's AI_KEYS and the limit). */
const ADMIN_MANAGED_AI = new Set(['ai_provider', 'ollama_url', 'ollama_model', 'anthropic_api_key', 'claude_model',
  'openai_api_key', 'openai_model', 'gemini_api_key', 'gemini_model', 'gemini_free_tier', 'ai_monthly_limit']);

const OWNER_FACTORS = 'Admin sign-in needs your password and your authenticator code in the same sign-in. This '
  + "browser's Cougar Cave session may have started with a code only. Sign in from a private window, "
  + "or clear this browser's cookies for the sign-in service, then try again.";
const NEEDS_BOTH = 'This needs a Cougar Cave sign-in that used both your password and your authenticator code. '
  + 'Sign out, then use Sign in with Cougar Cave.';
const stale = (age: number) => `For this change, sign in again: your last Cougar Cave sign-in was ${Math.floor(age / 60)} minutes ago. `
  + 'Sign out, then use Sign in with Cougar Cave from a private window so it asks for your password and code.';
const FRESH_SECONDS = 300;
const SKEW_SECONDS = 30;
const OWN_LOGIN_OFF = "Deal Hunter's own sign-in is off. Sign in with Cougar Cave.";
const BAD_VALUE = 'bad request: a value sent was the wrong type or format';
const ANDROID_APK = 'deal-hunter.apk';
/** A numeric id in a path, as the Python routes' (\d+). */
const ID = ':id(^\\d+$)';

export interface ApiOptions {
  now: () => number;
  /** The API port's number, shown in /api/state; null when there is none. */
  apiPort: number | null;
  /** Where the Android project publishes deal-hunter.apk and android.json. */
  downloadsDir: string;
  /** Resolves the hosts in people's addresses (Ollama, Home Assistant) to check they are public. */
  resolve?: Resolve;
  /** NHTSA's and CPSC's answers (VINs and recalls). Tests pass made-up ones. */
  nhtsa?: Get;
  cpsc?: CpscGet;
}

declare module 'fastify' {
  interface FastifyRequest { member: Member | null }
}

const CODES: Record<number, string> = { 400: 'bad_request', 401: 'signed_out', 403: 'forbidden', 404: 'not_found', 409: 'conflict',
  410: 'gone', 413: 'too_large', 422: 'need_ai', 501: 'not_yet', 502: 'upstream' };
const fail = (status: number, message: string, code = CODES[status] ?? 'bad_request') => new Refusal(status, code, message);

type Params = Record<string, string>;
type Handler = (req: FastifyRequest, body: Record<string, unknown>, params: Params, member: Member) => Promise<unknown>;

/** The query string as the Python app read it (parse_qs): the first value of each key, blank values left out. */
function queryParams(url: string): Params {
  const out: Params = {};
  const i = url.indexOf('?');
  if (i < 0) return out;
  for (const [k, v] of new URLSearchParams(url.slice(i + 1))) if (v !== '' && !(k in out)) out[k] = v;
  return out;
}

/** Python's pairing rule, kept: a PIN for a phone needs a browser sign-in that used an authenticator code (amr has
 * "mfa"), on top of the framework's own "recent sign-in". So an admin's phone always comes from a sign-in an admin
 * could make. Added on the whole app (app.ts), since the pairing routes are the framework's. */
export async function pinNeedsCode(req: FastifyRequest): Promise<void> {
  if (req.method !== 'POST' || req.url.split('?')[0] !== '/auth/devices/code') return;
  if (req.session?.kind === 'browser' && !(req.session.amr ?? []).includes('mfa')) {
    throw fail(403, 'Adding a phone needs a recent Cougar Cave sign-in. Sign in again, then add the phone.', 'needs_recent_signin');
  }
}

export async function api(app: FastifyInstance, options: ApiOptions): Promise<void> {
  const store = new Store(app.cave.db, options.now);
  const resolve = options.resolve ?? resolveHost;
  app.decorateRequest('member', null);

  // ---- who is asking ------------------------------------------------------------------------------------------

  /** Python's _is_privileged: People and the HA import for anyone, and all of an admin's settings. */
  const privileged = (method: string, path: string, member: Member) =>
    path.startsWith('/api/admin/') || path.startsWith('/api/import/ha') || (method === 'PUT' && path === '/api/settings' && member.admin);

  app.addHook('onRequest', async (req) => {
    const path = req.url.split('?')[0]!;
    if (!path.startsWith('/api/') && path !== '/download/android') return;
    const session = req.session;
    if (!session) return;   // signedIn routes answer 401 themselves
    const member = await store.memberFor(session.person.subject);
    if (member.disabled) {
      if (session.kind === 'device') throw fail(403, 'This account has no access to Deal Hunter.', 'no_access');
      throw fail(401, 'Sign in to Deal Hunter');
    }
    if (member.admin && session.kind === 'browser' && !bothFactors(session)) throw fail(403, OWNER_FACTORS, 'needs_both_factors');
    if (privileged(req.method, path, member)) {
      if (!bothFactors(session)) throw fail(403, NEEDS_BOTH, 'needs_both_factors');
      const age = options.now() - (session.authTime ?? 0);
      if (req.method !== 'GET' && !(age >= -SKEW_SECONDS && age <= FRESH_SECONDS)) throw fail(403, stale(age), 'needs_recent_signin');
    }
    req.member = member;
  });

  /** A route with the Python app's handling: signed in, its body and query as Python read them, a wrong value as 400. */
  const route = (method: 'GET' | 'POST' | 'PUT' | 'DELETE', url: string, handler: Handler) => {
    app.route({ method, url, config: { signedIn: true }, handler: async (req) => {
      const raw = req.body;
      if (raw !== undefined && raw !== null && (typeof raw !== 'object' || Array.isArray(raw))) throw fail(400, BAD_VALUE);
      try {
        return await handler(req, (raw ?? {}) as Record<string, unknown>, queryParams(req.url), req.member!);
      } catch (e) {
        if (e instanceof BadValue) throw fail(400, BAD_VALUE);
        throw e;
      }
    } });
  };
  const id = (req: FastifyRequest) => pyInt((req.params as { id: string }).id);
  const requireAdmin = (m: Member) => { if (!m.admin) throw fail(403, 'only the admin can do that'); };

  /** The person's name and email, from Cougar Cave (core.people), as auth.public_user gave them. */
  async function publicUser(member: Member, session: Session | null) {
    const [p] = await app.cave.db.select({ name: people.name, email: people.email }).from(people).where(sql`${people.id} = ${member.subject}`);
    return { id: member.id, name: p?.name ?? session?.person.name ?? '', email: p?.email ?? session?.person.email ?? '',
      role: member.admin ? 'admin' : 'member' };
  }

  // ---- state, watches, finds ----------------------------------------------------------------------------------

  route('GET', '/api/state', async (req, _b, _p, m) => ({
    watches: await store.listWatches(m.id),
    machines: await store.listMachines(m.id),
    settings: await store.publicSettings(m.id, m.admin),
    me: await publicUser(m, req.session),
    api_port: options.apiPort,
    now: options.now(),
  }));

  route('POST', '/api/watches', async (_req, body, _p, m) => {
    if (!truthy(body.name) || !truthy(body.query)) throw fail(400, 'A watch needs a name and a query');
    const limit = m.watch_limit;
    if (limit !== null && (await store.listWatches(m.id)).length >= limit) {
      throw fail(403, `watch limit reached (${limit}); delete one or ask Sam for more`);
    }
    const wid = await store.createWatch(body, m.id);
    return { id: wid, new: 0, errors: [] };
  });

  route('PUT', `/api/watches/${ID}`, async (req, body, _p, m) => {
    const wid = id(req);
    const markedSeen = await store.updateWatch(wid, body, m.id);
    if (markedSeen === null) throw fail(404, 'watch not found');
    const removed = await tidyFinds(wid, m.id);
    return { ok: true, removed: removed + await store.pruneCheapest((await store.getWatch(wid, m.id))!), marked_seen: markedSeen };
  });

  /** After an edit: drop finds the watch's excludes or prices now rule out (starred ones stay), and re-work "under
   * typical" from what's left, so the removed listings stop skewing it. */
  async function tidyFinds(watchId: number, memberId: number): Promise<number> {
    const watch = (await store.getWatch(watchId, memberId))!;
    const rows = await store.query(sql`SELECT id, title, total, status, year, miles FROM deals.listings WHERE watch_id = ${watchId} ORDER BY id`);
    const vehicle = watch.kind === 'vehicle';
    const rules = watch as unknown as matching.WatchRules;
    const gone = rows.filter((r) => r.status !== 'starred' && (
      !matching.stillWanted(r.title as string, r.total as number | null, rules)
      || (vehicle && !matching.checkVehicle(r.year as number | null, r.miles as number | null, rules)[0]))).map((r) => Number(r.id));
    for (const lid of gone) await app.cave.db.execute(sql`DELETE FROM deals.listings WHERE id = ${lid}`);
    const kept = rows.filter((r) => !gone.includes(Number(r.id)));
    for (const r of kept) {
      const others = kept.filter((k) => k.id !== r.id && k.total !== null).map((k) => [k.total, k.year] as [number, number | null]);
      const history = vehicle ? matching.vehicleHistory(r.year as number | null, others) : others.map(([t]) => t);
      await app.cave.db.execute(sql`UPDATE deals.listings SET deal_pct = ${matching.dealPct(r.total as number | null, history)} WHERE id = ${r.id}`);
    }
    return gone.length;
  }

  route('DELETE', `/api/watches/${ID}`, async (req, _b, _p, m) => {
    if (!await store.deleteWatch(id(req), m.id)) throw fail(404, 'watch not found');
    return { ok: true };
  });

  route('GET', `/api/watches/${ID}/history`, async (req, _b, params, m) => {
    // Asking prices a watch has found, by day: for a small chart and "good price vs typical".
    const watch = await store.getWatch(id(req), m.id);
    if (!watch) throw fail(404, 'watch not found');
    let days: number;
    try {
      days = Math.max(1, Math.min(365, pyInt(params.days ?? 90)));
    } catch {
      days = 90;
    }
    const since = options.now() - days * 86400;
    const rows = await store.query(sql`SELECT first_seen, total, status FROM deals.listings WHERE watch_id = ${watch.id} AND total IS NOT NULL
      AND first_seen >= ${since} ORDER BY first_seen, id`);
    const byDay = new Map<string, number[]>();
    for (const r of rows) {
      const day = new Date(Number(r.first_seen) * 1000).toISOString().slice(0, 10);
      byDay.set(day, [...(byDay.get(day) ?? []), r.total as number]);
    }
    const points = [...byDay].sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([date, p]) => ({ date, min: Math.min(...p), median: pyRound(median(p), 2), count: p.length }));
    const totals = rows.map((r) => r.total as number);
    const typical = matching.typicalPrice(totals);
    const live = rows.filter((r) => ['new', 'seen', 'starred'].includes(r.status as string)).map((r) => r.total as number);
    const best = live.length ? Math.min(...live) : null;
    const pct = best !== null ? matching.dealPct(best, totals) : null;
    return { watch_id: watch.id, days, points, typical: typical ? pyRound(typical, 2) : null,   // null until 5+ prices
      best_now: best, best_label: matching.dealLabel(pct), best_pct: pct };
  });

  // Finds without the sorted-on value (no miles stated, no price...) go last. NULLs sort as SQLite sorts them.
  const SIZE_NUMBER = sql.raw(`COALESCE(substring(l.size from '^[ \\t\\n\\f\\r]*([+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?)')::double precision, 0)`);
  const ORDERS: Record<string, ReturnType<typeof sql.raw>> = {
    newest: sql.raw('l.first_seen DESC'),
    price: sql.raw('l.total IS NULL, l.total ASC'),
    price_desc: sql.raw('l.total IS NULL, l.total DESC'),
    deal: sql.raw('l.deal_pct IS NULL, l.deal_pct DESC'),
    miles: sql.raw('l.miles IS NULL, l.miles ASC'),   // cars and trucks
    year_desc: sql.raw('l.year IS NULL, l.year DESC, l.miles IS NULL, l.miles ASC'),
    year_asc: sql.raw('l.year IS NULL, l.year ASC'),
    make: sql.raw('l.make IS NULL, lower(l.make COLLATE "C") ASC, l.year DESC NULLS LAST'),   // SQLite's NOCASE folds A-Z only
    size: sql`l.size IS NULL, ${SIZE_NUMBER} = 0, ${SIZE_NUMBER}, l.size COLLATE "C"`,   // clothes; CAST AS REAL as SQLite reads it
  };

  route('GET', '/api/listings', async (_req, _b, params, m) => {
    const where = [sql`w.member_id = ${m.id}`];
    if (params.watch) where.push(sql`l.watch_id = ${pyInt(params.watch)}`);
    if (params.source) {   // one or more, comma separated: "ebay,ebay_local"
      const wanted = params.source.split(',').filter((s) => s.trim()).slice(0, 20).map((s) => s.trim());
      where.push(sql`l.source IN (${sql.join(wanted.map((s) => sql`${s}`), sql`, `)})`);
    }
    const since = params.since_id;
    if (since !== undefined) {
      // For apps that alert: only finds added after the last one they saw, oldest first, any status unless asked.
      where.push(sql`l.id > ${pyInt(since)}`);
    }
    const status = params.status ?? (since !== undefined ? 'all' : 'active');
    if (status === 'active') where.push(sql`l.status IN ('new', 'seen', 'starred')`);
    else if (status !== 'all') where.push(sql`l.status = ${status}`);
    const order = (params.sort !== undefined && Object.hasOwn(ORDERS, params.sort) ? ORDERS[params.sort]! : null)
      ?? sql.raw(since !== undefined ? 'l.id ASC' : 'l.first_seen DESC');
    const listings = await store.query(sql`SELECT l.*, w.name AS watch_name FROM deals.listings l JOIN deals.watches w ON w.id = l.watch_id
      WHERE ${sql.join(where, sql` AND `)} ORDER BY ${order}, l.id LIMIT 500`);
    return { listings };
  });

  route('POST', `/api/listings/${ID}`, async (req, body, _p, m) => {
    if (!['new', 'seen', 'starred', 'dismissed'].includes(body.status as string)) throw fail(400, 'bad status');
    const lid = id(req);
    const changed = await store.query(sql`UPDATE deals.listings SET status = ${body.status as string} WHERE id = ${lid}
      AND watch_id IN (SELECT id FROM deals.watches WHERE member_id = ${m.id}) RETURNING watch_id`);
    if (!changed.length) throw fail(404, 'listing not found');
    // Starring, dismissing or restoring frees or takes a place among a watch's cheapest N.
    const [watch] = await store.query(sql`SELECT * FROM deals.watches WHERE id = ${changed[0]!.watch_id}`);
    if (watch!.keep_cheapest) await store.pruneCheapest(watch!);
    return { ok: true };
  });

  route('POST', '/api/listings/mark-seen', async (_req, body, _p, m) => {
    const mine = sql`watch_id IN (SELECT id FROM deals.watches WHERE member_id = ${m.id})`;
    if (truthy(body.watch)) {
      await app.cave.db.execute(sql`UPDATE deals.listings SET status = 'seen' WHERE status = 'new' AND watch_id = ${pyInt(body.watch)} AND ${mine}`);
    } else {
      await app.cave.db.execute(sql`UPDATE deals.listings SET status = 'seen' WHERE status = 'new' AND ${mine}`);
    }
    return { ok: true };
  });

  // ---- settings and the person ---------------------------------------------------------------------------------

  route('PUT', '/api/settings', async (_req, body, _p, m) => {
    // Blank secret fields in the form mean "keep the current value". The admin's AI is the owner console's.
    const changes: Record<string, unknown> = Object.fromEntries(Object.entries(body).filter(([k, v]) =>
      !(SECRET_KEYS.has(k) && pyIn(v, '', null, true)) && !(m.admin && ADMIN_MANAGED_AI.has(k))));
    if ('check_times' in changes) changes.check_times = cleanTimes(changes.check_times);
    if ('check_mode' in changes && !['interval', 'times'].includes(changes.check_mode as string)) delete changes.check_mode;
    if ('sizes' in changes) {   // {"department", "shoe", "top", "pants"}: short strings only
      const sizes = (changes.sizes && typeof changes.sizes === 'object' && !Array.isArray(changes.sizes) ? changes.sizes : {}) as Record<string, unknown>;
      changes.sizes = Object.fromEntries(['department', 'shoe', 'top', 'pants'].filter((k) => truthy(sizes[k]))
        .map((k) => [k, [...pySplit(String(sizes[k] === true ? 'True' : sizes[k])).join(' ')].slice(0, 20).join('')]));
    }
    // Addresses the server will fetch: Discord only for webhooks, and public addresses only for members.
    const current = await store.getSettings(m.id);
    try {
      if (truthy(changes.discord_webhook) && changes.discord_webhook !== current.discord_webhook) checkDiscord(String(changes.discord_webhook));
      for (const [key, what] of [['ollama_url', 'Ollama address'], ['ha_url', 'Home Assistant address']] as const) {
        if (truthy(changes[key]) && changes[key] !== current[key]) await checkAddress(String(changes[key]), m.admin, what, resolve);
      }
    } catch (e) {
      if (e instanceof BlockedURL) throw fail(400, e.message);
      throw e;
    }
    await store.updateSettings(changes, m.id, m.admin);   // members can only change their own settings
    return store.publicSettings(m.id, m.admin);
  });

  route('GET', '/api/me', async (req, _b, _p, m) => publicUser(m, req.session));

  // ---- people (admin) ------------------------------------------------------------------------------------------

  /** Local midnight today, and the first of this month: ai.py's _day_start and month_start. */
  const dayStart = (now: number) => { const d = new Date(now * 1000); return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 1000; };
  const monthStart = (now: number) => { const d = new Date(now * 1000); return new Date(d.getFullYear(), d.getMonth(), 1).getTime() / 1000; };

  route('GET', '/api/admin/users', async (_req, _b, _p, m) => {
    requireAdmin(m);
    const now = options.now();
    const adminId = await store.adminId();
    const rows = await store.query(sql`SELECT u.id, u.subject, p.name, p.email, CASE WHEN u.admin THEN 'admin' ELSE 'member' END AS role,
        u.disabled::int AS disabled, u.watch_limit, u.created_at,
        (SELECT COUNT(*)::int FROM deals.watches w WHERE w.member_id = u.id) AS watches,
        u.ai_shared::int AS ai_shared, u.ai_allowance, u.ai_daily_cap,
        (SELECT COALESCE(SUM(cost), 0) FROM deals.ai_usage a WHERE a.member_id = u.id AND a.paid_by = ${adminId} AND a.at >= ${monthStart(now)}) AS ai_spent,
        (SELECT COUNT(*)::int FROM deals.ai_usage a WHERE a.member_id = u.id AND a.paid_by = ${adminId} AND a.at >= ${dayStart(now)}) AS ai_today,
        COALESCE((SELECT s.value #>> '{}' FROM deals.user_settings s WHERE s.member_id = u.id AND s.key = 'ai_source'), 'shared') AS ai_source
      FROM deals.members u LEFT JOIN core.people p ON p.id = u.subject
      ORDER BY u.admin DESC, p.name COLLATE "C" NULLS FIRST, u.id`);
    // When each was last here: their newest browser session or phone, from the framework's sign-in records.
    const users = rows.map((r) => {
      const seen = app.cave.store.get('SELECT MAX(seen) AS seen FROM (SELECT seen FROM sessions WHERE subject = ? UNION ALL SELECT seen FROM devices WHERE subject = ?)',
        r.subject as string, r.subject as string);
      const { subject: _subject, ...user } = r;
      return { ...user, name: r.name ?? '', email: r.email ?? '', last_active: seen?.seen ?? null };
    });
    return { users, invites: [], default_watch_limit: DEFAULT_WATCH_LIMIT };
  });

  route('PUT', `/api/admin/users/${ID}`, async (req, body, _p, m) => {
    requireAdmin(m);
    const uid = id(req);
    const [target] = await store.query(sql`SELECT id FROM deals.members WHERE id = ${uid}`);
    if (!target) throw fail(404, 'not found');
    const set = (column: string, value: unknown) => app.cave.db.execute(sql`UPDATE deals.members SET ${sql.raw(column)} = ${value} WHERE id = ${uid}`);
    if ('disabled' in body) {
      if (uid === m.id) throw fail(400, "you can't disable yourself");
      await set('disabled', truthy(body.disabled));   // checked on every request, so it takes effect at once
    }
    if ('watch_limit' in body) await set('watch_limit', pyIn(body.watch_limit, null, '') ? null : Math.max(0, pyInt(body.watch_limit)));
    // Their use of the admin's AI: allowed at all, requests a day, and dollars a month of paid models.
    if ('ai_shared' in body) await set('ai_shared', truthy(body.ai_shared));
    if ('ai_daily_cap' in body) await set('ai_daily_cap', Math.max(0, pyInt(truthy(body.ai_daily_cap) ? body.ai_daily_cap : 0)));
    if ('ai_allowance' in body) await set('ai_allowance', Math.max(0, pyRound(pyFloat(truthy(body.ai_allowance) ? body.ai_allowance : 0), 2)));
    return { ok: true };
  });

  // ---- My Stuff: devices ----------------------------------------------------------------------------------------

  const nhtsa = options.nhtsa ?? nhtsaGet;
  const cpsc = options.cpsc ?? cpscGet;
  const notYet = () => fail(501, 'AI arrives in its own update; this works without "use AI" for now.', 'not_yet');
  const ownMachine = async (req: FastifyRequest, m: Member, message: string) => {
    const machine = await store.getMachine(id(req), m.id);
    if (!machine) throw fail(404, message);
    return machine;
  };

  route('POST', '/api/machines', async (_req, body, _p, m) => ({ id: await store.saveMachine(body, m.id) }));

  route('PUT', `/api/machines/${ID}`, async (req, body, _p, m) => {
    try {
      await store.saveMachine(body, m.id, id(req));
    } catch (e) {
      if (e instanceof DeviceNotFound) throw fail(404, 'device not found');
      throw e;
    }
    return { ok: true };
  });

  route('DELETE', `/api/machines/${ID}`, async (req, _b, _p, m) => {
    if (!await store.deleteMachine(id(req), m.id)) throw fail(404, 'device not found');
    return { ok: true };
  });

  route('POST', '/api/vin', async (_req, body) => {
    // Decode a VIN (NHTSA vPIC): make, model, year, trim, body, drive, engine.
    try {
      return await decodeVin('vin' in body ? body.vin : '', nhtsa);
    } catch (e) {
      if (e instanceof LookupFailed) throw fail(400, e.message);
      throw e;
    }
  });

  route('GET', `/api/machines/${ID}/recalls`, async (req, _b, _p, m) => {
    const machine = await ownMachine(req, m, 'device not found');
    const kind = (machine.kind as string) || 'pc';
    if (PARTS_KINDS.has(kind)) throw fail(400, 'recalls are for cars, appliances and other products, not built computers');
    try {
      if (kind === 'vehicle') {   // NHTSA
        const found = await vehicleRecalls(String(machine.make ?? ''), String(machine.model ?? ''), machine.year as number | null, options.now(), nhtsa);
        return { recalls: found, vehicle: `${pyStr(machine.year)} ${pyStr(machine.make)} ${pyStr(machine.model)}`, source: 'nhtsa' };
      }
      const found = await productRecalls(machine, options.now(), cpsc);   // CPSC
      const what = [machine.make, machine.model || machine.name].filter(truthy).map((x) => pyStr(x)).join(' ');
      return { recalls: found, vehicle: what, source: 'cpsc' };
    } catch (e) {
      if (e instanceof LookupFailed) throw fail(400, e.message);
      throw e;
    }
  });

  route('POST', `/api/machines/${ID}/suggest`, async (req, body, _p, m) => {
    const machine = await ownMachine(req, m, 'machine not found');
    const rules = PARTS_KINDS.has((machine.kind as string) || 'pc')
      ? hardware.suggest(machine as unknown as Parameters<typeof hardware.suggest>[0])
      : { platform: null, suggestions: [], explanation: 'the built-in rules only know PC parts; use AI or Watch this model' };
    if (truthy(body.use_ai)) throw notYet();
    return rules;
  });

  route('GET', '/api/specs/commands', async () => ({ windows: specs.WINDOWS_COMMAND, linux: specs.LINUX_COMMAND }));

  route('POST', `/api/machines/${ID}/specs`, async (req, body, _p, m) => {
    // Parts from pasted text: the Get specs command's output, or (with AI) anything else.
    const machine = await ownMachine(req, m, 'machine not found');
    const text = pyStrip(truthy(body.text) ? pyStr(body.text) : '');
    if (!text) throw fail(400, "Paste the command's output, or any text describing the computer");
    let parts: hardware.Part[];
    let method: string;
    if (specs.looksLikeReport(text)) {
      parts = specs.parseReport(text);
      method = 'report';
    } else if (truthy(body.use_ai)) {
      throw notYet();
    } else {
      throw fail(422, 'need_ai');
    }
    parts = parts.filter((p) => pyStrip(pyStr(p.model ?? '')));
    if (!parts.length) throw fail(400, "Couldn't find any parts in that text");
    const merged = specs.mergeParts(machine.parts as hardware.Part[], parts);
    await store.saveMachine({ ...machine, parts: merged }, m.id, machine.id as number);
    return { parts: merged, method, found: parts.length };
  });

  /** The Python app's RADAR_SKIP_KINDS: cheap gadgets and cars aren't what the radar is for. */
  const RADAR_SKIP_KINDS = new Set(['smart home', 'vehicle']);

  route('POST', '/api/radar', async (_req, body, _p, m) => {
    const watched = new Set((await store.listWatches(m.id)).map((w) => pyStrip(String(w.query)).toLowerCase()));
    const machines = await store.listMachines(m.id);
    const items: Record<string, unknown>[] = [];
    const skipped: Record<string, unknown>[] = [];
    for (const machine of machines) {
      if (!PARTS_KINDS.has((machine.kind as string) || 'pc')) continue;
      const rules = hardware.suggest(machine as unknown as Parameters<typeof hardware.suggest>[0]);
      if (!rules.platform) {
        skipped.push({ machine_id: machine.id, name: machine.name,
          why: !truthy(machine.parts) ? 'needs specs (press Get specs)' : rules.explanation });
        continue;
      }
      const usable = rules.suggestions.filter((s) => pyStrip(s.query));   // "RAM: DDR4 or DDR5?" has no query
      const picks = [...usable.filter((s) => s.category === 'cpu').slice(0, 1), ...usable.filter((s) => s.category === 'ram').slice(0, 1)];
      if (!picks.length) skipped.push({ machine_id: machine.id, name: machine.name, why: 'already has the best drop-in CPU' });
      for (const s of picks) items.push({ ...s, machine_id: machine.id, machine_name: machine.name, from: 'rules' });
    }
    const gear = machines.filter((g) => !PARTS_KINDS.has((g.kind as string) || 'pc') && !RADAR_SKIP_KINDS.has((g.kind as string) || 'pc') && truthy(g.model));
    if (gear.length && truthy(body.use_ai)) throw notYet();
    if (gear.length) skipped.push(...gear.map((g) => ({ machine_id: g.id, name: g.name, why: 'needs AI (turn it on in Settings)' })));
    for (const it of items) it.already = watched.has(pyStrip(String(it.query)).toLowerCase());
    return { items, skipped, gear_count: gear.length };
  });

  // ---- the Android app -----------------------------------------------------------------------------------------

  route('GET', '/api/app/android', async () => {
    const apk = join(options.downloadsDir, ANDROID_APK);
    if (!existsSync(apk)) return { available: false };
    let info: Record<string, unknown> = {};
    try {
      const infoPath = join(options.downloadsDir, 'android.json');
      if (existsSync(infoPath)) info = JSON.parse(readFileSync(infoPath, 'utf8')) as Record<string, unknown>;
    } catch {
      info = {};
    }
    const st = statSync(apk);
    return { available: true, url: '/download/android', version: info.versionName || info.version || null,
      version_code: info.versionCode ?? null, sha256: info.sha256 ?? null, size: st.size, published_at: st.mtimeMs / 1000 };
  });

  app.get('/download/android', async (req: FastifyRequest, reply: FastifyReply) => {
    if (!req.session) return reply.code(401).type('text/plain').send('Sign in to Deal Hunter to get the app');
    const apk = join(options.downloadsDir, ANDROID_APK);
    if (!existsSync(apk)) return reply.code(404).type('text/plain').send("The Android app hasn't been published yet");
    return reply.type('application/vnd.android.package-archive')
      .header('Content-Disposition', `attachment; filename="${ANDROID_APK}"`).send(createReadStream(apk));
  });

  // ---- retired: the app's own sign-in, and what replaced it --------------------------------------------------

  const gone = (method: 'GET' | 'POST' | 'DELETE' | 'PUT', url: string, message = OWN_LOGIN_OFF) =>
    app.route({ method, url, handler: async () => { throw fail(410, message); } });
  for (const [method, url] of [['POST', '/api/auth/setup'], ['POST', '/api/auth/login'], ['GET', '/api/auth/link'], ['POST', '/api/auth/accept'],
    ['POST', '/api/admin/invites'], ['DELETE', `/api/admin/invites/${ID}`], ['POST', `/api/admin/users/${ID}/reset`], ['POST', '/api/api-key'],
    ['GET', '/api/auth/status'], ['POST', '/api/auth/logout'], ['POST', '/api/me'], ['GET', '/api/tokens'], ['DELETE', `/api/tokens/${ID}`]] as const) {
    gone(method, url);
  }

  // Anything else under /api: signed out it answers like every other route, so nobody can map the API from outside.
  app.route({ method: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'], url: '/api/*', config: { signedIn: true },
    handler: async () => { throw fail(404, 'not found'); } });
}
