# synctx

**sync + context** — 폐쇄망 환경에서 AI 기반 개발 컨텍스트를 동기화하는 마이크로서비스 플랫폼

## 프로젝트 배경

### 문제

금융권 폐쇄망에서 AI 도구(Claude Code, Codex, Cursor 등)를 활용한 개발이 진행되고 있다.
개발 자체는 외부 인터넷 환경에서 이루어지고, GitHub을 통해 코드가 관리되며, 내부망으로 동기화되어 빌드·배포된다.

그런데 개발 서비스를 실제 사용하는 **현업 팀은 외부망에 접근할 수 없다**.
프로젝트 관리(OpenProject), 지식 관리(XWiki) 등 팀 협업 도구는 모두 내부에 있기 때문에, AI를 활용한 요건 분석, 문서 작성, 이슈 관리 등에서 생산성 한계가 발생한다.

또한 폐쇄망 특성상 외부 CDN에 접근할 수 없어, 마크다운 렌더링에 필요한 라이브러리(Mermaid, highlight.js 등)를 내부에서 자체 서빙해야 한다.

### 해결 방향

일반적인 내부→외부 데이터 반출은 제한되지만, **외부에서 내부로의 반입은 Git 동기화로 거의 자동화**되어 있다.
기본적으로 이 단방향 경로를 활용하여 다섯 가지 문제를 해결하며, 승인된 메일 게이트웨이가 있는 환경에서는 OpenProject의 제한된 메타데이터 변경만 외부 PR 생성 이벤트로 전달할 수 있다:

1. **문서 열람**: 외부에서 작성된 마크다운 문서를 내부에서 웹 기반으로 열람
2. **프레젠테이션**: 마크다운 문서를 Marp로 PDF/슬라이드 HTML로 변환하여 발표 자료로 활용
3. **다이어그램**: draw.io를 폐쇄망에서 셀프호스팅하여 다이어그램 작성·편집
4. **도구 동기화**: Git 저장소의 마크다운과 메타데이터를 내부 도구(XWiki, OpenProject)에 자동 동기화
5. **라이브러리 서빙**: 폐쇄망에서 프론트엔드 라이브러리를 서빙하는 내부 CDN 제공

### 핵심 가치

- **Human-AI Collaboration (사람-AI 협업)**: AI는 마크다운을 통해 지식을 생성/공유하고, 사람은 웹 뷰어와 내부 도구를 통해 이를 소비·관리하는 완벽한 협업 체계 구축
- **Harness Engineering & OKF**: 서로 다른 LLM(Cursor, Claude 등)이 마크다운을 공통 컨텍스트(메모리)로 사용하여 지식의 흐름(Orchestrated Knowledge Flow)을 끊김 없이 유지
- **Markdown 기반 스크럼/대시보드**: 외부망에서는 마크다운 기반의 칸반(Kanban)/대시보드로 프로젝트를 관리하고, 이는 내부망의 OpenProject로 자동 연동
- **지능형 라우팅**: 마크다운의 목적(프로젝트 관리 vs 지식 공유)에 따라 OpenProject와 XWiki로 자동 분기되어 동기화
- **제한적 피드백 반출**: OpenProject 상태 등 허용된 메타데이터만 서명된 메일 이벤트로 외부 PR 흐름에 전달
- **오프라인 독립성**: 외부 CDN 없이 모든 렌더링 라이브러리를 내부 CDN에서 서빙
- **문서→발표 전환**: 마크다운으로 작성한 문서를 바로 프레젠테이션으로 변환·출판
- **다이어그램 자급자족**: draw.io를 내부에서 운영하여 인터넷 없이 다이어그램 작성

## 아키텍처

마이크로서비스 구조로 K8s에서 운영되며, 각 서비스는 독립적으로 배포·확장된다.

