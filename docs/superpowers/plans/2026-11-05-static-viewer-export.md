# 정적 Viewer 내보내기와 설치 방식 선택 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 비공개 Git 저장소의 Markdown 문서를 Viewer 화면 그대로 정적 사이트로 내보내 Cloudflare Pages(Direct Upload)와 Access로 열람하게 하고, 설치 방식(전체·선택·정적 × K8s·Compose·정적)을 한 문서에서 고르게 한다.

**Architecture:** 빌드 때 실제 Viewer ASGI 앱을 프로세스 안에서 띄워(`httpx.ASGITransport`) 저장소·파일·트리 응답을 받아 `dist/view/…` 정적 파일로 저장하고, HTML을 파싱해 정적 모드용 CSS·shim을 주입한다. 테마 CSS는 외부 파일 7개로 빼고 shim이 선택을 반영한다. 배포는 GitHub Actions가 빌드·검사 후 `wrangler pages deploy`로 올린다.

**Tech Stack:** Python 3.13, FastAPI Viewer 기존 코드, httpx, pydantic(synctx_common), pytest, GitHub Actions + `cloudflare/wrangler-action`, Helm/Compose 기존 스크립트.

**Spec:** `docs/superpowers/specs/2026-11-05-static-viewer-export-design.md`

## Global Constraints

- 기존 `app.py`·`renderer.py`의 동작·기본값·Helm/Compose 기본값·이미지 태그 규칙을 바꾸지 않는다. 예외는 Task 2의 `CORPORATE_CSS`와 `THEME_VARS_CSS`의 `corporate` 블록뿐이다.
- 정적 사이트는 항상 `/view/` 아래에 둔다(`VIEW_PREFIX = "/view"`는 `app.py` 상수). 루트 `/`는 `_redirects`로 `/view/`.
- 테마 7종 전부 포함, 기본 `corporate`. `corporate` 글자 빨강(링크·코드)은 `#B02A14`, `#EF3B24`는 띠·막대·밑줄 장식에만, 본문 `#1A1A1A`·보조 `#6D6E71`·연한 배경 `#F7F7F7`.
- 첫 단계는 저장소당 ref 하나, 검색·회의 API·발표·다운로드·`synctx-html` 블록은 제외(진입점 숨김).
- 설정 파일은 전용 `static-export.yaml`, 저장소 항목은 `.synctx.yaml`과 같은 필드(`auth.credential_env` 포함). 운영 `.synctx.yaml` 모델·Helm·ConfigMap은 바꾸지 않는다.
- 한도 초과(파일 20,000개 또는 파일당 25 MiB)는 빌드 실패. 지원 안 하는 확장자·`max_render_size_bytes` 초과 파일만 건너뛰고 보고서에 기록.
- 토큰은 환경변수 `GIT_READ_TOKEN`으로만 받고 URL·로그·산출물에 남기지 않는다. 예외 문자열은 로그에 그대로 찍지 않고 `type(exc).__name__`만 기록한다.
- 시험 데이터에 `team_docs`·사내 규정 원문을 쓰지 않는다(임시 Git 저장소에 가상 문서만).
- Conventional Commits(한국어), 커밋 전 diff 확인, 관련 없는 변경은 한 커밋에 넣지 않는다. 브랜치 `feature/static-export-*`.
- 새 패키지 `services/viewer/src/static_export/`는 `src`가 `sys.path`에 있는 기존 방식(최상위 import)을 따른다. Dockerfile은 바꾸지 않는다.

## Review Focus

- 한글·공백·`#`·`?`가 든 파일명과 `..`가 든 경로: 저장 경로가 `dist/view/` 밖으로 나가지 않고 링크가 URL 인코딩된 채 서로 맞는다 → Task 5·7.
- 저장소 id가 Viewer 예약 경로(`api`, `static`, `search` 등)와 같음, 문서가 0개인 저장소, 빈 설정: 빌드 전에 명확한 오류 → Task 3.
- 빌드 로그·예외·산출물에 토큰 값이 새는 경우(`ghp_`, `github_pat_`, 환경변수 값, `.git`): 검사에서 실패 → Task 9.
- 한도 초과 또는 깨진 내부 링크가 있는데 배포가 진행되는 경우: 검사 실패로 배포 job이 실행되지 않음 → Task 9·11.
- 테마 선택값이 `localStorage`에 없거나 7종 밖일 때 기본 `corporate`로 열리고, Mermaid·hljs 테마가 선택과 어긋나지 않음 → Task 7.

---

## File Structure

| 경로 | 책임 |
|------|------|
| `services/viewer/src/static_export/config.py` | `static-export.yaml` 읽기·검증, 저장소 부분 선택 |
| `services/viewer/src/static_export/runtime.py` | 운영 `.synctx.yaml` 모양의 런타임 설정 생성, Viewer 앱 로드 |
| `services/viewer/src/static_export/crawl.py` | ASGI로 페이지·트리·파일 목록 요청, 결과 수집 |
| `services/viewer/src/static_export/pages.py` | HTML 파싱 후 정적 모드 주입, 경로 변환 |
| `services/viewer/src/static_export/themes.py` | 테마 CSS 외부 파일 출력, 테마 표 |
| `services/viewer/src/static_export/assets/static-mode.js` | 정적 모드 shim(fetch 주소 변환, 테마 전환) |
| `services/viewer/src/static_export/build.py` | 전체 빌드 조립, `_headers`·`_redirects`·`robots.txt`, 보고서 |
| `services/viewer/src/static_export/verify.py` | 산출물 검사(토큰·`.git`·한도·깨진 링크) |
| `services/viewer/src/static_export/__main__.py` | CLI(`build`, `verify`) |
| `scripts/static-export/build.sh`·`check-access.sh` | 로컬·CI 진입 스크립트 |
| `.github/workflows/static-export.yml` | 빌드 → 검사 → 배포 |
| `docs/install.md`, `helm/synctx/values-profile-*.yaml`, `deploy/docker/profiles/*.env` | 설치 방식 선택 |
| `services/viewer/tests/test_static_export_*.py` | 시험 |

