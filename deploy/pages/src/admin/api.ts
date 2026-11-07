import { errorBody, HttpError, readJson } from '@sch0718/firebase-session/worker';
import { isAdmin, isOwner, type RepoRole } from '../authz';
import type { Env } from '../env';
import { jsonResponse, sameOrigin } from '../http';
import { isIdentity, normalizeIdentity } from '../identity';
import { readSession, type SessionDeps } from '../session';
import { listAudit } from './audit';
import { createInvite } from './invites';
import { listAdmins, listMembers, removeAdmin, removeMember, setAdmin, setMember, type Actor, type ChangeResult } from './members';
import { loadRepos } from './repos';

const fail = (status: number, code: string) => jsonResponse(status, errorBody(code));

const CHANGE_STATUS: Record<Exclude<ChangeResult, 'ok'>, [number, string]> = {
  forbidden: [403, 'forbidden'],
  'last-manager': [409, 'last-manager'],
  'not-found': [404, 'not-found'],
};

function changed(result: ChangeResult): Response {
  if (result === 'ok') return jsonResponse(200, { ok: true });
  const [status, code] = CHANGE_STATUS[result];
  return fail(status, code);
}

async function body(req: Request): Promise<Record<string, unknown>> {
  const v = await readJson(req);
  if (typeof v !== 'object' || v === null || Array.isArray(v)) throw new HttpError(400, 'bad-request');
  return v as Record<string, unknown>;
}

function identityField(b: Record<string, unknown>): string {
  if (typeof b['identity'] !== 'string' || !isIdentity(b['identity'])) throw new HttpError(400, 'bad-identity');
  return normalizeIdentity(b['identity']);
}

function roleField(b: Record<string, unknown>): RepoRole {
  if (b['role'] !== 'manager' && b['role'] !== 'viewer') throw new HttpError(400, 'bad-role');
  return b['role'];
}

async function managedRepoIds(db: D1Database, identity: string): Promise<Set<string>> {
  const { results } = await db
    .prepare("SELECT repo_id FROM repo_members WHERE identity = ? AND role = 'manager'")
    .bind(identity)
    .all<{ repo_id: string }>();
  return new Set(results.map((r) => r.repo_id));
}

const inviteUrl = (req: Request, token: string) => `${new URL(req.url).origin}/invite/${token}`;

/** /api/admin/* (JSON). 쓰기 요청은 Origin을 확인하고, 권한은 변경 SQL에서 다시 확인한다. */
export async function handleAdminApi(req: Request, env: Env, deps: SessionDeps & { now?: () => number } = {}): Promise<Response> {
  const now = (deps.now ?? Date.now)();
  const path = new URL(req.url).pathname.replace(/^\/api\/admin/, '');
  const write = req.method !== 'GET' && req.method !== 'HEAD';
  if (write && !sameOrigin(req)) return fail(403, 'bad-origin');
  const session = await readSession(req, env, deps);
  if (!session) return fail(401, 'login-required');
  const actor: Actor = { identity: session.identity, owner: isOwner(env, session.identity) };
  const db = env.DB;
  try {
    if (path === '/me' && req.method === 'GET') {
      const repos = await loadRepos(env, req);
      const mine = actor.owner ? null : await managedRepoIds(db, actor.identity);
      return jsonResponse(200, {
        identity: actor.identity,
        owner: actor.owner,
        admin: actor.owner || (await isAdmin(db, actor.identity)),
        repos: repos.filter((r) => mine === null || mine.has(r.id)),
      });
    }

    const repoMatch = /^\/repos\/([^/]+)\/(members|invites)$/.exec(path);
    if (repoMatch) {
      const repoId = decodeURIComponent(repoMatch[1]!);
      if (!(await loadRepos(env, req)).some((r) => r.id === repoId)) return fail(404, 'not-found');
      if (repoMatch[2] === 'members') {
        if (req.method === 'GET') {
          if (!actor.owner && !(await managedRepoIds(db, actor.identity)).has(repoId)) return fail(403, 'forbidden');
          return jsonResponse(200, { members: await listMembers(db, repoId) });
        }
        if (req.method === 'POST') {
          const b = await body(req);
          return changed(await setMember(db, { repoId, identity: identityField(b), role: roleField(b), actor, now }));
        }
        if (req.method === 'DELETE') {
          const b = await body(req);
          return changed(await removeMember(db, { repoId, identity: identityField(b), actor, now }));
        }
      } else if (req.method === 'POST') {
        const created = await createInvite(db, { repoId, role: roleField(await body(req)), actor, now });
        return created
          ? jsonResponse(200, { url: inviteUrl(req, created.token), expiresAt: created.expiresAt })
          : fail(403, 'forbidden');
      }
      return fail(405, 'method-not-allowed');
    }

    if (path === '/admins' || path === '/admin-invites' || path === '/audit') {
      if (!actor.owner) return fail(403, 'forbidden');
      if (path === '/audit' && req.method === 'GET') return jsonResponse(200, { entries: await listAudit(db) });
      if (path === '/admin-invites' && req.method === 'POST') {
        const created = await createInvite(db, { repoId: null, role: 'admin', actor, now });
        return created ? jsonResponse(200, { url: inviteUrl(req, created.token), expiresAt: created.expiresAt }) : fail(403, 'forbidden');
      }
      if (path === '/admins') {
        if (req.method === 'GET') return jsonResponse(200, { admins: await listAdmins(db) });
        if (req.method === 'POST') {
          await setAdmin(db, identityField(await body(req)), actor.identity, now);
          return jsonResponse(200, { ok: true });
        }
        if (req.method === 'DELETE') {
          return (await removeAdmin(db, identityField(await body(req)), actor.identity, now))
            ? jsonResponse(200, { ok: true })
            : fail(404, 'not-found');
        }
      }
      return fail(405, 'method-not-allowed');
    }
  } catch (e) {
    if (e instanceof HttpError) return jsonResponse(e.status, errorBody(e.code, e.extra));
    throw e;
  }
  return fail(404, 'not-found');
}
