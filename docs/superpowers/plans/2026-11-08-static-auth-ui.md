# 정적 Viewer 로그인 모드 화면 모양 맞추기 구현 계획

**목표:** Pages Functions 화면(로그인·콜백·설정·거절·잠시 이용할 수 없음)이 Viewer와 같은 테마 변수·테마 전환·머리글로 보이게 한다. 동작은 바꾸지 않는다.
**설계:** [2026-11-08-static-auth-ui-design.md](../specs/2026-11-08-static-auth-ui-design.md)

## 전역 제약

- 사용자에게 보이는 글은 한국어. 새 기능 없음. 로그인·세션·권한·CSP·라우팅 동작 변경 없음.
- 기존 시험을 약하게 고치지 않는다(문자열 확인을 지우거나 느슨하게 바꾸지 않는다).
- React·MUI 등 새 의존성 없음. 서버가 그리는 HTML 문자열과 viewer.css 클래스만 쓴다.
- 직접 `docker build`·`kubectl` 금지. 비밀값을 파일·로그에 남기지 않는다.

## 작업 1: 정적 내보내기가 테마 파일 두 개를 낸다 (Python)

**파일**
- 수정 `services/viewer/src/static_export/pages.py`: `_boot_script(default_theme)`의 JS 본문을 `boot_js(default_theme) -> str`로 빼고, `_boot_script`는 `f"<script>{boot_js(default_theme)}</script>"`만 한다. 출력 바이트는 그대로.
- 수정 `services/viewer/src/static_export/build.py` `build_site`: `_copy_static` 뒤에
  ```python
  writer.write("view/static/theme-vars.css", THEME_VARS_CSS)          # from renderer import THEME_VARS_CSS
  writer.write("view/static/theme-boot.js", pages.boot_js(site.theme))
  ```
- 시험 `services/viewer/tests/test_static_export_build.py`
  - `test_build_writes_all_expected_files`의 기대 목록에 두 파일 추가.
  - 새 시험: `theme-vars.css` 내용 == `renderer.THEME_VARS_CSS`, `theme-boot.js` 내용 == `pages.boot_js(site.theme)`이고, 내보낸 아무 Viewer 페이지 HTML 안에 `<script>` + 그 내용 + `</script>`가 들어 있다(같은 부트임을 확인).
- 새 시험 `services/viewer/tests/test_static_auth_theme_labels.py`: `deploy/pages/src/pages/layout.ts`에서 `THEME_LABELS` 객체 블록을 정규식 `'([a-z-]+)':\s*'([^']+)'`로 읽어 `viewer_ui.THEME_LABELS`와 순서까지 같은지 본다(작업 2가 만든 뒤 통과).

**검증:** `uv run --frozen --all-packages pytest services/viewer/tests/test_static_export_build.py services/viewer/tests/test_static_export_pages.py services/viewer/tests/test_static_export_themes.py -q`

## 작업 2: Functions 공통 틀 (`deploy/pages/src/pages/layout.ts`)

**바꿀 것**
- `export const THEME_LABELS = { 'github-light': 'GitHub 밝게', 'github-dark': 'GitHub 어둡게', cayman: …}` 대신 키를 모두 따옴표로 쓴다: `'cayman': 'Cayman'` 등 7개, `viewer_ui.THEME_LABELS`와 같은 순서.
- `PERSON_ICON`, `ACCOUNT_LINK`를 `middleware.ts`에서 이리로 옮긴다. `middleware.ts`는 `import { ACCOUNT_LINK } from './pages/layout'` 하고 `export { ACCOUNT_LINK }`로 다시 내보낸다(`test/middleware.test.ts`가 `../src/middleware`에서 가져온다).
- 로고 SVG는 `viewer_ui._LOGO`와 같은 마크업.
- `page(title, body, opts: { head?: string; scripts?: string[]; account?: boolean; themeSelect?: boolean; wide?: boolean } = {})`:
  ```
  <!doctype html><html lang="ko" data-theme="github-light"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
  <title>{title} - synctx</title>
  <script src="/view/static/theme-boot.js"></script>
  <link rel="stylesheet" href="/view/static/theme-vars.css">
  <link rel="icon" href="/view/static/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/view/static/viewer.css">
  <style>{AUTH_STYLE}</style>{opts.head}
  </head><body class="page-auth">
  <a class="skip-link" href="#main">본문으로 건너뛰기</a>
  <header class="shell-header"><a class="shell-logo" href="/view/">{LOGO}<span>synctx</span></a><span class="shell-spacer"></span>
    {themeSelect !== false ? '<div class="shell-controls"><label class="shell-field"><span class="shell-field-label">테마</span><select data-action="theme" aria-label="테마">{7 options}</select></label></div>' : ''}
    {account ? ACCOUNT_LINK : ''}</header>
  <main id="main" class="auth-main{wide ? ' auth-wide' : ''}" tabindex="-1">{body}</main>
  <script src="/view/static/static-mode.js"></script>{module scripts}
  </body></html>
  ```
  옵션의 `selected`는 넣지 않는다(부트·static-mode.js가 저장된 테마로 맞춘다).
- `AUTH_STYLE`(한 상수, 설계 3절 목록 그대로):
  ```css
  .auth-main{box-sizing:border-box;width:100%;max-width:640px;margin:0 auto;padding:32px 16px 72px;display:flex;flex-direction:column;gap:24px}
  .auth-main.auth-wide{max-width:1080px}
  .auth-main .page-title{margin:0}
  .auth-main p{margin:0}
  .auth-main a:not(.btn){color:var(--primary)}
  .auth-main a:focus-visible,.auth-main .btn:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
  .auth-main form{margin:0}
  .auth-main .btn{min-height:44px;height:auto}
  .auth-main .btn:disabled{opacity:.5;cursor:not-allowed}
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
  ```