```mermaid
flowchart TB
    subgraph External["외부망"]
        direction TB
        AI["AI 도구들<br>(Claude Code, Codex, Cursor)"]
        GitHub["GitHub"]
        ExternalInbox[("External Mailbox<br>/ Importer")]
        PR["Markdown Update PR"]

        AI --> GitHub
        ExternalInbox -. "PR 생성" .-> PR
        PR --> GitHub
    end

    subgraph Internal["내부망(폐쇄망) — K8s Cluster"]
        direction TB
        GitLab[("동기화된 Repo<br>(GitLab 미러)")]
        GitHub -- "자동 미러링" --> GitLab

        Ingress{"K8s Ingress"}
        Viewer["Viewer Service"]
        CDN["CDN Service"]
        Drawio["Draw.io Service"]
        Marp["Marp Service"]
        Slides["Slides Server"]
        SyncWebhook["Sync Webhook<br>Receiver"]
        Sync["Sync Engine<br>(CronJob)"]
        Feedback["Outbound Feedback<br>Exporter (optional)"]
        MailGateway[("Approved<br>Mail Gateway")]

        XWiki[("XWiki")]
        OP[("OpenProject")]

        GitLab -. "Push Event" .-> Ingress

        Ingress -- "/view/*" --> Viewer
        Ingress -- "/cdn/*" --> CDN
        Ingress -- "/slides/*" --> Slides
        Ingress -- "/marp/*" --> Marp
        Ingress -- "/drawio/*" --> Drawio
        Ingress -- "/api/sync/webhook/gitlab" --> SyncWebhook

        GitLab --> Viewer
        GitLab --> Marp
        GitLab --> Sync

        Viewer -. "JS/CSS 로딩" .-> CDN

        Marp -- "PDF 생성" --> PDF["PDF 파일"]
        Marp -- "HTML 출판<br>(Git commit)" --> GitLab

        Slides -. "git pull (slides-sync)" .-> GitLab

        SyncWebhook -- "K8s Job 즉시 생성" --> Sync
        Sync --> XWiki
        Sync --> OP

        OP -. "허용 필드 변경" .-> Feedback
        Feedback -. "redacted + signed email" .-> MailGateway
    end

    MailGateway -. "mail" .-> ExternalInbox
```

### 마이크로서비스 구성

| 서비스 | 역할 | 기반 기술 | K8s 리소스 |
|--------|------|-----------|------------|
| **Viewer Service** | 마크다운 렌더링·열람, HTML/PDF 다운로드 | FastAPI + python-markdown | Deployment |
| **CDN Service** | 정적 라이브러리 서빙 + 파일 업로드 관리 | FastAPI + StaticFiles | Deployment + PVC |
| **Marp Service** | 마크다운 → PDF/프레젠테이션 HTML 변환 + 출판 | FastAPI + @marp-team/marp-cli | Deployment |
| **Draw.io Service** | 다이어그램 작성·편집 웹 도구 (셀프호스팅) | jgraph/drawio (Tomcat) | Deployment |
| **Sync Webhook** | GitLab Push Event 수신 및 Sync Job 트리거 | Deployment |
| **Sync Engine** | Git 변경 감지 → XWiki/OpenProject 동기화 | Python CLI + httpx | CronJob |
| **Slides Server** | 출판된 프레젠테이션 HTML 정적 서빙 | Nginx | Deployment + PVC |
| **Outbound Feedback Exporter** | OpenProject 제한 필드 변경 → 서명된 메일 이벤트 반출 (선택) | Python CLI + SMTP | CronJob 또는 Deployment |

### 배포 방식

서비스마다 켜고 끌 수 있다. 필요한 서비스만 배포하는 것이 기본 사용법이다(예: Viewer·Marp·Slides만, 여기에 Draw.io 추가).

| 서비스 | K8s (Helm) | Docker Compose | 옵션 이름 |
|--------|-----------|----------------|-----------|
| Viewer | 지원 | 지원 (1단계) | `viewer` |
| CDN | 지원 | 지원 (1단계) | `cdn` |
| Marp | 지원 | 지원 (1단계) | `marp` |
| Slides Server | 지원 | 지원 (1단계) | `slides` |
| Draw.io | 지원 | 지원 (1단계) | `drawio` |
| Sync Engine | 지원 | 2단계 | `sync` |
| Outbound Feedback | 지원 (기본 끔) | 2단계 | `feedback` |

