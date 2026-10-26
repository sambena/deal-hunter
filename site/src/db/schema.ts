// Deal Hunter's tables, in its own schema (in cave-db the owner console's Add app made it; locally the kit does).
// They keep the Python app's SQLite tables (../db.py) column for column, so the one-time copy can be checked row by
// row and the API answers as it does today:
// - ids are the old ones (the Android app keeps them), so every id column takes a given value; new rows count on from
//   the highest;
// - times stay epoch seconds as floats (double precision), never converted;
// - columns that held JSON text are jsonb; a setting's value is the JSON value the Python app stored.
// A person is their central subject (a uuid); names and emails come from `people` in @cougarcave/server, never copied.
// Each row belongs to a member by the member's id, as it belonged to an account by user_id.
// After changing this file: npm run db:generate (writes a migration to drizzle/), then delete its CREATE SCHEMA line
// (in cave-db the schema exists and the app may not make one; a test checks), then commit it. The app runs any new
// migrations when it starts.
import { boolean, doublePrecision, index, integer, jsonb, pgSchema, primaryKey, text, unique, uuid } from 'drizzle-orm/pg-core';

export const SCHEMA = 'deals';
export const appSchema = pgSchema(SCHEMA);

/** The sources a watch made before the Python app's migrations searched (db.py's column default). */
export const DEFAULT_SOURCES = ['ebay', 'ebay_local', 'ksl', 'craigslist', 'offerup', 'reddit', 'slickdeals', 'buildapcsales'];

/** Who may use Deal Hunter, with the limits the admin sets (the Python app's users, without email and password).
 * The id is the old account id, which the People screen and the rows below use. */
export const members = appSchema.table('members', {
  id: integer('id').primaryKey().generatedByDefaultAsIdentity(),
  subject: uuid('subject').notNull().unique(),
  admin: boolean('admin').notNull().default(false),
  disabled: boolean('disabled').notNull().default(false),
  /** Null: no limit (the admin). Invited friends had 10. */
  watchLimit: integer('watch_limit'),
  createdAt: doublePrecision('created_at').notNull(),
  /** May use the admin's AI; dollars a month of the admin's paid models; requests a day on the admin's AI. */
  aiShared: boolean('ai_shared').notNull().default(true),
  aiAllowance: doublePrecision('ai_allowance').notNull().default(0),
  aiDailyCap: integer('ai_daily_cap').notNull().default(20),
});

/** The shared settings (only the admin changes them): sources and their keys, the check schedule, Reddit subs... */
export const settings = appSchema.table('settings', {
  key: text('key').primaryKey(),
  value: jsonb('value').notNull(),
});

/** Each person's own settings: ZIP and radius, junk words, Discord, their AI, focus, sizes. */
export const userSettings = appSchema.table('user_settings', {
  memberId: integer('member_id').notNull().references(() => members.id, { onDelete: 'cascade' }),
  key: text('key').notNull(),
  value: jsonb('value').notNull(),
}, (t) => [primaryKey({ columns: [t.memberId, t.key] })]);

/** A search someone keeps running. */
export const watches = appSchema.table('watches', {
  id: integer('id').primaryKey().generatedByDefaultAsIdentity(),
  memberId: integer('member_id').notNull().references(() => members.id),
  name: text('name').notNull(),
  query: text('query').notNull(),
  exclude: jsonb('exclude').notNull().default([]),
  minPrice: doublePrecision('min_price'),
  maxPrice: doublePrecision('max_price'),
  condition: text('condition').notNull().default('any'),
  includeAuctions: boolean('include_auctions').notNull().default(false),
  sources: jsonb('sources').notNull().default(DEFAULT_SOURCES),
  enabled: boolean('enabled').notNull().default(true),
  notes: text('notes').notNull().default(''),
  /** One of the owner's devices; no foreign key, as in the Python app (deleting a device clears it). */
  machineId: integer('machine_id'),
  createdAt: doublePrecision('created_at').notNull(),
  lastPolled: doublePrecision('last_polled'),
  lastError: text('last_error'),
  polledSources: jsonb('polled_sources').notNull().default([]),
  /** Keep only this many cheapest finds; null keeps all. */
  keepCheapest: integer('keep_cheapest'),
  /** "#rrggbb" the person picked; null takes one from the palette by id. */
  color: text('color'),
  /** What each site returned on the last check, and why listings dropped; null before the first. */
  lastCheck: jsonb('last_check'),
  /** item | vehicle (year range, miles) | clothing (size, department). */
  kind: text('kind').notNull().default('item'),
  /** tech | cars | home | clothes as picked; null is automatic. */
  category: text('category'),
  yearMin: integer('year_min'),
  yearMax: integer('year_max'),
  maxMiles: integer('max_miles'),
  size: text('size'),
  department: text('department'),
}, (t) => [index('watches_member').on(t.memberId)]);

