import { CertsUnavailableError, errorBody, HttpError, readJson } from '@sch0718/firebase-session/worker';
import type { Env } from './env';
import { htmlResponse, jsonResponse, redirect, safeNext, sameOrigin } from './http';
import { callbackPage, loginPage, parseFirebaseWebConfig, unavailablePage } from './pages/login';
import { clearSessionCookie, readSession, sessionAuthFor, sessionCookie, type SessionDeps } from './session';

/** GET /login. 세션 없이 열린다. 이미 로그인했으면 next로 보낸다. */
export async function handleLogin(req: Request, env: Env, deps: SessionDeps = {}): Promise<Response> {
  const next = safeNext(new URL(req.url).searchParams.get('next'));
  const firebase = parseFirebaseWebConfig(env.FIREBASE_WEB_CONFIG);
  if (!firebase) return htmlResponse(503, unavailablePage('로그인 설정이 없습니다. 관리자에게 알려 주세요.'));
  if (await readSession(req, env, deps)) return redirect(next);
  return htmlResponse(
    200,
    loginPage({
      firebase,
      kakaoClientId: env.KAKAO_CLIENT_ID,
      naverClientId: env.NAVER_CLIENT_ID && env.NAVER_CLIENT_SECRET ? env.NAVER_CLIENT_ID : undefined,
      next,
    }),
    // next에 초대 링크가 들어갈 수 있어 Google·카카오·네이버로 주소를 넘기지 않는다.
    { 'Referrer-Policy': 'no-referrer' },
  );
}

function errorJson(e: unknown): Response {
  if (e instanceof HttpError) return jsonResponse(e.status, errorBody(e.code, e.extra));
  if (e instanceof CertsUnavailableError) return jsonResponse(503, errorBody('certs-unavailable'));
  throw e;
}

/**
 * /auth/* 라우트.
 * - POST /auth/session: Google ID 토큰(Authorization: Bearer)으로 세션을 만든다.
 * - POST /auth/session/social: 카카오·네이버 인가 코드로 세션을 만든다.
 * - GET /auth/callback: 카카오·네이버 콜백 화면.
 * - POST /auth/logout: 이 브라우저의 세션을 철회하고 쿠키를 지운다.
 * - POST /auth/sessions/revoke: 설정 화면의 폼. `id`(다른 기기 하나) 또는 `target=others`(이 기기를 뺀 전부)를 철회한다.
 * 세션 id는 쿠키로만 주고 본문에는 넣지 않는다.
 */
export async function handleAuth(req: Request, env: Env, deps: SessionDeps = {}): Promise<Response> {
  const path = new URL(req.url).pathname;
  if (req.method === 'GET' && path === '/auth/callback') {
    return htmlResponse(200, callbackPage(), { 'Referrer-Policy': 'no-referrer' });
  }
  if (req.method !== 'POST') return jsonResponse(404, errorBody('not-found'));
  if (!sameOrigin(req)) return jsonResponse(403, errorBody('bad-origin'));
  try {
    if (path === '/auth/session' || path === '/auth/session/social') {
      const body = await readJson(req);
      const auth = sessionAuthFor(env, req, deps);
      const created = path === '/auth/session' ? await auth.create(req, body) : await auth.createSocial(req, body);
      return jsonResponse(200, { ok: true }, { 'Set-Cookie': sessionCookie(created.sessionId) });
    }
    if (path === '/auth/logout') {
      const session = await readSession(req, env, deps);
      if (session) await session.auth.revoke(session.ctx, 'current');
      return redirect('/login', 303, { 'Set-Cookie': clearSessionCookie() });
    }
    if (path === '/auth/sessions/revoke') {
      const session = await readSession(req, env, deps);
      if (!session) return redirect('/login?next=%2Fsettings', 303);
      const form = await req.formData().catch(() => null);
      const id = form?.get('id');
      if (form?.get('target') === 'others') await session.auth.revoke(session.ctx, 'others');
      else if (typeof id === 'string' && id) {
        // 이미 로그아웃된 기기(두 번 누름 등)는 404다. 화면만 다시 보여 준다.
        await session.auth.revoke(session.ctx, { id }).catch((e: unknown) => {
          if (!(e instanceof HttpError && e.status === 404)) throw e;
        });
      }
      return redirect('/settings', 303);
    }
  } catch (e) {
    return errorJson(e);
  }
  return jsonResponse(404, errorBody('not-found'));
}
