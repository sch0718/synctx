# 정적 Viewer 내보내기와 설치 방식 선택 설계

> 상태: 초안(2026-11-05). 승인 전에는 구현하지 않는다.
> 성격: **운영 구성이 아니라 별도 시연·열람용** 기능이다. 폐쇄망 운영 원칙(런타임 외부망 없음, 이미지 태그 SemVer)은 운영 구성에 적용되며, 이 기능은 그 구성에 들어가지 않는다.

## 1. 배경과 목적

Viewer(`services/viewer/src/app.py`)는 FastAPI 서버라 GitHub Pages·Cloudflare Pages 같은 정적 호스팅에 올릴 수 없다. 사용자는 비공개(private) Git 저장소의 Markdown 문서를, 매번 HTML을 따로 만들지 않고 **Viewer 화면 그대로** 편하게 보고 싶어 한다.

이 설계는 두 가지를 다룬다.

1. **정적 Viewer 내보내기(static export)**: 비공개 저장소의 문서를 빌드 때 Viewer 화면으로 구워 Cloudflare Pages에 올리고 Cloudflare Access로 로그인 게이트를 건다.
2. **설치 방식 선택**: 사용자가 「전체 stack」「서비스별 선택」「정적 Viewer」 중 구성을 고르고, 「Kubernetes(Helm)」「Docker Compose」「클라우드 정적」 중 배포 대상을 고를 수 있게 설치 경로와 안내를 한 곳에 정리한다.

## 2. 목표·비목적·반목적

### 목표

- 연결한 저장소의 저장소 목록, 파일 트리, 본문(Mermaid·코드 강조), 목차, 문서 간 링크를 정적 사이트에서 Viewer와 같은 화면으로 본다.
- 저장소를 설정 파일 한 곳에서 추가하고, 빌드 때 일부만 골라 올리고, 화면에서 저장소를 전환한다.
- 비공개 문서가 Access 없이 공개되지 않게 하고, 토큰이 저장소·산출물에 남지 않게 한다.
- 설치 방식 3종(전체·선택·정적)을 대상 3종(K8s·Compose·정적)별로 한 문서에서 고르고 따라 하게 한다.
- 새 코드는 시험으로 덮고, 기존 서비스의 동작·기본값·이미지를 바꾸지 않는다.

### 비목적(이번 범위 밖)

- 검색, 회의 API·Jitsi·발표(`mode=present|meeting`)·증거 미리보기, 웹훅, 다운로드·PDF·Marp 내보내기 같은 **서버 기능**. 정적 화면에서는 해당 진입점을 숨긴다.
- 클라이언트 색인 검색(후속 단계. 6.4절).
- 같은 저장소의 여러 브랜치를 한 사이트에 싣는 것(첫 단계는 저장소당 ref 하나).
- 정적 사이트의 갱신 실시간성(재빌드 때만 갱신).

### 반목적(기술적으로 동작해도 실패로 보는 것)

- Access 없이 비공개 문서가 인터넷에 열리는 배포.
- 읽기 토큰·저장소 URL 자격증명이 산출물, 빌드 로그, 저장소에 남는 것.
- 운영 Helm/Compose 배포의 기본값이나 이미지가 이 기능 때문에 바뀌는 것.
- `team_docs`(DX 지침, 사내 규정 원문) 저장소를 설계·예시·시험 데이터로 쓰는 것.

## 3. 현재 상태(코드 확인 결과)

| 항목 | 확인한 사실 | 근거 |
|------|-------------|------|
| 화면 조립 | 저장소 목록·파일 트리·문서는 `app.py` 라우트가 `viewer_ui.page()`와 `render_markdown()`으로 서버에서 조립한다 | `app.py` `view_index`·`view_repo`·`_render_file_response` |
| 링크 | 문서 안 상대 링크를 `/view/{repo}/{path}?ref=` 형태로 다시 쓴다 | `renderer.rewrite_links` |
| 트리 | 처음에 한 단계만 그리고 폴더를 열 때 `/api/repos/{id}/tree?path=`를 호출한다. 필터용으로 `/api/repos/{id}/files`도 호출한다 | `static/viewer.js` |
| 전환 | 저장소·브랜치 선택은 `VIEW_PREFIX/{repo}` 또는 `?ref=`로 이동한다 | `viewer.js` `initSelects` |
| 테마 | 문서 CSS가 요청마다 서버에서 정해진다. 테마 7종 | `renderer.THEME_CSS` |
| 보안 헤더 | CSP·`X-Frame-Options`는 FastAPI 미들웨어가 응답마다 붙인다 | `app.py` `csp_middleware` |
| 번들 자산 | `asset_source: bundled`일 때 `static/vendor/`(Mermaid·highlight.js) 사본을 쓴다. 외부 CDN 불필요 | `standalone.py`, `_render_file_response` |
| 단독 실행 | `standalone.py`가 `.synctx.yaml`을 생성하고 Viewer를 mount한다. 토큰은 `auth.credential_env: GIT_READ_TOKEN`으로만 받는다 | `standalone.py`, `synctx-sample.yaml` |
| 도움말 생성 | `scripts/build_motion.py`는 Viewer 테마 색을 읽어 SVG·Markdown을 **저장소에 커밋하는 파일**로 만든다. 문서 HTML 생성기가 아니다 | `scripts/build_motion.py` |
| md-preview | `example-infra`의 옛 `20. Helm/md-preview/`는 FastAPI(markdown_converter)를 K8s에 올리는 서버 배포였고 2026-10-28 삭제됐다. 정적 내보내기 구현은 없다. 재사용할 것은 `repos.yaml`의 `auth.credential_env`(토큰 이름만 설정에 둠) 규칙인데 synctx가 이미 같다 | example-infra `5ec36e9f9`, 옛 차트 README |
| 설치 경로 | Helm은 서비스별 `<service>.enabled`(viewer·cdn·sync·feedback·jitsi·collabGateway·marp·slides·drawio), Compose는 `SYNCTX_SERVICES`(viewer·cdn·marp·slides·drawio)와 `deploy.sh`, 단일 Viewer는 `run.sh` | `helm/synctx/values.yaml`, `deploy/docker/synctx.env.example`, `scripts/docker/deploy.sh` |