- `logoutForm()`·`inlineJson()`은 그대로.

## 작업 3: 화면별 본문

- `src/pages/login.ts`
  - `loginPage`: `<h1 class="page-title">로그인</h1><p>…(지금 문구)</p><div class="login-buttons">…</div><p id="login-status" class="auth-muted" role="status" aria-live="polite"></p>` + 설정 JSON. 버튼 마크업·`data-login`·disabled 조건 그대로.
  - `callbackPage`: 지금 본문 + `{ head: no-referrer, scripts, themeSelect: false }`.
  - `unavailablePage`: 지금 문구 그대로.
- `src/pages/settings.ts`: `STYLE`과 `<div class="settings">` 래퍼를 삭제한다(제목·섹션·하단 링크가 `main.auth-main`의 직접 자식이어야 24px 간격이 걸린다). 섹션은 `<section class="auth-section" aria-labelledby="…"><h2 id="…">…</h2>…</section>`.
  - 계정: `<p>{공급자}로 로그인했습니다.</p><p><code class="identity">…</code></p><p class="auth-muted">권한을 받을 때 관리자에게 이 값을 알려 주세요.</p>`
  - 저장소: `<ul class="auth-list"><li><a href="/view/{id}/">{이름}</a></li>…</ul>` 또는 지금 안내 문구.
  - 관리(admin만): `<p class="auth-muted">저장소와 열람자를 관리합니다.</p><p><a class="btn" href="/admin">관리 화면 열기</a></p>`
  - 기기: `<ul class="auth-list settings-devices">` 행은 `<li><span class="device-name">{label}</span><span class="device-meta">마지막 사용 {시각}</span><div class="device-action">{이 기기면 <span class="device-current">이 기기</span>, 아니면 revokeForm}</div></li>`. 「다른 기기 모두 로그아웃」 조건(`others > 1`)과 폼 그대로, `<div class="auth-actions">`로 감싼다.
  - 로그아웃: `<p class="auth-muted">이 기기에서 로그아웃합니다.</p><div class="auth-actions">{logoutForm()}</div>`
  - 맨 아래 `<p><a href="/view/">문서 첫 화면으로</a></p>` 유지. `page(..., { account: true })`.
- `src/pages/denied.ts`: `deniedPage`는 `{ account: true }`, 로그아웃 폼은 `<div class="auth-actions">`로 감싼다. `forbiddenPage`·`errorPage` 문구 그대로.
- `src/admin/page.ts`: 관리 화면 `page(...)`에 `account: true, wide: true` 추가. 그 밖은 손대지 않는다.

## 작업 4: TS 시험 (`deploy/pages/test/pages.test.ts`, 새 파일)

순수 함수라 D1 없이 문자열로 본다.
- `page('t','<p>x</p>')`: `theme-boot.js` 위치 < `theme-vars.css` 위치 < `viewer.css` 위치, `<title>t - synctx</title>`, `class="shell-header"`, `data-action="theme"`, `<option` 7개, `static-mode.js`, `ACCOUNT_LINK` 없음.
- `page(..., { account: true })`에는 `ACCOUNT_LINK`가 `</header>` 바로 앞에 있다.
- `callbackPage()`에는 `data-action="theme"`가 없다. `loginPage(...)`에는 `ACCOUNT_LINK`가 없고 `data-login="google"`이 있다.
- `settingsPage({ identity:'a@example.com', repos:[], admin:false, sessions:[현재 기기, 다른 기기(id '"><x')] })`: `device-current`, `이 기기`, `name="id" value="&quot;&gt;&lt;x"`, `ACCOUNT_LINK` 포함, 섹션 제목 5개 중 관리 없음.
- `DeviceSessionRow` 필드는 `node_modules/@sch0718/firebase-session`의 타입을 보고 맞춘다(`id`, `label`, `lastUsedAt`, `current` 등).

## 검증 (순서대로)

1. `cd deploy/pages && npm test && npm run typecheck && npm run build:client` (node_modules 없으면 `npm ci`, 실패하면 브리프의 기존 node_modules 복사).
2. 작업 1의 pytest + `uv run --frozen --all-packages pytest services/viewer/tests -q -k "static_export or static_auth"`.
3. 화면 확인(스크래치 디렉터리, 커밋하지 않음):
   - 이 저장소를 원본으로 한 작은 `static-export.yaml`(로컬 경로 저장소 하나, theme github-light)로 `scripts/static-export/build.sh --config <임시> --out <스크래치>/site`.
   - esbuild로 `src/pages/*.ts`를 묶은 작은 Node 스크립트로 `login/index.html`, `auth/callback/index.html`, `settings/index.html`(가짜 기기 3대·저장소 2개·admin true), `denied/index.html`, `error/index.html`, `admin/index.html`을 같은 사이트 폴더에 쓴다.
   - `python3 -m http.server`로 띄워 인앱 브라우저에서 데스크톱(1280)·휴대폰(375) 폭, `localStorage['synctx-theme']`을 `github-light`·`github-dark`로 바꿔 스크린샷. `/view/` 첫 화면과 머리글·색·글꼴을 비교한다. 관리 화면은 폭이 지금과 같은 1080px(`wide`)이라 표 모양은 바뀌지 않으므로, 머리글·테마만 본다(관리 API는 띄우지 않는다).
4. 커밋: `feat(static-export): 로그인·설정 화면을 Viewer 모양으로 맞춘다`.