---

### Task 1: 확인 단계(문서만, 코드 변경 없음)

**Files:**
- Modify: `docs/superpowers/specs/2026-11-05-static-viewer-export-design.md`(13절 미확인 항목 갱신)

**Interfaces:**
- Produces: 13절 미확인 항목의 확인 결과. Task 11·12의 설정 안내가 이 결과를 따른다.

- [ ] **Step 1: 공식 문서로 아래 항목을 확인해 13절에 「확인함(출처)」 또는 「여전히 미확인」으로 기록한다.** Access 무료 조건(사용자 수·로그 보관, Cloudflare 요금제 공식 페이지), Direct Upload에 월 빌드 한도가 적용되는지, 브랜치 별칭(`<branch>.<project>.pages.dev`)이 Access로 보호되는지, Pages와 Workers Static Assets 중 현재 권장(사용자는 Pages 지정), Actions 비공개 저장소 무료 분.
- [ ] **Step 2: Cloudflare 문서는 Context7 또는 `developers.cloudflare.com` 원문을 쓰고, 확인 못 한 것은 추측하지 말고 「미확인」으로 둔다.**
- [ ] **Step 3: 커밋** — `docs: 정적 내보내기 설계의 Cloudflare 미확인 항목 확인 결과를 반영한다`

### Task 2: `corporate` 테마를 참고 양식에 맞춰 갱신

**Files:**
- Modify: `services/viewer/src/renderer.py`(`CORPORATE_CSS`, `THEME_VARS_CSS`의 `[data-theme="corporate"]` 블록만)
- Test: `services/viewer/tests/test_theme_contrast.py`, `services/viewer/tests/test_renderer.py`

**Interfaces:**
- Produces: 갱신된 `renderer.THEME_CSS["corporate"]`. Task 7이 이 문자열을 `themes/corporate.css`로 출력한다.

- [ ] **Step 1: 실패하는 시험 추가** — `test_renderer.py`에 `test_corporate_doc_css_follows_style_tokens`: `THEME_CSS["corporate"]`가 `#1A1A1A`·`#6D6E71`·`#F7F7F7`·`#B02A14`·`#EF3B24`를 포함하고 `#E01E1F`를 포함하지 않으며, `a {` 규칙의 색이 `#B02A14`이고 `#EF3B24`가 글자 색(`color:`)에 쓰이지 않는지 단언한다. `test_theme_contrast.py`에 `test_corporate_link_and_code_text_meet_aa`: `#B02A14` 대 `#FFFFFF`·`#F7F7F7` 대비가 4.5 이상.
- [ ] **Step 2: 시험 실행해 실패 확인** — `uv run pytest services/viewer/tests/test_renderer.py services/viewer/tests/test_theme_contrast.py -k corporate -v`
- [ ] **Step 3: 구현** — `CORPORATE_CSS`: 글꼴은 `-apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", Helvetica, Arial, sans-serif`, 코드 글꼴은 `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace`. 상단 6px `#EF3B24` 띠(`body::before`), `h1` 위 46×4px `#EF3B24` 막대, 인용문 좌측 4px `#EF3B24` 막대, 링크·인라인 코드 글자 `#B02A14`. `THEME_VARS_CSS`의 `corporate` 블록은 `--primary`를 `#B02A14` 계열로 정해 기존 `test_text_colors_meet_aa_on_theme_backgrounds`·`test_button_text_meets_aa_on_primary_backgrounds`가 통과하도록 하고, 장식용 토큰만 `#EF3B24`를 쓴다.
- [ ] **Step 4: 전체 Viewer 시험 통과 확인** — `uv run pytest services/viewer/tests -q` (기준을 낮추지 않는다. 깨지는 기존 시험은 원인을 보고한다.)
- [ ] **Step 5: 커밋** — `style(viewer): corporate 테마를 참고 양식 색·글꼴에 맞춘다` (운영 Viewer 모양이 바뀜을 본문에 적는다.)

### Task 3: 설정(`static-export.yaml`) 읽기와 검증

**Files:**
- Create: `services/viewer/src/static_export/__init__.py`, `config.py`, `static-export.sample.yaml`(저장소 루트 `static-export.sample.yaml`)
- Test: `services/viewer/tests/test_static_export_config.py`

**Interfaces:**
- Produces: `class SiteConfig(BaseModel)`(`title: str`, `theme: str = "corporate"`, `default_repo: str | None`, `repositories: list[SiteRepo]`), `class SiteRepo(BaseModel)`(`id, name, url, default_ref="main", auth: dict, include: list[str] = [], exclude: list[str] = []`), `def load_site_config(path: Path, selected: list[str] | None = None) -> SiteConfig`.