`build_motion.py`는 재사용 대상이 아니다. **재사용 대상은 Viewer 앱 자체**다(4절).

## 4. 결정 1 — 생성 방식: 실제 Viewer를 빌드 때 순회해 저장(사용자 승인: 추천안)

빌드 단계에서 Viewer ASGI 앱을 프로세스 안에서 띄우고(HTTP 포트 없이 ASGI 클라이언트로 호출), 저장소 목록 → 저장소 홈 → 모든 보이는 파일의 페이지와 트리 JSON을 요청해 결과를 파일로 쓴다.

```mermaid
flowchart LR
    A[설정 파일 읽기] --> B[저장소 clone 또는 fetch]
    B --> C[Viewer 앱을 ASGI로 기동]
    C --> D[페이지·트리 JSON 요청]
    D --> E[정적 경로로 후처리]
    E --> F[_headers·_redirects·자산 복사]
    F --> G[산출물 검사]
    G --> H[dist/ 출력]
```

선택 이유: 화면·링크·Mermaid·목차가 지금과 같고, 새 렌더 코드를 쓰지 않으므로 기존 렌더러 변경이 최소다. `app.py` 조립 로직을 복제하는 선택지는 변경이 커서 버렸다.

### 4.1 후처리(정적 경로화)

서버 화면의 동적 요소를 정적 규칙으로 바꾼다. 아래 이름과 규칙은 이 설계의 제안이며 계획 단계에서 구현 가능성을 시험으로 확정한다.

| 서버 동작 | 정적 처리 |
|-----------|-----------|
| `/view/{repo}/{path}` | `view/{repo}/{path}/index.html`로 저장(디렉터리형 URL). `VIEW_PREFIX`(`/view`)는 그대로 둔다 |
| `?ref=` | 저장소당 ref 하나만 싣는다. 링크에서 `?ref=`를 떼고 브랜치 선택은 숨긴다 |
| `/api/repos/{id}/tree?path=` | 빌드 때 폴더마다 `view/api/repos/{id}/tree/{path}.json`을 만든다. 정적 모드 shim이 `fetch` 주소를 이 파일로 바꾼다 |
| `/api/repos/{id}/files` | 같은 방식으로 `files.json` 하나 |
| 저장소·테마 선택 | 저장소 전환은 정적 링크 이동. 테마 선택은 유지하고 4.3절 방식으로 동작 |
| 검색·회의·다운로드·발표 진입점 | 숨김(서버 기능) |
| 보안 헤더(CSP 등) | 미들웨어가 하던 일을 `_headers` 파일로 만든다(Cloudflare Pages 지원) |

**구현 방식(내 판단)**: HTML 문자열 정규식 치환을 기본으로 삼지 않는다.
- `VIEW_PREFIX`는 `app.py`의 모듈 상수 `"/view"`로 고정돼 바꿀 수 없다. 그래서 정적 사이트도 **`/view/` 아래에 둔다**(`dist/view/...`). 사이트 루트 `/`는 `_redirects`로 `/view/`로 보낸다(치환 없음). (구현 계획 작성 중 코드로 확인해 고친 내용)
- `?ref=`는 순회 때 기본 ref만 쓰므로 링크에 붙지 않는다. 기본 ref가 아닌 ref를 쓰는 저장소는 설정의 `ref`로 그 저장소의 `default_ref`를 덮어써서 같은 효과를 낸다.
- 진입점 숨김은 정적 페이지에만 주입하는 `<style>`과 body 클래스(`page-static`)로 한다. HTML을 파싱해 `</head>`·`</body>` 앞에 주입한다.
- 기존 Viewer 코드는 수정하지 않는다. 위 방법으로 안 되는 항목이 나오면 그 항목마다 계획에서 근거를 적고 작은 옵션으로 한정한다.
- 숨김 CSS가 쓰는 선택자가 실제 화면에 있는지를 확인하는 **계약 시험**(렌더된 HTML에서 선택자가 존재하는지)을 둬서 Viewer 쪽 마크업이 바뀌면 시험이 실패하게 한다. 브라우저 선택 실행 시험은 보조다.

