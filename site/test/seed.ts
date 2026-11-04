// Puts a recorded starting point (test/fixtures/*.json, made once from the retired Python app) into the deals tables.
import type { Db } from '@cougarcave/server';
import { sql } from 'drizzle-orm';
import { SCHEMA } from '../src/db/schema.js';

export interface Start { rows: Record<string, unknown[]>; sequences: Record<string, { last: number; called: boolean }> }

const NAME = /^[a-z_]+$/;

export async function seed(db: Db, start: Start): Promise<void> {
  for (const [table, rows] of Object.entries(start.rows)) {
    if (!NAME.test(table)) throw new Error(`bad table name ${table}`);
    if (rows.length) await db.execute(sql.raw(`INSERT INTO ${SCHEMA}.${table} SELECT * FROM json_populate_recordset(NULL::${SCHEMA}.${table}, '${JSON.stringify(rows).replace(/'/g, "''")}'::json)`));
  }
  for (const [table, s] of Object.entries(start.sequences)) {
    if (!NAME.test(table)) throw new Error(`bad table name ${table}`);
    await db.execute(sql.raw(`SELECT setval('${SCHEMA}.${table}_id_seq', ${Number(s.last)}, ${s.called ? 'true' : 'false'})`));
  }
}