- [ ] **Step 1: 실패하는 시험** — `test_loads_repos_and_defaults`(theme 기본 `corporate`), `test_selected_subset_keeps_order_and_rejects_unknown_id`, `test_rejects_reserved_repo_id`(`api`·`static`·`search`·`meetings` 등 `app.RESERVED_PATHS`와 같은 값), `test_rejects_duplicate_id`, `test_rejects_empty_repositories`, `test_rejects_unknown_theme`(7종 밖), `test_rejects_url_with_credentials`(`https://user:pw@host/…`), `test_secret_values_are_never_loaded`(`auth`에 `credential_env`만 허용, `token` 키가 있으면 오류).
- [ ] **Step 2: 실패 확인** — `uv run pytest services/viewer/tests/test_static_export_config.py -v`
- [ ] **Step 3: 구현** — pydantic 모델. 예약 경로·테마 목록은 `renderer.THEME_CSS` 키와 `app.RESERVED_PATHS`를 import해서 쓴다(복제 금지). 검증 오류는 `SystemExit`가 아닌 `ValueError` 계열로 내고 CLI가 한국어 메시지로 바꾼다.
- [ ] **Step 4: 통과 확인 후 커밋** — `feat(static-export): 정적 내보내기 설정 파일을 읽고 검증한다`

### Task 4: 런타임 설정 생성과 Viewer 앱 로드

**Files:**
- Create: `services/viewer/src/static_export/runtime.py`
- Test: `services/viewer/tests/test_static_export_runtime.py`

**Interfaces:**
- Consumes: `SiteConfig`(Task 3), `standalone._load_viewer()`.
- Produces: `def write_runtime_config(site: SiteConfig, cache_dir: Path) -> Path`(운영 `.synctx.yaml` 모양: `authz.enabled false`, `viewer.asset_source bundled`, `viewer.repo_cache_path`, `collab.jitsi.enabled false`, `repositories[]`는 `id·name·url·default_ref·auth`(Viewer는 `watch_paths`·`ignore_paths`를 적용하지 않으므로 `include`·`exclude`를 이 설정에 옮기지 않는다. 제한은 Task 5 허용 목록에서만 한다. `auth`는 `credential_env`가 있으면 `type: token`을 함께 채운다. `RepoAuthConfig.type` 기본값이 `none`이고 `GitRepoManager._git_config_args`가 `token`일 때만 인증 헤더를 붙이기 때문이다. `standalone.prepare_config`도 같은 방식이다), `runtime.public_base_url` 없는 로컬 값), `def load_viewer(config_path: Path)`(환경변수 `SYNCTX_CONFIG_PATH`·`SYNCTX_ENABLED_SERVICES=viewer` 설정 후 앱 모듈 반환).

- [ ] **Step 1: 실패하는 시험** — `test_runtime_config_validates_with_synctx_config`(`SynctxConfig.model_validate`), `test_runtime_config_has_no_secret_values`(환경변수에 `ghp_secret`을 넣고 파일에 없음을 단언), `test_credential_env_sets_token_auth_type`(`credential_env`가 있는 저장소의 `auth.type`이 `token`이고 `GitRepoManager._git_config_args()`에 `Authorization` 헤더 인자가 들어감을 환경변수 값으로 단언), `test_include_exclude_are_not_passed_to_runtime_config`(제한은 Task 5 허용 목록에서만 한다), `test_authz_disabled_and_jitsi_disabled`.
- [ ] **Step 2: 실패 확인 후 구현** — `standalone.prepare_config`의 기본 구성과 같은 모양을 만든다(복제 대신 공통 함수 추출은 하지 않고 같은 키 구성을 시험으로 고정한다).
- [ ] **Step 3: 통과 확인 후 커밋** — `feat(static-export): 내보내기용 런타임 설정과 Viewer 로더를 만든다`

### Task 5: 순회(crawl)

**Files:**
- Create: `services/viewer/src/static_export/crawl.py`
- Test: `services/viewer/tests/test_static_export_crawl.py`(임시 Git 저장소 픽스처는 `tests/test_standalone.py`의 방식을 따른다)

**Interfaces:**
- Consumes: `load_viewer`(Task 4).
- Produces: `def build_allowlist(paths: Iterable[str], repo: SiteRepo) -> frozenset[str]`(저장소 파일 목록에 `include`·`exclude`를 적용한 내보내기 전용 허용 목록. 비교는 `services/sync/src/sync_engine.py` `_should_process`의 `watch_paths`·`ignore_paths`와 같은 경로 접두 비교다. 경로의 어느 구간이든 `.`으로 시작하면 기본 제외하고 `include`가 그 숨김 구간까지 적은 경우에만 허용한다(`exclude`가 우선). Viewer 파일·트리 API는 `watch_paths`·`ignore_paths`를 적용하지 않고 `file_paths`는 숨김 파일도 돌려주므로 Viewer 응답을 그대로 믿지 않는다. 페이지·트리 JSON·`files.json`·참조 자산(이미지·첨부)·회의 근거 발췌 모두 이 목록에 든 파일만 쓴다), `@dataclass class CrawlResult(pages: dict[str, str], json_files: dict[str, str], raw_files: dict[str, bytes], skipped: list[tuple[str, str]])`(키는 Viewer URL 경로, 예 `/view/repo-a/docs/a.md`. `raw_files`는 본문이 `/raw/{repo}/{path}`로 참조한 그림 원본이고 키는 `/raw/repo-a/docs/img/a.png`. `build_allowlist` 밖 그림은 수집하지 않는다), `async def crawl(viewer, site: SiteConfig) -> CrawlResult`.

