// npm run dev: the server (restarted when src/ changes) and the pages (rebuilt when client/ changes), together.
// Ctrl+C stops both.
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const vite = join(dirname(require.resolve('vite/package.json')), 'bin', 'vite.js');
const children = [
  spawn(process.execPath, [vite, 'build', '--watch', '--logLevel', 'warn'], { stdio: 'inherit' }),
  spawn(process.execPath, ['--env-file-if-exists=.env', '--import', 'tsx', '--watch', 'src/server.ts'], { stdio: 'inherit' }),
];
const stop = () => { for (const child of children) child.kill(); process.exit(0); };
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
for (const child of children) child.on('exit', (code) => { if (code) stop(); });
