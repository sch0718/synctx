import { fileURLToPath } from 'node:url';
import { cloudflareTest, readD1Migrations } from '@cloudflare/vitest-plugin';
import { defineConfig } from 'vitest/config';

// D1은 패키지 시험처럼 로컬 D1(miniflare)로 돈다. 마이그레이션은 test/setup.ts가 적용한다.
export default defineConfig(async () => {
  const migrations = await readD1Migrations(fileURLToPath(new URL('./migrations', import.meta.url)));
  return {
    plugins: [
      cloudflareTest({
        miniflare: {
          compatibilityDate: '2026-11-01',
          d1Databases: ['DB'],
          bindings: { TEST_MIGRATIONS: migrations },
        },
      }),
    ],
    test: { include: ['test/**/*.test.ts'], setupFiles: ['./test/setup.ts'] },
  };
});