- [ ] **Step 0: 허용 목록 시험** — `test_excluded_file_absent_from_pages_trees_files_and_assets`(가상 `internal/secret.md`·`.hidden.md`를 `exclude`에 두고, 페이지·트리 JSON·`files.json`·참조 자산·회의 근거 발췌에 그 경로와 본문 고유 문자열이 없음을 단언), `test_include_limits_everything_not_only_pages`.
- [ ] **Step 1: 실패하는 시험** — 임시 저장소(가상 문서: `README.md`, `docs/한글 문서.md`, `docs/a#b.md`, `docs/mermaid.md`, `big.md`(제한 초과), `bin.dat`)로: `test_pages_for_index_repo_and_every_viewable_file`, `test_tree_json_for_every_directory`(`/view/api/repos/{id}/tree?path=` 응답을 `json_files`에 저장), `test_referenced_raw_images_collected`(본문의 `img/a.png`와 `srcset` 후보가 `/raw/repo-a/docs/img/a.png`로 바뀐 주소를 `raw_files`에 바이트로 저장, 본문이 참조하지 않는 그림은 수집 안 함), `test_raw_image_over_limit_fails_build`(25 MiB 초과는 빌드 실패), `test_missing_raw_image_raises`, `test_files_json_saved`(마운트된 실제 앱의 `/view/api/repos/{id}/files` JSON 응답을 받는 통합 시험), `test_unsupported_and_oversize_are_skipped_with_reason`, `test_failed_page_raises_not_silently_skipped`(404·500은 예외), `test_waits_for_startup_sync_before_crawl`(clone을 지연시킨 저장소에서 순회가 시작 작업 완료 뒤에 시작됨), `test_clone_failure_aborts_build`(clone 실패 저장소가 있으면 예외), `test_fetch_failure_with_existing_cache_aborts_build`(기존 캐시가 있는 상태에서 fetch가 실패해도 예외), `test_does_not_log_exception_text`(`caplog`에 URL·토큰 없음).
- [ ] **Step 2: 실패 확인 후 구현** — `viewer.app`의 실제 라우트에는 `/view` 접두사가 없고(`/api/repos/{repo_id}/files`), 접두사는 `standalone.create_app`이 `Mount("/view", app=viewer.app)`로 붙인다. 그래서 `app = standalone.create_app(0, viewer=viewer)`(`refresh_interval=0`이면 갱신 작업 없음)로 마운트된 앱을 만들고, `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://static.local")`와 `app.router.lifespan_context(app)`로 lifespan(저장소 clone)을 실행한다. 이렇게 하면 요청 경로가 산출물의 공개 경로(`/view/…`)와 같다. 파일 목록은 `/view/api/repos/{id}/files`로 얻은 뒤 `build_allowlist`로 거른다. 트리 응답도 허용 목록 밖 항목을 지운 뒤 저장하고(허용 파일이 하나도 없는 디렉터리는 항목과 트리 JSON을 모두 만들지 않는다), 허용 목록 밖 파일을 가리키는 링크·자산 참조는 산출물에서 제거하거나 건너뛴 사유로 기록한다. 요청은 쿼리 없이(`ref` 미사용) 한다. 각 페이지 HTML의 `<img src>`·`srcset`에서 `/raw/` 주소를 모아 같은 클라이언트로 요청해 `raw_files`에 담는다(`renderer.py`가 상대 이미지를 `/raw/{repo}/{path}`로 바꾸고 `app.py`의 `raw_file`이 응답한다). 대상은 `app.py`의 `RAW_IMAGE_TYPES` 확장자뿐이며 파일당 25 MiB 한도는 Global Constraints를 따른다. lifespan 진입만으로는 clone이 끝나지 않는다. `viewer.app`의 lifespan은 `_sync_repos_on_startup`을 `create_task`로 시작하고 바로 yield하며, `GitRepoManager._ensure_sync`는 clone·fetch 실패를 `status="error"`로 기록하고 예외를 삼킨다. 그래서 작업 `viewer._startup_sync`가 끝날 때까지 기다린 뒤(`tests/test_standalone.py`가 `/readyz`를 기다리는 것과 같은 이유다), 선택한 모든 저장소의 `get_repo_manager(id).status`가 `ready`인지 확인하고 하나라도 `error`이면 이유(`last_error`, 토큰·URL 없이)와 함께 예외로 빌드를 중단한 뒤에 순회를 시작한다. 기존 캐시가 있는 상태의 fetch 실패도 `error`로 남으므로 오래된 캐시로 조용히 빌드하지 않는다.
- [ ] **Step 3: 통과 확인 후 커밋** — `feat(static-export): 실제 Viewer를 순회해 페이지와 트리 데이터를 수집한다`

### Task 6: 페이지 후처리

**Files:**
- Create: `services/viewer/src/static_export/pages.py`
- Test: `services/viewer/tests/test_static_export_pages.py`

