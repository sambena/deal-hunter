// The one-time copy of the Python app's SQLite database (data/deal-hunter.db) into the deals schema, and the checker
// that compares the two (#457). Every column as is, ids kept, each id sequence moved past the old one.
// - The old file is opened read-only and never written.
// - People: an account comes over only if it has an active central link for this site's issuer; its id stays the
//   member id and the link's subject becomes the member's subject. Any other account stops the copy, listed by id,
//   unless it is named in leaveOut, whose accounts are not copied with any of their rows.
// - The copy is one transaction: a refusal or a failure writes nothing. Tables that already hold rows are refused
//   unless replace is set, which empties them first.
// - The checker compares, per table: the row count, a checksum over every column of every row in key order, and the
//   next id. It reads the old values its own way (canonOld), not through the copy's conversion (toPg), so a mistake in
//   the conversion shows as a difference.
// The app's own sign-in (users' email and password, tokens, invites), the central sign-in state and phone pairings
// are not copied: the framework replaces them. Their counts are reported; the old file, kept with the backup, holds
// them and the central audit.
import { createHash } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import type { Db } from '@cougarcave/server';
import { sql } from 'drizzle-orm';
import { SCHEMA } from '../db/schema.js';

type Kind = 'int' | 'real' | 'text' | 'bool' | 'json' | 'uuid' | 'role';
interface Col { name: string; old: string; kind: Kind; nullable?: boolean }
interface Table { name: string; source: string; key: string[]; cols: Col[]; dropped?: string[]; serial?: boolean }
type Raw = Record<string, unknown>;
type Exec = Pick<Db, 'execute'>;

const c = (name: string, kind: Kind, nullable = false, old = name): Col => ({ name, old, kind, nullable });

/** The user_version the copy is written for: db.py's migrations up to 9. */
export const USER_VERSION = 9;

export const TABLES: Table[] = [
  { name: 'members', source: 'users', key: ['id'], serial: true, dropped: ['email', 'name', 'password_hash'],
    cols: [c('id', 'int'), c('subject', 'uuid', false, '_subject'), c('admin', 'role', false, 'role'), c('disabled', 'bool'),
      c('watch_limit', 'int', true), c('created_at', 'real'), c('ai_shared', 'bool'), c('ai_allowance', 'real'), c('ai_daily_cap', 'int')] },
  { name: 'settings', source: 'settings', key: ['key'], cols: [c('key', 'text'), c('value', 'json')] },
  { name: 'user_settings', source: 'user_settings', key: ['member_id', 'key'],
    cols: [c('member_id', 'int', false, 'user_id'), c('key', 'text'), c('value', 'json')] },
  { name: 'machines', source: 'machines', key: ['id'], serial: true,
    cols: [c('id', 'int'), c('member_id', 'int', false, 'user_id'), c('name', 'text'), c('notes', 'text'), c('parts', 'json'),
      c('kind', 'text'), c('model', 'text'), c('source_ref', 'text', true), c('make', 'text'), c('year', 'int', true),
      c('msrp', 'real', true), c('purchased', 'text'), c('price_paid', 'real', true), c('custom', 'json'), c('vin', 'text'),
      c('odometer', 'int', true)] },
  { name: 'watches', source: 'watches', key: ['id'], serial: true,
    cols: [c('id', 'int'), c('member_id', 'int', false, 'user_id'), c('name', 'text'), c('query', 'text'), c('exclude', 'json'),
      c('min_price', 'real', true), c('max_price', 'real', true), c('condition', 'text'), c('include_auctions', 'bool'),
      c('sources', 'json'), c('enabled', 'bool'), c('notes', 'text'), c('machine_id', 'int', true), c('created_at', 'real'),
      c('last_polled', 'real', true), c('last_error', 'text', true), c('polled_sources', 'json'), c('keep_cheapest', 'int', true),
      c('color', 'text', true), c('last_check', 'json', true), c('kind', 'text'), c('category', 'text', true),
      c('year_min', 'int', true), c('year_max', 'int', true), c('max_miles', 'int', true), c('size', 'text', true),
      c('department', 'text', true)] },
  { name: 'listings', source: 'listings', key: ['id'], serial: true,
    cols: [c('id', 'int'), c('watch_id', 'int'), c('source', 'text'), c('source_id', 'text'), c('title', 'text'),
      c('price', 'real', true), c('shipping', 'real', true), c('total', 'real', true), c('currency', 'text', true),
      c('url', 'text', true), c('image', 'text', true), c('location', 'text', true), c('condition', 'text', true),
      c('buying', 'text', true), c('first_seen', 'real'), c('status', 'text'), c('deal_pct', 'real', true),
      c('ai_note', 'text', true), c('seen_at', 'real', true), c('year', 'int', true), c('miles', 'int', true),
      c('title_status', 'text', true), c('size', 'text', true), c('make', 'text', true)] },
  { name: 'ai_usage', source: 'ai_usage', key: ['id'], serial: true,
    cols: [c('id', 'int'), c('member_id', 'int', false, 'user_id'), c('at', 'real'), c('provider', 'text'), c('model', 'text'),
      c('action', 'text'), c('input_tokens', 'int'), c('output_tokens', 'int'), c('cost', 'real'), c('paid_by', 'int', true)] },
];

