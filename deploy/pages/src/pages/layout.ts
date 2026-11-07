import { escapeHtml } from '../http';

/** 로그인·안내·관리 화면 공통 틀. Viewer의 viewer.css를 그대로 쓴다. */
export function page(title: string, body: string, opts: { head?: string; scripts?: string[] } = {}): string {
  const scripts = (opts.scripts ?? []).map((src) => `<script type="module" src="${escapeHtml(src)}"></script>`).join('');
  return (
    '<!doctype html><html lang="ko"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<meta name="robots" content="noindex">' +
    `<title>${escapeHtml(title)}</title>` +
    '<link rel="icon" href="/view/static/favicon.svg" type="image/svg+xml">' +
    '<link rel="stylesheet" href="/view/static/viewer.css">' +
    (opts.head ?? '') +
    '</head><body class="page-auth"><div class="layout layout-home"><main id="main" class="home">' +
    body +
    `</main></div>${scripts}</body></html>`
  );
}

/** HTML 안 <script type="application/json">에 넣을 JSON. `</script>`로 빠져나가지 못하게 `<`를 바꾼다. */
export function inlineJson(id: string, value: unknown): string {
  const text = JSON.stringify(value).replace(/</g, '\\u003c');
  return `<script type="application/json" id="${escapeHtml(id)}">${text}</script>`;
}

export function logoutForm(): string {
  return '<form method="post" action="/auth/logout"><button type="submit" class="btn">로그아웃</button></form>';
}
