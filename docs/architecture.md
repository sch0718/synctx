# 아키텍처 설계

## 1. 시스템 개요

synctx는 마이크로서비스 아키텍처로 구성되며, K8s 클러스터에서 운영된다.

| 서비스 | 역할 | K8s 리소스 |
|--------|------|------------|
| **Viewer Service** | 마크다운 렌더링·열람 | Deployment |
| **CDN Service** | 정적 라이브러리 서빙 + 파일 업로드 | Deployment + PVC |
| **Marp Service** | 마크다운 → PDF/슬라이드 HTML 변환 + 출판 | Deployment |
| **Draw.io Service** | 다이어그램 작성·편집 (셀프호스팅) | Deployment |
| **Sync Engine** | Git → XWiki/OpenProject 동기화 | CronJob |
| **Slides Server** | 출판된 프레젠테이션 HTML 서빙 | Deployment + PVC |

```
                    ┌─── K8s Ingress ─────────────────────────┐
                    │                                          │
  Git 저장소         │  /view/*    → Viewer Service            │
  (GitLab 미러) ─────│  /cdn/*     → CDN Service               │
        │           │  /slides/*  → Slides Server             │
        │           │  /marp/*    → Marp Service              │
        │           │  /drawio/*  → Draw.io Service           │
        │           └──────────────────────────────────────────┘
        │                    │           │           │
        │                    ▼           ▼           ▼
        │             ┌──────────┐ ┌──────────┐ ┌──────────┐
        ├────────────→│  Viewer  │ │   CDN    │ │ Draw.io  │
        │             │ Service  │ │ Service  │ │ Service  │
        │             └────┬─────┘ └──────────┘ └──────────┘
        │                  │
        │                  │  JS/CSS 로딩
        │                  └──────────→ CDN
        │
        │             ┌──────────┐
        ├────────────→│   Marp   │
        │             │ Service  │
        │             └────┬─────┘
        │                  │
        │           ┌──────┴──────┐
        │           ▼             ▼
        │        PDF 생성     HTML 출판 → Git 커밋
        │                              │
        │                              ▼
        │                       ┌──────────┐
        │                       │  Slides  │
        │                       │  Server  │
        │                       └──────────┘
        │
        ▼
 ┌──────────────┐
 │ Sync Engine  │  (CronJob)
 └──────┬───────┘
        │
   ┌────┴────┐
  XWiki  OpenProject
```

## 2. 핵심 설계 결정

### 2.1 마이크로서비스 아키텍처

각 기능을 독립 서비스로 분리한다.

- **독립 배포**: 각 서비스를 개별적으로 업데이트·롤백
- **독립 확장**: 뷰어 트래픽이 많으면 Viewer만 스케일 아웃
- **기술 다양성**: Viewer/CDN은 Python, Marp는 Node.js, Slides는 Nginx
- **장애 격리**: Marp Service 장애가 Viewer에 영향을 주지 않음

서비스 간 통신은 K8s 내부 Service DNS를 사용한다:
- `http://cdn-service:8000/cdn/mermaid/10.9.1/mermaid.min.js`
- `http://marp-service:8000/api/marp/convert`

### 2.2 단방향 동기화 (외부→내부)

양방향 동기화는 충돌 해결이 복잡하고, 내부→외부 반출이 보안 정책상 불가능하므로 단방향으로 한정한다.

- **소스 오브 트루스**: Git 저장소 (외부에서 작성, 내부 GitLab에 미러링)
- **파생 데이터**: XWiki 페이지, OpenProject 이슈, 출판된 프레젠테이션 HTML
- 내부에서 직접 수정하면 다음 동기화 시 덮어써짐

### 2.3 Git diff 기반 증분 동기화

전체 문서를 매번 동기화하지 않고, 마지막 동기화 이후 변경된 파일만 처리한다.

```bash
git diff --name-only <last-sync-sha>..HEAD -- '*.md'
```

- 마지막 동기화 커밋 SHA는 상태 파일(`.synctx-state.json`)에 기록
- Added/Modified/Deleted 구분하여 처리
- 삭제 시 대상 도구 반영은 설정으로 제어 (기본: 마킹)

### 2.4 파일 경로 → 도구 리소스 매핑

```
docs/
├── 아키텍처/
│   └── 시스템-설계.md        → XWiki: Space=아키텍처, Page=시스템-설계
├── 요건/
│   └── REQ-001.md            → OpenProject: Work Package
└── 회의록/
    └── 2026-06-19-킥오프.md  → XWiki: Space=회의록, Page=2026-06-19-킥오프
```