정적 모드 shim은 `viewer.js`를 고치지 않고 따로 둔 작은 스크립트로, 정적 사이트 페이지에만 주입한다. 기존 Viewer 화면·테스트는 바꾸지 않는다.

### 4.2 한도 정책(내 판단)

파일 수 20,000개 또는 파일당 25 MiB를 넘으면 **빌드를 실패**시킨다(일부만 올라가면 링크가 깨진 채 공개되므로). 20분 빌드 시간 제한은 빌드 단계별 소요를 보고서에 남겨 초과 위험을 미리 보게 한다. 개별 파일이 지원 확장자가 아니거나 `max_render_size_bytes`를 넘는 경우만 건너뛰고 보고서에 적는다. clone은 기존 `GitRepoManager`와 `credential_env` 방식을 재사용하는 것을 기본으로 하고(토큰은 URL에 넣지 않음), 재사용 불가 사유가 있으면 계획에서 근거를 적는다. 빌드 로그에는 토큰이 들어갈 수 있는 예외 문자열을 그대로 찍지 않는다(`standalone.py`와 같은 규칙).

### 4.3 테마: 기존 7종 모두 포함, 기본은 `corporate`(사용자 요청)

기존 테마 7종(`github-light`·`github-dark`·`cayman`·`minimal`·`hacker`·`modernist`·`corporate`)을 모두 넣고 테마 선택을 유지한다. 서버는 테마를 요청마다 문서 `<style>`에 인라인하므로, 문서마다 7벌을 굽지 않고 **테마 CSS를 외부 파일로 빼는** 방식으로 정적화한다.

- 빌드가 `themes/<이름>.css`(문서 테마 CSS)와 코드 강조 CSS(`vendor/hljs/<이름>.min.css`는 번들 자산에 이미 있음)를 한 벌씩만 출력한다. 산출물 증가는 테마 7개 분량(수십 KB)이다.
- 페이지는 기본 테마(`corporate`)로 한 번만 렌더한다. 정적 모드 shim이 `localStorage`의 선택을 읽어 head에서 테마 `<link>`의 주소를 바꾼다(깜빡임 방지를 위해 본문 렌더 전에 실행).
- Mermaid·코드 강조 테마는 문서 본문 렌더 때 정해지므로, 테마를 바꾸면 페이지를 다시 읽는다(현재 Viewer의 `theme_via_query` 방식과 같은 동작). shim이 테마별 Mermaid 테마(`THEME_MERMAID`)와 hljs 테마(`THEME_HLJS`) 표를 가진다.
- 기본값은 사이트 설정(`static-export.yaml`의 `theme`)으로 바꿀 수 있고 기본은 `corporate`다.

**`corporate` 테마를 참고 양식에 맞춰 갱신(사용자 요청)**

참고 양식의 기준은 `team_docs/tools/html/convert/`의 `corporate.css`(Marp 슬라이드 테마), `convert_to_pdf.py`(A4 표지 디자인 토큰), `convert_md_to_docx.py`(Word 서식)이다. 이 파일들의 **색·글꼴 토큰만** 읽었다(문서 본문은 읽지 않음). 현재 Viewer `CORPORATE_CSS`(`renderer.py`)와 비교한 결과:

| 항목 | 참고 양식 | 현재 Viewer `corporate` | 대비비(흰 배경) |
|------|---------------|----------------------|----------------|
| 강조 빨강 | `#EF3B24` | `#E01E1F` | 3.95 : 1 / 4.8 : 1 |
| 보조 회색 | `#6D6E71` | `#454F5B` 등 | 5.1 : 1 |
| 본문 글자 | `#1A1A1A` | `#1C252E` | 17.4 : 1 |
| 연한 배경 | `#F7F7F7` | `#F9FAFB` | — |
| 코드 글자 빨강(docx) | `#B02A14` | `#1C252E` | 6.59 : 1 |
| 글꼴 | 시스템 한글 글꼴(Apple SD Gothic Neo, Noto Sans KR, Malgun Gothic) | Public Sans, Nanum Gothic | — |
| 장식 | 상단 6px 빨강 띠, 제목 위 빨강 막대, 인용문 좌측 빨강 막대 | 제목 아래 빨강 밑줄, 인용문 좌측 막대 | — |

차이가 있으므로 `corporate` 테마를 갱신한다. 적용 원칙(내 판단):

- 강조 빨강 `#EF3B24`는 띠·막대·밑줄 같은 **장식**에 쓴다.
- **글자로 쓰는 빨강(링크 등)은 `#EF3B24`를 쓰지 않는다.** 흰 배경 대비 3.95:1로 일반 글자 기준(WCAG AA 4.5:1)에 미달하기 때문이다. 링크·코드 글자는 참고 양식 안에 있는 어두운 빨강 `#B02A14`(6.59:1)를 쓴다. 이 선택은 사용자가 확인했다.
- 본문·보조 글자·배경은 참고 양식 값(`#1A1A1A`·`#6D6E71`·`#F7F7F7`)으로 맞춘다.
- 글꼴은 참고 양식의 시스템 한글 글꼴 목록으로 맞춘다. 외부 글꼴 파일을 받지 않는다(폐쇄망·번들 원칙).
- 기존 `test_theme_contrast.py`가 `corporate`를 이미 검사하는지 확인하고, 갱신 후에도 통과해야 한다. 토큰이 바뀌므로 합격 기준을 낮추지 않는다.

