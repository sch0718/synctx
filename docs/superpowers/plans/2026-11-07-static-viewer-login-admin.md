# 정적 Viewer 로그인·열람 제한·관리 화면 구현 계획

> 설계: [2026-11-07-static-viewer-login-admin-design.md](../specs/2026-11-07-static-viewer-login-admin-design.md)
> 실행: superpowers:subagent-driven-development(권장) 또는 executing-plans. 단계 표시는 `- [ ]`.

**목표:** 정적 Viewer를 로그인한 사람만, 저장소마다 정해진 사람만 보게 하고, 열람자·담당자·관리자를 사이트 안 화면에서 관리한다(단계 A). 화면에서 저장소를 추가하는 단계 B는 A를 실제로 쓴 뒤에 따로 계획한다.

**구조:** 지금 빌드 산출물(`dist/`)은 그대로 두고, 그 위에 Pages Functions(`deploy/pages/`)를 얹는다. middleware가 세션 쿠키와 D1 역할로 저장소별 경로를 판정한다. 로그인은 `@sch0718/firebase-session`.

**기술:** TypeScript, Cloudflare Pages Functions·D1, wrangler 4, vitest, esbuild(로그인 화면 JS 묶기), Python(Viewer 템플릿 소폭).

## 공통 제약

- 프로젝트 전제: 열람자 수십 명, 여러 관리자, 무료 운영.
- 검토: 단계마다 구현 뒤 1회, 최대 2회차. 2회차에는 Critical만 고친다. 지적이 좁은 경계 사례로 옮겨 가면 끝낸다.
- 시크릿·토큰 값은 채팅·저장소·로그·산출물에 두지 않는다. 사용자가 `wrangler secret`·`gh secret set`으로 직접 넣는다.
- 쿠키 이름 `__Host-synctx_session`(HttpOnly, Secure, SameSite=Lax, Path=/, 30일). 세션 헤더 이름(패키지 내부용) `X-Synctx-Session`.
- identity 형식: Google은 소문자 이메일, 소셜은 `kakao:<sub>`·`naver:<sub>`.
- 실패 시 닫힘: D1·인증서 오류는 503이고, 문서를 열어 주지 않는다. Functions 무료 요청 한도 초과는 middleware가 실행되지 않을 수 있으므로 Pages 플랫폼의 `Fail closed` 설정으로 차단한다(설계 7절).
- 같은 동작을 여러 층에서 겹쳐 시험하지 않는다.
- 명령은 별도 표시가 없으면 저장소 루트에서 실행한다. A2~A5의 `npm`·`npx` 명령은 `deploy/pages`에서 실행하고, 각 검증은 새 셸에서 시작한다(앞선 `cd`에 의존하지 않는다).

---

## 단계 0: 패키지 옵션(sch0718-toolkit-web, 별도 세션·PR)

### 작업 0: Google 세션 전용 인증

**파일:** `packages/firebase-session/src/worker/session.ts`(`create` 153행 근처 INSERT, `authenticate` 189행), `session.test.ts`, `.changeset/*.md`, README의 「카카오·네이버 로그인」 절.

- [ ] `SessionAuthOptions`에 `googleSessionOnly?: boolean`을 더한다. 켜면 `create`가 `identity` 열에 소문자 이메일을 넣는다. 꺼져 있으면 지금과 같다.
- [ ] `authenticate`의 세션 전용 경로가 `identity`가 이메일인 행(`kakao:`·`naver:`로 시작하지 않음)을 `o.googleSessionOnly`가 켜진 경우에만 `provider: 'google'`로 받는다. 허용 판정은 `allowedEmails` 다음에 `isAllowed`를 부른다. 지금 `social`이 있어야만 세션 전용 경로로 가는 조건(`o.social && …`)을 `(o.social || o.googleSessionOnly) && …`로 바꾸되, 이는 진입 조건일 뿐이다. 경로 안에서도 이메일 identity 수락에는 `o.googleSessionOnly`를 별도로 검사한다. 옵션이 꺼져 있으면 이메일 identity가 DB에 남아 있어도 ID 토큰 없는 Google 요청은 기존 `missing-token` 경로로 간다. `kakao:`·`naver:` identity는 기존 `social` 설정에 따른 인증을 유지한다.
- [ ] 시험: 옵션을 켜면 ID 토큰 없이 Google 세션만으로 인증된다, 켜서 만든 Google 세션을 같은 DB에서 옵션을 끈 인증기로 읽으면 `missing-token`이다(`social` 설정 유무 모두 확인), Google·소셜 혼합 설정에서 옵션이 켜지면 Google·카카오·네이버 세션이 각각 인증되고 꺼지면 Google만 `missing-token`이며 소셜 세션은 기존대로 인증된다, 철회·만료·허용 목록에서 빠짐은 401·403이다, 0002 마이그레이션이 없으면 기존 오류 문구가 나온다.
- [ ] 검증(toolkit 저장소 루트에서): `pnpm exec vitest run packages/firebase-session/src`, `pnpm lint`. 패키지에는 `test` 스크립트가 없고 루트 `vitest.config.ts`가 시험을 모은다. changeset(minor) 커밋 → PR → 병합 → 릴리스 PR 병합으로 새 버전(예: 0.4.0)이 발행된다.

