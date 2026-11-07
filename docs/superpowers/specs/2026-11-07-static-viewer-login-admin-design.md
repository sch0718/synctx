# 정적 Viewer 로그인·저장소별 열람 제한·관리 화면 설계

> 상태: 초안(2026-11-07). 승인 전에는 구현하지 않는다.
> 성격: [정적 Viewer 내보내기](2026-11-05-static-viewer-export-design.md)의 후속이다. 폐쇄망 운영 구성(Helm·Compose)에는 들어가지 않는다.

## 1. 배경과 전제

- 지금 정적 Viewer(`synctx.sch0718.dev`)는 공개 모드(`STATIC_EXPORT_PUBLIC=true`)라 주소를 아는 사람은 모두 본다. 저장소 추가는 `static-export.yaml`을 고쳐 PR로만 한다.
- 원하는 것: 로그인한 사람만, 저장소마다 정해진 사람만 보게 한다. 여러 관리자가 사이트 안 화면에서 저장소를 추가하고 열람자를 관리한다.
- 전제(사용자 확인, 2026-11-07): 열람자는 저장소마다 다르다. 관리자는 여러 명이고 권한은 저장소별 담당자로 나눈다. 다른 사람의 비공개 GitHub 저장소도 추가한다. 무료 운영(Cloudflare Pages·Functions·D1 무료 범위).
- 기존 장치로 안 되는 이유: Cloudflare Access는 경로별 앱을 저장소마다 API로 맞춰야 하고 관리자 화면이 없다. 서버 Viewer의 authz는 사이트 전체 역할(`X-Forwarded-Groups`)이라 저장소별 판정이 없다. 읽기 토큰 하나로는 남의 비공개 저장소를 읽을 수 없다.
- 로그인은 landmark를 따른다: 공용 패키지 `@sch0718/firebase-session`(Google은 Firebase, 카카오·네이버는 OIDC, D1 기기 세션).

### 비목적

- 서버 Viewer(FastAPI)의 권한 모델 변경. 이 설계는 정적 사이트에만 적용한다.
- 공급자 사이 계정 연결·병합, 그룹, 저장소 안 폴더별 권한.
- 실시간 갱신. 문서 변경은 지금처럼 재빌드 때 반영한다.

### 반목적

- 권한 없는 사람이 다른 저장소의 문서·그림·트리 JSON·저장소 이름·근거 발췌를 받는 것.
- 한 관리자가 자기가 통제하지 않는 GitHub 저장소를 사이트에 올리는 것(5.2절).
- GitHub App 개인 키, 세션 서명 키, 읽기 토큰이 저장소·산출물·로그에 남는 것.

## 2. 전체 구조

```
브라우저 ─▶ Pages Functions
            ├─ _middleware  (/view/*, /raw/*; /view/static/*은 _routes.json에서 제외)
            │    세션 쿠키 확인 → 경로의 저장소 id 판정 → next()로 정적 파일 응답
            ├─ /login, /auth/*        로그인(firebase-session/worker)
            ├─ /admin, /api/admin/*   관리 화면
            └─ /invite/<token>        초대 수락
정적 산출물(지금 빌드 그대로) ── D1(세션·역할·초대·기록)
GitHub App ── 문서 저장소 읽기(빌드) · synctx 설정 파일 쓰기·워크플로 실행(관리 화면)
```

| 구성요소 | 위치 | 하는 일 |
|---|---|---|
| 정적 산출물 | `scripts/static-export/build.sh` | 지금과 같다. 저장소별 경로(`/view/<id>/`, `/raw/<id>/`, `/view/api/repos/<id>/`)로 나뉘어 있다 |
| Functions·관리 화면 | `deploy/pages/`(새 TypeScript 하위 프로젝트) | middleware, 로그인, 관리 API, 관리 화면 정적 파일 |
| D1 | Cloudflare D1 `synctx-pages` | 세션(패키지 스키마)과 역할·초대·변경 기록 |
| GitHub App | `synctx-reader`(이름 가칭) | 문서 저장소 Contents 읽기, synctx 저장소 Contents 쓰기·Actions 쓰기 |

배포는 지금 워크플로의 deploy job이 `deploy/pages/`에서 `wrangler pages deploy`로 산출물과 Functions를 함께 올린다. D1 바인딩은 `deploy/pages/wrangler.toml`에 둔다.

## 3. 사람과 권한

### 3.1 역할

