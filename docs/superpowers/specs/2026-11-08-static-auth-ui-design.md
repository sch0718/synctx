# 정적 Viewer 로그인 모드 화면 모양 맞추기 설계

> 상태: 확정(2026-11-08). 성격: [정적 Viewer 로그인·관리 설계](2026-11-07-static-viewer-login-admin-design.md)의 화면 손질. 동작은 바꾸지 않는다.

## 1. 문제와 전제

- 쓰는 사람: 소유자 1명과 초대한 지인 몇 명. Cloudflare 무료 한도 안에서 운영한다.
- Pages Functions가 그리는 화면(`/login`, `/auth/callback`, `/settings`, 거절·주소 거절, 잠시 이용할 수 없음)은 `viewer.css`만 링크한다. Viewer 페이지가 함께 쓰는 테마 변수(`THEME_VARS_CSS`), 테마 부트(`html[data-theme]`), 머리글(`header.shell-header`)이 없어 흰 바탕·기본 버튼으로 보인다.
- `page-auth`, `login-buttons`에는 CSS가 없고 `.settings`는 설정 화면 인라인 스타일에만 조금 있다.
- 이미 있는 것으로 안 되는 이유: 정적 사이트의 테마 변수는 각 Viewer HTML 안 `<style>`에만 있고 Functions가 링크할 파일이 없다.

### 비목적

- 새 기능(설정 화면의 테마 선택 섹션, 저장소 선택·검색을 머리글에 넣기, 관리 화면 재디자인).
- 로그인·세션·권한 동작, 라우팅, CSP 변경.

## 2. 결정: 정적 내보내기가 테마 파일을 낸다 (사용자 선택, 2026-11-08)

`static_export` 빌드가 두 파일을 더 쓴다.

| 파일 | 내용 | 원본 |
|---|---|---|
| `view/static/theme-vars.css` | `THEME_VARS_CSS` 그대로 | `renderer.py` |
| `view/static/theme-boot.js` | 정적 페이지 부트와 같은 JS(설정의 기본 테마, `SYNCTX_STATIC` 설정) | `pages.py`의 `_boot_script` 본문 |

- `_boot_script`를 `boot_js(default_theme)`(JS 본문)와 `<script>` 감싸기로 나눈다. Viewer 페이지 결과는 바이트 그대로다.
- `/view/static/*`는 `_routes.json`에서 Functions 밖이라 로그인 없이 받는다. 공개·Access 모드 산출물에도 같이 들어가지만 쓰지 않을 뿐 해는 없다.
- 기본 테마는 `static-export.yaml`의 `theme`을 따른다(운영은 `github-light`).

## 3. Functions 공통 틀 (`src/pages/layout.ts`)

`page(title, body, opts)`가 Viewer `viewer_ui.page()`와 같은 순서로 그린다.

```
<html lang="ko" data-theme="github-light">          ← 부트가 덮어쓴다(파일이 없을 때의 대비값)
<head> charset · viewport · robots noindex · <title>{title} - synctx</title>
       <script src="/view/static/theme-boot.js"></script>
       <link rel="stylesheet" href="/view/static/theme-vars.css">
       favicon · <link rel="stylesheet" href="/view/static/viewer.css"> · 인증 화면 STYLE · opts.head
<body class="page-auth">
  skip-link · header.shell-header( 로고(/view/) · shell-spacer · 테마 select(data-action="theme") · [설정 버튼] )
  <main id="main" class="auth-main" tabindex="-1"> body </main>
  <script src="/view/static/static-mode.js"></script>   ← 테마 바꾸면 localStorage 저장 후 새로 고침(기존 코드)
  opts.scripts(module)
```

- 옵션 `themeSelect: false`면 테마 select를 빼고 저장된 테마만 적용한다. 로그인 중(`/auth/callback`) 화면에만 쓴다(테마를 바꾸면 새로 고쳐져 진행 중인 콜백 처리를 잃는다).
- 옵션 `wide: true`면 본문 최대 폭을 Viewer `.home`과 같은 1080px로 둔다. 표가 넓은 관리 화면만 쓴다.
- 옵션 `account: true`면 머리글 끝에 `ACCOUNT_LINK`(사람 모양 설정 버튼)를 붙인다. 로그인한 화면(설정, 거절, 관리)만 켠다. 로그인·콜백·잠시 이용할 수 없음은 끈다.
- `ACCOUNT_LINK`는 `layout.ts`로 옮기고 `middleware.ts`는 그것을 가져다 쓴다(같은 표식 하나).
- 테마 이름표 7개(`THEME_LABELS`)는 TS에 복사하고, Python 시험이 `viewer_ui.THEME_LABELS`와 같은지 확인한다.
- 휴대폰 폭(767px 이하)에서 Viewer는 머리글 컨트롤을 숨기고 서랍 메뉴로 옮기지만, 인증 화면은 서랍이 없고 머리글이 비어 있으므로 `.page-auth`에서는 테마 select와 `shell-spacer`를 그대로 보인다.

