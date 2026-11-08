import { escapeHtml } from '../http';

export const THEME_LABELS = {
  'github-light': 'GitHub 밝게',
  'github-dark': 'GitHub 어둡게',
  'cayman': 'Cayman',
  'minimal': 'Minimal',
  'hacker': 'Hacker',
  'modernist': 'Modernist',
  'corporate': 'Corporate',
};

const LOGO =
  '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" ' +
  'aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/></svg>';

const PERSON_ICON =
  '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/>' +
  '<path d="M4 21c0-4 4-7 8-7s8 3 8 7"/></svg>';

/** Viewer 머리글 끝에 붙이는 설정 버튼(로그아웃·기기 관리는 설정 화면에 있다). */
export const ACCOUNT_LINK = `<a class="icon-btn shell-account" href="/settings" aria-label="설정·로그아웃" title="설정·로그아웃">${PERSON_ICON}</a>`;

const AUTH_STYLE = `
.auth-main{box-sizing:border-box;width:100%;max-width:640px;margin:0 auto;padding:32px 16px 72px;display:flex;flex-direction:column;gap:24px}
.auth-main.auth-wide{max-width:1080px}
.auth-main .page-title{margin:0}
.auth-main p{margin:0}
.auth-main a:not(.btn){color:var(--primary)}
.auth-main a:focus-visible,.auth-main .btn:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
.auth-main form{margin:0}
.auth-main:not(.auth-wide) .btn{min-height:44px;height:auto}
.auth-main:not(.auth-wide) .btn:disabled{opacity:.5;cursor:not-allowed}
.auth-section{display:flex;flex-direction:column;gap:8px}
.auth-section h2{margin:0;font-size:15px;font-weight:800}
.auth-muted{color:var(--text-secondary);font-size:13px}
.auth-actions{display:flex;flex-wrap:wrap;gap:8px}
.login-buttons{display:flex;flex-direction:column;gap:8px;max-width:320px}
.login-buttons .btn{justify-content:center}
.auth-list{list-style:none;margin:0;padding:0;border:1px solid var(--border-color);border-radius:var(--radius-ui);background:var(--bg-card)}
.auth-list li{display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;padding:12px 14px;border-top:1px solid var(--border-color)}
.auth-list li:first-child{border-top:0}
.device-name{font-weight:600}
.device-meta{color:var(--text-secondary);font-size:13px}
.device-action{margin-left:auto}
.device-current{padding:2px 8px;border-radius:999px;background:var(--primary-lighter);color:var(--primary);font-size:12px;font-weight:700}
code.identity{padding:2px 6px;border-radius:6px;background:var(--bg-neutral);font-family:var(--font-mono-ui);font-size:13px;word-break:break-all}
@media (max-width:767px){.page-auth .shell-header .shell-controls{display:flex}.page-auth .shell-header .shell-spacer{display:block}}
`;

/** 로그인·안내·관리 화면 공통 틀. Viewer의 viewer.css를 그대로 쓴다. */
export function page(title: string, body: string, opts: {
  head?: string; scripts?: string[]; account?: boolean; themeSelect?: boolean; wide?: boolean;
} = {}): string {
  const scripts = (opts.scripts ?? []).map((src) => `<script type="module" src="${escapeHtml(src)}"></script>`).join('');
  const themeOptions = Object.entries(THEME_LABELS)
    .map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
  const themeSelect = opts.themeSelect !== false
    ? '<div class="shell-controls"><label class="shell-field"><span class="shell-field-label">테마</span>' +
      `<select data-action="theme" aria-label="테마">${themeOptions}</select></label></div>`
    : '';
  return (
    '<!doctype html><html lang="ko" data-theme="github-light"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<meta name="robots" content="noindex">' +
    `<title>${escapeHtml(title)} - synctx</title>` +
    '<script src="/view/static/theme-boot.js"></script>' +
    '<link rel="stylesheet" href="/view/static/theme-vars.css">' +
    '<link rel="icon" href="/view/static/favicon.svg" type="image/svg+xml">' +
    '<link rel="stylesheet" href="/view/static/viewer.css">' +
    `<style>${AUTH_STYLE}</style>` + (opts.head ?? '') +
    '</head><body class="page-auth"><a class="skip-link" href="#main">본문으로 건너뛰기</a>' +
    `<header class="shell-header"><a class="shell-logo" href="/view/">${LOGO}<span>synctx</span></a>` +
    '<span class="shell-spacer"></span>' + themeSelect + (opts.account ? ACCOUNT_LINK : '') + '</header>' +
    `<main id="main" class="auth-main${opts.wide ? ' auth-wide' : ''}" tabindex="-1">` + body +
    '</main><script src="/view/static/static-mode.js"></script>' + scripts + '</body></html>'
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