- **K8s (Helm)**: `<서비스>.enabled`로 켜고 끈다. 예: `--set drawio.enabled=false`. 꺼진 서비스는 Deployment·Service·Ingress 경로를 만들지 않는다.
- **Docker Compose**: 옵션 파일 `deploy/docker/synctx.env`의 `SYNCTX_SERVICES=viewer,cdn,marp,slides`처럼 켤 서비스를 쉼표로 적는다. 외부 포트는 `gateway`(nginx) 하나만 연다.
- 서비스 사이 의존성(CDN 없이 Viewer를 쓰는 경우 등)과 알려진 한계는 [아키텍처 설계](docs/architecture.md)의 "서비스 옵션과 의존성"을 참조한다.

### Docker Compose 빠른 시작

폐쇄망 레지스트리(`registry.local:5000`)에 synctx 이미지가 올라가 있어야 한다. Docker Compose v2.17 이상이 필요하다.

```bash
cp deploy/docker/synctx.env.example deploy/docker/synctx.env
cp deploy/docker/secrets.env.example deploy/docker/secrets.env
# synctx.env: SYNCTX_SERVICES, SYNCTX_IMAGE_TAG(0.1.0-<git-sha>), SYNCTX_SLIDES_REPO_URL 수정
# secrets.env: GIT_READ_TOKEN, GIT_PUBLISH_TOKEN 입력
# SYNCTX_CONFIG_FILE이 가리키는 .synctx.yaml 준비 (인증 프록시가 없으면 authz.enabled: false)

scripts/docker/deploy.sh --check   # 옵션 검사와 게이트웨이 설정 미리 보기(배포하지 않음)
scripts/docker/deploy.sh           # 배포
scripts/docker/down.sh             # 중지 (볼륨 유지)
```

운영 명령은 `scripts/docker/compose.sh -- ps`처럼 실행한다. 절차 전체는 [운영 서비스 설계](docs/production-service-design.md)의 Docker 배포 Runbook을 참조한다.

## Viewer Service (마크다운 뷰어)

기존 `markdown_converter`(md-preview)의 발전형. Git 저장소의 마크다운 문서를 웹에서 열람한다.

### 주요 기능

- **Git 저장소 뷰어** — 다중 저장소 트리 탐색, 문서 열람, webhook/polling 동기화
- **마크다운 렌더링** — 서버사이드 렌더링 (python-markdown), 7개 테마 지원
- **Mermaid 다이어그램** — 클라이언트 사이드 렌더링 (CDN Service에서 mermaid.js 로딩)
- **코드 하이라이팅** — highlight.js 기반 190+ 언어 구문 강조
- **HTML/PDF 다운로드** — 자체 완결형 HTML, Chrome CDP 기반 PDF
- **완전 오프라인** — 모든 외부 라이브러리를 CDN Service에서 로딩
- **회의 모드** — 회의 문서(`synctx.type: meeting`)에서 메모·형광펜·회의 정리(결정·액션 아이템), `.md`·메모 포함 HTML(읽기 전용) 내보내기와 가져오기. 메모는 브라우저(localStorage)에만 저장 (Phase 8.1a)
- **인터랙티브 HTML 임베드** — Markdown의 ` ```synctx-html ` 블록이 가리키는 저장소 HTML 묶음(모션 그래픽 등)을 한 파일로 합쳐 sandbox iframe으로 재생. 운영자가 지정한 신뢰 저장소에서만 동작 (Phase 9)

## CDN Service (내부 CDN)

폐쇄망에서 프론트엔드 라이브러리를 서빙하는 정적 파일 서버. Viewer, Marp, XWiki 등 내부 서비스가 공유한다.

### 주요 기능

- **정적 파일 서빙** — JS, CSS, 폰트 등을 HTTP로 서빙
- **파일 업로드** — 웹 UI 드래그앤드롭 + REST API
- **버전 관리** — `/{library}/{version}/{file}` 디렉토리 구조
- **디렉토리 탐색** — 업로드된 파일 목록 조회 및 관리

### 기본 제공 라이브러리

| 라이브러리 | 버전 | 용도 |
|-----------|------|------|
| mermaid | 10.9.1 | 다이어그램 렌더링 |
| highlight.js | — | 코드 구문 강조 |
| Chart.js | 4.4.4 | 차트/그래프 |

## Marp Service (프레젠테이션)

마크다운 문서를 [Marp](https://marp.app/)를 사용하여 PDF 또는 프레젠테이션 HTML로 변환한다.

### 주요 기능

- **PDF 변환 + 다운로드** — Marp CLI로 마크다운 → 슬라이드 PDF 생성, 브라우저에서 다운로드
- **프레젠테이션 HTML 출판** — 마크다운 → 자체 완결형 슬라이드 HTML 생성 → Git 저장소의 지정 경로에 커밋 → Slides Server에서 바로 열람
- **Marp 테마 지원** — 기본 테마(default, gaia, uncover) + 커스텀 테마(CDN에서 CSS 로딩)
- **실시간 미리보기** — 변환 전 슬라이드 미리보기

### Marp 마크다운 예시

````markdown
---
marp: true
theme: gaia
paginate: true
---

# 프로젝트 아키텍처

synctx 시스템 설계 개요

---

## 마이크로서비스 구성

- Viewer Service
- CDN Service
- Marp Service
- Sync Engine

---

## 동기화 흐름

```mermaid
graph LR
  A[GitHub] --> B[GitLab Mirror]
  B --> C[synctx]
  C --> D[XWiki]
  C --> E[OpenProject]
