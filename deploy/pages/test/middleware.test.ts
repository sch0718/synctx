import { describe, expect, it } from 'vitest';
import { handleAdminApi } from '../src/admin/api';
import { createInvite } from '../src/admin/invites';
import { handleAdminPage } from '../src/admin/page';
import { handleAuth, handleLogin } from '../src/auth';
import { handleInvite } from '../src/invite';
import type { Env } from '../src/env';
import { DEFAULT_CSP, RAW_SVG_CSP } from '../src/http';
import { handleRequest } from '../src/middleware';
import { loginCsp } from '../src/pages/login';
import { addMember, makeEnv, OWNER, post, request, seedSession } from './helpers';

const INDEX_HTML =
  '<!doctype html><html><body><ul>' +
  '<li data-repo-id="repo-a"><a href="/view/repo-a">A 저장소</a></li>' +
  '<li data-repo-id="repo-b"><a href="/view/repo-b">B 저장소</a></li></ul>' +
  '<select><option value="">저장소 선택</option><option value="repo-a" data-repo-id="repo-a">A 저장소</option>' +
  '<option value="repo-b" data-repo-id="repo-b">B 저장소</option></select></body></html>';

/** Pages가 정적 파일이나 각 함수로 넘기는 next() 대신. 정적 응답에는 _headers의 CSP가 이미 붙어 있다고 본다. */
function nextFor(req: Request, env: Env) {
  return async (): Promise<Response> => {
    const path = new URL(req.url).pathname;
    if (path === '/login') return handleLogin(req, env);
    if (path.startsWith('/auth/')) return handleAuth(req, env);
    if (path === '/admin') return handleAdminPage(req, env);
    if (path.startsWith('/api/admin/')) return handleAdminApi(req, env);
    if (path.startsWith('/invite/')) return handleInvite(req, env, path.slice('/invite/'.length));
    const staticHeaders = { 'Content-Security-Policy': 'default-src *' };
    if (path.endsWith('.svg')) return new Response('<svg/>', { headers: { ...staticHeaders, 'Content-Type': 'image/svg+xml' } });
    if (path.endsWith('.json')) return new Response('{"tree":[]}', { headers: { ...staticHeaders, 'Content-Type': 'application/json' } });
    if (path.endsWith('.png')) return new Response('png', { headers: { ...staticHeaders, 'Content-Type': 'image/png' } });
    return new Response(INDEX_HTML, { headers: { ...staticHeaders, 'Content-Type': 'text/html; charset=utf-8' } });
  };
}

async function run(req: Request, env: Env = makeEnv()) {
  return handleRequest({ request: req, env, next: nextFor(req, env) });
}

function expectCommonHeaders(res: Response, csp: string) {
  expect(res.headers.get('Content-Security-Policy')).toBe(csp);
  expect(res.headers.get('X-Frame-Options')).toBe('DENY');
  expect(res.headers.get('X-Content-Type-Options')).toBe('nosniff');
  expect(res.headers.get('X-Robots-Tag')).toBe('noindex');
  expect(res.headers.get('Cache-Control')).toBe('no-store');
}