/** Tables whose rows the framework replaces, reported by count only. */
export const RETIRED = ['tokens', 'invites', 'pair_codes', 'paired_devices', 'central_identities', 'central_state',
  'central_link_states', 'central_logout_jti', 'central_logout_attempts', 'central_audit'];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const INT_MIN = -(2 ** 31), INT_MAX = 2 ** 31 - 1;
const BATCH = 200;

export class Refused extends Error {}

export interface Old {
  /** Rows to copy, per table, with the old values as SQLite gave them (bigint for integers), in key order. */
  rows: Map<string, Raw[]>;
  /** The id each table's sequence must go past: the highest id ever given (sqlite_sequence), or the highest row. */
  lastIds: Map<string, number>;
  /** Accounts with no active link for the issuer, not left out: "account 5 (no link for this issuer)". */
  unlinked: string[];
  /** Accounts left out, with what of theirs is not copied. */
  leftOut: string[];
  /** Rows of retired tables, by table. */
  retired: Record<string, number>;
}

const q = (name: string) => `"${name.replace(/"/g, '""')}"`;
const rowsOf = (result: unknown): Raw[] => (Array.isArray(result) ? result : (result as { rows?: Raw[] }).rows ?? []);
const where = (t: Table, row: Raw) => `${t.name} ${t.key.map((k) => `${k} ${String(row[t.cols.find((x) => x.name === k)!.old])}`).join(', ')}`;

/** Opens the old file read-only. */
export function openOld(file: string): DatabaseSync {
  return new DatabaseSync(file, { readOnly: true });
}

/** Reads everything the copy takes from the old file, refusing a file this copy wasn't written for. */
export function readOld(src: DatabaseSync, issuer: string, leaveOut: number[] = []): Old {
  const version = Number((src.prepare('PRAGMA user_version').get() as { user_version: number }).user_version);
  if (version !== USER_VERSION) throw new Refused(`the old database is at version ${version}; this copy is for version ${USER_VERSION}`);
  for (const t of TABLES) {
    const theirs = (src.prepare(`PRAGMA table_info(${q(t.source)})`).all() as { name: string }[]).map((x) => x.name);
    if (!theirs.length) throw new Refused(`the old database has no ${t.source} table`);
    const ours = [...t.cols.map((x) => x.old).filter((x) => !x.startsWith('_')), ...(t.dropped ?? [])];
    const onlyOld = theirs.filter((x) => !ours.includes(x));
    const onlyNew = ours.filter((x) => !theirs.includes(x));
    if (onlyOld.length || onlyNew.length) {
      throw new Refused(`${t.source}: columns differ (only in the old database: ${onlyOld.join(', ') || 'none'}; only in the copy: ${onlyNew.join(', ') || 'none'})`);
    }
  }

  const select = (t: Table, extra = '') => {
    const cols = t.cols.filter((x) => !x.old.startsWith('_')).map((x) => `${q(t.source)}.${q(x.old)}`);
    const stmt = src.prepare(`SELECT ${cols.join(', ')}${extra} FROM ${q(t.source)}${t.source === 'users'
      ? ' LEFT JOIN central_identities ci ON ci.user_id = users.id AND ci.issuer = ? AND ci.status = \'active\'' : ''} ORDER BY ${t.key.map((k) => `${q(t.source)}.${q(t.cols.find((x) => x.name === k)!.old)}`).join(', ')}`);
    stmt.setReadBigInts(true);
    return (t.source === 'users' ? stmt.all(issuer) : stmt.all()) as Raw[];
  };

  const out: Old = { rows: new Map(), lastIds: new Map(), unlinked: [], leftOut: [], retired: {} };
  const users = select(TABLES[0]!, ', ci.subject AS _subject');
  const left = new Set(leaveOut);
  const known = new Set(users.map((u) => Number(u.id)));
  for (const id of left) if (!known.has(id)) throw new Refused(`there is no account ${id} to leave out`);
  for (const u of users) {
    if (u._subject === null && !left.has(Number(u.id))) out.unlinked.push(`account ${String(u.id)} (${String(u.role)}): no active link for this issuer`);
  }
  const members = users.filter((u) => !left.has(Number(u.id)));
  out.rows.set('members', members);

  const watchOwner = new Map<number, number>();
  for (const t of TABLES.slice(1)) {
    let rows = select(t);
    if (t.name === 'watches') for (const r of rows) watchOwner.set(Number(r.id), Number(r.user_id));
    const owner = (r: Raw) => t.name === 'listings' ? watchOwner.get(Number(r.watch_id)) : r.user_id === undefined ? undefined : Number(r.user_id);
    const dropped = rows.filter((r) => { const o = owner(r); return o !== undefined && left.has(o); });
    if (dropped.length) out.leftOut.push(`${t.name}: ${dropped.length} row(s) of the accounts left out`);
    rows = rows.filter((r) => !dropped.includes(r));
    if (t.name === 'ai_usage') {
      const paidByLeft = rows.filter((r) => r.paid_by !== null && left.has(Number(r.paid_by)));
      if (paidByLeft.length) throw new Refused(`ai_usage: ${paidByLeft.length} row(s) of copied accounts were paid by an account left out`);
    }
    out.rows.set(t.name, rows);
  }

  let sequence = new Map<string, number>();
  try {
    sequence = new Map((src.prepare('SELECT name, seq FROM sqlite_sequence').all() as { name: string; seq: number }[]).map((r) => [r.name, Number(r.seq)]));
  } catch { /* no AUTOINCREMENT table has had a row yet */ }
  for (const t of TABLES.filter((x) => x.serial)) {
    const max = Number((src.prepare(`SELECT COALESCE(MAX(id), 0) AS m FROM ${q(t.source)}`).get() as { m: number }).m);
    out.lastIds.set(t.name, Math.max(max, sequence.get(t.source) ?? 0));
  }
  for (const name of RETIRED) {
    try {
      out.retired[name] = Number((src.prepare(`SELECT COUNT(*) AS n FROM ${q(name)}`).get() as { n: number }).n);
    } catch {
      out.retired[name] = 0;
    }
  }
  return out;
}

