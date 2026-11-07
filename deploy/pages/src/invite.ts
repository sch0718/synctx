import { acceptInvite, inviteState, type InviteState } from './admin/invites';
import { loadRepos } from './admin/repos';
import type { Env } from './env';
import { escapeHtml, htmlResponse, loginRedirect, redirect, sameOrigin } from './http';
import { normalizeIdentity } from './identity';
import { logoutForm, page } from './pages/layout';
import { readSession, type SessionDeps } from './session';

const ROLE_LABEL = { admin: '관리자', manager: '담당자', viewer: '열람자' } as const;
const NO_REFERRER = { 'Referrer-Policy': 'no-referrer' };

function closed(state: Exclude<InviteState, { status: 'ok' }>): Response {
  const why = state.status === 'expired' ? '기한이 지난' : state.status === 'used' ? '이미 사용된' : '찾을 수 없는';
  return htmlResponse(
    410,
    page('초대', `<h1 class="page-title">초대</h1><p>${why} 초대입니다. 초대한 사람에게 새 링크를 받아 주세요.</p>`),
    NO_REFERRER,
  );
}

/** /invite/<token>. 로그인한 뒤 수락 버튼을 보여 주고, POST로 수락한다. */
export async function handleInvite(req: Request, env: Env, token: string, deps: SessionDeps = {}): Promise<Response> {
  const url = new URL(req.url);
  const session = await readSession(req, env, deps);
  if (!session) return req.method === 'GET' ? loginRedirect(url) : redirect('/login', 303);
  const now = Date.now();
  if (req.method === 'POST') {
    if (!sameOrigin(req)) return htmlResponse(403, page('초대', '<p>허용되지 않는 요청입니다.</p>'), NO_REFERRER);
    const result = await acceptInvite(env.DB, token, normalizeIdentity(session.identity), now);
    if (result.status !== 'ok') return closed(result);
    return redirect(result.role === 'viewer' && result.repoId ? `/view/${encodeURIComponent(result.repoId)}/` : '/admin', 303);
  }
  if (req.method !== 'GET') return htmlResponse(405, page('초대', '<p>허용되지 않는 요청입니다.</p>'));
  const state = await inviteState(env.DB, token, now);
  if (state.status !== 'ok') return closed(state);
  const repoName = state.repoId
    ? ((await loadRepos(env, req)).find((r) => r.id === state.repoId)?.name ?? state.repoId)
    : null;
  const what = repoName ? `「${escapeHtml(repoName)}」 저장소의 ${ROLE_LABEL[state.role]}` : ROLE_LABEL[state.role];
  return htmlResponse(
    200,
    page(
      '초대',
      '<h1 class="page-title">초대</h1>' +
        `<p>${what}(으)로 초대받았습니다. 지금 로그인한 계정 <code class="identity">${escapeHtml(session.identity)}</code>로 수락합니다.</p>` +
        `<form method="post" action="/invite/${escapeHtml(encodeURIComponent(token))}"><button type="submit" class="btn btn-primary">수락</button></form>` +
        '<p>다른 계정으로 받으려면 로그아웃한 뒤 이 링크를 다시 여세요.</p>' +
        logoutForm(),
    ),
    NO_REFERRER,
  );
}