이 갱신은 정적 내보내기뿐 아니라 **운영 Viewer와 HTML 내보내기의 `corporate` 모양도 바꾼다.** 그래서 정적 기능 구현과 분리된 단계(10절 2단계)로 두고, `renderer.py`의 해당 상수만 수정한다.

### 4.4 제외 처리

- 회의 front matter(`synctx.meeting`) 문서도 **일반 문서로** 렌더한다. 회의 모드 버튼은 숨긴다.
- `synctx-html` 블록(Phase 9)은 `/render/{token}/…` 서버 경로가 필요하므로 정적 사이트에서 렌더하지 않고 안내 문구로 대체한다.
- 지원하지 않는 확장자·크기 초과(`max_render_size_bytes`) 파일은 건너뛰고 빌드 보고서에 적는다.

## 5. 결정 2 — 저장소 추가·선택(사용자 승인: 설정 파일 + 부분 선택 + 화면 전환)

- **설정 파일 한 곳**: 전용 파일 `static-export.yaml`을 둔다(내 판단). `repositories[]` 항목은 `.synctx.yaml`과 같은 필드(id·name·url·default_ref·auth.credential_env·watch_paths·ignore_paths)를 쓰고, 사이트 제목·기본 저장소·저장소별 포함·제외 경로를 더한다. 운영용 `.synctx.yaml` 모델·Helm·ConfigMap·`production-service-design.md` 스키마 절은 바꾸지 않는다. 이 파일은 비밀 값을 담지 않으므로 저장소에 커밋한다.
- **숨김 경로 기본 제외**: 경로의 어느 구간이든 `.`으로 시작하면(`.claude/`·`.github/`·`.hidden.md`) 기본으로 내보내지 않는다. Viewer 파일 API는 숨김 파일을 거르지 않으므로(`file_paths()`) 설정 없이는 비공개 저장소의 내부 노트가 올라갈 수 있어서다. `include`가 그 숨김 구간까지 적은 경우에만 허용하고(`include: ["docs/"]`는 `docs/.draft/`를 켜지 않는다), `exclude`가 항상 우선한다. 같은 허용 목록이 페이지·트리·`files.json`·그림·회의 근거 발췌에 적용된다.
- **빌드 때 부분 선택**: 빌드 명령에 저장소 id 목록을 넘기면 그 저장소만 구워 올린다(예: 환경변수 `STATIC_EXPORT_REPOS=a,b`). 비우면 설정 파일의 전체.
- **화면 전환**: 사이트 헤더의 저장소 선택 목록은 이번 빌드에 포함된 저장소로 구성한다.
- 저장소 추가 = 설정 파일 PR 한 번 + 토큰 환경변수 하나 등록.

## 6. 결정 3 — 빌드·배포·접근 제한

### 6.1 빌드 위치(사용자 결정: GitHub Actions 빌드 + Cloudflare Pages Direct Upload)

GitHub Actions가 빌드하고 `wrangler pages deploy`(`cloudflare/wrangler-action`)로 올린다. 구조는 gazua `.github/workflows/deploy.yml`(빌드 job과 배포 job 분리, `concurrency`, 최소 `permissions`)을 따르되 Pages 배포만 Cloudflare로 바꾼다. 워크플로 파일은 이 저장소 `.github/workflows/static-export.yml`(신규, 현재 `.github`는 없다)에 둔다.

| 항목 | 내용 | 상태 |
|------|------|------|
| 트리거 | `workflow_dispatch`(저장소 id 목록 입력으로 부분 선택)와 선택적 `schedule`. 문서 저장소가 바뀐 것을 자동으로 알 방법은 없으므로 갱신은 수동 실행 또는 일정 실행 | 내 판단 |
| 배포 전 게이트 | 빌드 job에서 산출물 검사(6.2·6.3절)가 실패하면 배포 job이 실행되지 않는다. 필요하면 `environment` 승인(작성자와 다른 사람 승인)을 둘 수 있다 | 내 판단 |
| 프로젝트 종류 | Direct Upload 프로젝트. Git 연동 프로젝트로 바꿀 수 없다. 프로젝트는 미리 만들어 둔다(`wrangler-action` 예시는 기존 프로젝트를 가정) | 문서 확인 |
| 필요한 시크릿 | `CLOUDFLARE_API_TOKEN`(Account → Cloudflare Pages → Edit 권한), `CLOUDFLARE_ACCOUNT_ID`, 저장소 읽기 토큰 | 문서 확인 |
| 러너 | `ubuntu-latest`에서 uv로 빌드(Cloudflare 빌드 이미지의 uv 문제가 사라진다) | 내 판단 |
| 한도(Free) | 사이트당 파일 20,000개·파일당 25 MiB(Wrangler 업로드 한도). 월 500회 빌드 한도는 Git 연동 빌드에 해당하며 Direct Upload에는 해당하지 않는 것으로 보이나 **미확인** | 문서 확인 |