---

## 단계 A: 로그인·열람 제한·사람 관리(synctx, PR 하나)

### 작업 A1: Viewer 산출물 표식(Python)

**파일:** `services/viewer/src/app.py:767`(저장소 카드), `services/viewer/src/viewer_ui.py:92`(`repo_options`), `viewer_ui.py:279`(검색 저장소 선택), `services/viewer/src/static_export/build.py:146`(`_redirects`·`_synctx/repos.json`), `services/viewer/src/static_export/crawl.py:81`(`_restrict_evidence_reads`), 시험 `services/viewer/tests/test_viewer_ui.py`, `tests/test_static_export_evidence.py`, `tests/test_static_export_*.py`.

- [ ] 카드 `<li>`와 저장소 `<option>` 두 곳에 `data-repo-id="{esc(r.id)}"`를 붙인다. 브랜치 `<option>`에는 붙이지 않는다.
- [ ] `_redirects`를 `/ /view/ 302`로 바꾼다. `default_repo`는 첫 화면 안에서만 쓴다. `verify.py`의 `_redirects` 검사가 새 값과 맞는지 확인한다.
- [ ] 빌드가 `_synctx/repos.json`(`[{"id","name"}]`)을 남긴다. 산출물 검사를 통과해야 한다.
- [ ] `_restrict_evidence_reads`: 근거 읽기의 첫 인자(페이지 저장소 id)와 다른 저장소의 읽기는 `ValueError`로 거절한다. 다른 저장소 발췌는 HTML(`evidence-body`)과 JSON(`data-synctx-evidence`) 모두에 들어가고 middleware 필터로 지울 수 없어서다(설계 4절).
- [ ] 시험: 카드·옵션에 `data-repo-id`가 있고 이스케이프된다, `_redirects` 값. `test_static_export_evidence.py`의 `cross_repo=True` 경우는 기대를 바꾼다: repo-a 페이지 어디에도 repo-b 발췌(`VISIBLE-EVIDENCE-123`)가 없고 repo-b를 읽지 않는다. `cross_repo=False` 경우는 그대로 통과한다.
- [ ] 검증: `cd services/viewer && uv run pytest -q tests`.

### 작업 A2: `deploy/pages/` 뼈대와 D1 스키마

**파일(새로):** `deploy/pages/package.json`, `deploy/pages/package-lock.json`, `.npmrc`(`@sch0718:registry=https://npm.pkg.github.com`, 토큰은 환경변수 `NODE_AUTH_TOKEN`), `tsconfig.json`, `vitest.config.ts`, `wrangler.toml`, `migrations/0001_users_sessions.sql`·`0002_social_sessions.sql`(패키지에서 복사), `migrations/0003_synctx_roles.sql`, `.gitignore`.

- [ ] `wrangler.toml`: `name = "synctx"`, `pages_build_output_dir = "site"`(배포 직전 `dist/`를 복사해 둘 폴더, git 제외), `[[d1_databases]] binding = "DB" database_name = "synctx-pages"`. database_id는 사용자가 D1을 만든 뒤 넣는다(id는 비밀이 아니다).
- [ ] `0003`: 설계 3.3절의 `site_roles`, `repo_members`, `invites`, `audit_log`. B 전용 `installations`는 B에서 `0004`로 더한다. 기본 키와 `repo_members(identity)` 색인.
- [ ] 의존성: `@sch0718/firebase-session`(단계 0 버전), `firebase@12`(패키지의 선택적 peerDependency, 로그인 JS가 `firebase/app`·`firebase/auth`를 묶는다), `wrangler@4`, `vitest`, `@cloudflare/vitest-pool-workers`, `esbuild`, `typescript`. 버전은 정확히 고정한다.
- [ ] 최초 설치: `deploy/pages/`에서 `NODE_AUTH_TOKEN`을 환경변수로 제공하고 `npm install`로 `package-lock.json`을 생성한다. `package.json`과 lockfile을 함께 버전 관리에 포함한다. 이후 재설치와 A6의 CI에서는 `npm ci`를 쓴다.
- [ ] 검증(`deploy/pages/`에서): `npm ci && npx wrangler d1 migrations apply synctx-pages --local`이 성공해야 한다. 이어서 `npx wrangler d1 execute synctx-pages --local --command "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name;"`으로 설계 3.3절의 여섯 테이블(`users`, `sessions`, `site_roles`, `repo_members`, `invites`, `audit_log`)이 모두 있는지 확인한다. A2 완료는 이 스키마 확인으로 판단하고, `npx vitest run`은 시험을 추가하는 A3부터 실행한다.

