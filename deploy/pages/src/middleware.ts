import { canView, classifyPath, isFunctionPath, visibleRepos } from './authz';
import type { Env } from './env';
import { DEFAULT_CSP, htmlResponse, loginRedirect, RAW_SVG_CSP, redirect, withSecurityHeaders } from './http';
import { deniedPage, errorPage, forbiddenPage } from './pages/denied';
import { loginCsp, parseFirebaseWebConfig } from './pages/login';
import { readSession, type SessionDeps } from './session';

export interface MiddlewareInput {
  request: Request;
  env: Env;
  next: () => Promise<Response>;
  deps?: SessionDeps;
}

type Routed = { res: Response; csp: string };

function isHtml(res: Response): boolean {
  return (res.headers.get('Content-Type') ?? '').toLowerCase().startsWith('text/html');
}

/** 볼 수 없는 저장소의 `[data-repo-id]` 요소(첫 화면 카드, 저장소 선택 항목)를 지운다. */
export function filterRepoElements(res: Response, visible: Set<string> | 'all'): Response {
  if (visible === 'all' || !isHtml(res)) return res;
  return new HTMLRewriter()
    .on('[data-repo-id]', {
      element(el) {
        if (!visible.has(el.getAttribute('data-repo-id') ?? '')) el.remove();
      },
    })
    .transform(res);
}

async function route({ request, env, next, deps }: MiddlewareInput): Promise<Routed> {
  const url = new URL(request.url);
  const path = url.pathname;
  if (isFunctionPath(path)) {
    const res = await next();
    if (path === '/login' && res.status === 200 && isHtml(res)) {
      const cfg = parseFirebaseWebConfig(env.FIREBASE_WEB_CONFIG);
      if (cfg) return { res, csp: loginCsp(cfg.authDomain) };
    }
    return { res, csp: DEFAULT_CSP };
  }
  const target = classifyPath(path);
  if (target.kind === 'public') return { res: await next(), csp: DEFAULT_CSP };
  if (target.kind === 'deny') return { res: htmlResponse(403, forbiddenPage()), csp: DEFAULT_CSP };

  const session = await readSession(request, env, deps);
  if (!session) return { res: loginRedirect(url), csp: DEFAULT_CSP };
  if (target.kind === 'root') return { res: redirect('/view/'), csp: DEFAULT_CSP };
  if (target.kind === 'repo' && !(await canView(env, session.identity, target.repoId))) {
    return { res: htmlResponse(403, deniedPage(session.identity)), csp: DEFAULT_CSP };
  }
  const asset = await next();
  const res = isHtml(asset) ? filterRepoElements(asset, await visibleRepos(env, session.identity)) : asset;
  const svg = target.kind === 'repo' && path.startsWith('/raw/') && /\.svg$/i.test(path) && res.status === 200;
  return { res, csp: svg ? RAW_SVG_CSP : DEFAULT_CSP };
}

/** 모든 요청의 마지막 단계. 예외는 503으로 바꾸고(문서를 열어 주지 않는다), 최종 응답에 공통 헤더를 붙인다. */
export async function handleRequest(input: MiddlewareInput): Promise<Response> {
  let routed: Routed;
  try {
    routed = await route(input);
  } catch (e) {
    console.error('synctx_middleware_failed', e instanceof Error ? e.name : typeof e);
    routed = { res: htmlResponse(503, errorPage()), csp: DEFAULT_CSP };
  }
  return withSecurityHeaders(routed.res, routed.csp);
}