describe('판정표', () => {
  const repoPaths = ['/view/repo-a/', '/view/repo-a/docs/a.md/', '/raw/repo-a/img/a.png', '/view/api/repos/repo-a/tree.json'];

  it.each(repoPaths)('로그인하지 않으면 %s → /login?next=…', async (path) => {
    const res = await run(request(path));
    expect(res.status).toBe(302);
    expect(res.headers.get('Location')).toBe(`/login?next=${encodeURIComponent(path)}`);
  });

  it.each(repoPaths)('열람자·담당자·전체 관리자는 %s 통과, 다른 저장소 사람은 403', async (path) => {
    await addMember('repo-a', 'viewer@example.com', 'viewer');
    await addMember('repo-a', 'manager@example.com', 'manager');
    await addMember('repo-b', 'other@example.com', 'viewer');
    for (const who of ['viewer@example.com', 'manager@example.com', OWNER]) {
      expect((await run(request(path, { session: await seedSession(who) }))).status).toBe(200);
    }
    const denied = await run(request(path, { session: await seedSession('other@example.com') }));
    expect(denied.status).toBe(403);
    const html = await denied.text();
    expect(html).toContain('other@example.com');
    expect(html).not.toContain('repo-a');
  });

  it('역할이 없는 사람은 없는 저장소와 같은 403을 받는다', async () => {
    const session = await seedSession('nobody@example.com');
    const a = await run(request('/view/repo-a/', { session }));
    const missing = await run(request('/view/no-such/', { session }));
    expect([a.status, missing.status]).toEqual([403, 403]);
    expect(await a.text()).toBe(await missing.text());
  });

  it('첫 화면은 로그인만 확인하고 /는 /view/로 보낸다', async () => {
    expect((await run(request('/view/'))).status).toBe(302);
    const session = await seedSession('nobody@example.com');
    expect((await run(request('/view/', { session }))).status).toBe(200);
    const root = await run(request('/', { session }));
    expect([root.status, root.headers.get('Location')]).toEqual([302, '/view/']);
    expect((await run(request('/'))).headers.get('Location')).toBe('/login?next=%2F');
  });

  it('_synctx와 알 수 없는 /view·/raw 경로는 로그인해도 403', async () => {
    const session = await seedSession(OWNER);
    for (const path of ['/_synctx/repos.json', '/view/api/x.json', '/raw/', '/view/repo-a/..%2Frepo-b/', '/view/repo-a%2F..%2Frepo-b/', '/x.html'])
      expect((await run(request(path, { session }))).status, path).toBe(403);
  });

  it('URL이 정규화한 %2E%2E는 그 결과 경로로 판정한다', async () => {
    // /view/%2E%2E/repo-a/ → /repo-a/(저장소 경로가 아니다)
    expect((await run(request('/view/%2E%2E/repo-a/'))).status).toBe(403);
    expect((await run(request('/view/%2E%2E/repo-a/', { session: await seedSession(OWNER) }))).status).toBe(403);
  });

  it('/login과 정적 자원은 세션 없이 통과한다', async () => {
    expect((await run(request('/login'))).status).toBe(200);
    expect((await run(request('/view/static/viewer.css'))).status).toBe(200);
    expect((await run(request('/robots.txt'))).status).toBe(200);
  });

  it('D1 오류는 503이고 문서를 내주지 않는다', async () => {
    const session = await seedSession(OWNER);
    const env = makeEnv({ DB: { prepare: () => { throw new Error('d1 down'); } } as unknown as D1Database });
    const res = await run(request('/view/repo-a/', { session }), env);
    expect(res.status).toBe(503);
    expect(await res.text()).not.toContain('A 저장소');
  });
});

describe('저장소 이름 숨김', () => {
  it('볼 수 없는 저장소의 카드·선택 항목을 지우고 자기 것은 남긴다', async () => {
    await addMember('repo-a', 'viewer@example.com', 'viewer');
    const html = await (await run(request('/view/', { session: await seedSession('viewer@example.com') }))).text();
    expect(html).toContain('data-repo-id="repo-a"');
    expect(html).not.toContain('repo-b');
    expect(html).not.toContain('B 저장소');
    expect(html).toContain('저장소 선택');
  });

  it('전체 관리자에게는 모두 보인다', async () => {
    const html = await (await run(request('/view/', { session: await seedSession(OWNER) }))).text();
    expect(html).toContain('B 저장소');
  });
});