**Interfaces:**
- Produces: `def page_output_path(url_path: str) -> str`(`/view/r/docs/한글.md` → `view/r/docs/한글.md/index.html`, 디코딩한 경로 사용, `..`·절대 경로·NUL은 `ValueError`), `def finalize_page(html: str, *, default_theme: str) -> str`(HTML 파서로 `</head>` 앞에 `<style id="static-hide">`·`<link id="theme-css">`·부트 스크립트, `<body>` 클래스에 `page-static` 추가, `</body>` 앞에 `static-mode.js` 참조), `HIDE_SELECTORS: tuple[str, ...]`(검색·회의·다운로드·발표·테마 외 서버 진입점 선택자).

- [ ] **Step 1: 실패하는 시험** — `test_output_path_encodes_korean_space_hash`, `test_output_path_rejects_traversal`(`/view/r/../../etc/passwd`), `test_finalize_injects_once`(두 번 적용해도 중복 없음), `test_finalize_keeps_body_content_byte_identical`(`<article class="markdown-body">` 안 문서 내용이 바이트 단위로 같음. Task 7이 교체하는 서버 부트 스크립트·테마 CSS·hljs 링크·Mermaid 초기화 스크립트는 대상이 아니다), **계약 시험** `test_hide_selectors_exist_in_rendered_viewer_html`(실제 `view_repo`·문서 응답 HTML에 `HIDE_SELECTORS` 각각이 존재해야 함 — Viewer 마크업이 바뀌면 실패).
- [ ] **Step 2: 실패 확인 후 구현** — 정규식 대신 `html.parser.HTMLParser`로 삽입 위치를 찾는다. 선택자는 실제 렌더 HTML(`viewer_ui.py`, `app.py` `_build_toolbar`)을 읽어 정한다.
- [ ] **Step 3: 통과 확인 후 커밋** — `feat(static-export): 정적 페이지 경로 변환과 정적 모드 주입을 만든다`

### Task 7: 테마 외부화와 shim

**Files:**
- Create: `services/viewer/src/static_export/themes.py`, `services/viewer/src/static_export/assets/static-mode.js`
- Modify: `services/viewer/src/static_export/pages.py`(`finalize_page`에 테마 교체 단계 추가)
- Test: `services/viewer/tests/test_static_export_themes.py`, `services/viewer/tests/js/static-mode.test.js`(기존 `tests/js` 실행 방식 확인 후 따른다)

**Interfaces:**
- Produces: `def export_theme_assets(out_dir: Path) -> dict[str, str]`(`view/static/themes/<이름>.css` 7개 출력, 반환은 이름→상대 경로), `THEME_TABLE`(이름→`{css, hljs, mermaid}`; `renderer.THEME_CSS`·`THEME_HLJS`·`THEME_MERMAID`에서 만든다). `static-mode.js`: 전역 설정 `SYNCTX_STATIC`가 있을 때 `fetch`의 `/view/api/repos/{id}/tree?path=…`를 `/view/api/repos/{id}/tree/{path}.json`으로, `/files`를 `files.json`으로 바꾸고, `localStorage['synctx-theme']`를 읽어 테마 `<link>`·`data-theme`·hljs 링크를 바꾸며, 테마 선택 변경 시 저장 후 재로드한다.

- [ ] **Step 1: 실패하는 시험** — `test_exports_all_seven_themes`, `test_default_is_corporate_when_unset`, `test_unknown_stored_theme_falls_back_to_corporate`, `test_theme_table_matches_renderer_tables`(`THEME_HLJS`·`THEME_MERMAID`와 일치), `test_finalize_replaces_server_theme_parts`(문서 HTML에서 서버 부트 스크립트·`<style>` 문서 CSS·hljs 링크·`mermaid.initialize` 인라인 스크립트가 교체되고 `theme: '<고정 문자열>'`이 남지 않음, Mermaid 그림이 없는 문서는 초기화 스크립트를 새로 넣지 않음), JS 시험: 선택값→CSS·hljs·Mermaid 매핑, tree·files 주소 변환 규칙(경로에 한글·공백 포함).
- [ ] **Step 2: 실패 확인 후 구현** — 문서 CSS는 `renderer.THEME_CSS`를 그대로 쓴다. 서버가 낸 문서 HTML은 순회 요청에 쿼리가 없어 `github-light` 기준으로 고정돼 있다: head의 `<style>`(문서 CSS)·hljs `<link>`, 본문 끝 인라인 `mermaid.initialize({ startOnLoad: false, theme: '<문자열>' })`(`app.py` `mermaid_scripts`). 이 초기화는 `data-theme`를 읽지 않으므로 `data-theme`를 바꾸거나 재로드하는 것만으로는 Mermaid 테마가 바뀌지 않는다. 또 문서 페이지는 `theme_via_query=True`라 head 앞쪽에 `_THEME_BOOT_QUERY`(`viewer_ui.py`, 저장값이 있으면 `?theme=`를 붙여 재이동)가, 저장소 목록 페이지는 `_THEME_BOOT_ATTR`가 이미 들어 있어 새 부트 스크립트보다 먼저 실행된다. 그래서 `finalize_page`가 다음을 교체한다. ① 서버 부트 스크립트를 정적 부트 스크립트로 바꾼다(저장값을 7종으로 검증해 없거나 밖이면 `corporate`, `data-theme` 설정, 선택 테마의 `{css, hljs, mermaid}`를 전역 `SYNCTX_STATIC`에 둔다). ② head의 문서 CSS `<style>`을 `<link id="theme-css">`로, hljs 링크를 `id`가 붙은 링크로 바꾸고 정적 부트 스크립트가 선택 테마의 경로를 넣는다. ③ 인라인 `mermaid.initialize` 스크립트의 테마 문자열을 `SYNCTX_STATIC.mermaid`로 바꾼다(`mermaid.run()` 유무는 그대로, 초기화가 없는 문서는 손대지 않는다). 실행 순서는 정적 부트 스크립트(head) → `viewer-core.js`·`viewer.js` → hljs → Mermaid 초기화 → `static-mode.js`(`</body>` 앞)다. 테마 선택 변경은 `static-mode.js`가 저장 후 재로드해 ①~③을 다시 거친다. 구현 시 실제 문서 HTML에서 각 위치를 확인해 기록한다.
- [ ] **Step 3: 통과 확인 후 커밋** — `feat(static-export): 테마 7종을 외부 CSS로 내보내고 정적 전환 shim을 추가한다`