### 인증 화면 STYLE (layout.ts 인라인, settings.ts의 STYLE을 대체)

landmark 설정 화면의 모양을 viewer.css 변수로 옮긴다.

- `.auth-main`: 최대 폭 640px, 가운데, 위아래 여백, 섹션 사이 24px(flex column gap).
- `.auth-section`: 제목 `h2` 15px·굵게(800), 안쪽 간격 8px.
- `.auth-muted`: `--text-secondary`, 13px.
- `.auth-main .btn`: 최소 높이 44px. 비활성 버튼은 흐리게.
- `.login-buttons`: 세로 버튼 묶음(최대 320px, 가운데 정렬 글자).
- `.auth-list`: 테두리 카드 목록(저장소, 기기 공용). 행은 flex wrap, 행 사이 구분선.
- `.device-name`(굵게), `.device-meta`(보조 색), `.device-current`(작은 표식, `--primary-lighter` 바탕·`--primary` 글자), 행 끝 동작은 오른쪽으로.
- `.auth-main a:not(.btn)`: `--primary` 색, 포커스 윤곽선. `.btn`의 색은 그대로 둔다(viewer.css에는 일반 링크 색 규칙이 없다).
- `code.identity`: 고정폭, `--bg-neutral` 바탕, 줄바꿈 허용.

## 4. 화면별 구성

- 로그인: 제목 「로그인」, 안내 한 줄, Google·카카오·네이버 버튼(지금처럼 설정 없는 공급자는 disabled), `#login-status`, 설정 JSON. 클라이언트 JS는 그대로.
- 로그인 중(`/auth/callback`, `themeSelect: false`): 제목, 상태 줄, 로그인 화면 링크. no-referrer 메타 유지.
- 설정(`/settings`, `account: true`): 제목 「설정」 아래 섹션 순서 고정.
  1. 계정: 「Google로 로그인했습니다.」, `code.identity`, 보조 글.
  2. 볼 수 있는 저장소: `ul.auth-list`의 `<li><a href="/view/<id>/">이름</a></li>`, 없으면 안내 글.
  3. 관리(관리할 수 있을 때만): 보조 글 + `a.btn` 「관리 화면 열기」.
  4. 로그인한 기기: `ul.auth-list` 행마다 이름, 「마지막 사용 …」, 이 기기면 `이 기기` 표식, 아니면 「로그아웃시키기」 폼. 다른 기기가 2대 이상이면 「다른 기기 모두 로그아웃」(지금 조건 유지).
  5. 로그아웃: 보조 글 + 로그아웃 버튼.
- 거절(`deniedPage`, `account: true`), 주소 거절(`forbiddenPage`), 잠시 이용할 수 없음(`errorPage`, `unavailablePage`): 같은 틀에 제목·안내·링크. 거절의 로그아웃 버튼은 유지.
- 관리 화면·초대 화면은 공통 틀만 바뀐다. 관리 화면에는 `account: true`, `wide: true`를 준다.

## 5. 시험과 검증

- 기존 시험은 문자열을 약하게 바꾸지 않는다. `settings.test.ts`의 `<a href="/view/repo-a/">A 저장소</a>`, `href="/admin"`, `action="/auth/logout"` 확인은 그대로 통과해야 한다.
- 새 TS 시험(`test/pages.test.ts`): 부트 스크립트가 테마 CSS·viewer.css보다 앞에 있음, 머리글·테마 select 7개, `account`에 따른 설정 버튼 유무, 로그인 화면에 설정 버튼 없음, 기기 행(이 기기 표식, 로그아웃시키기 폼의 id 값 이스케이프).
- 새 Python 시험: 빌드가 `theme-vars.css`(= `THEME_VARS_CSS`)와 `theme-boot.js`(= 페이지 인라인 부트 본문)를 쓴다. TS의 `THEME_LABELS`가 Python과 같다.
- `deploy/pages`의 `npm test`, `npm run typecheck`, `npm run build:client` 통과.
- 화면 확인: 렌더한 HTML과 정적 자산을 로컬 정적 서버에 올려 로그인·설정 화면을 데스크톱·휴대폰 폭, 밝은·어두운 테마로 스크린샷을 찍고 정적 내보내기한 Viewer 첫 화면과 색·글꼴·머리글을 비교한다. 관리 화면은 폭이 지금과 같아(1080px) 표 모양이 바뀌지 않으므로 머리글·테마만 본다.