describe('공통 헤더와 CSP', () => {
  it('로그인 HTML에는 로그인 CSP 하나만', async () => {
    const res = await run(request('/login'));
    expectCommonHeaders(res, loginCsp('synctx-test.firebaseapp.com'));
    expect(res.headers.get('Content-Security-Policy')).toContain('frame-src https://synctx-test.firebaseapp.com');
  });

  it('허용된 raw SVG에만 SVG CSP, SVG 경로의 거절은 기본 CSP', async () => {
    await addMember('repo-a', 'viewer@example.com', 'viewer');
    const ok = await run(request('/raw/repo-a/a.svg', { session: await seedSession('viewer@example.com') }));
    expect(ok.status).toBe(200);
    expectCommonHeaders(ok, RAW_SVG_CSP);
    expectCommonHeaders(await run(request('/raw/repo-a/a.svg')), DEFAULT_CSP);
    const denied = await run(request('/raw/repo-a/a.svg', { session: await seedSession('other@example.com') }));
    expect(denied.status).toBe(403);
    expectCommonHeaders(denied, DEFAULT_CSP);
  });

  it('정적 HTML·JSON·HTMLRewriter 결과·302·403·503·인증·초대·관리 HTML/API에 기본 CSP와 공통 헤더', async () => {
    const session = await seedSession(OWNER);
    const { token } = (await createInvite(makeEnv().DB, { repoId: 'repo-a', role: 'viewer', actor: { identity: OWNER, owner: true }, now: Date.now() }))!;
    const cases: [Request, number][] = [
      [request('/view/', { session }), 200],
      [request('/view/repo-a/', { session }), 200],
      [request('/view/api/repos/repo-a/tree.json', { session }), 200],
      [request('/view/repo-a/'), 302],
      [request('/_synctx/repos.json', { session }), 403],
      [post('/auth/session', {}), 401],
      [request('/auth/callback'), 200],
      [request('/admin', { session }), 200],
      [request('/admin'), 302],
      [request('/admin', { session: await seedSession('nobody@example.com') }), 403],
      [request('/api/admin/me', { session }), 200],
      [request('/api/admin/me'), 401],
      [post('/api/admin/repos/repo-a/members', { identity: 'a@example.com', role: 'viewer' }, { session }), 200],
      [request(`/invite/${token}`, { session }), 200],
      [request(`/invite/${token}`), 302],
      [post(`/invite/${token}`, {}, { session }), 303],
      [request(`/invite/${token}`, { session }), 410],
    ];
    for (const [req, status] of cases) {
      const res = await run(req);
      expect(res.status, req.url).toBe(status);
      expectCommonHeaders(res, DEFAULT_CSP);
    }
    const env = makeEnv({ DB: { prepare: () => { throw new Error('down'); } } as unknown as D1Database });
    expectCommonHeaders(await run(request('/view/repo-a/', { session }), env), DEFAULT_CSP);
  });

  it('상태·본문·Location·각 Set-Cookie를 보존한다', async () => {
    const session = await seedSession('reader@example.com');
    const logout = await run(post('/auth/logout', {}, { session }));
    expect(logout.status).toBe(303);
    expect(logout.headers.get('Location')).toBe('/login');
    expect(logout.headers.getSetCookie()).toHaveLength(1);
    expect(logout.headers.get('Referrer-Policy')).toBeNull();
    const callback = await run(request('/auth/callback'));
    expect(callback.headers.get('Referrer-Policy')).toBe('no-referrer');
    expect(await callback.text()).toContain('로그인 중');
    const twoCookies = await handleRequest({
      request: request('/api/admin/me'),
      env: makeEnv(),
      next: async () => {
        const h = new Headers({ 'Content-Type': 'application/json' });
        h.append('Set-Cookie', 'a=1');
        h.append('Set-Cookie', 'b=2');
        return new Response('{"x":1}', { status: 201, headers: h });
      },
    });
    expect(twoCookies.status).toBe(201);
    expect(twoCookies.headers.getSetCookie()).toEqual(['a=1', 'b=2']);
    expect(await twoCookies.text()).toBe('{"x":1}');
  });
});