### Task 8: 빌드 조립

**Files:**
- Create: `services/viewer/src/static_export/build.py`
- Test: `services/viewer/tests/test_static_export_build.py`

**Interfaces:**
- Consumes: Task 3~7.
- Produces: `def build_site(site: SiteConfig, out_dir: Path, cache_dir: Path) -> BuildReport`(`BuildReport`: `page_count, file_count, total_bytes, skipped, stage_seconds`), `dist/_headers`(CSP `default-src 'self'; script-src 'self' 'unsafe-inline'; …`는 `app.py` 미들웨어와 같은 값을 번들 모드 기준으로, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `X-Robots-Tag: noindex`, `Cache-Control: no-store` 또는 짧은 값), `dist/_redirects`(`/ /view/ 302`), `dist/robots.txt`(전체 Disallow), `dist/view/static/`(기존 `services/viewer/src/static/` 복사), `dist/raw/{repo}/{path}`(`CrawlResult.raw_files` 기록. 본문 `src`·`srcset`의 `/raw/...` 주소는 바꾸지 않고 그대로 가리킨다. 경로 검사는 다른 출력과 같고 파일명은 Task 6의 인코딩 규칙을 따른다). `_headers`에는 `/raw/*.svg`용으로 `app.py`의 `RAW_SVG_CSP`와 같은 값과 `X-Content-Type-Options: nosniff`를 넣는다(저장소 SVG를 주소로 직접 열어도 스크립트가 실행되지 않게 한다).

- [ ] **Step 1: 실패하는 시험** — `test_build_writes_all_expected_files`, `test_index_lists_only_selected_repos`, `test_headers_csp_matches_app_middleware_directives`(앱 미들웨어 CSP 지시어와 비교), `test_build_is_deterministic`(두 번 빌드한 파일 해시 동일), `test_no_dot_git_and_no_token_in_dist`, `test_report_lists_skipped_files`, `test_raw_images_written_and_svg_header_preserved`(`dist/raw/repo-a/docs/img/a.png` 존재, `_headers`의 SVG 규칙이 `RAW_SVG_CSP`와 일치), `test_build_rejects_unsafe_out_dir`(저장소 루트·그 상위, `cache_dir`와 같거나 포함하거나 그 안, 설정 파일 위치를 포함하는 경로, 홈 디렉터리, 이전 빌드 표지 없는 비어 있지 않은 디렉터리는 `out_dir`로 거부하고 아무것도 지우지 않음).
- [ ] **Step 2: 실패 확인 후 구현** — `write` 전에 모든 출력 경로가 `out_dir` 안인지 확인한다. 빌드 시작 때 `out_dir`을 비우되, 먼저 `resolve()`한 경로로 위 거부 조건을 검사하고 통과한 경우에만 지운다. 빌드가 `out_dir`에 표지 파일(`.static-export-out`)을 남기고, 표지가 없는 비어 있지 않은 디렉터리는 전용 출력 디렉터리가 아니므로 거부한다(오류는 설정 오류로 종료 코드 2). 심볼릭 링크인 `out_dir`도 거부한다.
- [ ] **Step 3: 통과 확인 후 커밋** — `feat(static-export): 정적 사이트 빌드를 조립한다`

### Task 9: 산출물 검사

**Files:**
- Create: `services/viewer/src/static_export/verify.py`
- Test: `services/viewer/tests/test_static_export_verify.py`

**Interfaces:**
- Produces: `def verify_dist(out_dir: Path, *, secret_values: Sequence[str], exclude_markers: Sequence[str] = ()) -> list[str]`(위반 목록, 비어 있어야 통과). 검사: 토큰 값 문자열, `ghp_`·`github_pat_`·`Authorization` 패턴, `.git` 존재, 파일 20,000개 초과, 25 MiB 초과, 깨진 내부 링크·자산 참조(`href`·`src`·`srcset`이 `dist` 안에서 해결되지 않음, `/raw/...` 그림 포함. 단 문서 툴바의 내려받기 링크 `/view/api/repos/<id>/download?…`(`app.py` `_build_toolbar`)는 Task 6이 본문을 그대로 두고 CSS로만 숨기므로 모든 문서에 남는다. 이 경로만 `verify.py`의 명시적 제외 규칙 `SERVER_ONLY_HREF_PATTERNS`로 건너뛰고, 그 밖의 `/view/api/…` 주소는 제외하지 않는다), 제외 파일의 경로·본문 고유 문자열 존재(`exclude_markers`로 받은 값을 `dist` 전체에서 찾음), `_headers`·`_redirects`·`robots.txt` 존재.

