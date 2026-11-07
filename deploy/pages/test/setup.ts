import { applyD1Migrations, env } from 'cloudflare:test';
import { beforeEach } from 'vitest';

await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

beforeEach(async () => {
  await env.DB.batch(
    ['audit_log', 'invites', 'repo_members', 'site_roles', 'sessions', 'users'].map((t) => env.DB.prepare(`DELETE FROM ${t}`)),
  );
});
