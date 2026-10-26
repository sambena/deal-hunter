// The page: client/ is built into dist/public, which the server serves at /. The kit (/cave/…) is served by the
// server from @cougarcave/ui, so the build leaves those imports alone: one Lit per page, shared with the kit.
import { defineConfig } from 'vite';

export default defineConfig({
  root: 'client',
  build: {
    outDir: '../dist/public',
    emptyOutDir: true,
    target: 'es2023',
    rollupOptions: { external: (id) => id.startsWith('/cave/') },
  },
  test: {
    root: '.',
    include: ['test/**/*.test.ts', 'client/**/*.test.ts'],
    alias: { '/cave/cave-ui.js': '@cougarcave/ui' },
  },
});
