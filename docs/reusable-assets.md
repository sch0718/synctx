# 재활용 자산 목록

기존 저장소에서 synctx 마이크로서비스에 가져올 수 있는 코드와 자산을 정리한 문서.

## 소스 저장소

| 저장소 | 경로 | 설명 |
|--------|------|------|
| **markdown_converter** | `~/workspace/markdown_converter` | 마크다운 프리뷰 웹 서비스 (md-preview) |
| **example-infra** | `~/workspace/example-infra` | 사내 시스템 인프라 저장소 |
| └ 문서변환 | `10. 문서/문서변환/` | 독립 실행형 변환 스크립트 모음 |
| └ docsify 브랜치 | `feature/docsify` | Docsify 기반 문서 사이트 + 내부 CDN |

---

## 1. 직접 재활용 — 코드를 가져와 핵심 모듈로 사용

### 1.1 renderer.py → Viewer Service `renderer.py`

- **소스**: `markdown_converter/src/md_preview/renderer.py`
- **기능**: Markdown → HTML 렌더링 엔진
- **포함 내용**:
  - `render_markdown()` — MD→HTML 변환 (tables, fenced_code, TOC, sane_lists, md_in_html)
  - `github_slugify()` — GitHub 스타일 heading ID 생성
  - `preprocess_md()` — 테이블 헤더 앞 빈 줄 삽입 (파서 호환)
  - `fix_heading_ids()` — heading ID를 GitHub 방식으로 재작성
  - `transform_mermaid_blocks()` — mermaid 코드 블록을 mermaid.js 형태로 변환
  - `rewrite_links()` — 상대 이미지/링크 경로 재작성
  - `extract_title()` — 첫 번째 h1 제목 추출
  - `render_download_html()` — 자체 완결형 HTML 생성 (JS/CSS 인라인)
  - 7개 테마 CSS (GitHub Light/Dark, Cayman, Minimal, Hacker, Modernist, Corporate)
- **대상 서비스**: Viewer Service
- **수정 필요 사항**:
  - `rewrite_links()`의 경로 재작성 로직을 서비스 URL 패턴에 맞게 조정
  - CDN Service URL로 라이브러리 경로 변경
  - 테마 중 필요한 것만 선별하여 경량화 가능

### 1.2 git_repo.py → shared/synctx-common `git_manager.py`

- **소스**: `markdown_converter/src/md_preview/git_repo.py`
- **기능**: Git 저장소 관리 (clone, fetch, tree, file read)
- **포함 내용**:
  - `GitRepoManager` 클래스
  - clone 모드: full, shallow, blobless, treeless
  - PAT/SSH 인증 (credential_file, credential_env)
  - sparse-checkout 지원
  - `list_tree()`, `read_file()`, `head_sha()`, `last_commit()`
  - 민감 정보 마스킹 (`_sanitize_git_text()`)
  - 한글 경로 지원 (`core.quotepath=false`)
- **대상 서비스**: shared/synctx-common (Viewer, Sync Engine, Marp가 공유)
- **확장 필요 사항**:
  - `diff_since(sha)` — 특정 커밋 이후 변경된 파일 목록 반환 (Added/Modified/Deleted 구분)
  - `changed_files()` — 변경된 .md 파일만 필터링
  - `commit_and_push()` — Marp Service 출판 시 Git 커밋+푸시

### 1.3 config.py → shared/synctx-common `config.py`

- **소스**: `markdown_converter/src/md_preview/config.py`
- **기능**: YAML 기반 저장소 프로필 설정 로더
- **포함 내용**:
  - `RepoProfile` — 저장소 설정
  - `AuthConfig` — 인증 설정 (PAT, SSH, credential_file, credential_env)
  - `WebhookConfig` — 웹훅 설정
  - `load_config()` — YAML 파일 또는 환경변수에서 설정 로드
- **대상 서비스**: shared/synctx-common
- **확장 필요 사항**:
  - `XWikiTargetConfig`, `OpenProjectTargetConfig` — 동기화 대상
  - `MarpConfig` — Marp 설정 (publish_repo, themes 등)
  - `CdnConfig` — CDN 설정 (storage_path, allowed_extensions 등)
  - `MappingRule`, `SyncConfig` — 경로 매핑, 동기화 옵션

### 1.4 mermaid_to_svg.py → Sync Engine `converters/mermaid.py`

- **소스**: `example-infra/10. 문서/문서변환/mermaid_to_svg.py`
- **기능**: Mermaid 코드 블록 → SVG 일괄 변환
- **포함 내용**:
  - `find_mmdc()` — mmdc CLI 자동 감지
  - `convert_block_to_svg()` — 단일 블록 변환
  - `read_svg_inline()` — SVG를 인라인 삽입용 문자열로 변환
  - `process_file()` — 파일 내 모든 Mermaid 블록 처리
- **대상 서비스**: Sync Engine
- **수정 필요 사항**:
  - CLI argparse 제거, 함수형 API로 리팩토링
  - `extract_mermaid_blocks(md_text) → list[MermaidBlock]` 형태로 단순화