| 역할 | 정하는 곳 | 할 수 있는 일 |
|---|---|---|
| 전체 관리자 | Pages 시크릿 `OWNER_ACCOUNTS`(콤마 구분) | 모든 것. 관리자 지정·해제, 모든 저장소의 담당자 권한 |
| 관리자 | D1 `site_roles` | 새 저장소 추가(추가한 저장소의 담당자가 된다), 자기 GitHub 설치 연결 |
| 저장소 담당자 | D1 `repo_members(role=manager)` | 그 저장소의 공개 범위·열람자·담당자 편집, 열람 |
| 열람자 | D1 `repo_members(role=viewer)` | 그 저장소 열람 |

- 사람을 가리키는 값(identity)은 landmark와 같다: Google은 소문자 이메일, 카카오·네이버는 `kakao:<id>`·`naver:<id>`. 공급자마다 다른 사람이다.
- 로그인 자체는 열어 둔다(패키지 `isAllowed: () => true`). 로그인해도 역할이 없으면 아무 저장소도 보이지 않는다.
- 저장소의 마지막 담당자는 지울 수 없다(전체 관리자는 예외).

### 3.2 사람 추가

1. **초대 링크(기본)**: 담당자가 저장소와 역할(열람자·담당자)을 골라 만든다. 1회용, 7일 만료, 토큰은 SHA-256 해시만 저장한다. 받은 사람이 아무 공급자로 로그인해 수락하면 등록된다. 관리자 초대는 전체 관리자만 만든다.
2. **직접 입력**: identity 값을 적는다. 권한이 없어 막힌 사람의 403 화면은 「관리자에게 이 값을 알려 주세요: `<identity>`」를 보여 준다.

### 3.3 D1 스키마

| 테이블 | 열 |
|---|---|
| `users`, `sessions` | 패키지 `migrations/0001`·`firebase-session-0002`를 복사해 적용 |
| `site_roles` | `identity` PK, `role`(admin), `added_by`, `added_at` |
| `repo_members` | (`repo_id`, `identity`) PK, `role`(manager·viewer), `added_by`, `added_at` |
| `invites` | `token_hash` PK, `repo_id`(관리자 초대는 NULL), `role`, `expires_at`, `created_by`, `used_by`, `used_at` |
| `installations` | `installation_id` PK, `account_login`, `bound_identity`, `bound_at`(5.2절). 단계 B에서 만든다 |
| `audit_log` | `at`, `actor`, `action`, `target`, `detail` |

`repo_id`는 `static-export.yaml`의 저장소 id다. 설정에서 저장소를 지우면 관리 화면이 같은 요청에서 그 저장소의 `repo_members`·`invites`도 지운다.

## 4. 열람 판정(middleware)

| 경로 | 판정 |
|---|---|
| `/view/static/*` | Functions를 거치지 않는다(`_routes.json` exclude). 저장소 내용이 없다 |
| `/view/<id>/…`, `/raw/<id>/…`, `/view/api/repos/<id>/…` | 그 저장소의 열람자·담당자이거나 전체 관리자면 통과 |
| `/view/`, `/` | 로그인만 확인. HTML은 아래 필터를 거친다 |
| 그 밖의 `/view/*`·`/raw/*` | 거절(403). 알 수 없는 경로를 열어 두지 않는다 |

