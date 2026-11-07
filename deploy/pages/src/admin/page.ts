import { purgeSessions, type D1Database as PackageD1 } from '@sch0718/firebase-session/worker';
import { canOpenAdmin } from '../authz';
import type { Env } from '../env';
import { escapeHtml, htmlResponse, loginRedirect } from '../http';
import { deniedPage } from '../pages/denied';
import { logoutForm, page } from '../pages/layout';
import { readSession, type SessionDeps } from '../session';
import { purgeExpiredInvites } from './invites';

/** GET /admin. 전체 관리자·관리자·담당자만 연다. 열 때 만료 초대와 오래된 세션을 지운다. */
export async function handleAdminPage(req: Request, env: Env, deps: SessionDeps = {}): Promise<Response> {
  const url = new URL(req.url);
  if (req.method !== 'GET') return htmlResponse(405, page('허용되지 않는 요청', '<p>허용되지 않는 요청입니다.</p>'));
  if (url.pathname !== '/admin' && url.pathname !== '/admin/') {
    return htmlResponse(404, page('없는 화면', '<p>없는 화면입니다.</p>'));
  }
  const session = await readSession(req, env, deps);
  if (!session) return loginRedirect(url);
  if (!(await canOpenAdmin(env, session.identity))) {
    return htmlResponse(403, deniedPage(session.identity));
  }
  const now = Date.now();
  await purgeExpiredInvites(env.DB, now);
  await purgeSessions(env.DB as unknown as PackageD1, now);
  return htmlResponse(
    200,
    page(
      '관리',
      '<h1 class="page-title">관리</h1>' +
        `<p>로그인: <code class="identity">${escapeHtml(session.identity)}</code></p>` +
        '<div id="admin-root"><p>불러오는 중입니다.</p></div>' +
        '<p><a href="/view/">문서 첫 화면으로</a> · <a href="/settings">설정</a></p>' +
        logoutForm(),
      { scripts: ['/view/static/synctx-auth/admin.js'] },
    ),
  );
}