// ---- the copy's conversion -------------------------------------------------------------------------------------

/** One old value as the new column takes it. A value of another type is refused, never converted quietly. */
function toPg(value: unknown, col: Col, at: string): unknown {
  if (value === null || value === undefined) {
    if (col.nullable) return null;
    throw new Refused(`${at}: ${col.name} is empty, and the new column needs a value`);
  }
  const bad = () => new Refused(`${at}: ${col.name} holds ${typeof value} ${JSON.stringify(String(value)).slice(0, 40)}, not ${col.kind}`);
  switch (col.kind) {
    case 'int':
      if (typeof value === 'bigint' && value >= BigInt(INT_MIN) && value <= BigInt(INT_MAX)) return Number(value);
      throw bad();
    case 'real':
      if (typeof value === 'number' && Number.isFinite(value)) return value;
      if (typeof value === 'bigint' && Number.isSafeInteger(Number(value))) return Number(value);
      throw bad();
    case 'bool':
      if (value === 0n || value === 1n) return value === 1n;
      throw bad();
    case 'text':
      if (typeof value === 'string') return value;
      throw bad();
    case 'uuid':
      if (typeof value === 'string' && UUID.test(value)) return value;
      throw bad();
    case 'role':
      if (value === 'admin' || value === 'member') return value === 'admin';
      throw bad();
    case 'json':
      if (typeof value !== 'string') throw bad();
      try {
        JSON.parse(value);   // checked here; Postgres reads the text itself, so numbers keep every digit
        return value;
      } catch {
        throw new Refused(`${at}: ${col.name} is not valid JSON`);
      }
  }
}