- 로그인하지 않았으면 `/login?next=<같은 출처 경로>`로 302. `next`는 `/`로 시작하는 같은 출처 경로만 받는다.
- 권한이 없으면 403 화면(identity 안내). 저장소가 있는지 없는지 구분해 알려 주지 않는다.
- **저장소 이름 숨김**: 모든 HTML 응답을 `HTMLRewriter`로 거쳐, 볼 수 없는 저장소의 `[data-repo-id]` 요소(첫 화면 카드, 저장소 전환 `<option>`)를 지운다. 이를 위해 Viewer 템플릿의 저장소 카드와 전환 항목에 `data-repo-id`를 붙인다(Python 쪽 변경은 이것과 아래 근거 발췌 제한 둘이다). 기본 저장소로 보내는 `_redirects`의 `/ → /view/<default_repo>`는 지우고 `/`는 `/view/`로 보낸다.
- **다른 저장소 근거 발췌**: 회의 문서가 `references`로 다른 저장소를 인용하면 지금 빌드는 그 발췌를 페이지 HTML(`evidence-body`)과 JSON(`data-synctx-evidence`) 두 곳에 넣는다. 둘 다 `[data-repo-id]` 필터로는 지워지지 않고, 열람자는 저장소마다 다르므로 정적 내보내기는 **페이지와 같은 저장소의 발췌만** 넣는다. `static_export/crawl.py`의 `_restrict_evidence_reads`가 페이지 저장소와 다른 저장소 읽기를 거절한다(대상 저장소의 include/exclude 검사는 그대로). 발췌마다 edge에서 거르는 방식은 실제로 필요해지면 따로 정한다.
- **세션 쿠키**: 로그인 성공 때 Functions가 `__Host-synctx_session` 쿠키를 발급한다(HttpOnly, Secure, SameSite=Lax, Path=/, 30일). 값은 패키지 세션 id다. 패키지의 `authenticate`는 헤더를 읽으므로 middleware가 쿠키 값을 세션 헤더로 옮긴 요청으로 부른다. 패키지 0.3.0(2026-11-07 확인)의 `authenticate`는 `Authorization` 헤더가 없으면 세션만으로 인증하지만, 그 경로는 `identity` 열이 있는 소셜 세션만 받는다. Google 세션은 `identity`가 NULL이라 매 요청 Firebase ID 토큰을 요구하고, 페이지 이동에는 그 토큰을 실을 수 없다. 그래서 A 단계 앞에 패키지 PR을 낸다: 옵션(예: `googleSessionOnly: true`)을 켜면 `create`가 Google 세션에도 `identity`(소문자 이메일)를 저장하고, 세션 전용 `authenticate`가 그 세션을 받는다. 기존 호출의 동작은 바꾸지 않는다.
- 매 요청 D1에서 세션 유효성과 역할을 확인한다. 열람자를 지우거나 세션을 철회하면 다음 요청부터 막힌다.
- **응답 헤더**: 정적 자원용 `_headers`는 유지하지만, [Cloudflare 헤더 문서](https://developers.cloudflare.com/pages/configuration/headers/)에 따라 Functions가 생성한 응답에는 적용되지 않으므로 Functions에서 직접 붙인다. 기준은 `services/viewer/src/static_export/build.py`의 `_headers_file()`이다: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `X-Robots-Tag: noindex`, `Cache-Control: no-store`와 기본 CSP(`build.py`의 `CSP`).
  - middleware의 공통 헤더 적용 함수가 각 함수의 응답·조기 반환·오류를 503으로 바꾼 응답을 포함한 최종 응답에 적용한다. 로그인·인증·초대·관리 HTML과 API, 403·503·리다이렉트뿐 아니라 `next()`로 받은 정적 응답과 `HTMLRewriter`로 바꾼 응답도 포함한다. 응답을 복사할 때 상태·본문·`Location`·각 `Set-Cookie`를 보존한다.
  - CSP는 하나의 정책으로 설정한다. 로그인 HTML만 Firebase에 필요한 출처를 허용하는 별도 CSP를 쓰고, 허용되어 실제 SVG를 반환하는 `/raw/…/*.svg` 응답은 `services/viewer/src/app.py`의 `RAW_SVG_CSP`(`default-src 'none'; style-src 'unsafe-inline'; sandbox`)를 쓴다. 나머지는 기본 CSP다. SVG 경로에서 반환한 로그인 리다이렉트·403·503에는 SVG CSP를 쓰지 않는다. 기존 CSP에 다른 정책을 덧붙여 로그인이나 SVG 정책과 겹치게 하지 않는다. Functions를 거치지 않는 `/view/static/*`은 계속 `_headers`로 처리한다.
- 관리 API의 쓰기 요청은 `Origin` 헤더가 사이트 출처와 같을 때만 받는다(CSRF).

## 5. 관리 화면

### 5.1 화면

| 화면 | 누가 | 내용 |
|---|---|---|
| 저장소 목록 | 관리자·담당자 | 내가 담당하는 저장소, 마지막 빌드 상태(GitHub Actions 최근 실행) |
| 저장소 추가 | 관리자 | GitHub 주소 → 설치 확인 → id·이름·ref·공개 범위 → 저장 |
| 저장소 편집 | 담당자 | 공개 범위(include/exclude), 열람자·담당자, 초대 링크, 저장소 제거 |
| 관리자 | 전체 관리자 | 관리자 지정·해제, 관리자 초대 |
| 변경 기록 | 전체 관리자 | `audit_log` 최근 항목 |

화면은 서버에서 만든 HTML과 작은 JS로 만든다. 프런트엔드 프레임워크는 쓰지 않는다.

### 5.2 저장소 추가 흐름

1. 관리자가 GitHub 주소(`owner/repo`)를 넣는다.
2. 관리 API가 App JWT로 `GET /repos/{owner}/{repo}/installation`을 부른다. 설치가 없으면 설치 링크(`https://github.com/apps/<slug>/installations/new`)를 보여 준다.
3. **설치 연결(소유 확인)**: App 설정의 Setup URL에 `https://synctx.sch0718.dev/admin/github/setup`을, Callback URL에 `https://synctx.sch0718.dev/admin/github/callback` 하나만 등록한다. 「설치 중 사용자 인가 요청」은 끈다. 켜면 설치 직후 code가 첫 Callback URL로 바로 와서 관리자 세션에 묶은 `state`를 붙일 수 없다([GitHub 문서](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)).
   - 설치를 마치면 GitHub가 `/admin/github/setup?installation_id=…`로 돌려보낸다. 로그인한 관리자만 받는다. `installation_id`는 믿지 않고(위조 가능, [GitHub 문서](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url)) 소유 확인을 시작한다: 무작위 `state`를 만들어 `__Host-synctx_gh_state` 쿠키(HttpOnly, Secure, SameSite=Lax, Path=/, 10분. `__Host-` 접두어는 `Path=/`를 요구한다. 설정과 삭제에 같은 속성을 쓴다)에 `state`·`installation_id`·대상 `owner/repo`·현재 세션 id의 SHA-256을 담고, `https://github.com/login/oauth/authorize?client_id=…&redirect_uri=<origin>/admin/github/callback&state=…`로 보낸다. 이미 설치된 저장소를 추가할 때도 같은 흐름으로 시작한다.
   - `/admin/github/callback?code=…&state=…`은 쿠키가 있고, 만료 전이고, 쿼리 `state`가 쿠키와 같고, 쿠키의 세션 해시가 지금 세션과 같을 때만 진행한다. 맞든 틀리든 쿠키를 지운다(재사용 거절). 그다음 code를 사용자 토큰으로 바꾼다. 사용자 토큰은 아래 확인 직후 버린다.
4. **저장소 단위 확인**: 설치 하나에 접근할 수 있어도 그 안의 모든 저장소를 통제한다는 뜻은 아니다(사용자 토큰은 사용자와 App이 함께 접근하는 저장소만 본다). 사용자 토큰으로 `GET /user/installations`에 쿠키의 `installation_id`가 있고, `GET /user/installations/{installation_id}/repositories`에 대상 `owner/repo`가 있으며 그 항목의 `permissions.admin`이 true일 때만 다음으로 간다. 아니면 403이고 설정 파일을 바꾸지 않는다. 확인에 성공하면 그 설치를 관리자에게 연결해 둔다(`installations.bound_identity`, 표시용). 저장소를 추가할 때마다 이 확인을 다시 한다.
5. id(영문 소문자·숫자·`-`, 중복 금지), 표시 이름, ref(기본 브랜치를 미리 채움), include/exclude를 받는다. include 후보로 최상위 폴더 목록을 체크박스로 보여 주고, 직접 입력도 받는다. include가 비면 저장하지 않는다(저장소 전체를 실수로 여는 것을 막는다. 전체를 열려면 `/`를 명시).
6. 저장: App 설치 토큰으로 synctx 저장소 `static-export.yaml`을 GitHub Contents API로 고친다. 읽은 `sha`를 함께 보내 동시 수정을 막는다(409면 「다른 관리자가 먼저 고쳤습니다. 다시 불러오세요」). 커밋 메시지에 관리자 identity를 남기지 않고 `audit_log`에 남긴다.
7. 같은 요청에서 `repo_members`에 추가한 관리자를 담당자로 넣고, `workflow_dispatch`로 빌드·배포를 시작한다. 화면은 실행 상태를 보여 준다.

관리 API가 고치는 파일은 `static-export.yaml` 하나로 코드에서 고정한다. App에는 `workflows` 권한을 주지 않는다(워크플로 파일을 고칠 수 없다).

### 5.3 설정 파일 변화

```yaml
repositories:
  - id: study-notes
    name: 학습 노트
    url: https://github.com/sch0718/work-docs.git
    default_ref: main
    auth:
      github_app: true        # 새 필드. credential_env와 함께 쓰지 않는다
    include: [02.학습/]
```

- `auth.github_app: true`면 빌드가 그 저장소 owner의 설치 토큰을 만들어 읽는다. 기존 `credential_env`도 그대로 지원한다.
- 열람자·담당자는 설정 파일에 두지 않는다(D1).

## 6. 빌드 변경

- 워크플로 build job에 App ID와 개인 키(시크릿 `GH_APP_ID`, `GH_APP_PRIVATE_KEY`)를 넘긴다. 빌드 스크립트가 설정을 읽어 `github_app` 저장소의 owner마다 설치 토큰(Contents 읽기, 해당 저장소로 범위 제한)을 만들고, 그 저장소의 git 인증에만 쓴다. 토큰은 환경변수로만 다루고 산출물 검사(토큰 문자열)를 그대로 돈다.
- 한 저장소라도 읽지 못하면 지금처럼 빌드가 실패하고 배포하지 않는다. 사이트는 이전 배포로 남는다. 관리 화면 목록에 실패한 실행과 원인 저장소를 보여 주고, 담당자나 전체 관리자가 고치거나 제거한다.
- deploy job은 `dist`를 `deploy/pages/`의 출력 폴더로 두고 Functions와 함께 올린다. 배포 후 점검은 `check-access.sh`를 로그인 모드에 맞게 재사용한다: 세션 없이 `/view/<id>/`를 요청하면 `/login`으로 302여야 통과.
- 이 사이트는 저장소 변수 `STATIC_EXPORT_LOGIN=true`로 로그인 모드를 고르고 `STATIC_EXPORT_PUBLIC`을 지운다. 둘 다 없으면 지금의 Access 모드다. 공개·Access 모드 코드는 남겨 둔다(다른 사이트가 쓸 수 있다).

## 7. 오류와 운영

| 상황 | 동작 |
|---|---|
| D1·인증서 조회 실패 | 503. 문서를 열어 주지 않는다(실패 시 닫힘) |
| Functions 무료 요청 한도 초과 | Pages 플랫폼의 `Fail closed`로 정적 문서 대신 오류 페이지를 반환한다. middleware의 503 처리가 실행된다는 뜻은 아니다 |
| 세션 만료·철회 | 로그인으로 302 |
| GitHub API 실패·한도 | 관리 화면에 오류 표시, 설정 파일은 바뀌지 않는다 |
| 빌드 실패 | 이전 배포 유지, 관리 화면에 표시 |
| App 설치 제거 | 다음 빌드가 실패한다. 담당자가 저장소를 제거하거나 다시 설치한다 |

- Pages 일반 환경변수: `FIREBASE_WEB_CONFIG`(Firebase 웹 앱 설정 JSON, 비밀 아님. 로그인 화면이 브라우저에 넘긴다).
- 시크릿(Pages): `OWNER_ACCOUNTS`, `FIREBASE_PROJECT_ID`, `KAKAO_CLIENT_*`, `NAVER_CLIENT_*`, `GH_APP_ID`, `GH_APP_PRIVATE_KEY`(PKCS#8), `GH_APP_CLIENT_ID`·`GH_APP_CLIENT_SECRET`(설치 연결용). GitHub Actions: `GH_APP_ID`, `GH_APP_PRIVATE_KEY`.
- 사용자가 직접 하는 일: GitHub App 만들기·개인 키 받기, Firebase 프로젝트(landmark 것 재사용 여부 결정)와 카카오·네이버 앱에 `https://synctx.sch0718.dev/auth/callback` 등록, GitHub App에 Setup URL·Callback URL 등록(5.2절), D1 만들기, 시크릿 등록. 값은 채팅에 붙여 넣지 않는다.
- 첫 로그인 배포의 필수 조건: 사용자가 Cloudflare 대시보드 Workers & Pages → 해당 Pages 프로젝트 → Settings → Runtime → Fail open / closed에서 `Fail closed`를 선택·저장하고 다시 열어 확인한다. 실제 계정 설정은 아직 미확인이고, 확인하지 못하면 로그인 배포를 진행하지 않는다. `Fail open`이면 무료 요청 한도 소진 시 Functions 인증을 건너뛰고 정적 파일을 제공하므로 middleware의 오류 처리만으로는 막을 수 없다([Cloudflare 공식 문서](https://developers.cloudflare.com/pages/functions/routing/#fail-open--closed)).
- 로그인 모드 설치·복구 절차에도 `Fail closed` 재확인을 넣는다. 한도 초과 시 이 설정을 유지하고 한도 회복 후 9절의 로그인 모드 접근 점검을 다시 한다. 복구를 위해 `Fail open`으로 바꾸지 않는다. 정상 상태의 302 점검이나 로컬 middleware 시험은 플랫폼의 한도 초과 설정을 검증하지 못하므로 대시보드 확인을 별도로 기록한다.
- 오래된 세션·만료 초대 정리는 관리 화면을 열 때 함께 지운다(Cron을 따로 두지 않는다).
- 무료 범위: Functions 하루 10만 요청, D1 읽기 하루 500만 행(공식 문서로 재확인 필요). 문서 한 쪽 열람에 Functions 호출 3~5번, D1 읽기 2~3행으로 본다.

## 8. 단계

규모가 커서 패키지 PR 하나와 synctx PR 둘로 나눈다. 각 단계가 혼자 쓸모 있다.

| 단계 | 내용 | 끝났을 때 |
|---|---|---|
| 0 | 패키지 PR(Google 세션 전용 인증, 4절) | 패키지 새 버전 발행 |
| A | `deploy/pages/` 뼈대, 로그인, 세션 쿠키, middleware, D1 역할·초대, 관리 화면의 열람자·담당자·관리자 편집, `data-repo-id`, 워크플로 deploy 변경 | 로그인한 사람만 저장소별로 본다. 저장소 추가는 아직 PR로 한다(`credential_env` 그대로) |
| B | GitHub App, 설치 연결, 저장소 추가·편집·제거 화면, 설정 파일 커밋, 워크플로 실행·상태, 빌드의 `github_app` 인증 | 관리자가 화면에서 남의 비공개 저장소까지 추가한다 |

- A를 배포한 뒤 실제로 2명 이상에게 초대를 보내 쓰는지 확인하고 B를 시작한다.
- 사용 확인 시점: B 병합 후 한 달(2026-12-말). 그때까지 화면으로 저장소를 추가한 일이 없으면 B의 관리 화면 쓰기 기능을 걷어내는 PR을 제안한다.

## 9. 시험

- `deploy/pages/`: vitest로 middleware 판정표(4절의 경로별 통과·302·403, `next` 검증), `HTMLRewriter` 필터, 역할 규칙(마지막 담당자, 관리자 초대 권한), 초대 1회용·만료, 설치 연결(`state` 없음·불일치·만료·재사용·다른 세션이면 거절, 사용자 토큰에 설치가 없거나 대상 저장소가 없거나 `permissions.admin`이 false면 거절), 설정 파일 수정(sha 충돌, `static-export.yaml` 외 경로 거절, include 비면 거절). D1은 패키지 시험처럼 로컬 D1(miniflare)로 돈다.
- Functions 응답 헤더: 로그인·인증·초대·관리 HTML/API, 302·403·503, 통과한 정적 HTML/JSON과 `HTMLRewriter` 결과에 공통 보안 헤더와 `no-store`가 있는지 확인한다. 기본 CSP·로그인 전용 CSP·허용된 raw SVG 전용 CSP가 각각 하나만 적용되고, SVG 경로의 거절·오류에는 기본 CSP가 적용되는지 확인한다. `Location`·각 `Set-Cookie`·상태·본문 보존도 확인한다.
- Python: `data-repo-id`가 카드와 전환 항목에 붙는지, 다른 저장소를 인용한 회의 페이지의 HTML·JSON 어디에도 그 저장소 발췌가 없고 그 저장소를 읽지 않는지, `github_app` 설정 검증(`credential_env`와 동시 사용 거절).
- 워크플로 시험(`test_static_export_workflow.py`): App 시크릿이 build job에만 있는지, deploy가 `deploy/pages/`에서 Functions와 함께 올리는지.
- 수동: 배포 후 세션 없이 `/view/<id>/`·`/raw/<id>/…`·`/view/api/repos/<id>/tree.json` 요청이 302인지, 다른 저장소 열람자로 403인지, 첫 화면에 권한 없는 저장소 이름이 없는지.

## 10. 남은 결정(구현 계획 전에 확인)

1. Firebase 프로젝트: landmark 것을 재사용할지, synctx용을 새로 만들지.
2. 패키지 순서: `isAllowed` 콜백은 0.3.0에 있다. Google 세션 전용 인증은 없으므로(4절) 패키지 PR 병합·발행 → synctx가 그 버전 사용 → A 단계 PR 순서로 간다. 패키지 PR은 sch0718-toolkit-web 저장소의 별도 작업이다.
