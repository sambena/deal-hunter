// drizzle-kit (npm run db:generate) writes migrations for Deal Hunter's own schema only.
import { defineConfig } from 'drizzle-kit';

export default defineConfig({
  dialect: 'postgresql',
  schema: './src/db/schema.ts',
  out: './drizzle',
  schemaFilter: ['deals'],
});
