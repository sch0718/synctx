import { describe, expect, it } from 'vitest';
import { safeNext } from '../src/http';
import { clearSessionCookie, readCookie, readSession, SESSION_COOKIE, sessionCookie } from '../src/session';
import { makeEnv, request, seedSession } from './helpers';

describe('세션 쿠키', () => {
  it('속성을 고정한다', () => {
    expect(sessionCookie('abc')).toBe(
      `${SESSION_COOKIE}=abc; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=2592000`,
    );
    expect(clearSessionCookie()).toBe(`${SESSION_COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0`);
  });

  it('여러 쿠키 가운데 세션 쿠키만 읽는다', () => {
    const req = request('/', { headers: { Cookie: `a=1; ${SESSION_COOKIE}=xyz; b=2` } });
    expect(readCookie(req, SESSION_COOKIE)).toBe('xyz');
    expect(readCookie(request('/'), SESSION_COOKIE)).toBeNull();
  });

  it('쿠키 값을 세션 헤더로 옮겨 Google·소셜 세션을 인증한다', async () => {
    const env = makeEnv();
    const google = await seedSession('reader@example.com');
    const kakao = await seedSession('kakao:Abc123');
    expect((await readSession(request('/', { session: google }), env))?.identity).toBe('reader@example.com');
    expect((await readSession(request('/', { session: kakao }), env))?.identity).toBe('kakao:Abc123');
  });

  it('Authorization 헤더가 있어도 세션만으로 판정한다', async () => {
    const session = await seedSession('reader@example.com');
    const req = request('/', { session, headers: { Authorization: 'Bearer not-a-token' } });
    expect((await readSession(req, makeEnv()))?.identity).toBe('reader@example.com');
  });

  it('없거나 철회됐거나 형식이 틀린 세션은 null', async () => {
    const env = makeEnv();
    const revoked = await seedSession('reader@example.com', { revoked: true });
    expect(await readSession(request('/'), env)).toBeNull();
    expect(await readSession(request('/', { session: revoked }), env)).toBeNull();
    expect(await readSession(request('/', { session: 'short' }), env)).toBeNull();
  });

  it('D1 오류는 그대로 던진다(호출자가 503)', async () => {
    const session = await seedSession('reader@example.com');
    const broken = makeEnv({ DB: { prepare: () => { throw new Error('d1 down'); } } as unknown as D1Database });
    await expect(readSession(request('/', { session }), broken)).rejects.toThrow('d1 down');
  });
});

describe('next 검사', () => {
  it.each([
    ['/view/repo-a/', '/view/repo-a/'],
    ['/view/repo-a/a.md?x=1', '/view/repo-a/a.md?x=1'],
    [null, '/view/'],
    ['', '/view/'],
    ['https://evil.test/', '/view/'],
    ['//evil.test/', '/view/'],
    ['/\\evil.test/', '/view/'],
    ['view/', '/view/'],
    ['/view/\nx', '/view/'],
  ])('%s → %s', (input, expected) => {
    expect(safeNext(input)).toBe(expected);
  });
});