`GITHUB_TOKEN`으로는 **다른 비공개 저장소를 읽을 수 없다**. 읽기 토큰은 6.2절의 별도 시크릿이다.

### 6.2 비공개 저장소 인증
- 읽기 토큰(대상 저장소 Contents 읽기 전용 fine-grained PAT 권장)을 **GitHub Actions 저장소 Secrets**에 두고 워크플로에서 `GIT_READ_TOKEN` 환경변수로 넘긴다. 설정 파일에는 이름만 있다(`auth.credential_env`). 시크릿은 포크·외부 PR 워크플로에 전달되지 않도록 `pull_request`가 아니라 `workflow_dispatch`·`schedule`에서만 쓴다.
- 토큰은 로그에 찍지 않는다. clone은 기존 방식(자격증명을 URL에 넣지 않음)으로 한다.
- 산출물 검사는 토큰 값 문자열과 `ghp_`·`github_pat_`·`Authorization` 같은 패턴, 그리고 `.git` 디렉터리가 `dist/`에 없는지 확인한다.
- 토큰은 최소 권한·만료일을 두고 정기 교체한다. 저장소 id별로 토큰을 분리할지(저장소마다 다른 환경변수 이름)는 설정 파일의 `credential_env`로 지원한다.

### 6.3 Access와 노출 방지(핵심)

Cloudflare 문서로 확인한 사실과 대응:

- Pages의 「Enable access policy」는 **미리보기 배포만** 보호한다. 운영 `*.pages.dev`와 커스텀 도메인은 보호하지 않는다.
- 운영 `*.pages.dev`는 Access 앱의 서브도메인 와일드카드를 지우는 절차(Known issues 문서)로 별도 보호해야 하고, 커스텀 도메인은 Zero Trust에서 Self-hosted 앱을 따로 만든다. 커스텀 도메인은 Access가 걸리기 전에 연결해야 한다는 제약이 있다.
- 브랜치 별칭(`<branch>.<project>.pages.dev`) 보호 여부는 문서에 없어 **미확인**이다.

대응(방어 3겹):

1. **미리보기 배포 만들지 않기**: 워크플로가 항상 운영 브랜치(`--branch=<production branch>`)로만 배포하게 고정한다. 그래도 Direct Upload는 다른 브랜치 이름으로 올리면 미리보기 URL이 생기므로, 미리보기 URL(`*.<project>.pages.dev`)에도 Access를 건다. 브랜치 별칭 보호 여부는 **미확인**.
2. **Access 앱**: 운영 `*.pages.dev`, 미리보기(`*.<project>.pages.dev`), 커스텀 도메인 **각각**에 Access 앱을 둔다. 허용 대상은 본인 이메일 등 명시 목록.
3. **Access 점검 스크립트(`check-access.sh`)**: 세션 없이 요청했을 때 문서 URL이 200이 아니라 Access 로그인으로 가는지(302 또는 Access 응답) 확인한다. 주소를 공유하지 않는 것만으로는 업로드된 문서가 막히지 않으므로, 점검을 배포 순서에 묶는다.
   - **사전 점검**: 첫 배포에서는 비밀이 없는 자리표시 페이지만 먼저 올려 보호 대상 호스트(운영 `*.pages.dev`·미리보기·커스텀 도메인)에 점검을 돌린다. 통과해야 비공개 문서 산출물을 배포한다.
   - **배포 후 점검**: 워크플로의 배포 job 뒤에 점검 단계를 필수로 연결한다(스크립트가 실패하면 워크플로도 실패).
   - **실패 시 차단(사용자 결정: 수동)**: 점검이 실패하면 워크플로가 실패한다. 이미 올라간 배포는 자동으로 제거하지 않고, 사람이 11절 순서(사이트를 먼저 내리고 Access 설정을 고친 뒤 다시 점검)로 막는다. Pages 배포 삭제가 `Cloudflare Pages: Edit` 토큰 권한으로 가능한지는 **미확인**이라 자동 제거는 넣지 않는다. 통과 전에는 URL을 공유하지 않는다는 절차도 함께 둔다.
   - 워크플로에 이 단계를 넣는 일은 계획 문서의 Task 11에서 반영한다. 실제 Cloudflare 계정의 정책 상태는 **미확인**이다.
4. 산출물에 `robots.txt`(전체 Disallow)와 `X-Robots-Tag: noindex`, `Cache-Control`을 `_headers`로 둔다.

Access 무료 조건: Cloudflare 요금제 페이지는 「50명 미만 팀」 무료를 안내하는 것으로 보이나, 공식 문서 확인이 **미확인**이다(검색 결과는 제3자 정리). 계획 단계 첫 작업으로 공식 페이지에서 확인한다.

### 6.4 후속 선택지 기록: 검색

클라이언트 색인 검색은 이번에 제외한다. 후속 설계 때 확인할 것: 한글 토큰화 품질, 색인 크기(파일당 25 MiB, 20,000개 한도), 색인에 비공개 본문이 그대로 들어가는 점, 색인도 Access 뒤에 있어야 하는 점.

