import { canOpenAdmin, visibleRepos } from './authz';
import { loadRepos } from './admin/repos';
import type { Env } from './env';
import { htmlResponse, loginRedirect } from './http';
import { page } from './pages/layout';
import { settingsPage } from './pages/settings';
import { readSession, type SessionDeps } from './session';

/** GET /settings. 로그인한 사람 누구나 연다. */
export async function handleSettings(req: Request, env: Env, deps: SessionDeps = {}): Promise<Response> {
  const url = new URL(req.url);
  if (req.method !== 'GET') return htmlResponse(405, page('허용되지 않는 요청', '<p>허용되지 않는 요청입니다.</p>'));
  const session = await readSession(req, env, deps);
  if (!session) return loginRedirect(url);
  const [visible, repos, admin, sessions] = await Promise.all([
    visibleRepos(env, session.identity),
    loadRepos(env, req),
    canOpenAdmin(env, session.identity),
    session.auth.list(session.ctx),
  ]);
  return htmlResponse(
    200,
    settingsPage({
      identity: session.identity,
      repos: visible === 'all' ? repos : repos.filter((r) => visible.has(r.id)),
      admin,
      sessions,
    }),
  );
}
