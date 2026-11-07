import type { Env } from './env';
import { normalizeIdentity } from './identity';

export type RepoRole = 'manager' | 'viewer';

export function ownerSet(env: Env): Set<string> {
  return new Set(
    (env.OWNER_ACCOUNTS ?? '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .map(normalizeIdentity),
  );
}

export function isOwner(env: Env, identity: string): boolean {
  return ownerSet(env).has(identity);
}

export async function isAdmin(db: D1Database, identity: string): Promise<boolean> {
  return (await db.prepare('SELECT 1 FROM site_roles WHERE identity = ?').bind(identity).first()) !== null;
}

export async function repoRole(db: D1Database, identity: string, repoId: string): Promise<RepoRole | null> {
  const row = await db
    .prepare('SELECT role FROM repo_members WHERE repo_id = ? AND identity = ?')
    .bind(repoId, identity)
    .first<{ role: RepoRole }>();
  return row?.role ?? null;
}

export async function canView(env: Env, identity: string, repoId: string): Promise<boolean> {
  return isOwner(env, identity) || (await repoRole(env.DB, identity, repoId)) !== null;
}

export async function visibleRepos(env: Env, identity: string): Promise<Set<string> | 'all'> {
  if (isOwner(env, identity)) return 'all';
  const { results } = await env.DB.prepare('SELECT repo_id FROM repo_members WHERE identity = ?')
    .bind(identity)
    .all<{ repo_id: string }>();
  return new Set(results.map((r) => r.repo_id));
}

const REPO_ID = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

export type PathKind =
  | { kind: 'repo'; repoId: string }
  | { kind: 'index' }
  | { kind: 'root' }
  | { kind: 'public' }
  | { kind: 'deny' };

/**
 * 정적 산출물 경로 판정(설계 4절). 구간마다 URL 디코딩한 뒤 비교하고, 디코딩해서 `/`·`\`·`.`·`..`가 되는 구간은 거절한다.
 * 저장소 경로: `/view/<id>…`, `/raw/<id>/…`, `/view/api/repos/<id>/…`. `/view/static/`·`/view/api/`는 저장소가 아니다.
 */
export function classifyPath(pathname: string): PathKind {
  let segments: string[];
  try {
    segments = pathname.split('/').slice(1).map(decodeURIComponent);
  } catch {
    return { kind: 'deny' };
  }
  const inner = segments.slice(0, -1);
  if (inner.some((s) => s === '' || s === '.' || s === '..') || segments.some((s) => /[/\\\u0000]/.test(s)))
    return { kind: 'deny' };
  const [first, second, third, fourth] = segments;
  if (segments.length === 1 && first === '') return { kind: 'root' };
  if (segments.length === 1 && first === 'robots.txt') return { kind: 'public' };
  if (first === 'view') {
    if (second === undefined || (second === '' && segments.length === 2)) return { kind: 'index' };
    if (second === 'index.html' && segments.length === 2) return { kind: 'index' };
    if (second === 'static') return { kind: 'public' };
    if (second === 'api') {
      return third === 'repos' && fourth && REPO_ID.test(fourth) && segments.length > 4
        ? { kind: 'repo', repoId: fourth }
        : { kind: 'deny' };
    }
    return second && REPO_ID.test(second) ? { kind: 'repo', repoId: second } : { kind: 'deny' };
  }
  if (first === 'raw') {
    return second && REPO_ID.test(second) && segments.length > 2 ? { kind: 'repo', repoId: second } : { kind: 'deny' };
  }
  return { kind: 'deny' };
}

/** 로그인·인증·초대·관리 화면처럼 각 함수가 스스로 판정하는 경로. */
export function isFunctionPath(pathname: string): boolean {
  return (
    pathname === '/login' ||
    pathname === '/admin' ||
    /^\/(auth|invite|admin|api\/admin)\//.test(pathname)
  );
}