## 7. 결정 4 — 설치 방식 선택 체계(사용자 추가 요구)

사용자가 구성을 고르고 배포 대상을 고를 수 있게 한다. 기존 설치 경로는 이미 서비스별 켜기·끄기를 지원하므로 **새 설치 도구를 만들지 않고, 선택 기준과 사전 정의 구성을 문서와 프리셋 파일로 제공**한다.

### 7.1 구성(무엇을 설치하나)

| 구성 | 포함 | 용도 |
|------|------|------|
| 전체 stack | Viewer, CDN, Marp, Slides, Draw.io, Sync·Feedback(선택), Collab Gateway·Jitsi(선택) | 사내 운영 |
| 서비스별 선택 | 위 목록에서 필요한 것만 | 부분 도입 |
| 정적 Viewer | 정적 사이트(읽기 전용 Viewer 화면) | 비공개 저장소 열람·시연 |

### 7.2 대상(어디에 설치하나)과 지원 범위

| | 전체 stack | 서비스별 선택 | 정적 Viewer |
|---|---|---|---|
| Kubernetes(Helm) | 지원(`helm/synctx`, values 기본) | 지원(`<service>.enabled`) | 해당 없음 |
| Docker Compose | 「Compose 지원 서비스 전체」만 지원(`SYNCTX_SERVICES`의 viewer·cdn·marp·slides·drawio, Jitsi는 별도 옵션 `SYNCTX_JITSI`). Sync·Feedback·Collab Gateway는 Helm만 | 지원(`SYNCTX_SERVICES`) | 해당 없음 |
| 클라우드 정적(Cloudflare Pages) | 해당 없음(서버 기능 필요) | 해당 없음 | **신규**(이 설계) |
| 단일 Viewer 로컬 | — | `run.sh`(Viewer만) | — |

Compose가 지원하는 서비스 이름(viewer·cdn·marp·slides·drawio)과 Helm이 지원하는 서비스 이름(sync·feedback·jitsi·collabGateway 포함)이 다르므로, Compose에서 Sync·Feedback·Collab Gateway는 지원하지 않는다는 점을 표에 명시한다. 「전체 stack」은 대상별로 의미가 다르다. Helm의 전체는 `helm/synctx` 서비스 전부이고, Compose의 전체는 위의 지원 서비스 전부다. 설치 문서와 프리셋 이름에 이 차이를 그대로 적는다(Compose 프리셋 이름은 `full`이 아니라 지원 범위를 드러내는 이름을 쓰고, Helm과 같은 이름을 쓰면 설명에서 구분한다).

Compose의 Jitsi는 `SYNCTX_SERVICES`와 별개로 `SYNCTX_JITSI=true`로 켜며, `scripts/docker/deploy.sh`의 검사가 요구하는 설정을 프리셋 안내에 적는다: viewer 필수, 고정 `stable-<번호>` 이미지 태그, `SYNCTX_JITSI_HOST`와 `https://<호스트>` 형식의 공개 주소, TLS 인증서·키, `SYNCTX_JITSI_ADVERTISE_IPS`, XMPP 암호(`secrets.env`)와 JWT 비밀. 프리셋 기본값은 Jitsi를 끈다(기본 꺼짐과 같다). 정확한 현재 지원 범위는 계획 단계 첫 작업으로 `compose.yaml`과 `values.yaml`을 읽어 표를 확정한다.

### 7.3 제공물

1. `docs/install.md`(신규): 「무엇을 어디에 설치할까」 결정 표, 대상별 명령, 구성별 기능 가용 표(서버 기능은 정적에서 안 됨), 정적 Viewer 설치·Access·토큰 절차, 롤백. README 「시작하기」에서 링크한다.
2. 사전 정의 구성(프리셋, 제안 이름): 문서만 보기(viewer+cdn), 발표 포함(+marp+slides), 전체. Helm은 `helm/synctx/values-profile-*.yaml`, Compose는 `deploy/docker/profiles/*.env`. 기본 `values.yaml`·`synctx.env.example`은 바꾸지 않는다.
3. 정적 Viewer 설치: `scripts/static-export/`(신규) 아래 빌드·검사 스크립트와 Cloudflare Pages 설정 안내.

### 7.4 지킬 것

- 이미지 태그 `latest` 금지·SemVer 규칙은 K8s·Compose 구성에 그대로 둔다.
- 정적 Viewer는 컨테이너 이미지를 만들지 않는다(이미지 태그 규칙 대상 아님).
- Compose 직접 `docker compose up` 금지 같은 기존 규칙은 변한 것이 없다.

## 8. 코드 배치(제안)