/** A listing a watch found: new, seen, starred, dismissed or pruned (hidden by keep-the-cheapest). */
export const listings = appSchema.table('listings', {
  id: integer('id').primaryKey().generatedByDefaultAsIdentity(),
  watchId: integer('watch_id').notNull().references(() => watches.id, { onDelete: 'cascade' }),
  source: text('source').notNull(),
  sourceId: text('source_id').notNull(),
  title: text('title').notNull(),
  price: doublePrecision('price'),
  shipping: doublePrecision('shipping'),
  total: doublePrecision('total'),
  currency: text('currency'),
  url: text('url'),
  image: text('image'),
  location: text('location'),
  condition: text('condition'),
  buying: text('buying'),
  firstSeen: doublePrecision('first_seen').notNull(),
  status: text('status').notNull().default('new'),
  dealPct: doublePrecision('deal_pct'),
  aiNote: text('ai_note'),
  /** The last check that still found it. */
  seenAt: doublePrecision('seen_at'),
  /** Vehicles: model year, odometer miles, title (clean, salvage, rebuilt), make. */
  year: integer('year'),
  miles: integer('miles'),
  titleStatus: text('title_status'),
  /** Clothing: the listing's size, when the source or title says. */
  size: text('size'),
  make: text('make'),
}, (t) => [unique('listings_watch_source_id').on(t.watchId, t.source, t.sourceId),
           index('listings_watch_first_seen').on(t.watchId, t.firstSeen)]);

/** Everything someone owns ("My Stuff"): PCs and servers with a parts list, other things with a make and model. */
export const machines = appSchema.table('machines', {
  id: integer('id').primaryKey().generatedByDefaultAsIdentity(),
  memberId: integer('member_id').notNull().references(() => members.id),
  name: text('name').notNull(),
  notes: text('notes').notNull().default(''),
  parts: jsonb('parts').notNull().default([]),
  kind: text('kind').notNull().default('pc'),
  model: text('model').notNull().default(''),
  /** Where it was imported from (Home Assistant), if it was. */
  sourceRef: text('source_ref'),
  make: text('make').notNull().default(''),
  year: integer('year'),
  msrp: doublePrecision('msrp'),
  /** The purchase date as the person typed it. */
  purchased: text('purchased').notNull().default(''),
  pricePaid: doublePrecision('price_paid'),
  /** The person's own fields: [{"label", "value"}]. */
  custom: jsonb('custom').notNull().default([]),
  vin: text('vin').notNull().default(''),
  odometer: integer('odometer'),
}, (t) => [index('machines_member').on(t.memberId)]);

/** One AI request: what it cost, whose it was, and whose AI it ran on (their own, or the admin's when shared). */
export const aiUsage = appSchema.table('ai_usage', {
  id: integer('id').primaryKey().generatedByDefaultAsIdentity(),
  memberId: integer('member_id').notNull().references(() => members.id),
  at: doublePrecision('at').notNull(),
  provider: text('provider').notNull(),
  model: text('model').notNull(),
  action: text('action').notNull(),
  inputTokens: integer('input_tokens').notNull(),
  outputTokens: integer('output_tokens').notNull(),
  cost: doublePrecision('cost').notNull(),
  paidBy: integer('paid_by').references(() => members.id),
}, (t) => [index('ai_usage_at').on(t.at)]);