### 2.5 Front Matter 기반 메타데이터

마크다운 파일 상단의 YAML front matter로 동기화 대상과 메타데이터를 지정한다.

```yaml
---
synctx:
  target: xwiki
  space: "아키텍처"
  page: "시스템 설계"
  tags: [설계, v2]
---
```

OpenProject 이슈:

```yaml
---
synctx:
  target: openproject
  project: example-project
  type: task
  status: in_progress
  assignee: "홍길동"
---
```

Marp 프레젠테이션:

```yaml
---
marp: true
theme: gaia
paginate: true
synctx:
  publish_path: "slides/아키텍처"    # 출판 경로 (선택)
---
```

### 2.6 내부 CDN을 통한 라이브러리 서빙

폐쇄망에서는 외부 CDN에 접근할 수 없다. CDN Service가 모든 프론트엔드 라이브러리를 서빙한다.

- **웹 UI + API로 파일 관리**: 새 라이브러리 추가 시 재배포 불필요
- **버전 디렉토리 구조**: `/{library}/{version}/{file}`
- **공유 CDN**: Viewer, Marp, XWiki, 기타 내부 서비스가 동일한 CDN을 사용

## 3. Viewer Service 설계

### 3.1 렌더링 파이프라인

```
마크다운 파일 (Git 저장소)
        │
        ▼
  서버사이드 렌더링 (python-markdown)
  ├── front matter 파싱 + 제거
  ├── 테이블 전처리 (빈 줄 삽입)
  ├── Markdown → HTML 변환
  ├── heading ID 재작성 (GitHub 스타일)
  ├── Mermaid 코드 블록 → <div class="mermaid"> 변환
  └── 상대 경로 링크/이미지 재작성
        │
        ▼
  클라이언트 렌더링 (브라우저)
  ├── Mermaid 다이어그램 (mermaid.js ← CDN Service)
  ├── 코드 구문 강조 (highlight.js ← CDN Service)
  └── 테마 CSS 적용
```

### 3.2 API

```
GET /api/repos                      저장소 목록
GET /api/repos/{id}/tree            트리 탐색
GET /api/repos/{id}/render          마크다운 렌더링
GET /api/repos/{id}/download        HTML/PDF 다운로드
POST /api/repos/{id}/webhook        Git push 알림
```

### 3.3 기능 목록

| 기능 | 설명 | 기존 md-preview |
|------|------|-----------------|
| Git 저장소 탐색 | 다중 저장소, 브랜치, 트리 | 있음 (재활용) |
| 마크다운 렌더링 | python-markdown 기반 HTML 변환 | 있음 (재활용) |
| 테마 | 7개 테마 CSS | 있음 (재활용) |
| Mermaid | 클라이언트 사이드 렌더링 | CDN 방식으로 변경 |
| 코드 하이라이팅 | highlight.js 구문 강조 | CDN 방식으로 변경 |
| HTML 다운로드 | 자체 완결형 HTML 생성 | 있음 (재활용) |
| PDF 다운로드 | Chrome CDP 기반 | 있음 (재활용) |
| Marp 연동 | "PDF 변환" / "출판" 버튼 | **신규** |

### 3.4 Marp 연동

Viewer에서 마크다운 파일을 열람할 때, `marp: true` front matter가 감지되면 추가 UI를 표시한다:

- **PDF 다운로드 버튼** → Marp Service의 변환 API 호출 → PDF 다운로드
- **출판 버튼** → Marp Service의 출판 API 호출 → Git에 HTML 커밋 → Slides Server URL 반환

## 4. CDN Service 설계

### 4.1 디렉토리 구조

```
cdn-data/
├── mermaid/10.9.1/mermaid.min.js
├── highlight.js/11.9.0/
│   ├── highlight.min.js
│   └── styles/github.min.css
├── chart.js/4.4.4/chart.umd.min.js
├── htmx/2.0.2/htmx.min.js
├── alpine.js/3.14.1/alpine.min.js
└── vue/3.4.31/vue.global.prod.js
```

### 4.2 API

```
GET    /cdn/{path}                  정적 파일 서빙
POST   /api/cdn/upload              단일/다중 파일 업로드
GET    /api/cdn/list                전체 라이브러리 목록
GET    /api/cdn/list/{library}      특정 라이브러리의 버전·파일 목록
DELETE /api/cdn/{path}              파일 삭제
GET    /api/cdn/admin               업로드 관리 웹 UI
```