- 빌드 도구: `services/viewer/src/static_export/`(새 패키지). 기존 `app.py`·`renderer.py`는 수정하지 않는 것을 목표로 한다. 정적 모드 판별이 어쩔 수 없이 필요하면 작은 플래그로 한정하고 계획에서 근거를 남긴다.
- 정적 모드 shim: 새 JS 파일 하나(`viewer.js` 수정 없음).
- 진입 스크립트: `scripts/static-export/build.sh`(빌드 명령), `scripts/static-export/verify-dist.py`(산출물 검사), `scripts/static-export/check-access.sh`(배포 후 공개 응답 점검).
- 시험: `services/viewer/tests/test_static_export*.py`. 임시 Git 저장소를 만들어 쓰는 기존 하네스를 재사용한다.

## 9. 시험 전략(새 기능은 시험 필수)

- 단위: 경로 변환(`?ref=`·상대 링크·한글·공백·`#앵커`), 트리 JSON 생성, 제외 규칙, 설정 검증(저장소 id 형식, 중복).
- 통합: 임시 Git 저장소(비밀 값 없는 가상 문서만)로 빌드해 `dist/`에서 페이지·링크·Mermaid 스크립트 참조·트리 JSON이 서로 맞는지 정적 파일만으로 확인한다. 깨진 링크 검사기.
- 산출물 검사: 토큰 패턴·`.git`·원본 경로 노출 시 빌드가 실패하는지.
- 브라우저: 기존 Chromium 선택 실행 하네스(`tests/browser`)로 폴더 펼치기·저장소 전환·Mermaid 렌더를 정적 파일 서버에서 확인(선택 실행).
- 테마: 7종 CSS 파일이 모두 출력되는지, 기본이 `corporate`인지, 각 테마의 글자·배경 대비가 기준을 넘는지, 테마 전환 shim의 선택값→CSS·Mermaid·hljs 매핑이 `THEME_*` 표와 일치하는지.
- 회귀: 기존 Viewer 테스트 전체가 그대로 통과하고 `scripts/tests/test_motion.py`가 영향받지 않는다.
- 시험 데이터에 `team_docs`·사내 규정 원문을 쓰지 않는다.

## 10. 단계 분해

단계 게이트(내 판단): 2~3단계(내보내기 코어·화면)는 Cloudflare 확인 결과와 무관하므로 1단계와 병행해 먼저 계획·구현한다. 4단계(배포 경로)는 1단계 결과가 나온 뒤에 확정한다. 첫 배포는 반드시 **Access 앱 → 프로젝트·도메인 연결 → 배포 후 점검(`check-access.sh`) 통과 → 주소 공유** 순서로 하고, 점검 통과 전에 URL을 공유하지 않는다는 절차를 설치 문서에 필수로 둔다. Access 설정 자체는 자동 시험이 불가능하므로 점검 스크립트의 판정 로직(응답 코드·리다이렉트 판별)만 가짜 응답으로 시험한다.

1. 확인 단계(코드 변경 없음): Access 무료 조건·미리보기 배포 비활성화 가능 여부·Direct Upload의 월 빌드 한도 적용 여부를 확인한다.
2. `corporate` 테마를 참고 양식에 맞춰 갱신(독립 단계. 운영 Viewer 모양도 바뀌므로 별도 커밋·검토). 대비 시험 통과 확인.
3. 내보내기 코어: 설정 스키마, ASGI 순회, 후처리, `dist/` 출력, 산출물 검사.
4. 정적 모드 화면(테마 CSS 외부화·테마 전환 shim 포함): 트리 JSON·shim·진입점 숨김·`_headers`.
5. 배포 경로: 빌드 스크립트, GitHub Actions 워크플로, Cloudflare Pages(Direct Upload) 설정 안내, Access 설정 안내, 배포 후 점검 스크립트.
6. 설치 체계: `docs/install.md`, 프리셋 파일, README·관련 문서 갱신. 프리셋은 Helm·Compose 양쪽에 같은 3종(문서만·발표 포함·전체)을 두고, 시험은 `helm template` 렌더에서 켜진 서비스 확인, Compose는 옵션 파일 파싱·`deploy.sh --check`로 확인하며, 기본 `values.yaml`·`synctx.env.example`이 바뀌지 않았음을 diff로 확인한다.
7. 후속(별도): 클라이언트 검색, 다중 ref.

## 11. 롤백

- 사이트: Cloudflare Pages 배포 목록에서 이전 배포로 되돌리거나 프로젝트를 삭제한다. Access 앱을 지우면 인증 없이 열릴 수 있으므로 **사이트를 먼저 내리고** Access를 나중에 지운다.
- 코드: 변경은 두 종류로 나뉘며 별도 커밋으로 둔다. (1) 정적 기능(새 패키지·스크립트·워크플로·문서)은 되돌려도 기존 서비스에 영향이 없다. (2) `corporate` 테마 갱신(`renderer.py`의 `CORPORATE_CSS`·`THEME_VARS_CSS`)은 운영 Viewer와 HTML 내보내기의 `corporate` 모양을 바꾸므로, 되돌리면 그 모양이 이전(`#E01E1F`·Public Sans)으로 돌아간다. 두 변경은 서로 독립이라 하나만 되돌릴 수 있다.
- 토큰: 노출이 의심되면 즉시 폐기·재발급한다.

## 12. 갱신할 문서(3개 문서 일관성 규칙)

이 기능은 서비스·Secret·Role·Phase를 추가하지 않는 시연용 도구다. 일관성 규칙은 다음과 같이 적용한다.

