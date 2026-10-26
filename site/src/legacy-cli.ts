// The one-time move of the Python app's data, run where the app runs (inside its container: node dist/legacy-cli.js …).
//   node dist/legacy-cli.js copy  --db <old deal-hunter.db> [--leave-out 5,6] [--replace]   copy, then check
//   node dist/legacy-cli.js check --db <old deal-hunter.db> [--leave-out 5,6] [--self-check]
// The old file is only read. The issuer is this site's (OIDC_ISSUER), or --issuer <url>: accounts come over by their
// active central link for it. An account without one stops the copy, listed by id; link it in the old app first, or
// name it in --leave-out (then none of its rows are copied). --self-check plants three differences in a transaction
// that is rolled back, and fails unless the checker reports each one.
// Exit codes: 0 copied (or checked) and matching, 1 a difference, 2 refused or failed. The reports name accounts and
// rows by id only.
import { fileURLToPath } from 'node:url';
import { loadConfig, openDatabase } from '@cougarcave/server';
import { SCHEMA } from './db/schema.js';
import { check, copy, describe, openOld, readOld, Refused, selfCheck } from './legacy/copy.js';

const [command, ...rest] = process.argv.slice(2);
const option = (name: string) => { const i = rest.indexOf(name); return i >= 0 ? rest[i + 1] : undefined; };
const file = option('--db');
const known = ['--db', '--issuer', '--leave-out', '--replace', '--self-check'];
const unknown = rest.filter((a) => a.startsWith('--') && !known.includes(a));
if (!file || !['copy', 'check'].includes(command ?? '') || unknown.length) {
  console.error('Usage: node dist/legacy-cli.js copy|check --db <old deal-hunter.db> [--issuer <url>] [--leave-out 5,6] [--replace] [--self-check]');
  process.exit(2);
}
const leaveOut = (option('--leave-out') ?? '').split(',').filter(Boolean).map(Number);
if (leaveOut.some((n) => !Number.isInteger(n) || n <= 0)) {
  console.error('--leave-out takes account ids, comma-separated');
  process.exit(2);
}
const config = loadConfig();
const issuer = option('--issuer') ?? config.OIDC_ISSUER ?? '';
if (!issuer) {
  console.error('Set OIDC_ISSUER, or give --issuer: accounts come over by their central link for it');
  process.exit(2);
}

const src = openOld(file);
const database = await openDatabase({ ...(config.DATABASE_URL ? { url: config.DATABASE_URL } : {}), dataDir: config.DATA_DIR });
const secrets = [config.DATABASE_URL].filter((s): s is string => Boolean(s));
const mask = (text: string) => secrets.reduce((t, s) => t.split(s).join('<DATABASE_URL>'), text);
try {
  const old = readOld(src, issuer, leaveOut);
  if (old.leftOut.length) console.log(`Left out (accounts ${leaveOut.join(', ')}):\n  ${old.leftOut.join('\n  ')}`);
  console.log(`Not copied (the framework replaces them): ${Object.entries(old.retired).map(([t, n]) => `${t} ${n}`).join(', ')}`);
  if (command === 'copy') {
    await database.migrate(fileURLToPath(new URL('../drizzle/', import.meta.url)), SCHEMA);
    const counts = await copy(database.db, old, { replace: rest.includes('--replace') });
    console.log(`Copied: ${Object.entries(counts).map(([t, n]) => `${t} ${n}`).join(', ')}`);
  }
  const results = await check(database.db, old);
  console.log(describe(results).join('\n'));
  const differ = results.filter((r) => r.differences.length).length;
  console.log(differ ? `\n${differ} table(s) differ` : '\nEvery table matches.');
  process.exitCode = differ ? 1 : 0;
  if (!differ && rest.includes('--self-check')) {
    const s = await selfCheck(database.db, old);
    const missed = s.planted.filter((p) => !s.caught.some((c) => c.startsWith(p.split(' ')[0]!)));
    console.log(`Self-check (rolled back): planted ${s.planted.join('; ')}\n  the checker reported:\n  ${s.caught.join('\n  ')}`);
    if (missed.length || s.caught.length < s.planted.length) {
      console.log(`SELF-CHECK FAILED: not caught: ${missed.join('; ') || 'too few differences reported'}`);
      process.exitCode = 1;
    }
  }
} catch (err) {
  console.error(mask(err instanceof Refused ? `refused: ${err.message}` : `failed: ${(err as Error).message} (the copy is one transaction: a failure inside it writes nothing)`));
  process.exitCode = 2;
} finally {
  src.close();
  await database.close();
}