### 작업 A3: 로그인과 세션 쿠키

**파일:** `deploy/pages/src/env.ts`(바인딩·시크릿 타입), `src/session.ts`(패키지 연결, 쿠키 발급·읽기·삭제), `functions/auth/[[path]].ts`(`POST /auth/session` Google, `POST /auth/session/social`, `GET /auth/callback`, `POST /auth/logout`), `functions/login.ts`(`GET /login` 라우트), `src/pages/login.ts`(로그인 화면 HTML, 공급자 버튼 세 개), `client/login.ts` → esbuild로 `site/view/static/synctx-auth/login.js`(`/view/static/*`은 `_routes.json`에서 Functions를 거치지 않으므로 `functions/auth/[[path]].ts`와 겹치지 않는다), 시험 `test/session.test.ts`, `test/auth.test.ts`.

- [ ] `createSessionAuth({ …, sessionHeader: 'X-Synctx-Session', googleSessionOnly: true, isAllowed: () => true, social: { kakao: { …, redirectUris: [<origin>/auth/callback] }, naver: { …, redirectUris: [<origin>/auth/callback] }, keys } })`. `redirectUris`는 공급자별 설정의 속성이다(`SocialProviderConfig`). 허용 판정은 하지 않는다(역할은 D1에서 본다).
- [ ] 세션을 만들면 응답에 `Set-Cookie: __Host-synctx_session=<sessionId>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=2592000`을 붙이고, 본문에는 세션 id를 넣지 않는다.
- [ ] `readSession(req)`: 쿠키 값을 `X-Synctx-Session` 헤더로 옮긴 새 Request로 `authenticate`를 불러 `{ identity }`를 돌려준다. 없거나 401이면 null, 인증서·D1 오류는 그대로 던진다(호출자가 503).
- [ ] `next` 검사: `/`로 시작하고 `//`·`/\`로 시작하지 않는 같은 출처 경로만 받는다. 아니면 `/view/`.
- [ ] Firebase 웹 설정: 로그인 HTML은 `env.FIREBASE_WEB_CONFIG`(JSON, `apiKey`·`authDomain`·`projectId`·`appId`)를 `<script type="application/json">`로 넣고, `client/login.ts`가 읽어 패키지 클라이언트에 넘긴다. 값이 없거나 형식이 틀리면 로그인 화면은 503이다.
- [ ] `functions/login.ts`의 `onRequestGet`은 `src/pages/login.ts`가 만든 로그인 HTML을 `Content-Type: text/html; charset=utf-8`로 200 응답한다. 세션 없이 열리고, 위 규칙으로 검사한 `next`를 로그인 흐름에 넘긴다.
- [ ] 로그인 화면 CSP는 Firebase 로그인에 필요한 출처만 연다(landmark `vite` 플러그인의 CSP 값을 참고해 그대로 옮긴다). A3은 이 값을 `src/pages/login.ts`의 상수 `LOGIN_CSP`로 정의만 하고 응답에 직접 붙이지 않는다. 설정은 A4의 공통 헤더 적용 함수가 로그인 HTML에 이 값 하나만 넣는다(기본 CSP와 중복하지 않는다).
- [ ] 시험: 쿠키 속성, 쿠키→헤더 변환, `next` 검사표, 세션 없는 `GET /login`이 200 HTML(공급자 버튼 세 개), 로그아웃이 세션을 철회하고 쿠키를 지운다, 쓰기 요청의 `Origin` 불일치는 403.
- [ ] 로컬 사이트 준비(A1·A2 완료 뒤): 저장소 읽기 자격 증명은 `static-export.yaml`의 `auth.credential_env`로 제공한다. 저장소 루트에서 아래 명령을 실행해 A1의 관리용 목록까지 복사하고 로그인 JS를 만든다. A5 수동 검증에서도 이 사이트를 사용한다.

  ```sh
  scripts/static-export/build.sh build --config static-export.yaml --out dist
  mkdir -p deploy/pages/site
  cp -R dist/. deploy/pages/site/
  test -f deploy/pages/site/_synctx/repos.json
  (cd deploy/pages && npx esbuild client/login.ts --bundle --outfile=site/view/static/synctx-auth/login.js)
  ```

  `npm ci` 직후의 깨끗한 설치에서 이 esbuild 명령이 모듈 누락 없이 통과해야 한다.

- [ ] 검증: `npx vitest run`, `npx tsc --noEmit`. 로컬에서 `npx wrangler pages dev site`로 Google과 카카오(또는 패키지의 개발용 가짜 소셜)를 거쳐 각각 쿠키가 생기는지, `/view/static/synctx-auth/login.js`가 200과 JavaScript `Content-Type`으로 오는지 본다.

### 작업 A4: 권한 판정과 middleware

**파일:** `src/authz.ts`, `functions/_middleware.ts`, `site/_routes.json`(로컬 검증 전에 생성하고 배포 단계에서도 생성), `src/pages/denied.ts`, 시험 `test/authz.test.ts`, `test/middleware.test.ts`.

- [ ] `authz.ts`: `isOwner(env, identity)`(`OWNER_ACCOUNTS` 콤마 목록), `repoRole(db, identity, repoId): 'manager'|'viewer'|null`, `canView(...)`, `visibleRepos(db, identity): Set<string>|'all'`.
- [ ] `repoIdFromPath(path)`: `/view/<id>/…`, `/raw/<id>/…`, `/view/api/repos/<id>/…`에서 id를 꺼낸다. `/view/static/`·`/view/api/`·`/view/`는 저장소 경로가 아니다. URL 디코딩 뒤 비교한다.
- [ ] middleware 판정표(설계 4절): 정적 자원은 `_routes.json`에서 제외, 저장소 경로는 `canView`, `/view/`·`/`는 로그인만 확인, `/_synctx/*`는 항상 403(관리 API만 `env.ASSETS`로 읽는다), 그 밖의 `/view/*`·`/raw/*`는 403. `/login`·`/auth/*`·`/invite/*`·`/admin`·`/api/admin/*`는 각 함수가 처리한다. 로그인하지 않았으면 302 `/login?next=…`, 권한이 없으면 403 화면(identity 안내, 저장소가 있는지는 말하지 않음), 오류는 503.
- [ ] 통과한 HTML 응답은 `HTMLRewriter`로 `[data-repo-id]` 중 `visibleRepos`에 없는 요소를 지운다. HTML이 아니면 그대로 둔다.
- [ ] 설계 4절의 공통 헤더 적용 함수를 middleware에 둔다. `_headers`는 Functions 생성 응답에 적용되지 않으므로, `services/viewer/src/static_export/build.py`의 `_headers_file()`과 같은 `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `X-Robots-Tag: noindex`, `Cache-Control: no-store`를 직접 설정한다. 기본 CSP는 같은 파일의 `CSP`를 기준으로 한다.
  - 각 함수의 로그인·인증·초대·관리 HTML/API, middleware의 조기 반환(302·403), 예외를 503으로 바꾼 응답과 `next()`·`HTMLRewriter` 결과 모두 최종 반환 전에 적용한다. 응답을 복사할 때 상태·본문·`Location`·각 `Set-Cookie`를 보존한다.
  - 로그인 HTML에는 A3의 `LOGIN_CSP`, 허용되어 실제 raw SVG를 반환하는 응답에는 `services/viewer/src/app.py`의 `RAW_SVG_CSP`를 설정한다. 나머지는 기본 CSP이며 SVG 경로의 로그인 리다이렉트·403·503도 이에 포함한다. CSP는 덧붙이지 않고 한 정책만 설정한다. `/view/static/*`은 기존 `_headers`를 유지한다.
- [ ] `_routes.json`: `include: ["/*"]`, `exclude: ["/view/static/*"]`.
- [ ] `test/middleware.test.ts`에서 공통 헤더와 CSP를 확인한다: 로그인·인증·초대·관리 HTML/API, 302·403·503, 허용된 정적 HTML/JSON, `HTMLRewriter` 결과. 기본·로그인·허용된 raw SVG의 CSP가 각각 하나만 있고, SVG 경로의 거절·오류는 기본 CSP이며, 모든 응답에 `no-store` 등 공통 헤더가 있는지 확인한다. `Location`·각 `Set-Cookie`·상태·본문도 보존되는지 확인한다. A5 응답을 추가할 때 이 시험표를 완성한다.
- [ ] 시험: 판정표 전 경로(로그인 없음·열람자·담당자·다른 저장소·전체 관리자), `/login`은 세션 없이 통과, 인코딩된 경로(`/view/%2E%2E/`, 대소문자), 필터가 다른 저장소 카드·옵션을 지우고 자기 것은 남긴다, D1 오류 시 503.
- [ ] 검증: `npx vitest run`. 로컬 `npx wrangler pages dev site`에서 세션 없이 `/view/study-notes/`를 열어 302 `Location`을 따라가면 로그인 화면이 200인지 본다(실제 파일 라우팅 확인, 다시 302거나 404면 실패).

### 작업 A5: 사람 관리 API와 화면

**파일:** `functions/api/admin/[[path]].ts`, `src/admin/members.ts`, `src/admin/invites.ts`, `src/admin/audit.ts`, `functions/admin/[[path]].ts`(HTML 화면), `functions/invite/[token].ts`, `client/admin.ts` → `site/view/static/synctx-auth/admin.js`, 시험 `test/members.test.ts`, `test/invites.test.ts`.

- [ ] API(JSON, 쓰기는 `Origin` 검사):
  - `GET /api/admin/me` → 역할, 담당 저장소
  - `GET|POST|DELETE /api/admin/repos/:id/members` → 담당자·전체 관리자만. 마지막 담당자는 지우지 못한다(전체 관리자는 예외)
  - `POST /api/admin/repos/:id/invites` → 역할(viewer·manager), 1회용, 7일
  - `GET|POST|DELETE /api/admin/admins` → 전체 관리자만
  - `POST /api/admin/admin-invites` → 전체 관리자만
  - `GET /api/admin/audit` → 전체 관리자만
- [ ] 저장소 목록은 `/view/api/repos/` 아래 산출물이 아니라 빌드가 남기는 `site/_synctx/repos.json`(id·이름만)을 읽는다. 이 파일은 middleware가 막는다(관리 API만 `env.ASSETS.fetch`로 읽는다). `static-export.yaml`에 없는 `repo_id`로 쓰기 요청이 오면 404.
- [ ] 초대: 토큰 32바이트 무작위, D1에는 SHA-256만 저장한다. `/invite/<token>`은 로그인 뒤 수락 버튼을 보여 주고, POST로 수락하면 `used_*`를 채우고 역할을 넣는다. 만료·사용됨이면 안내만 한다.
- [ ] 모든 쓰기를 `audit_log`에 남긴다. 관리 화면을 열 때 만료 초대와 `purgeSessions`를 한 번 돌린다.
- [ ] 동시 요청에서도 설계 3.1·3.2절의 규칙을 지킨다. 권한 변경·초대 소비와 그 성공을 나타내는 `audit_log` 삽입은 같은 D1 `batch()` 트랜잭션에 넣는다. 어느 SQL이 실패해도 전부 롤백한다. 사전 SELECT 결과만 보고 별도 쓰기를 하지 않는다. D1의 자동 커밋과 batch 롤백 보장은 [공식 문서](https://developers.cloudflare.com/d1/worker-api/d1-database/#batch)를 따른다.
  - 초대 소비는 `token_hash` 일치, `used_at IS NULL AND used_by IS NULL`, `expires_at > 수락 시각`을 WHERE에 둔 조건부 UPDATE로 한다. 같은 batch의 역할 반영과 감사 삽입도 이 요청의 소비 성공에만 이어지도록 SQL 조건으로 묶는다(예: 직전 변경 건수를 `changes()`로 전달). 변경 0건은 SQL 오류가 아니므로 batch에 넣는 것만으로는 부족하다. 기존 `used_by`가 같은 identity라는 이유로 재수락을 성공시키지 않는다. 소비하지 못한 요청은 역할·성공 감사 기록을 남기지 않고 만료·사용됨 안내를 한다.
  - 담당자 삭제 또는 viewer로 변경하는 모든 경로(POST의 기존 역할 덮어쓰기·초대 수락 포함)는 변경 SQL 자체에서 대상 역할과 같은 저장소의 다른 담당자 존재를 검사한다. 전체 관리자가 아니면 마지막 담당자를 없애는 변경은 0건이어야 한다. 관리 API에서는 요청자의 담당자 권한도 변경 SQL에서 다시 확인한다. 초대 수락으로 마지막 담당자가 강등될 수 있다면 소비 UPDATE에도 같은 보호 조건을 넣어, 역할 반영이 거절되는 초대를 소비하지 않는다. 감사 삽입은 실제 변경에만 이어지도록 같은 batch에서 조건을 건다. 전체 관리자 예외는 유지한다.
- [ ] 화면: 내 저장소 목록 → 저장소별 열람자·담당자 표(추가·삭제·초대 링크 복사), 전체 관리자에게만 관리자·변경 기록 탭. 서버 HTML과 작은 JS만 쓰고 Viewer `viewer.css`를 재사용한다.
- [ ] 시험: 역할별 허용·거절표, 마지막 담당자 규칙, 초대 1회용·만료·관리자 초대 권한, 모르는 `repo_id` 404, audit 기록.
  - 설계 9절처럼 로컬 D1(miniflare)을 사용한다. 같은 초대를 서로 다른 identity 및 같은 identity로 동시에 POST하면 각각 성공이 정확히 하나이고, 소비·역할 반영·성공 감사 기록도 한 번뿐인지 확인한다.
  - 담당자가 둘인 저장소에서 전체 관리자가 아닌 두 담당자의 삭제·강등 요청을 동시에 보낸다(삭제/삭제, 강등/강등, 삭제/강등). 담당자가 최소 한 명 남고 거절된 변경의 성공 감사 기록이 없는지 확인한다. 마지막 담당자에 대한 POST 역할 덮어쓰기와 viewer 초대 수락도 보호하며, 전체 관리자 예외는 별도로 확인한다.
  - 역할 반영 또는 감사 삽입 SQL을 실패시키면 초대의 `used_*`·역할·감사 기록이 모두 요청 전 상태로 롤백되는지 확인한다. 담당자 변경 뒤 감사 삽입 실패도 같은 기준으로 확인한다.
- [ ] A4의 `test/middleware.test.ts` 헤더 시험표에 초대·관리 HTML/API 응답을 추가해 공통 헤더·CSP와 상태·본문·`Location`·각 `Set-Cookie` 보존 검증을 완성한다.
- [ ] 수동 검증 준비: A3에서 만든 `site/_synctx/repos.json`과 A4의 `site/_routes.json`이 있는지 확인한다. `deploy/pages`에서 `npx esbuild client/admin.ts --bundle --outfile=site/view/static/synctx-auth/admin.js`로 관리 JS를 만든다. 설정이나 Viewer 산출물이 바뀌었으면 A3의 빌드·복사·로그인 JS 생성 명령을 다시 실행한다.
- [ ] 검증: `npx vitest run`, `npx wrangler pages dev site`로 초대 링크를 만들어 다른 공급자 계정으로 수락하는 흐름을 손으로 한 번 확인한다.

### 작업 A6: 빌드·배포 연결과 문서

**파일:** `.github/workflows/static-export.yml`(deploy job), `services/viewer/src/static_export/access_check.py`, `services/viewer/tests/test_static_export_access_check.py`, `scripts/static-export/check-access.sh`(인자 전달 변경이 필요하면), `services/viewer/tests/test_static_export_workflow.py`, `docs/install.md`, `docs/architecture.md`, `docs/development.md`, `docs/production-service-design.md`.

- [ ] 배포 모드를 저장소 변수 둘로 고른다. `STATIC_EXPORT_PUBLIC == 'true'`이면 공개, `STATIC_EXPORT_LOGIN == 'true'`이면 로그인, 둘 다 아니면 지금의 Access 모드다. 둘 다 true이면 build job 첫 단계에서 실패시킨다. 변수는 다음 배포에 올릴 구성을 고를 뿐이고, 이미 배포된 사이트를 바꾸지 않는다.
  - 공개·Access 모드는 지금처럼 저장소 루트에서 `pages deploy dist --project-name=… --branch=main`을 실행한다. `deploy/pages`의 Functions·JS·`_routes.json`을 빌드하거나 복사하지 않는다.
  - 로그인 모드 단계(아래 Node 준비부터 배포 후 점검까지)는 모두 `vars.STATIC_EXPORT_LOGIN == 'true'`일 때만 돌고, 루트 `dist` 배포 단계에는 반대 조건을 둔다. 두 배포가 함께 돌지 않는다.
  - 자리표시 페이지를 올리는 `preflight` job은 Access 모드 전용이다. 조건을 `inputs.first_deploy && vars.STATIC_EXPORT_PUBLIC != 'true' && vars.STATIC_EXPORT_LOGIN != 'true'`로 바꿔 로그인 모드에서는 돌지 않게 한다(돌면 Functions 없는 자리표시 페이지가 운영 배포를 덮고 Access 점검에서 실패한다).
- [ ] 로그인 모드 deploy job:
  - `permissions: { contents: read, packages: read }`
  - `actions/setup-node@<고정 태그>`로 준비하고, `deploy/pages`에서 `npm ci`를 돌린다. `NODE_AUTH_TOKEN`은 이 단계에만 `secrets.GITHUB_TOKEN`으로 준다.
  - `dist/`를 `deploy/pages/site/`로 복사하고, esbuild로 로그인·관리 JS를 만들고, `_routes.json`을 둔다.
  - `wrangler pages deploy site --project-name=… --branch=main`을 `deploy/pages`에서 실행한다.
  - 배포 후 점검은 `check-access.sh --mode login`으로 부른다. 세션 없이 문서 주소를 요청해 같은 출처의 `/login?next=…`로 302되는지 확인한다. 상대 Location은 요청 URL을 기준으로 해석하고, 절대 Location도 출처와 `/login` 경로를 확인한다. 다른 출처나 다른 경로로의 302는 통과시키지 않는다. `STATIC_EXPORT_CHECK_URLS`에 `/view/study-notes/`를 둔다.
- [ ] `check-access.sh`는 `--mode access|login`(기본 `access`)을 `access_check.py`에 넘긴다. `judge_access_response`와 `main`에 로그인 모드 판정을 연결한다. 기존 Access 모드의 302·303 로그인 리다이렉트 및 401·403 판정, 문서 본문 노출·오류·네트워크 실패의 거절은 유지한다.
- [ ] `test_static_export_access_check.py`에 로그인 모드의 상대·같은 출처 절대 `/login` 302 통과, 다른 출처·다른 경로 거절, 본문 노출·오류 거절과 `--mode` 연결 시험을 추가한다. 기존 Access 시험은 유지한다.
- [ ] D1 마이그레이션은 워크플로에서 하지 않는다. 지금 Cloudflare 토큰은 Pages 권한뿐이다. 사용자가 로컬에서 `npx wrangler d1 migrations apply synctx-pages --remote`를 실행한다(문서에 적는다).
- [ ] 워크플로 시험(`test_static_export_workflow.py`): `packages: read` 외 권한 없음, `NODE_AUTH_TOKEN`이 로그인 모드 설치 단계에만 있음, 로그인 배포는 `deploy/pages`에서 `site`를 올리고 공개·Access 배포는 루트에서 `dist`만 올림, 양쪽 모두 `--branch=main`, 두 배포 조건이 서로 배타적임, preflight 조건에 로그인 모드 제외가 있음, 로그인 배포 후 점검이 `--mode login`임, 기존 시크릿 규칙 유지. 기존 `test_access_check_is_skipped_only_for_explicit_public_mode`는 새 조건에 맞게 기대를 바꾼다.
- [ ] 문서:
  - `install.md`에 「로그인 모드」 절을 쓴다. 준비물(D1, Firebase·카카오·네이버 리다이렉트 `https://synctx.sch0718.dev/auth/callback`, Pages 시크릿 목록), 첫 배포 순서(이전 공개 배포 삭제 포함), 공개 모드로 되돌리는 순서를 담는다: `static-export.yaml`의 include/exclude가 공개해도 되는 범위인지 확인 → `STATIC_EXPORT_LOGIN`을 지우고 `STATIC_EXPORT_PUBLIC=true` 설정 → 워크플로를 다시 실행해 Functions 없는 공개 산출물을 배포 → 세션 없는 브라우저에서 문서가 로그인 리다이렉트 없이 열리는지 확인. 변수만 바꿔서는 기존 배포가 바뀌지 않는다. A7에서 이전 공개 배포를 지우므로 옛 배포로 롤백하지 않고 새로 빌드·배포한다. 로그인 모드의 설치·복구 절차에는 Pages 프로젝트의 Settings → Runtime → Fail open / closed에서 `Fail closed`를 저장·재확인하는 단계를 넣는다. 무료 한도 초과 시에는 한도 회복 후 로그인 모드 접근 점검을 다시 하며, 복구를 위해 `Fail open`으로 바꾸지 않는다.
  - `docs/production-service-design.md` 8.8(정적 구성 전용 절)을 갱신한다(CLAUDE.md의 Secret·Role 추가 규칙):
    - 비밀 값 표에 A7의 Pages 시크릿(`OWNER_ACCOUNTS`, `FIREBASE_PROJECT_ID`, `KAKAO_CLIENT_ID`·`KAKAO_CLIENT_SECRET`, `NAVER_CLIENT_ID`·`NAVER_CLIENT_SECRET`)을 저장 위치 「Cloudflare Pages 시크릿」으로 더한다. 비밀이 아닌 `FIREBASE_WEB_CONFIG`는 「Pages 일반 환경변수」로 적는다. 설치 단계의 `NODE_AUTH_TOKEN`은 등록하지 않는 값(워크플로가 `secrets.GITHUB_TOKEN`을 넣음, `packages: read`)으로 적는다. 단계 B의 GitHub App 시크릿은 넣지 않는다.
    - 역할 표를 더한다(설계 3.1: 전체 관리자 `OWNER_ACCOUNTS`, 관리자 D1 `site_roles`, 담당자·열람자 D1 `repo_members`). identity 형식과 마지막 담당자 규칙을 적는다.
    - 운영 절차에 D1 원격 마이그레이션은 사용자가 직접 실행한다는 것, 설계 7절의 오류 동작(D1·인증서 실패 시 503, 세션 만료·철회 시 재로그인, 빌드 실패 시 이전 배포 유지), 전체 관리자가 마지막 담당자를 지워 담당자가 없는 저장소는 전체 관리자가 다시 지정한다는 것을 적는다. 설치 순서는 `install.md`의 「로그인 모드」 절을 가리킨다.
    - 서버 Viewer의 3.4 Secret 표·Roles 표·`.synctx.yaml` 스키마·`authz` 예시는 바꾸지 않는다(설계의 비목적: 정적 사이트에만 적용).
  - `docs/architecture.md` 10.9와 `docs/development.md`의 정적 Viewer 설명에는 로그인 모드(Pages Functions·D1)를 한두 줄로 더하고, `docs/development.md` 프로젝트 구조에 `deploy/pages/`를 넣는다.
- [ ] 검증(각각 저장소 루트에서): `cd services/viewer && uv run pytest -q tests`, `cd deploy/pages && npx vitest run`.

### 작업 A7: 첫 배포(사용자와 함께)

- [ ] 사용자가 한다:
  - D1 `synctx-pages`를 만든다. database_id를 알려 주면 `wrangler.toml`에 넣는다.
  - Firebase 프로젝트를 정한다(설계 10절 1번).
  - 카카오·네이버 앱에 리다이렉트 주소를 등록한다.
  - Pages 시크릿을 넣는다: `OWNER_ACCOUNTS`, `FIREBASE_PROJECT_ID`, `KAKAO_CLIENT_ID`·`KAKAO_CLIENT_SECRET`, `NAVER_CLIENT_ID`·`NAVER_CLIENT_SECRET`. Firebase 웹 앱 설정(`apiKey`·`authDomain`·`projectId`·`appId`)은 비밀이 아니므로 Pages 일반 환경변수 `FIREBASE_WEB_CONFIG`(JSON)로 넣는다.
  - toolkit의 `@sch0718/firebase-session` 패키지 설정 → Manage Actions access에 synctx 저장소를 Read로 등록한다(첫 CI 설치 전에, 이미 했는지 확인).
  - 원격 마이그레이션을 적용한다.
  - 첫 로그인 배포의 필수 조건: Cloudflare 대시보드 Workers & Pages → 해당 Pages 프로젝트 → Settings → Runtime → Fail open / closed에서 `Fail closed`를 선택·저장하고 다시 열어 확인한다. 실제 계정 설정은 아직 미확인이다. 확인하지 못하면 로그인 배포를 진행하지 않는다. 한도 초과 시 동작은 설계 7절을 따른다.
- [ ] 저장소 변수: `STATIC_EXPORT_CHECK_URLS`와 `STATIC_EXPORT_LOGIN=true`를 넣고 `STATIC_EXPORT_PUBLIC`을 지운다(배포 직전에 하고, 그 전까지 사이트는 공개 모드 그대로 둔다).
- [ ] 워크플로를 실행한다.
- [ ] 설계 9절의 수동 확인을 한다:
  - 세션 없이 `/view/study-notes/`, `/raw/study-notes/…/gitflow.png`, `/view/api/repos/study-notes/tree.json`, `/_synctx/repos.json`을 열면 302 또는 403이다.
  - 본인 계정으로는 열린다.
  - 다른 계정은 초대 전에는 403이고 첫 화면에 저장소 이름이 없다. 초대를 수락한 뒤에는 열린다.
- [ ] 이전 공개 배포를 지운다. 새 배포에만 Functions가 붙고, 이전 배포의 고유 주소(`<hash>.<프로젝트>.pages.dev`)는 공개 산출물을 계속 내준다([Cloudflare 문서](https://developers.cloudflare.com/pages/configuration/preview-deployments/#preview-aliases)).
  - 사용자가 Pages 배포 목록에서 로그인 배포보다 앞선 배포를 모두 지운다(지금 CI 토큰으로 지울 수 있는지는 미확인이라 워크플로에서 하지 않는다).
  - 지우기 전에 이전 고유 주소 하나를 적어 두고, 지운 뒤 그 주소의 `/view/study-notes/`가 문서를 내주지 않는지 확인한다. 지워지지 않는 배포가 남으면 사람이 따로 확인한다.
- [ ] 2명 이상에게 초대를 보내 실제로 쓰는지 확인한다. 확인 시점을 기록한다.

---

## 단계 B: 화면에서 저장소 추가(A를 쓴 뒤 따로 계획)

설계 5.2·5.3·6절의 범위다. A 배포 뒤 초대 사용이 확인되면 이 절을 작업 단위로 풀어 쓴다.

- GitHub App 만들기(Contents 읽기·쓰기, Actions 쓰기, Setup URL `/admin/github/setup`, Callback URL `/admin/github/callback`, 「설치 중 사용자 인가 요청」은 끔)
- 설치·저장소 확인: `/admin/github/setup`에서 세션에 묶은 일회용 `state`를 발급하고 `/admin/github/callback`에서 검증한 뒤, 사용자 토큰으로 대상 저장소의 `permissions.admin`을 확인한다(설계 5.2절 3·4). 시험: `state` 없음·불일치·만료·재사용·다른 세션 거절, 설치 없음·저장소 없음·admin 아님 거절
- 저장소 추가·편집·제거 API와 화면(`static-export.yaml` sha 충돌 처리, include 필수)
- `workflow_dispatch`와 실행 상태 표시
- D1 `0004`: `installations` 테이블(설계 3.3절)
- 빌드의 `auth.github_app` 설치 토큰
- 워크플로 시크릿 `GH_APP_ID`·`GH_APP_PRIVATE_KEY`