### 1.5 convert_md_to_html.py (참고)

- **소스**: `example-infra/10. 문서/문서변환/convert_md_to_html.py`
- **비고**: `renderer.py`와 기능이 겹침. `renderer.py`가 더 발전된 버전이므로 `renderer.py`를 주력으로 사용.
  다만 이 스크립트의 `preprocess_mermaid()` 함수 (mmdc 기반 인라인 SVG 변환)는 참고 가치가 있음.

### 1.6 convert_to_pdf.py → Viewer Service `converters/pdf.py`

- **소스**: `example-infra/10. 문서/문서변환/convert_to_pdf.py`
- **기능**: HTML → PDF 변환 (Chrome DevTools Protocol)
- **포함 내용**:
  - `ChromeCDP` 클래스 — Chrome headless 제어, WebSocket 통신
  - `build_cover_html()` — 표지 HTML 생성
  - `extract_sections()` — HTML 본문 추출
  - `build_print_html()` — 인쇄용 HTML 조합
- **대상 서비스**: Viewer Service (문서 PDF 다운로드용. Marp PDF는 Marp CLI가 처리)
- **비고**: `markdown_converter/src/md_preview/pdf_converter.py`에 더 발전된 버전(Remote CDP 지원)이 있음

### 1.7 테마 CSS (7종)

- **소스**: `markdown_converter/src/md_preview/renderer.py` 내 `THEME_CSS` 딕셔너리
- **목록**: GitHub Light, GitHub Dark, Cayman, Minimal, Hacker, Modernist, Corporate
- **대상 서비스**: Viewer Service
- **비고**: 테마 CSS를 별도 파일로 분리하여 CDN Service에서 관리하는 것도 고려

### 1.8 사내 example-fe 디자인 시스템 자산

- **소스**: `~/workspace/example-fe`
- **기능**: UI/UX 일관성을 위한 디자인 토큰 및 레이아웃 구조
- **포함 내용**:
  - GNB, LNB, Content Area 등 기본 레이아웃 패턴
  - 색상 팔레트, 타이포그래피, 여백 등 디자인 토큰
  - 버튼, 모달, 카드, 폼 등 공통 컴포넌트의 시각적 스타일 (CSS)
- **대상 서비스**: Viewer Service (및 스크럼 보드)
- **수정 필요 사항**:
  - Viewer Service는 FastAPI 기반 서버사이드 렌더링이므로, React 컴포넌트 코드를 직접 가져오지 않고 CSS 변수 및 스타일시트만 추출하여 적용
  - 마크다운 렌더링 결과물(테이블, 코드 블록 등)과 조화롭게 어울리도록 CSS 조정

### 1.9 pyproject.toml

- **소스**: `markdown_converter/pyproject.toml`
- **활용**: 각 서비스의 `pyproject.toml` 베이스. 의존성 목록 참고.
- **주요 의존성**: fastapi, uvicorn, markdown, pyyaml, pypdf, httpx

### 1.10 내부 CDN 벤더 파일

- **소스**: `example-infra` feature/docsify 브랜치 `docs/cdn/`
- **포함 내용**:
  - mermaid 10.9.1
  - chart.js 4.4.4
  - (참고용) docsify 4.13.1 및 플러그인
- **대상 서비스**: CDN Service의 초기 데이터 (`cdn-data/` 디렉토리)
- **활용**: `cdn-data/{library}/{version}/` 구조에 맞게 재배치. 단, **docsify 관련 파일은 synctx가 서버사이드 렌더링을 사용하므로 기본 반입 대상에서 제외**한다.
- **관련 스크립트**: `20. Helm/scripts/download-cdn-vendor.sh` → `scripts/download-cdn-vendor.sh`
- **추가 필요 라이브러리**: highlight.js (코드 구문 강조) — 기존 CDN에 미포함이므로 다운로드 스크립트에 추가 필요

### 1.11 Nginx 설정 + Helm Chart

- **소스**: `example-infra` feature/docsify 브랜치
  - `docs/nginx.conf` — Docsify 정적 서빙용 Nginx 설정
  - `docs/Dockerfile` — Nginx + 정적 파일 이미지
  - `20. Helm/docs-site/` — Helm Chart (deployment, service, ingress)
- **대상 서비스**: Slides Server (Nginx 설정, Dockerfile 참고), Helm Chart 전체 구조 참고
- **수정 필요 사항**: Docsify 전용 설정을 synctx Slides Server에 맞게 수정

---

## 2. 패턴/아이디어 재활용 — 설계를 가져와 새로 구현

### 2.1 XWiki REST API 호출 패턴

제안된 아이디어에서 가져온 XWiki REST API 사용법.
상세 레퍼런스는 [xwiki-api-reference.md](xwiki-api-reference.md) 참조.

### 2.2 Git diff 기반 변경 감지

```bash
# 변경된 파일 목록 (Added/Modified/Deleted 구분)
git diff --name-status <last-sync-sha>..HEAD -- '*.md'

# 출력 예시:
# A     docs/신규문서.md
# M     docs/수정문서.md
# D     docs/삭제문서.md
```