### 4.3 파일 업로드

```bash
# 단일 파일
curl -X POST "http://cdn-service:8000/api/cdn/upload" \
  -F "file=@mermaid.min.js" \
  -F "path=mermaid/11.0.0/mermaid.min.js"

# 다중 파일
curl -X POST "http://cdn-service:8000/api/cdn/upload" \
  -F "files=@highlight.min.js" \
  -F "files=@github.min.css" \
  -F "base_path=highlight.js/11.9.0/"
```

### 4.4 캐시 전략

버전이 URL에 포함되므로 영구 캐시 가능:

```
Cache-Control: max-age=31536000, immutable
```

## 5. Marp Service 설계

### 5.1 개요

[Marp](https://marp.app/)는 마크다운을 슬라이드로 변환하는 도구다. `@marp-team/marp-cli`를 사용하여 PDF와 프레젠테이션 HTML을 생성한다.

### 5.2 변환 모드

| 모드 | 입력 | 출력 | 사용 시나리오 |
|------|------|------|---------------|
| PDF 다운로드 | 마크다운 (.md) | PDF 파일 | 발표 자료 인쇄/배포 |
| HTML 다운로드 | 마크다운 (.md) | 자체 완결형 HTML | 오프라인 프레젠테이션 |
| HTML 출판 | 마크다운 (.md) | HTML → Git 커밋 → 웹 접근 | 팀 내 공유 |

### 5.3 API

```
POST /api/marp/convert              PDF/HTML 변환 + 다운로드
POST /api/marp/publish              HTML 생성 → Git 커밋 → URL 반환
POST /api/marp/preview              슬라이드 미리보기 HTML 반환
GET  /api/marp/themes               사용 가능한 테마 목록
```

### 5.4 PDF 변환 흐름

```
POST /api/marp/convert
  Body: { repo_id, file_path, ref, format: "pdf" }

  1. Git 저장소에서 .md 파일 읽기
  2. marp --pdf --allow-local-files input.md -o output.pdf
  3. PDF 파일을 응답으로 반환 (Content-Disposition: attachment)
```

```bash
# Marp CLI 명령어
marp --pdf --allow-local-files --html \
  --theme-set /themes/custom.css \
  input.md -o output.pdf
```

### 5.5 HTML 출판 흐름

```
POST /api/marp/publish
  Body: { repo_id, file_path, ref, publish_path? }

  1. Git 저장소에서 .md 파일 읽기
  2. front matter에서 publish_path 추출 (또는 요청에서 지정)
  3. marp --html input.md -o output.html
  4. HTML 내 외부 리소스를 인라인으로 포함 (자체 완결형)
  5. Git 저장소의 출판 경로에 커밋
     예: _published/slides/{publish_path}/{제목}.html
  6. Slides Server URL 반환
     예: https://slides.internal/{repo}/{publish_path}/{제목}.html
```

### 5.6 Git 출판 모듈 (`git_publisher.py`)

```
GitPublisher
├── publish(repo_id, html_content, publish_path)
│   ├── clone/fetch 출판 대상 저장소
│   ├── HTML 파일을 지정 경로에 배치
│   ├── git add + commit
│   └── git push (내부 GitLab)
├── list_published(repo_id)           # 출판된 슬라이드 목록
└── unpublish(repo_id, publish_path)  # 출판 취소 (파일 삭제 + 커밋)
```

출판된 HTML은 Git 저장소에 커밋되므로 이력 관리가 가능하다.
Slides Server는 해당 저장소의 `_published/` 디렉토리를 마운트하여 정적 서빙한다.

### 5.7 Marp 테마

기본 테마 외에 커스텀 테마를 CDN Service에서 관리할 수 있다:

```yaml
# .synctx.yaml
marp:
  themes:
    - default
    - gaia
    - uncover
  custom_themes:                     # CDN에서 로딩
    - name: corporate
      css_url: "/cdn/marp-themes/corporate.css"
```

### 5.8 Dockerfile

Marp CLI는 Node.js 기반이므로 Python + Node.js 멀티 런타임 이미지를 사용한다.

```dockerfile
FROM python:3.12-slim

# Node.js 설치 (Marp CLI 실행용)
RUN apt-get update && apt-get install -y nodejs npm chromium \
    && npm install -g @marp-team/marp-cli \
    && apt-get clean

# Chromium 경로 설정 (Marp PDF 변환용)
ENV CHROME_PATH=/usr/bin/chromium
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

COPY . /app
WORKDIR /app
RUN pip install -e .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 6. Slides Server 설계

### 6.1 개요

Marp Service가 출판한 프레젠테이션 HTML을 정적으로 서빙하는 경량 Nginx 서버.

### 6.2 Nginx 설정

```nginx
server {
    listen 80;
    server_name slides.internal;

    root /data/published;

    location / {
        autoindex on;
        try_files $uri $uri/ =404;
    }

    # 캐시 설정 (출판물은 변경될 수 있으므로 짧은 캐시)
    location ~* \.html$ {
        add_header Cache-Control "no-cache, must-revalidate";
    }
}
```

### 6.3 데이터 흐름

```
Marp Service                  Slides Server
    │                              │
    ├── HTML 생성                   │
    ├── Git 저장소에 커밋            │
    │                              │
    └── (Git clone/pull 주기적) ───→│
                                   ├── /data/published/
                                   │   ├── repo-a/slides/아키텍처.html
                                   │   ├── repo-a/slides/설계리뷰.html
                                   │   └── repo-b/발표/킥오프.html
                                   │
                                   └── 정적 서빙
```

Slides Server는 주기적으로 출판 저장소를 `git pull`하거나, Marp Service로부터 webhook을 받아 갱신한다.

## 7. Draw.io Service 설계

### 7.1 개요

[draw.io](https://www.drawio.com/)(diagrams.net)의 셀프호스팅 버전. 공식 Docker 이미지(`jgraph/drawio`)를 그대로 사용하므로 별도 코드 개발이 불필요하다. Tomcat 기반 Java 웹 애플리케이션으로, 완전 오프라인 환경에서 동작한다.

### 7.2 기능

| 기능 | 설명 |
|------|------|
| 다이어그램 작성 | 플로우차트, 시퀀스, ERD, 네트워크, UML, C4 등 |
| 템플릿 | 내장 템플릿 라이브러리 (소프트웨어, 네트워크, 비즈니스 등) |
| 파일 저장 | 브라우저 로컬 저장, `.drawio`/`.xml` 다운로드 |
| 이미지 내보내기 | SVG, PNG, PDF, JPEG |
| 공유 다이어그램 | `.drawio` 파일을 Git에 커밋하여 팀원과 공유 |
| 완전 오프라인 | 외부 서비스 연결 없이 독립 동작 |

### 7.3 배포

별도 개발 없이 공식 이미지를 내부 레지스트리에 미러링하여 배포한다:

```bash
# 외부 네트워크에서 이미지 가져오기
docker pull jgraph/drawio:latest
docker tag jgraph/drawio:latest registry.internal/jgraph/drawio:latest
docker save registry.internal/jgraph/drawio:latest -o drawio.tar

# 내부망에서 로딩
docker load -i drawio.tar
docker push registry.internal/jgraph/drawio:latest
```

### 7.4 환경 변수 설정

draw.io 컨테이너의 주요 설정:

```yaml
env:
  - name: DRAWIO_SELF_CONTAINED
    value: "1"                    # 외부 서비스 연결 차단 (완전 오프라인)
  - name: DRAWIO_CSP_HEADER
    value: "default-src 'self';"  # Content Security Policy
  - name: PLANTUML_URL
    value: ""                     # PlantUML 서버 없음 (폐쇄망)
```

### 7.5 Viewer 연동

Viewer Service에서 `.drawio` 파일을 감지하면 draw.io 편집 링크를 제공한다:

```
사용자: Viewer에서 Git 트리 탐색 → architecture.drawio 클릭
        │
        ├── 1. Viewer가 .drawio 확장자 감지
        ├── 2. "Draw.io에서 열기" 링크 표시
        └── 3. 클릭 시 Draw.io Service URL로 이동
               https://synctx.internal/drawio/
               (사용자가 파일을 직접 열기)
```

`.drawio` 파일은 XML 기반이므로 Viewer에서 직접 렌더링하지 않고, Draw.io Service로 안내한다.
향후 서버사이드에서 `.drawio` → SVG/PNG 변환 기능을 추가하면 Viewer 인라인 미리보기도 가능하다.

### 7.6 XWiki 연동

Sync Engine이 `.drawio` 파일을 감지하면 SVG로 변환하여 XWiki 첨부파일로 업로드할 수 있다.
draw.io CLI(`draw.io --export`)를 사용하거나, draw.io의 XML을 파싱하여 처리한다.

## 8. 설정 체계

### 8.1 통합 설정 (`.synctx.yaml`)

모든 서비스의 설정을 하나의 ConfigMap으로 관리한다.

```yaml
synctx:
  version: "1"

  # Git 설정
  git:
    watch_paths:
      - "docs/**/*.md"
      - "issues/**/*.md"
      - "slides/**/*.md"
    ignore_paths:
      - "docs/drafts/**"
      - "_published/**"
      - "**/README.md"

  # 디렉토리 → 대상 매핑
  mappings:
    - path: "docs/"
      target: xwiki
      space_from: directory
    - path: "issues/"
      target: openproject
      project: example-project

  # 동기화 대상
  targets:
    xwiki:
      base_url: "https://wiki.internal"
      wiki: xwiki
      auth:
        type: basic
        credential_env: XWIKI_TOKEN
    openproject:
      base_url: "https://op.internal"
      auth:
        type: token
        credential_env: OP_API_KEY

  # Viewer 설정
  viewer:
    host: "0.0.0.0"
    port: 8000
    cdn_base_url: "http://cdn-service:8000/cdn"
    themes: [github-light, github-dark, cayman]
    default_theme: github-light

  # CDN 설정
  cdn:
    storage_path: "/data/cdn"
    max_upload_size: 50_000_000
    allowed_extensions: [.js, .css, .woff, .woff2, .ttf, .svg, .png, .map]
    upload_auth:
      enabled: false

  # Marp 설정
  marp:
    publish_repo: "https://gitlab.internal/team/published-slides.git"
    publish_branch: main
    publish_base_path: "_published/slides"
    themes: [default, gaia, uncover]
    custom_themes: []
    chrome_path: "/usr/bin/chromium"

  # Sync 설정
  sync:
    on_delete: mark
    dry_run: false
    state_file: .synctx-state.json

  # 변환 설정
  converters:
    mermaid:
      enabled: true
      output_format: svg
      theme: default
```

### 8.2 상태 파일 (`.synctx-state.json`)

```json
{
  "last_sync_sha": "abc1234",
  "last_sync_at": "2026-06-19T10:30:00Z",
  "synced_files": {
    "docs/아키텍처/시스템-설계.md": {
      "sha": "def5678",
      "target": "xwiki",
      "resource_id": "아키텍처.시스템-설계",
      "synced_at": "2026-06-19T10:30:00Z"
    }
  }
}
```

## 9. 모듈 설계

### 9.1 shared/synctx-common

서비스 간 공유하는 공통 라이브러리. 각 서비스가 pip dependency로 참조한다.

```
synctx-common/
├── config.py          # 공통 설정 스키마 (dataclass)
├── git_manager.py     # Git 저장소 관리 (clone, fetch, tree, read, diff)
├── auth.py            # 인증 헬퍼 (credential_env → header 변환)
└── front_matter.py    # YAML front matter 파서
```

### 9.2 Viewer Service 모듈

```
viewer/src/
├── app.py              # FastAPI 앱 + 라우터 등록
├── repo_routes.py      # 저장소 API (트리, 렌더링, 다운로드)
├── renderer.py         # Markdown → HTML 렌더링 엔진
│   ├── render_markdown()
│   ├── transform_mermaid_blocks()
│   ├── rewrite_links()
│   └── extract_title()
└── static/             # 프론트엔드
```

### 9.3 CDN Service 모듈

```
cdn/src/
├── app.py              # FastAPI 앱 + StaticFiles 마운트
└── cdn_routes.py       # 업로드/목록/삭제 API + 관리 웹 UI
    ├── upload_files()
    ├── list_libraries()
    ├── list_versions()
    └── delete_file()
```

### 9.4 Marp Service 모듈

```
marp/src/
├── app.py              # FastAPI 앱
├── marp_routes.py      # 변환/출판/미리보기 API
│   ├── convert()       # PDF/HTML 변환 + 다운로드
│   ├── publish()       # HTML 출판 (Git 커밋)
│   └── preview()       # 슬라이드 미리보기
├── git_publisher.py    # Git 출판 모듈
│   ├── publish()
│   ├── list_published()
│   └── unpublish()
└── config.py           # Marp 전용 설정
```

### 9.5 Sync Engine 모듈

```
sync/src/
├── cli.py              # CLI 엔트리포인트 (synctx sync)
├── sync_engine.py      # 동기화 오케스트레이션
│   ├── detect_changes()
│   ├── parse_files()
│   ├── map_targets()
│   ├── convert()
│   ├── sync_to_targets()
│   └── update_state()
├── path_mapper.py      # 경로 → 도구 리소스 매핑
└── targets/
    ├── xwiki.py        # XWiki REST API 클라이언트
    └── openproject.py  # OpenProject REST API 클라이언트
```

## 10. Helm Chart 설계

### 10.1 Chart 구조

```
helm/synctx/
├── Chart.yaml
├── values.yaml
└── templates/
    ├── _helpers.tpl
    ├── configmap.yaml              # .synctx.yaml → ConfigMap
    ├── secret.yaml                 # 인증 정보 (외부 Secret 참조)
    │
    ├── viewer-deployment.yaml
    ├── viewer-service.yaml
    │
    ├── cdn-deployment.yaml
    ├── cdn-service.yaml
    ├── cdn-pvc.yaml                # CDN 파일 저장소
    │
    ├── marp-deployment.yaml
    ├── marp-service.yaml
    │
    ├── drawio-deployment.yaml
    ├── drawio-service.yaml
    │
    ├── sync-cronjob.yaml           # 동기화 CronJob
    │
    ├── slides-deployment.yaml
    ├── slides-service.yaml
    ├── slides-pvc.yaml             # 출판된 HTML 저장소
    │
    └── ingress.yaml                # 통합 Ingress
```

### 10.2 values.yaml 핵심 설정

```yaml
global:
  imageRegistry: "registry.internal"
  imagePullSecrets:
    - name: registry-secret

viewer:
  replicaCount: 2
  image:
    repository: synctx/viewer
    tag: latest
  resources:
    requests: { cpu: 100m, memory: 256Mi }
    limits: { cpu: 500m, memory: 512Mi }

cdn:
  replicaCount: 1
  image:
    repository: synctx/cdn
    tag: latest
  persistence:
    enabled: true
    size: 10Gi
    storageClassName: nfs
  resources:
    requests: { cpu: 50m, memory: 128Mi }

marp:
  replicaCount: 1
  image:
    repository: synctx/marp
    tag: latest
  resources:
    requests: { cpu: 200m, memory: 512Mi }
    limits: { cpu: 1000m, memory: 1Gi }  # Chromium 사용으로 리소스 다소 필요

sync:
  schedule: "*/5 * * * *"
  image:
    repository: synctx/sync
    tag: latest
  resources:
    requests: { cpu: 50m, memory: 128Mi }

drawio:
  replicaCount: 1
  image:
    repository: jgraph/drawio
    tag: latest
  env:
    DRAWIO_SELF_CONTAINED: "1"
  resources:
    requests: { cpu: 100m, memory: 256Mi }
    limits: { cpu: 500m, memory: 512Mi }

slides:
  replicaCount: 1
  image:
    repository: nginx
    tag: "1.25-alpine"
  persistence:
    enabled: true
    size: 5Gi
    storageClassName: nfs

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: synctx.internal
      paths:
        - path: /view
          service: viewer
        - path: /cdn
          service: cdn
        - path: /marp
          service: marp
        - path: /drawio
          service: drawio
        - path: /slides
          service: slides
  tls: []

config:
  # .synctx.yaml 내용이 ConfigMap으로 주입됨
```

### 10.3 Ingress 라우팅

```yaml
# templates/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "synctx.fullname" . }}
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"  # CDN 업로드
spec:
  ingressClassName: nginx
  rules:
    - host: synctx.internal
      http:
        paths:
          - path: /view
            pathType: Prefix
            backend:
              service:
                name: {{ include "synctx.fullname" . }}-viewer
                port: { number: 8000 }
          - path: /cdn
            pathType: Prefix
            backend:
              service:
                name: {{ include "synctx.fullname" . }}-cdn
                port: { number: 8000 }
          - path: /marp
            pathType: Prefix
            backend:
              service:
                name: {{ include "synctx.fullname" . }}-marp
                port: { number: 8000 }
          - path: /drawio
            pathType: Prefix
            backend:
              service:
                name: {{ include "synctx.fullname" . }}-drawio
                port: { number: 8080 }
          - path: /slides
            pathType: Prefix
            backend:
              service:
                name: {{ include "synctx.fullname" . }}-slides
                port: { number: 80 }
```

### 10.4 PVC (Persistent Volume Claim)

CDN 파일과 출판된 HTML은 PVC로 영속화한다:

```yaml
# CDN PVC
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "synctx.fullname" . }}-cdn-data
spec:
  accessModes: [ReadWriteMany]   # CDN Service + 초기 데이터 로딩
  storageClassName: nfs
  resources:
    requests:
      storage: {{ .Values.cdn.persistence.size }}

