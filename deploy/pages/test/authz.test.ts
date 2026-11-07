import { describe, expect, it } from 'vitest';
import { classifyPath, isFunctionPath, visibleRepos } from '../src/authz';
import { normalizeIdentity } from '../src/identity';
import { addMember, makeEnv, OWNER } from './helpers';

describe('경로 판정', () => {
  it.each([
    ['/', { kind: 'root' }],
    ['/robots.txt', { kind: 'public' }],
    ['/view/', { kind: 'index' }],
    ['/view', { kind: 'index' }],
    ['/view/index.html', { kind: 'index' }],
    ['/view/static/viewer.css', { kind: 'public' }],
    ['/view/repo-a', { kind: 'repo', repoId: 'repo-a' }],
    ['/view/repo-a/', { kind: 'repo', repoId: 'repo-a' }],
    ['/view/repo-a/docs/%ED%95%9C.md/', { kind: 'repo', repoId: 'repo-a' }],
    ['/raw/repo-a/img/a.png', { kind: 'repo', repoId: 'repo-a' }],
    ['/view/api/repos/repo-a/tree.json', { kind: 'repo', repoId: 'repo-a' }],
    ['/view/%72epo-a/', { kind: 'repo', repoId: 'repo-a' }],
    ['/view/Repo-A/', { kind: 'repo', repoId: 'Repo-A' }],
    ['/view/api/', { kind: 'deny' }],
    ['/view/api/repos/', { kind: 'deny' }],
    ['/view/api/repos/repo-a', { kind: 'deny' }],
    ['/view/api/other/x.json', { kind: 'deny' }],
    ['/raw/', { kind: 'deny' }],
    ['/raw/repo-a', { kind: 'deny' }],
    ['/view/%2E%2E/', { kind: 'deny' }],
    ['/view/repo-a/%2E%2E/repo-b/', { kind: 'deny' }],
    ['/view/repo-a/..%2Frepo-b/', { kind: 'deny' }],
    ['/view/repo-a%2F..%2Frepo-b/', { kind: 'deny' }],
    ['/view/repo-a/a%5Cb', { kind: 'deny' }],
    ['/view//repo-a/', { kind: 'deny' }],
    ['/view/%E0%A4%A/', { kind: 'deny' }],
    ['/view/-x/', { kind: 'deny' }],
    ['/_synctx/repos.json', { kind: 'deny' }],
    ['/other.html', { kind: 'deny' }],
  ])('%s', (path, expected) => {
    expect(classifyPath(path)).toEqual(expected);
  });

  it('각 함수가 판정하는 경로', () => {
    for (const p of ['/login', '/auth/session', '/auth/callback', '/invite/abc', '/admin', '/admin/', '/api/admin/me'])
      expect(isFunctionPath(p)).toBe(true);
    for (const p of ['/login/x', '/administrator', '/api/other', '/view/admin']) expect(isFunctionPath(p)).toBe(false);
  });
});

describe('identity', () => {
  it('이메일은 소문자, 소셜은 접두어만 소문자', () => {
    expect(normalizeIdentity(' Reader@Example.COM ')).toBe('reader@example.com');
    expect(normalizeIdentity('KAKAO:AbC')).toBe('kakao:AbC');
  });
});

describe('볼 수 있는 저장소', () => {
  it('전체 관리자는 전부, 나머지는 repo_members', async () => {
    const env = makeEnv({ OWNER_ACCOUNTS: ` ${OWNER.toUpperCase()} , kakao:Me` });
    await addMember('repo-a', 'reader@example.com', 'viewer');
    await addMember('repo-b', 'boss@example.com', 'manager');
    expect(await visibleRepos(env, OWNER)).toBe('all');
    expect(await visibleRepos(env, 'kakao:Me')).toBe('all');
    expect(await visibleRepos(env, 'reader@example.com')).toEqual(new Set(['repo-a']));
    expect(await visibleRepos(env, 'nobody@example.com')).toEqual(new Set());
  });
});