### 2.3 PAT/SSH 인증 패턴

`config.py`의 `AuthConfig` 패턴을 모든 서비스의 인증에 동일하게 적용:
- `credential_env` — 환경변수에서 토큰 로드 (K8s Secret → env 주입에 적합)
- `credential_file` — 파일에서 토큰 로드

### 2.4 폐쇄망 배포 패턴

- Docker 이미지에 모든 의존성 번들링
- CDN 파일과 출판 HTML은 PVC로 영속화
- `pull-and-save-images.sh` 패턴으로 이미지 tar 반입
- Helm Chart 기반 K8s 배포
- 내부 레지스트리(`registry.internal`)에 이미지 push 후 배포

---

## 3. 신규 개발 필요

### Viewer Service

| 모듈 | 설명 | 복잡도 |
|------|------|--------|
| `app.py` | FastAPI 서버 (라우터 등록) | 낮음 |
| `repo_routes.py` | 저장소 API (기존 md-preview 기반이나 구조 변경) | 중 |
| Marp 연동 UI | PDF 다운로드/출판 버튼 + Marp Service API 호출 | 중 |
| Draw.io 연동 | `.drawio` 파일 감지 → Draw.io Service 열기 링크 | 낮음 |

### CDN Service

| 모듈 | 설명 | 복잡도 |
|------|------|--------|
| `app.py` | FastAPI 서버 + StaticFiles 마운트 | 낮음 |
| `cdn_routes.py` | 파일 업로드 API + 관리 웹 UI | 중 |

### Marp Service

| 모듈 | 설명 | 복잡도 |
|------|------|--------|
| `app.py` | FastAPI 서버 | 낮음 |
| `marp_routes.py` | 변환/출판/미리보기 API | 중 |
| `git_publisher.py` | HTML → Git 커밋 출판 모듈 | 중 |

### Sync Engine

| 모듈 | 설명 | 복잡도 |
|------|------|--------|
| `sync_engine.py` | 동기화 오케스트레이션 | 높음 |
| `path_mapper.py` | 경로 + front matter → 대상 매핑 | 중 |
| `targets/xwiki.py` | XWiki REST API 클라이언트 | 중 |
| `targets/openproject.py` | OpenProject REST API v3 클라이언트 | 중 |
| `cli.py` | CLI 엔트리포인트 | 낮음 |

### Draw.io Service

| 모듈 | 설명 | 복잡도 |
|------|------|--------|
| Helm 배포 설정 | `jgraph/drawio` 공식 이미지 배포 (별도 코드 개발 불필요) | 낮음 |
| 이미지 미러링 | 외부 Docker 이미지 → 내부 레지스트리 반입 | 낮음 |

### Slides Server

| 모듈 | 설명 | 복잡도 |
|------|------|--------|
| `nginx.conf` | 정적 파일 서빙 설정 | 낮음 |
| `Dockerfile` | Nginx + 설정 번들 | 낮음 |

### 공통

| 모듈 | 설명 | 복잡도 |
|------|------|--------|
| `shared/synctx-common` | 공통 라이브러리 패키지 | 중 |
| `helm/synctx/` | Helm Chart 전체 | 중 |
| `scripts/pull-and-save-images.sh` | Docker 이미지 오프라인 반입 스크립트 (`jgraph/drawio` 포함) | 낮음 |

---

## 4. 파일 복사 체크리스트

구현 시작 시 기존 저장소에서 복사할 파일 목록:

```bash
# shared/synctx-common으로 (Viewer, Sync Engine, Marp가 공유)
cp markdown_converter/src/md_preview/git_repo.py  synctx/shared/synctx-common/src/synctx_common/git_manager.py
cp markdown_converter/src/md_preview/config.py     synctx/shared/synctx-common/src/synctx_common/config.py

# Viewer Service로
cp markdown_converter/src/md_preview/renderer.py   synctx/services/viewer/src/renderer.py

# Sync Engine으로
cp "example-infra/10. 문서/문서변환/mermaid_to_svg.py"  synctx/services/sync/src/converters/mermaid.py
cp "example-infra/10. 문서/문서변환/convert_to_pdf.py"  synctx/services/viewer/src/converters/pdf.py

# CDN 초기 데이터 (docsify 브랜치에서)
# feature/docsify 브랜치의 docs/cdn/ → synctx/cdn-data/
# ※ mermaid/chart.js만 선별 반입, docsify 제외, highlight.js 추가 다운로드 및 {library}/{version}/ 구조에 맞게 재배치 필요

# Helm Chart / Nginx 참고 (docsify 브랜치에서)
# 20. Helm/docs-site/ → synctx/helm/synctx/ 구조 참고
# docs/nginx.conf → synctx/services/slides/nginx.conf 참고

# 벤더 다운로드 스크립트
cp "example-infra/20. Helm/scripts/download-cdn-vendor.sh"  synctx/scripts/download-cdn-vendor.sh
```

복사 후 각 파일에서 불필요한 부분 제거하고, 서비스별 패키지 구조에 맞게 import 경로를 수정해야 한다.