# Slides PVC
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "synctx.fullname" . }}-slides-data
spec:
  accessModes: [ReadWriteMany]   # Marp Service (쓰기) + Slides Server (읽기)
  storageClassName: nfs
  resources:
    requests:
      storage: {{ .Values.slides.persistence.size }}
```

### 10.5 배포 명령

```bash
# 폐쇄망에서 Helm 배포
helm install synctx ./helm/synctx \
  --namespace synctx \
  --create-namespace \
  -f values-production.yaml

# 업그레이드
helm upgrade synctx ./helm/synctx \
  --namespace synctx \
  -f values-production.yaml

# 개별 서비스만 이미지 업데이트
helm upgrade synctx ./helm/synctx \
  --set marp.image.tag=v1.2.0
```

## 11. 제약 사항과 고려 사항

| 항목 | 상태 | 비고 |
|------|------|------|
| 외부 CDN 접근 | 불가 | 폐쇄망 → CDN Service로 대체 |
| 외부 이미지 레지스트리 | 불가 | `pull-and-save-images.sh`로 tar 반입 |
| Marp CLI Chromium | 필요 | Marp PDF 변환 시 headless Chromium 사용, 이미지에 번들 |
| XWiki Incoming Webhook | 미제공 | pull 모델(CronJob) 사용 |
| XWiki 배치 API | 미제공 | 단건 PUT 병렬 실행 (`asyncio` + `httpx`) |
| 양방향 동기화 | 미지원 | 보안 정책상 내부→외부 반출 불가 |
| 한글 파일명 | 지원 | `git -c core.quotepath=false` |
| 삭제 동기화 | 선택적 | 기본: 마킹 |
| CDN/Slides 영속성 | PVC 필수 | NFS StorageClass 권장 (ReadWriteMany) |
| Draw.io 외부 연결 | 차단 | `DRAWIO_SELF_CONTAINED=1`로 완전 오프라인 동작 |
| Draw.io 파일 저장 | 브라우저 로컬 | 서버사이드 저장소 미제공, Git 커밋으로 공유 |

## 12. 구현 우선순위

### Phase 1: Viewer Service + CDN Service

1. `shared/synctx-common` 공통 라이브러리 (git_manager, config, auth)
2. CDN Service: 정적 파일 서빙 + 업로드 API + 웹 UI
3. Viewer Service: md-preview 코드 이식, CDN 연동
4. 코드 하이라이팅 (highlight.js) 통합
5. Docker 이미지 빌드
6. Helm Chart 기본 구조 (viewer + cdn + ingress)

### Phase 2: Marp Service + Slides Server + Draw.io Service

1. Marp Service: PDF 변환 + 다운로드 API
2. Marp Service: HTML 출판 + Git 커밋 (`git_publisher.py`)
3. Slides Server: Nginx 정적 서빙
4. Draw.io Service: `jgraph/drawio` 이미지 미러링 + Helm 배포 (별도 개발 불필요)
5. Viewer에 Marp 연동 UI (PDF 다운로드 / 출판 버튼)
6. Viewer에 `.drawio` 파일 감지 → Draw.io 열기 링크
7. Helm Chart에 marp + slides + drawio 추가

### Phase 3: Sync Engine (XWiki)

1. Git 변경 감지 (`git_manager.py` 확장)
2. 마크다운 파싱 + front matter 추출
3. XWiki REST API 클라이언트 (`xwiki.py`)
4. Mermaid → SVG 변환 + 첨부파일 업로드
5. Helm Chart에 sync CronJob 추가

### Phase 4: Sync Engine (OpenProject)

1. OpenProject REST API 클라이언트 (`openproject.py`)
2. Front matter → Work Package 매핑
3. 이슈 생성/업데이트 로직

### Phase 5: 컨텍스트 하네스

1. AI 도구 간 컨텍스트 공유 포맷 정의
2. `.context/` 디렉토리 구조 설계
3. 컨텍스트 동기화 엔진