- [ ] **Step 1: 실패하는 시험** — 항목마다 위반을 심은 임시 `dist`로 한 건씩 검출, 정상 `dist`는 빈 목록, `test_verify_ignores_hidden_download_links`(문서마다 있는 숨긴 `/view/api/repos/<id>/download?…` 링크는 통과), `test_verify_flags_broken_document_link_in_body`(같은 `dist`에서 본문의 실제 깨진 문서 링크는 실패), `test_verify_does_not_echo_secret_in_messages`(위반 메시지에 비밀 값 원문이 없음).
- [ ] **Step 2: 실패 확인 후 구현·통과·커밋** — `feat(static-export): 산출물 검사로 비밀·한도·깨진 링크를 막는다`

### Task 10: CLI와 스크립트

**Files:**
- Create: `services/viewer/src/static_export/__main__.py`, `scripts/static-export/build.sh`
- Test: `services/viewer/tests/test_static_export_cli.py`

**Interfaces:**
- Produces: `python -m static_export build --config static-export.yaml --out dist [--repos a,b]`(환경변수 `STATIC_EXPORT_REPOS`도 받음, 인자가 우선), `python -m static_export verify --out dist`. 종료 코드: 성공 0, 설정 오류 2, 검사 실패 1. 빌드 끝에서 자동으로 verify를 실행하고 실패 시 `dist`를 남기되 종료 코드 1.

- [ ] **Step 1: 실패하는 시험** — 종료 코드 3종, `--repos`가 설정 순서를 유지, 인자·환경변수 우선순위, 오류 메시지가 한국어이며 토큰 값을 포함하지 않음.
- [ ] **Step 2: 실패 확인 후 구현** — `scripts/static-export/build.sh`는 `uv run --project … python -m static_export build …`를 감싼다(직접 `docker`·`kubectl` 금지 규칙은 해당 없음).
- [ ] **Step 3: 통과 확인 후 커밋** — `feat(static-export): 빌드·검사 CLI와 진입 스크립트를 추가한다`

### Task 11: GitHub Actions 워크플로와 배포 후 점검

**Files:**
- Create: `.github/workflows/static-export.yml`, `scripts/static-export/check-access.sh`, `services/viewer/src/static_export/access_check.py`
- Test: `services/viewer/tests/test_static_export_workflow.py`, `services/viewer/tests/test_static_export_access_check.py`

**Interfaces:**
- Produces: `def judge_access_response(status: int, location: str | None, body_head: str) -> bool`(세션 없는 요청이 **막혔으면** True: 302/303으로 `cloudflareaccess.com`·`/cdn-cgi/access/` 로그인으로 가거나 401/403. 200이고 문서 본문 표지가 보이면 False). 워크플로: `workflow_dispatch`(입력 `repos`)와 선택적 `schedule`, 빌드 job(`contents: read`, uv 설치, 빌드·검사, `dist` 업로드)과 배포 job(`needs: build`, `cloudflare/wrangler-action`, `pages deploy dist --project-name=<변수> --branch=<운영 브랜치 고정>`), `concurrency`, 사전 점검 단계(첫 배포 전, 비밀 없는 자리표시 페이지로 보호 호스트 점검)와 배포 후 `check-access.sh` 필수 단계(실패 시 워크플로 실패, 자동 제거는 하지 않음), 시크릿 `CLOUDFLARE_API_TOKEN`·`CLOUDFLARE_ACCOUNT_ID`·`GIT_READ_TOKEN`, `pull_request` 트리거 없음.

- [ ] **Step 1: 실패하는 시험** — 워크플로 YAML을 파싱해 `pull_request` 트리거가 없고 배포 job이 `needs: build`이며 `--branch` 값이 고정이고 시크릿이 `${{ secrets.* }}`로만 쓰이는지, `judge_access_response`의 케이스(200 본문 노출=False, Access 로그인 302=True, 403=True, 5xx=False).
- [ ] **Step 2: 실패 확인 후 구현** — gazua `.github/workflows/deploy.yml`의 job 분리·`permissions`·`concurrency` 구조를 따르되 Pages 배포만 Cloudflare로 한다. `check-access.sh <URL>`은 인증 없이 URL 몇 개를 요청해 `judge_access_response`로 판정하고 하나라도 False면 비정상 종료한다.
- [ ] **Step 3: 통과 확인 후 커밋** — `feat(static-export): Actions 빌드·배포 워크플로와 Access 점검을 추가한다`

### Task 12: 설치 방식 선택 체계

**Files:**
- Create: `docs/install.md`, `helm/synctx/values-profile-docs.yaml`, `values-profile-present.yaml`, `values-profile-full.yaml`, `deploy/docker/profiles/docs.env`, `present.env`, `full.env`
- Test: `scripts/tests/test_install_profiles.py`