```
````

### 출판 흐름

```mermaid
sequenceDiagram
    actor User as 사용자
    participant Viewer as Viewer Service
    participant Marp as Marp Service
    participant Git as Git 저장소
    participant Slides as Slides Server

    User->>Viewer: .md 파일 열람 → "출판" 클릭
    Viewer->>Marp: POST /api/marp/publish
    Marp->>Marp: 1. Marp CLI로 HTML 생성
    Marp->>Git: 2. 출판 경로에 커밋 (_published/slides/...)
    Slides->>Git: 3. git pull (slides-sync sidecar)
    Marp-->>Viewer: Slides Server URL 반환
    Viewer-->>User: 출판 완료 및 URL 제공 (https://slides.internal/...)
```

## Sync Engine (동기화 엔진)

Git 저장소의 마크다운과 메타데이터를 내부 협업 도구에 자동 반영하는 서비스.
기본적으로 5분 주기의 **CronJob**으로 동작하며, Git 커밋 시 즉각적인 반영을 위해 GitLab 웹훅을 수신하여 동기화를 즉시 트리거하는 **Webhook Receiver**가 함께 구성된다.

### 동기화 대상

| 산출물 | 외부 형태 | 내부 도구 | 동기화 방식 |
|--------|-----------|-----------|-------------|
| 기술 문서 | Markdown | XWiki | REST API (`markdown/1.2`) |
| 이슈/작업 | Markdown + Front Matter | OpenProject | REST API |
| 요건 정의 | Markdown | XWiki / OpenProject | REST API |
| 회의록/의사결정 | Markdown | XWiki | REST API |
| 다이어그램 | Mermaid / draw.io | XWiki (첨부파일) | SVG 변환 → 첨부 API |
| 인터랙티브 HTML | Markdown의 `synctx-html` 블록 + 저장소 HTML 묶음 | XWiki / OpenProject | XWiki html 매크로 안 sandbox iframe, OpenProject는 Viewer 링크 (Phase 9) |

## Draw.io Service (다이어그램 편집기)

[draw.io](https://www.drawio.com/)(diagrams.net)를 폐쇄망에서 셀프호스팅한다. 공식 Docker 이미지(`jgraph/drawio`)를 사용하며, 인터넷 연결 없이 완전히 독립적으로 동작한다.

### 주요 기능

- **다이어그램 작성·편집** — 플로우차트, 시퀀스, ERD, 네트워크, UML 등 모든 draw.io 기능
- **완전 오프라인** — 외부 연결 없이 내부에서 독립 운영
- **파일 저장** — 브라우저 로컬 저장 또는 `.drawio` 파일 다운로드, Git 저장소에 커밋하여 공유
- **이미지 내보내기** — SVG, PNG, PDF 형태로 내보내기
- **Viewer 연동** — Viewer에서 `.drawio` 파일을 감지하면 Draw.io Service로 열기 링크 제공

### 배포

별도 개발 없이 공식 Docker 이미지를 K8s에 배포한다:

```yaml
# 검증한 jgraph/drawio 버전을 내부 레지스트리에 미러링 후 사용
image: registry.internal/jgraph/drawio:<pinned-version>
```

## Slides Server (프레젠테이션 서버)

Marp Service가 출판한 프레젠테이션 HTML 파일을 정적으로 서빙하는 경량 서버.

- Nginx 기반 정적 파일 서버
- 출판된 HTML 파일을 PVC에서 읽어 서빙
- URL 패턴: `https://slides.internal/{repo}/{path}/{제목}.html`

## 주요 개념 (Harness Engineering & OKF)

synctx는 단순한 동기화 도구를 넘어, **AI와 사람이 지식을 공유하는 하네스(Harness)** 역할을 합니다.

- **OKF (Orchestrated Knowledge Flow)**: 지식의 생성(AI/사람) → 저장(Git/Markdown) → 소비(XWiki/OpenProject/Viewer)로 이어지는 흐름을 체계적으로 오케스트레이션합니다. 서로 다른 LLM 간에도 마크다운 문서를 통해 컨텍스트가 끊김 없이 흐릅니다.
- **Harness Engineering**: AI가 쉽게 읽고 쓸 수 있도록 마크다운 문서의 구조(Front Matter, Task List 등)를 규격화합니다. 이 규격화된 문서는 AI에게는 '프롬프트 컨텍스트'가 되고, 사람에게는 '스크럼 보드'나 '기술 문서'가 됩니다.
- **Context Routing**: 마크다운 파일의 메타데이터(Front Matter)를 분석하여, 이슈 트래킹 용도면 **OpenProject**로, 지식 공유 및 설계 용도면 **XWiki**로 자동 라우팅하여 동기화합니다.
- **Harness Validator**: `.context/`, Front Matter, board/issue 관계, 문서 agent-readiness를 검사하여 AI가 만든 산출물이 사람과 내부 도구 모두에서 소비 가능한지 검증합니다.

## 프로젝트 구조 (계획)

### synctx 구현 저장소

```
synctx/
├── README.md
├── Makefile                           # 빌드/테스트/배포 자동화
├── docs/                              # 프로젝트 참고 문서
│   ├── architecture.md
│   ├── production-service-design.md
│   ├── reusable-assets.md
│   └── xwiki-api-reference.md
│
├── services/                          # 마이크로서비스
│   ├── viewer/                        # Viewer Service
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── app.py                 # FastAPI 서버 (라우트 통합, lifespan, 칸반)
│   │       ├── renderer.py            # Markdown → HTML 렌더링
│   │       ├── meeting.py             # 회의 모드 판단·설정 JSON·메모 포함 HTML 조립
│   │       ├── static/meeting/        # 회의 모드 JS(memo-core.js, meeting.js)·CSS
│   │       └── static/style.css       # 프론트엔드 스타일시트
│   │
│   ├── cdn/                           # CDN Service
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       └── app.py                 # FastAPI 서버 (업로드/서빙/관리 통합)
│   │
│   ├── marp/                          # Marp Service
│   │   ├── Dockerfile                 # multi-stage (node + python)
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   └── app.py                 # FastAPI 서버 (변환/출판/미리보기)
│   │   └── tests/
│   │       └── test_marp_api.py       # API 통합 테스트
│   │
│   ├── sync/                          # Sync Engine
│   │   ├── Dockerfile                 # multi-stage (node + python)
│   │   ├── pyproject.toml
│   │   ├── puppeteer-config.json
│   │   ├── src/
│   │   │   ├── sync_cli.py            # CLI 엔트리포인트 (synctx-sync)
│   │   │   ├── sync_engine.py         # 동기화 오케스트레이션 + retry
│   │   │   ├── harness_validator.py   # 하네스 검증 (synctx_common.harness 위임)
│   │   │   ├── state.py               # state.json atomic read/write
│   │   │   ├── lease.py               # 실행 잠금 (O_CREAT|O_EXCL)
│   │   │   ├── sync_converters/mermaid.py # Mermaid → SVG 변환
│   │   │   └── targets/               # 동기화 대상
│   │   │       ├── xwiki.py           # XWiki REST API 클라이언트
│   │   │       └── openproject.py     # OpenProject REST API 클라이언트
│   │   └── tests/                     # 단위/통합 테스트
│   │       ├── test_state.py
│   │       ├── test_sync_engine.py
│   │       ├── test_xwiki_client.py
│   │       └── test_op_client.py
│   │
│   ├── feedback/                      # Outbound Feedback Exporter (Phase 7 선택)
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   ├── feedback_cli.py        # polling/exporter 엔트리포인트
│   │   │   ├── app.py                 # FastAPI 관리 API
│   │   │   ├── exporter.py            # OpenProject 변경 감지
│   │   │   ├── redactor.py            # allowlist/redaction
│   │   │   ├── signer.py              # HMAC 서명 + key rotation
│   │   │   ├── mailer.py              # approved SMTP 발송
│   │   │   └── event_store.py         # event_id 중복 방지와 재전송 상태
│   │   └── tests/                     # 단위 테스트
│   │       ├── test_signer.py
│   │       ├── test_redactor.py
│   │       └── test_event_store.py
│   │
│   ├── drawio/                        # Draw.io Service (외부 이미지)
│   │
│   └── slides/                        # Slides Server (Nginx)
│       ├── nginx.conf
│       └── sync-sidecar.sh
│
├── shared/                            # 서비스 간 공유 라이브러리
│   └── synctx-common/
│       ├── pyproject.toml
│       ├── tests/                     # 단위 테스트
│       │   ├── test_auth.py
│       │   ├── test_frontmatter.py
│       │   ├── test_git_manager.py
│       │   ├── test_harness.py
│       │   └── test_observability.py
│       └── src/synctx_common/
│           ├── config.py              # 공통 설정 스키마 (Pydantic)
│           ├── git_manager.py         # 공통 Git 모듈
│           ├── auth.py                # 공통 인증 헬퍼
│           ├── frontmatter.py         # Front matter 파서 + 검증
│           ├── observability.py       # healthz/readyz/version/metrics
│           ├── harness.py             # Harness Validator 로직
│           └── cli.py                 # CLI 엔트리포인트
│
├── helm/                              # Helm Charts
│   └── synctx/
│       ├── Chart.yaml
│       ├── values.yaml
│       ├── values-production.yaml
│       ├── files/
│       │   ├── nginx.conf             # Slides Nginx 설정
│       │   └── sync-sidecar.sh        # Slides sync sidecar 스크립트
│       └── templates/
│           ├── _helpers.tpl
│           ├── validate.yaml          # 서비스 옵션 의존성 검사 (synctx.validate)
│           ├── NOTES.txt              # 켜진 서비스 요약과 의존성 경고
│           ├── services.yaml          # 전 서비스 Service 리소스
│           ├── configmap.yaml         # .synctx.yaml ConfigMap
│           ├── secret.yaml            # Feedback HMAC/salt/SMTP
│           ├── ingress.yaml
│           ├── viewer-deployment.yaml
│           ├── cdn-deployment.yaml
│           ├── cdn-pvc.yaml
│           ├── marp-deployment.yaml
│           ├── drawio-deployment.yaml
│           ├── sync-cronjob.yaml
│           ├── sync-state-pvc.yaml
│           ├── sync-rbac.yaml
│           ├── sync-networkpolicy.yaml
│           ├── feedback-cronjob.yaml  # Phase 7 선택
│           ├── slides-deployment.yaml
│           ├── slides-configmap.yaml
│           ├── slides-pvc.yaml
│           ├── servicemonitor.yaml
│           └── prometheusrule.yaml
│
├── deploy/
│   └── docker/                        # Docker Compose 배포
│       ├── compose.yaml               # 서비스 정의 (서비스마다 profile)
│       ├── compose.tls.yaml           # TLS일 때 덧붙이는 443 포트·인증서 마운트
│       ├── synctx.env.example         # 옵션·이미지·포트 예시 (복사해서 synctx.env로 사용)
│       ├── secrets.env.example        # 토큰 예시 (복사해서 secrets.env로 사용)
│       └── .gitignore                 # synctx.env, secrets.env, .generated/, tls/ 제외
│
├── cdn-data/                          # CDN 초기 벤더 파일
│   ├── mermaid/10.9.1/
│   ├── highlight.js/
│   └── ...
│
└── scripts/
    ├── docker/                        # Docker Compose 배포 스크립트
    │   ├── deploy.sh                  # 검사 → 게이트웨이 설정 생성 → up → 꺼진 서비스 정리 → CDN 채우기
    │   ├── down.sh                    # 중지 (--volumes일 때만 볼륨 삭제)
    │   ├── compose.sh                 # 배포와 같은 인자로 docker compose 명령 실행
    │   └── seed_cdn.py                # cdn 컨테이너 안에서 vendor 파일 채우기
    ├── download-cdn-vendor.sh         # CDN 벤더 파일 다운로드
    ├── generate-manifest.sh           # SHA-256 artifact manifest 생성
    ├── backup-restore.sh              # PVC 백업/복구
    ├── smoke-test.sh                  # 배포 후 smoke test
    ├── verify-authz.sh                # 권한 매핑 검증
    ├── external_importer.py           # External Mail Importer (Phase 7)
    └── tests/
        └── test_external_importer.py  # Importer 단위 테스트
```

### 사용자 workspace 하네스 규격

```text
workspace/
├── .context/                          # LLM 공유 하네스 컨텍스트
│   ├── system_prompt.md
│   ├── project_state.yaml
│   ├── glossary.md
│   ├── decision-log.md
│   └── blueprints/                    # 작업/문서/요건 작성 표준
├── board/                             # 스프린트/버전 manifest
├── issues/                            # OpenProject Work Package source
├── docs/                              # XWiki source
└── reports/
    └── harness/                       # validate-harness 결과
```

## 기술 스택

| 영역 | 기술 |
|------|------|
| 언어 | Python 3.13, Node.js (Marp CLI) |
| 프론트엔드 | HTML/CSS/Vanilla JS (서버 렌더링 기반, Phase 6 고인터랙션 기능에 한해 React/Vite 별도 번들로 선택 도입) |
| 웹 서버 | FastAPI + Uvicorn |
| Markdown 렌더링 | `markdown` 라이브러리 (서버사이드) |
| 프레젠테이션 | `@marp-team/marp-cli` |
| 다이어그램 | draw.io / diagrams.net (`jgraph/drawio`) |
| HTTP 클라이언트 | `httpx` (비동기 지원) |
| 설정 | YAML (`pyyaml`) |
| Mermaid 변환 | `@mermaid-js/mermaid-cli` (mmdc) |
| PDF 변환 | Marp CLI (슬라이드), Chrome CDP (문서) |
| CDN 파일 관리 | FastAPI + `aiofiles` |
| 정적 서빙 | Nginx (Slides Server) |
| 컨테이너 | Docker |
| 오케스트레이션 | Kubernetes + Helm Chart |
| 동기화 실행 | K8s CronJob / GitLab CI pipeline |

## 기존 자산 활용

| 소스 | 활용 모듈 | 용도 |
|------|-----------|------|
| `markdown_converter` | renderer.py, git_repo.py, config.py, app.py | Viewer Service 핵심 엔진 |
| `example-infra/문서변환` | mermaid_to_svg.py, convert_to_pdf.py | 문서 변환 파이프라인 |
| `example-infra/docsify` | CDN 벤더 파일, download-cdn-vendor.sh, Helm Chart, nginx.conf | CDN 초기 데이터 + Slides Server + 배포 참고 |

상세 자산 목록은 [docs/reusable-assets.md](docs/reusable-assets.md) 참조.

## 문서

| 문서 | 설명 |
|------|------|
| [아키텍처 설계](docs/architecture.md) | 마이크로서비스 설계, Marp, CDN, Helm Chart, 설정 체계 |
| [운영 서비스 설계](docs/production-service-design.md) | 실제 서비스 운영을 위한 보안, 상태 관리, 배포, 체크리스트 |
| [재활용 자산 목록](docs/reusable-assets.md) | 기존 저장소에서 가져올 코드와 활용 방안 |
| [XWiki API 레퍼런스](docs/xwiki-api-reference.md) | XWiki REST API 사용법과 제약 사항 |
