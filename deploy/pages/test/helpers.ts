import { env as testEnv } from 'cloudflare:test';
import type { CertSource } from '@sch0718/firebase-session/worker';
import type { Env } from '../src/env';
import { SESSION_COOKIE } from '../src/session';

export const ORIGIN = 'https://synctx.test';
export const OWNER = 'owner@example.com';
export const FIREBASE_WEB_CONFIG = JSON.stringify({
  apiKey: 'test-api-key',
  authDomain: 'synctx-test.firebaseapp.com',
  projectId: 'synctx-test',
  appId: '1:2:web:3',
});

export const REPOS = [
  { id: 'repo-a', name: 'A 저장소' },
  { id: 'repo-b', name: 'B 저장소' },
];

/** env.ASSETS 대신. /_synctx/repos.json만 돌려준다. */
export const assets = {
  fetch: async (input: RequestInfo | URL) => {
    const url = new URL(input instanceof Request ? input.url : String(input));
    return url.pathname === '/_synctx/repos.json'
      ? new Response(JSON.stringify(REPOS), { headers: { 'Content-Type': 'application/json' } })
      : new Response('not found', { status: 404 });
  },
} as unknown as Fetcher;

export function makeEnv(over: Partial<Env> = {}): Env {
  return {
    DB: testEnv.DB,
    ASSETS: assets,
    OWNER_ACCOUNTS: OWNER,
    FIREBASE_PROJECT_ID: 'synctx-test',
    FIREBASE_WEB_CONFIG,
    KAKAO_CLIENT_ID: 'kakao-client',
    NAVER_CLIENT_ID: 'naver-client',
    NAVER_CLIENT_SECRET: 'naver-secret',
    ...over,
  };
}

async function sha256Hex(s: string): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

let n = 0;
/** 세션을 DB에 바로 만든다(패키지 create와 같은 행 모양). 쿠키에 실을 세션 id를 돌려준다. */
export async function seedSession(identity: string, opts: { revoked?: boolean } = {}): Promise<string> {
  n += 1;
  const sessionId = `${'s'.repeat(38)}${String(n).padStart(5, '0')}`;
  const userId = `u-${n}`;
  const now = Date.now();
  await testEnv.DB.batch([
    testEnv.DB.prepare(
      'INSERT INTO users (id, firebase_sub, email, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)',
    ).bind(userId, identity.includes(':') ? identity : `sub-${n}`, identity.includes(':') ? '' : identity, now, now),
    testEnv.DB.prepare(
      'INSERT INTO sessions (id_hash, user_id, label, created_at, last_used_at, revoked_at, identity) VALUES (?, ?, ?, ?, ?, ?, ?)',
    ).bind(await sha256Hex(sessionId), userId, 't', now, now, opts.revoked ? now : null, identity),
  ]);
  return sessionId;
}

export function request(path: string, init: RequestInit & { session?: string } = {}): Request {
  const headers = new Headers(init.headers);
  if (init.session) headers.set('Cookie', `other=1; ${SESSION_COOKIE}=${init.session}`);
  return new Request(`${ORIGIN}${path}`, { ...init, headers });
}

export function post(path: string, body: unknown, init: RequestInit & { session?: string; origin?: string | null } = {}) {
  const headers = new Headers(init.headers);
  headers.set('Content-Type', 'application/json');
  if (init.origin !== null) headers.set('Origin', init.origin ?? ORIGIN);
  return request(path, { ...init, method: init.method ?? 'POST', headers, body: JSON.stringify(body) });
}

export async function addMember(repoId: string, identity: string, role: 'manager' | 'viewer') {
  await testEnv.DB.prepare(
    'INSERT INTO repo_members (repo_id, identity, role, added_by, added_at) VALUES (?, ?, ?, ?, ?)',
  )
    .bind(repoId, identity, role, OWNER, Date.now())
    .run();
}

function b64url(bytes: Uint8Array | string): string {
  const raw = typeof bytes === 'string' ? new TextEncoder().encode(bytes) : bytes;
  return btoa(String.fromCharCode(...raw)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** 시험용 Google 인증서와 ID 토큰 서명기. */
export async function fakeGoogle() {
  const pair = (await crypto.subtle.generateKey(
    { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' },
    true,
    ['sign', 'verify'],
  )) as CryptoKeyPair;
  const certs: CertSource = { key: async (kid) => (kid === 'k1' ? (pair.publicKey as never) : null) };
  async function idToken(email: string): Promise<string> {
    const now = Math.floor(Date.now() / 1000);
    const head = b64url(JSON.stringify({ alg: 'RS256', kid: 'k1', typ: 'JWT' }));
    const body = b64url(
      JSON.stringify({
        iss: 'https://securetoken.google.com/synctx-test',
        aud: 'synctx-test',
        sub: `google-${email}`,
        iat: now,
        exp: now + 3600,
        auth_time: now,
        email,
        email_verified: true,
      }),
    );
    const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', pair.privateKey, new TextEncoder().encode(`${head}.${body}`));
    return `${head}.${body}.${b64url(new Uint8Array(sig))}`;
  }
  return { certs, idToken };
}