**Interfaces:**
- Consumes: Task 1 확인 결과, `helm/synctx/values.yaml`의 `<service>.enabled`, `deploy/docker/synctx.env.example`의 `SYNCTX_SERVICES`.
- Produces: 프리셋 3종(문서만 = viewer+cdn, 발표 포함 = +marp+slides, 전체). Compose 프리셋은 Compose가 지원하는 서비스(viewer·cdn·marp·slides·drawio)만 쓰고 이름도 Helm `full`과 구분하며(`full`이라 부르지 않는다), Jitsi는 `SYNCTX_JITSI`로 따로 켜는 옵션이라 프리셋은 꺼 둔 채 필요한 설정(`SYNCTX_JITSI_HOST`·공개 주소·TLS·`SYNCTX_JITSI_ADVERTISE_IPS`·시크릿)을 `docs/install.md`에 적는다. 롤백 절은 정적 기능과 `corporate` 테마를 별도로 되돌리는 절차로 나눠 적는다. 또한 Sync·Feedback·Collab Gateway는 문서에 「Helm만」으로 적는다.

- [ ] **Step 1: 지원 범위 표 확정** — `compose.yaml`과 `values.yaml`을 읽어 구성×대상 지원 표(spec 7.2)를 확정한다. 표와 다르면 spec을 고친다.
- [ ] **Step 2: 실패하는 시험** — `helm template`(helm이 없으면 skip) 결과에서 프리셋별로 켜진 서비스 집합이 정의와 같음, Compose 프리셋의 `SYNCTX_SERVICES`가 지원 서비스 안에 있음·`deploy.sh --check` 호출 가능한 형식, **기본 `values.yaml`·`synctx.env.example`이 변경되지 않았음**(`git diff --quiet` 확인).
- [ ] **Step 3: 구현** — `docs/install.md`: 「무엇을 어디에 설치할까」 결정 표, 대상별 명령, 구성별 기능 가용 표(정적에서 안 되는 기능), 정적 Viewer 설치(Pages 프로젝트 생성 → Access 앱 → 시크릿 등록 → 첫 배포 순서 → `check-access.sh` 통과 후 공유), 롤백(사이트를 먼저 내리고 Access를 나중에 지움), 토큰 폐기. 이미지 태그 `latest` 금지 규칙은 K8s·Compose 절에 유지한다.
- [ ] **Step 4: 통과 확인 후 커밋** — `docs(install): 설치 방식 선택 안내와 구성 프리셋을 추가한다`

### Task 13: 문서 갱신과 최종 확인

**Files:**
- Modify: `README.md`(「시작하기」에 설치 방식 선택 링크와 정적 열람 한 줄), `docs/development.md`(배포 방식·프로젝트 구조), `docs/architecture.md`(정적 내보내기 요약, 운영 구성이 아님 명시), `docs/production-service-design.md`(시연용 구성의 `GIT_READ_TOKEN`·Access 점검을 분리해 기록, 운영 체크리스트·Phase 번호 불변), `docs/local-development.md`(로컬 내보내기 실행), `CLAUDE.md`(문서 구조 표에 `docs/install.md`)
- Test: 기존 문서 일관성 시험이 있으면 실행

- [ ] **Step 1: 위 문서를 갱신한다.** 정적 Viewer는 폐쇄망 운영 원칙의 대상이 아닌 별도 시연·열람용임과, Infra 가드가 없다는 식의 과장 없이 확인된 한계(Access 설정은 자동 시험 불가, 배포 후 점검 필요)를 그대로 적는다.
- [ ] **Step 2: 전체 시험 실행** — `uv run pytest services/viewer/tests shared/synctx-common/tests scripts/tests -q`. 통과 수와 skip 수를 보고한다.
- [ ] **Step 3: 실제 빌드 한 번** — 임시 Git 저장소(가상 문서)로 `scripts/static-export/build.sh`를 실행해 `dist/`를 만들고, `python -m http.server`로 열어 저장소 목록·트리 펼침·본문·Mermaid·테마 전환(`corporate` 기본 화면과 `github-dark` 전환 뒤 Mermaid 그림 색이 선택 테마와 맞는지 포함)을 브라우저(기존 Chromium 선택 시험 하네스 또는 Browser 도구)로 확인한다. 확인하지 못한 항목은 보고서에 「미확인」으로 적는다.
- [ ] **Step 4: 커밋** — `docs: 정적 내보내기와 설치 방식 선택을 문서에 반영한다`

---

## Self-Review 기록

- Spec 커버리지: 4(생성·후처리·한도·테마) → Task 2·5~9, 5(설정) → Task 3, 6.1~6.3(Actions·토큰·Access) → Task 11·12, 7(설치 체계) → Task 12, 9(시험) → 각 Task, 10(단계 게이트: 1단계 병행) → Task 1은 코드 Task와 독립, 11(롤백) → Task 12, 12(문서) → Task 13.
- 타입 일관성: `SiteConfig`·`SiteRepo`(Task 3) → Task 4·5·8에서 같은 이름, `CrawlResult`(Task 5) → Task 8, `THEME_TABLE`(Task 7) → Task 8.
- 알려진 위험: `HIDE_SELECTORS`는 실제 HTML에서 정해야 하므로 Task 6 계약 시험이 기준이다. Mermaid 테마 반영은 Task 7 Step 2의 교체 절차와 Task 13 Step 3의 브라우저 확인으로 본다.
