/** 응답 도우미와 공통 보안 헤더. 값은 services/viewer의 _headers_file()·CSP·RAW_SVG_CSP와 같아야 한다. */

/** services/viewer/src/static_export/build.py의 CSP */
export const DEFAULT_CSP =
  "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'";

/** services/viewer/src/app.py의 RAW_SVG_CSP */
export const RAW_SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox";

export const COMMON_HEADERS: Readonly<Record<string, string>> = {
  'X-Frame-Options': 'DENY',
  'X-Content-Type-Options': 'nosniff',
  'X-Robots-Tag': 'noindex',
  'Cache-Control': 'no-store',
};

/** 상태·본문·Location·각 Set-Cookie를 보존한 채 공통 헤더와 CSP 하나를 설정한다. */
export function withSecurityHeaders(res: Response, csp: string): Response {
  const out = new Response(res.body, res);
  for (const [name, value] of Object.entries(COMMON_HEADERS)) out.headers.set(name, value);
  out.headers.set('Content-Security-Policy', csp);
  return out;
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function htmlResponse(status: number, body: string, headers: Record<string, string> = {}): Response {
  return new Response(body, { status, headers: { 'Content-Type': 'text/html; charset=utf-8', ...headers } });
}

export function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', ...headers },
  });
}

export function redirect(location: string, status = 302, headers: Record<string, string> = {}): Response {
  return new Response(null, { status, headers: { Location: location, ...headers } });
}

/** 쓰기 요청은 Origin이 사이트 출처와 같을 때만 받는다(CSRF). */
export function sameOrigin(req: Request): boolean {
  return req.headers.get('Origin') === new URL(req.url).origin;
}

/** 로그인 뒤 돌아갈 주소. `/`로 시작하는 같은 출처 경로만 받고, 아니면 `/view/`. */
export function safeNext(value: string | null | undefined): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return '/view/';
  if (/[\u0000-\u001f\u007f]/.test(value)) return '/view/';
  return value;
}

export function loginRedirect(url: URL): Response {
  return redirect(`/login?next=${encodeURIComponent(url.pathname + url.search)}`);
}