| 문서 | 갱신 |
|------|------|
| `README.md` | 「시작하기」에 설치 방식 선택 링크, 정적 열람 한 줄(개발자용 내용은 두지 않음) |
| `docs/install.md` | 신규 |
| `docs/development.md` | 배포 방식 절에 정적 Viewer 항목, 프로젝트 구조에 새 경로 |
| `docs/architecture.md` | 정적 내보내기 설계 요약과 운영 구성이 아님을 명시, 이 설계 링크 |
| `docs/production-service-design.md` | 비밀 값(`GIT_READ_TOKEN`) 취급과 Access 점검을 시연용 구성으로 분리해 기록. 운영 체크리스트·Phase 번호는 바꾸지 않는다 |
| `docs/local-development.md` | 로컬에서 내보내기를 실행하고 확인하는 법 |
| `CLAUDE.md` | 문서 구조 표에 `docs/install.md` 추가 |

Phase 번호(1~9)는 바꾸지 않는다(내 판단: 운영 단계가 아니라 별도 시연 기능이므로 Phase에 넣지 않는다).

## 13. 남은 결정·미확인

| 항목 | 상태 |
|------|------|
| Access 무료 조건(사용자 수·로그 보관) | **여전히 미확인**(2026-11-05 확인 시도: `account-limits` 문서는 Access 앱 500개만 명시하고 무료 사용자 수·로그 보관은 없다. 요금제 페이지는 직접 읽지 못했고, 검색 요약은 「50명 미만 무료」를 가리키나 제3자 정리라 확정하지 않는다. 2026-11-05 재확인: 요금제 페이지(`plans/zero-trust-services`)와 `account-limits` 문서 모두 무료 사용자 수·로그 보관 기간을 적지 않는다. 설정 전에 Cloudflare 대시보드의 Zero Trust 요금제 화면에서 직접 확인) |
| Pages 미리보기 배포 비활성화 가능 여부, 브랜치 별칭 보호 | **여전히 미확인**(2026-11-05: 미리보기 배포 문서는 `<hash>.<project>.pages.dev`와 브랜치 별칭 존재를 설명하지만 Access 정책이 별칭까지 막는지는 적지 않았다. 확인 방법: 정책을 켠 뒤 시크릿 창에서 별칭 URL이 로그인으로 가는지 시험. 이 시험은 설치 절차의 사전 점검에 포함. 2026-11-05 재확인: 미리보기 배포 문서는 기본값이 「공개」라고만 적고 끄는 방법·Direct Upload 언급이 없으며, Access 정책은 「무작위 미리보기 링크만」 보호하고 `*.pages.dev`·커스텀 도메인은 보호하지 않는다고 적는다. 별칭 보호 여부는 문서에 없다. 이미 올린 배포 삭제는 `wrangler pages deployment delete`가 있으나 브랜치의 최신 배포는 지울 수 없다고 한다) |
| Direct Upload에 월 빌드 한도가 적용되는지 | **여전히 미확인**(2026-11-05: Pages 한도 문서는 월 500회(Free)를 적지만 Git·Direct Upload를 구분하지 않는다. 같은 문서가 Free 사이트당 파일 20,000개·파일당 25 MiB, 유료는 `PAGES_WRANGLER_MAJOR_VERSION=4` 설정 시 100,000개라고 적는다. 2026-11-05 재확인: 한도 문서의 「Builds per month」 행은 배포 방식을 한정하는 문구가 없고, 「Builds」 설명이 Git 푸시를 예로 든다. 적용 여부를 문서만으로 확정할 수 없다. 이 기능은 빌드를 Actions에서 하므로 영향이 있으면 월 배포 횟수 한도로 나타난다) |
| Actions 러너 사용량(비공개 저장소 무료 분) | **확인함**(2026-11-05, GitHub 문서): GitHub Free는 비공개 저장소에 월 2,000분(표준 러너, Linux 배수 1). 이 기능의 빌드·배포 1회가 이를 넘지 않는지는 실제 빌드 시간으로 점검 |
| Pages와 Workers Static Assets 중 어느 쪽이 현재 권장인지 | **여전히 미확인**(2026-11-05: Workers 마이그레이션 문서는 Workers의 기능 범위가 넓다고만 적고 신규 프로젝트 권장이나 Pages 지원 종료 일정은 적지 않는다. 사용자는 Pages를 지정했으므로 Pages로 진행하고, 공지가 바뀌었는지 설치 전에 확인) |
| GitHub Actions Secrets 3종 등록, Cloudflare API 토큰 발급, Pages 프로젝트(Direct Upload) 생성 | 사용자 설정 필요 |
| Compose·Helm 지원 서비스 범위 표 확정 | 계획 단계 첫 작업 |
| 설정 형식(`.synctx.yaml` 확장 대신 전용 `static-export.yaml`) | 내 판단 |
| 링크·코드 글자 빨강 | 결정함(사용자): `#B02A14`(대비 6.59:1). `#EF3B24`는 장식에만 사용 |
| 빌드 위치 | 결정함: Actions + Direct Upload |