/** Copies the old rows in, in one transaction. Refuses if any account would be lost, or the tables hold rows. */
export async function copy(db: Db, old: Old, options: { replace?: boolean } = {}): Promise<Record<string, number>> {
  if (old.unlinked.length) throw new Refused(`accounts with no active link for this issuer: ${old.unlinked.join('; ')}. Link them in the old app, or leave them out.`);
  const full: string[] = [];
  for (const t of TABLES) {
    const [row] = rowsOf(await db.execute(sql.raw(`SELECT count(*)::int AS n FROM ${SCHEMA}.${q(t.name)}`)));
    if (Number(row!.n) > 0) full.push(t.name);
  }
  if (full.length && !options.replace) throw new Refused(`the deals tables already hold rows (${full.join(', ')}). Replace them only on purpose (--replace).`);

  // Every value converted first, so a refusal comes before anything is written.
  const converted = new Map(TABLES.map((t) => [t.name, old.rows.get(t.name)!.map((r) =>
    t.cols.map((col) => ({ col, value: toPg(r[col.old], col, where(t, r)) })))]));
  const counts: Record<string, number> = {};
  await db.transaction(async (tx) => {
    if (full.length) await tx.execute(sql.raw(`TRUNCATE ${TABLES.map((t) => `${SCHEMA}.${q(t.name)}`).join(', ')} RESTART IDENTITY`));
    for (const t of TABLES) {
      const rows = converted.get(t.name)!;
      for (let i = 0; i < rows.length; i += BATCH) {
        const values = sql.join(rows.slice(i, i + BATCH).map((r) => sql`(${sql.join(r.map(({ col, value }) =>
          col.kind === 'json' && value !== null ? sql`${value}::jsonb` : col.kind === 'uuid' ? sql`${value}::uuid` : sql`${value}`), sql`, `)})`), sql`, `);
        await tx.execute(sql`INSERT INTO ${sql.raw(`${SCHEMA}.${q(t.name)}`)} (${sql.raw(t.cols.map((x) => q(x.name)).join(', '))}) OVERRIDING SYSTEM VALUE VALUES ${values}`);
      }
      if (t.serial) {
        const last = old.lastIds.get(t.name)!;
        // setval(n, true): the next id is n + 1. With nothing before it, the sequence starts at 1 as on a new table.
        await tx.execute(sql.raw(`SELECT setval(pg_get_serial_sequence('${SCHEMA}.${t.name}', 'id'), ${Math.max(last, 1)}, ${last > 0})`));
      }
      counts[t.name] = rows.length;
    }
  });
  return counts;
}

// ---- the checker -----------------------------------------------------------------------------------------------

/** Sorts object keys, so two JSON values compare by content: jsonb keeps its own key order. */
function canonicalJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalJson);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((k) => [k, canonicalJson((value as Raw)[k])]));
  }
  return value;
}

/** An old value in the checker's common form, read straight from SQLite's value. */
function canonOld(value: unknown, kind: Kind): unknown {
  if (value === null || value === undefined) return null;
  switch (kind) {
    case 'int': return typeof value === 'bigint' ? value.toString() : `?${String(value)}`;
    case 'real': return typeof value === 'bigint' ? Number(value) : value;
    case 'bool': return typeof value === 'bigint' ? Number(value) : `?${String(value)}`;
    case 'role': return value === 'admin' ? 1 : value === 'member' ? 0 : `?${String(value)}`;
    case 'json': try { return canonicalJson(JSON.parse(String(value))); } catch { return `?${String(value)}`; }
    default: return value;
  }
}

/** A new value in the same form, read from Postgres. */
function canonNew(value: unknown, kind: Kind): unknown {
  if (value === null || value === undefined) return null;
  switch (kind) {
    case 'int': return String(value);
    case 'real': return Number(value);
    case 'bool': case 'role': return value === true ? 1 : value === false ? 0 : `?${String(value)}`;
    case 'json': return canonicalJson(JSON.parse(String(value)));   // read as Postgres's own text (::text)
    default: return value;
  }
}

const rowHash = (row: unknown[]) => JSON.stringify(row);
const checksum = (rows: unknown[][]) => {
  const hash = createHash('sha256');
  for (const r of rows) hash.update(rowHash(r) + '\n');
  return hash.digest('hex');
};

/** Postgres's key order for text can follow its collation; both sides are sorted here by code unit instead. */
function byKey(t: Table) {
  const idx = t.key.map((k) => t.cols.findIndex((x) => x.name === k));
  const key = (r: unknown[]) => idx.map((i) => (t.cols[i]!.kind === 'int' ? Number(r[i]) : r[i]));
  return (a: unknown[], b: unknown[]) => {
    const ka = key(a), kb = key(b);
    for (let i = 0; i < ka.length; i++) {
      if (ka[i]! < kb[i]!) return -1;
      if (ka[i]! > kb[i]!) return 1;
    }
    return 0;
  };
}

async function nextId(db: Exec, t: Table): Promise<number> {
  const [row] = rowsOf(await db.execute(sql.raw(`SELECT pg_get_serial_sequence('${SCHEMA}.${t.name}', 'id') AS seq`)));
  const [state] = rowsOf(await db.execute(sql.raw(`SELECT last_value, is_called FROM ${String(row!.seq)}`)));
  return state!.is_called ? Number(state!.last_value) + 1 : Number(state!.last_value);
}

