import { env as testEnv } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { handleAuth, handleLogin } from '../src/auth';
import { SESSION_COOKIE } from '../src/session';
import { fakeGoogle, makeEnv, post, request, seedSession } from './helpers';

describe('GET /login', () => {
  it('세션 없이 200 HTML과 공급자 버튼 세 개, 검사한 next', async () => {
    const res = await handleLogin(request('/login?next=//evil.test/'), makeEnv());
    expect(res.status).toBe(200);
    expect(res.headers.get('Content-Type')).toBe('text/html; charset=utf-8');
    const html = await res.text();
    expect(html.match(/data-login="(google|kakao|naver)"/g)).toHaveLength(3);
    expect(html).toContain('"next":"/view/"');
    expect(html).toContain('/view/static/synctx-auth/login.js');
  });

  it('설정 JSON이 script를 빠져나가지 못한다', async () => {
    const html = await (await handleLogin(request('/login?next=/view/%3C/script%3E'), makeEnv())).text();
    expect(html).not.toContain('/view/</script>');
    expect(html).toContain('\\u003c/script>');
  });

  it('FIREBASE_WEB_CONFIG가 없거나 틀리면 503', async () => {
    expect((await handleLogin(request('/login'), makeEnv({ FIREBASE_WEB_CONFIG: undefined }))).status).toBe(503);
    expect((await handleLogin(request('/login'), makeEnv({ FIREBASE_WEB_CONFIG: '{"apiKey":1}' }))).status).toBe(503);
  });

  it('이미 로그인했으면 next로 보낸다', async () => {
    const session = await seedSession('reader@example.com');
    const res = await handleLogin(request('/login?next=/view/repo-a/', { session }), makeEnv());
    expect(res.status).toBe(302);
    expect(res.headers.get('Location')).toBe('/view/repo-a/');
  });
});

describe('POST /auth/session', () => {
  it('Google ID 토큰으로 세션을 만들고 쿠키로만 준다', async () => {
    const google = await fakeGoogle();
    const env = makeEnv();
    const req = post('/auth/session', { label: 'x' }, { headers: { Authorization: `Bearer ${await google.idToken('Reader@Example.com')}` } });
    const res = await handleAuth(req, env, { certs: google.certs });
    expect(res.status).toBe(200);
    const cookie = res.headers.get('Set-Cookie') ?? '';
    expect(cookie).toMatch(new RegExp(`^${SESSION_COOKIE}=[A-Za-z0-9_-]{43}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=2592000$`));
    const sessionId = cookie.split(';')[0]!.split('=')[1]!;
    expect(await res.text()).not.toContain(sessionId);
    const row = await testEnv.DB.prepare('SELECT identity FROM sessions').first<{ identity: string }>();
    expect(row?.identity).toBe('reader@example.com');
  });

  it('토큰이 없으면 401, Origin이 다르면 403', async () => {
    expect((await handleAuth(post('/auth/session', {}), makeEnv())).status).toBe(401);
    expect((await handleAuth(post('/auth/session', {}, { origin: 'https://evil.test' }), makeEnv())).status).toBe(403);
    expect((await handleAuth(post('/auth/session', {}, { origin: null }), makeEnv())).status).toBe(403);
  });

  it('소셜 콜백의 redirectUri가 사이트 콜백과 다르면 거절한다', async () => {
    const body = { provider: 'kakao', code: 'c', codeVerifier: 'v'.repeat(43), redirectUri: 'https://evil.test/auth/callback' };
    const res = await handleAuth(post('/auth/session/social', body), makeEnv());
    // 소셜 설정이 빠지면 404가 나므로 400으로 좁혀 카카오 켜짐 조건도 함께 지킨다
    expect(res.status).toBe(400);
    expect(res.headers.get('Set-Cookie')).toBeNull();
  });
});

describe('/auth 기타', () => {
  it('콜백 화면은 리퍼러를 보내지 않는다', async () => {
    const res = await handleAuth(request('/auth/callback?code=x&state=y'), makeEnv());
    expect(res.status).toBe(200);
    expect(res.headers.get('Referrer-Policy')).toBe('no-referrer');
  });

  it('로그아웃은 세션을 철회하고 쿠키를 지운다', async () => {
    const env = makeEnv();
    const session = await seedSession('reader@example.com');
    const res = await handleAuth(post('/auth/logout', {}, { session }), env);
    expect(res.status).toBe(303);
    expect(res.headers.get('Location')).toBe('/login');
    expect(res.headers.get('Set-Cookie')).toContain(`${SESSION_COOKIE}=; `);
    const row = await testEnv.DB.prepare('SELECT revoked_at FROM sessions').first<{ revoked_at: number | null }>();
    expect(row?.revoked_at).not.toBeNull();
  });

  it('Origin이 다른 로그아웃은 403이고 세션을 그대로 둔다', async () => {
    const session = await seedSession('reader@example.com');
    const res = await handleAuth(post('/auth/logout', {}, { session, origin: 'https://evil.test' }), makeEnv());
    expect(res.status).toBe(403);
    const row = await testEnv.DB.prepare('SELECT revoked_at FROM sessions').first<{ revoked_at: number | null }>();
    expect(row?.revoked_at).toBeNull();
  });
});