export interface TableCheck { table: string; oldRows: number; newRows: number; checksum: boolean; nextId: string; differences: string[] }

/** Compares every table; an empty differences list on every table means the copy matches. */
export async function check(db: Exec, old: Old): Promise<TableCheck[]> {
  const out: TableCheck[] = [];
  for (const t of TABLES) {
    const sort = byKey(t);
    const oldRows = old.rows.get(t.name)!.map((r) => t.cols.map((col) => canonOld(r[col.old], col.kind))).sort(sort);
    // jsonb as its text, so the driver's own parsing plays no part and SQL NULL stays apart from JSON null.
    const newRows = rowsOf(await db.execute(sql.raw(`SELECT ${t.cols.map((x) => (x.kind === 'json' ? `${q(x.name)}::text AS ${q(x.name)}` : q(x.name))).join(', ')} FROM ${SCHEMA}.${q(t.name)}`)))
      .map((r) => t.cols.map((col) => canonNew(r[col.name], col.kind))).sort(sort);
    const same = checksum(oldRows) === checksum(newRows);
    const differences: string[] = [];
    if (oldRows.length !== newRows.length) differences.push(`${oldRows.length} old rows, ${newRows.length} new`);
    if (!same) {
      // Name the rows that differ, by key: missing, extra, or which columns changed.
      const keyOf = (r: unknown[]) => JSON.stringify(t.key.map((k) => r[t.cols.findIndex((x) => x.name === k)]));
      const newByKey = new Map(newRows.map((r) => [keyOf(r), r]));
      const oldKeys = new Set(oldRows.map(keyOf));
      for (const r of oldRows) {
        const n = newByKey.get(keyOf(r));
        if (!n) differences.push(`missing ${keyOf(r)}`);
        else if (rowHash(n) !== rowHash(r)) differences.push(`changed ${keyOf(r)}: ${t.cols.filter((_, i) => rowHash([r[i]]) !== rowHash([n[i]])).map((x) => x.name).join(', ')}`);
      }
      for (const r of newRows) if (!oldKeys.has(keyOf(r))) differences.push(`extra ${keyOf(r)}`);
      if (!differences.length) differences.push('checksum differs');
    }
    let next = '-';
    if (t.serial) {
      const expected = old.lastIds.get(t.name)! + 1;
      const actual = await nextId(db, t);
      next = String(actual);
      if (actual !== expected) differences.push(`next id ${actual}, expected ${expected}`);
    }
    out.push({ table: t.name, oldRows: oldRows.length, newRows: newRows.length, checksum: same, nextId: next, differences });
  }
  return out;
}

class Rollback extends Error {}

/** Plants three differences inside a transaction (a changed value, a missing row, an extra row), runs the checker
 * there, and rolls everything back. Each plant must show up in what the checker reports. */
export async function selfCheck(db: Db, old: Old): Promise<{ planted: string[]; caught: string[] }> {
  const planted: string[] = [];
  let caught: string[] = [];
  const listing = old.rows.get('listings')![0];
  const usage = old.rows.get('ai_usage')!.at(-1);
  if (!listing || !usage) throw new Refused('the self-check needs at least one listing and one AI record');
  try {
    await db.transaction(async (tx) => {
      await tx.execute(sql`UPDATE ${sql.raw(`${SCHEMA}.listings`)} SET title = title || ' (planted)' WHERE id = ${Number(listing.id)}`);
      planted.push(`listings ["${String(listing.id)}"] title changed`);
      await tx.execute(sql`DELETE FROM ${sql.raw(`${SCHEMA}.ai_usage`)} WHERE id = ${Number(usage.id)}`);
      planted.push(`ai_usage ["${String(usage.id)}"] removed`);
      await tx.execute(sql`INSERT INTO ${sql.raw(`${SCHEMA}.settings`)} (key, value) VALUES ('planted', '1'::jsonb)`);
      planted.push('settings ["planted"] added');
      caught = (await check(tx, old)).flatMap((r) => r.differences.map((d) => `${r.table}: ${d}`));
      throw new Rollback();
    });
  } catch (e) {
    if (!(e instanceof Rollback)) throw e;
  }
  return { planted, caught };
}

/** For the report and the command line: one line per table. */
export function describe(results: TableCheck[]): string[] {
  return results.map((r) => `${r.table.padEnd(14)} ${String(r.oldRows).padStart(7)} old ${String(r.newRows).padStart(7)} new  `
    + `checksum ${r.checksum ? 'same' : 'DIFFERS'}  next id ${r.nextId}${r.differences.length ? `\n    ${r.differences.join('\n    ')}` : ''}`);
}
