# Production Service Design

이 문서는 synctx를 MVP가 아니라 실제 내부 서비스로 운영하기 위한 상세 설계와 구현 체크리스트를 정의한다.
기본 아키텍처는 [architecture.md](architecture.md)를 따른다. 여기서는 운영 안정성, 보안, 동기화 일관성, 배포 재현성, 단계별 완료 기준을 구체화한다.

## 1. 서비스 가능 범위

### 1.1 목표

첫 운영 버전은 다음을 만족해야 한다.

- 내부 GitLab 미러의 Markdown 저장소를 웹에서 안정적으로 탐색하고 렌더링한다.
- Mermaid, highlight.js, Marp, draw.io를 외부 인터넷 없이 사용할 수 있다.
- Markdown 문서를 XWiki로, 이슈 문서를 OpenProject로 단방향 동기화한다.
- 선택 기능으로 OpenProject의 허용된 메타데이터 변경만 서명된 메일 이벤트로 외부에 전달할 수 있다.
- 동기화 실패가 일부 파일에만 영향을 주며, 다음 실행에서 안전하게 재시도된다.
- 사용자, 관리자, 서비스 계정 권한이 분리된다.
- 모든 외부 의존성은 버전과 해시가 고정되어 폐쇄망 반입이 재현 가능하다.

### 1.2 비목표

다음은 첫 운영 버전에서 제외한다.

- 내부 도구(XWiki/OpenProject)에서 GitHub 또는 외부망으로의 일반 역동기화
- 브라우저 기반 Markdown 편집기
- draw.io 파일의 서버사이드 저장
- 다중 테넌트 과금/조직 관리
- XWiki/OpenProject의 모든 필드 자동 발견과 자동 매핑

제한적 Outbound Feedback은 이 비목표의 예외이지만, Markdown을 직접 수정하거나 외부 저장소에 직접 push하지 않는다.
외부에서는 검증된 메일 이벤트를 기반으로 PR만 생성한다.

## 2. 운영 아키텍처 결정

### 2.1 출판 데이터의 단일 원천

출판된 슬라이드 HTML의 단일 원천은 `publish_repo` Git 저장소다.

- Marp Service는 HTML 생성 후 `publish_repo`에 커밋하고 push한다.
- Slides Server는 PVC에 직접 쓰지 않는다.
- Slides Server는 `slides-sync` sidecar가 `publish_repo`를 pull한 working tree를 읽기 전용으로 서빙한다.
- Marp Service는 push 성공 후 내부 refresh API를 호출한다. refresh 실패 시에도 sidecar의 polling이 최종 반영한다.

이 결정으로 Git 이력과 서빙 상태의 기준이 분리되지 않는다.

### 2.2 동기화 상태 저장소

Sync Engine 상태는 내부 PVC에 저장한다.

```
/data/sync-state/
├── repos/
│   └── {repo_id}/
│       ├── state.json
│       ├── state.json.bak
│       ├── runs/
│       │   └── 2026-06-19T10-30-00Z.json
│       └── failures/
│           └── {run_id}.json
└── locks/
```

상태 파일은 Git 저장소에 커밋하지 않는다. 내부 동기화 상태는 운영 파생 데이터이며 외부 반출 대상이 아니다.

### 2.3 실행 잠금

중복 실행은 세 단계로 막는다.

- K8s CronJob: `concurrencyPolicy: Forbid` (CronJob 스케줄 간의 중복 실행 방지)
- Webhook Receiver: 별도의 경량 **Deployment**로 동작하며, GitLab Push Event 수신 시 Kubernetes API를 호출하여 `synctx-sync` CronJob 기반의 일회성 Job을 즉시 생성한다. (최소 권한 원칙에 따라 Job 생성/조회 권한만 가진 **Webhook 전용 ServiceAccount**를 사용)
- Sync Engine: Kubernetes Lease 기반 per-repo lock
  - **상호작용 (안전망)**: Webhook이 생성한 수동 Job과 CronJob이 생성한 스케줄 Job은 서로 다른 주체에 의해 생성되므로 `concurrencyPolicy: Forbid`가 상호 간의 중복을 막아주지 못한다. 따라서 Webhook Job과 CronJob Job이 동시에 실행되더라도, **최종적으로 Kubernetes Lease를 획득한 하나의 Pod만 동기화를 수행**하고 나머지는 대기 후 종료된다. CronJob은 Webhook이 놓친 변경을 주기적으로 처리하는 안전망(Safety Net) 역할을 한다.

Lease 이름은 `synctx-sync-{repo_id}`다. 수동 실행, CronJob, 재시작 상황이 겹쳐도 같은 repo는 동시에 처리하지 않는다.
Lease 권한이 없는 로컬 개발 환경에서는 파일 lock으로 fallback한다.

### 2.4 상태 갱신 원칙

`last_successful_sha`는 해당 run의 critical item이 모두 성공했을 때만 전진한다.

부분 실패가 있으면 다음 실행도 이전 `last_successful_sha..HEAD` 범위를 다시 계산한다.
이미 성공한 파일은 `synced_files[path].content_sha`가 같으면 skip하므로 중복 호출을 피한다.

### 2.5 대상 리소스 식별자

모든 외부 대상에는 synctx 관리 식별자를 남긴다.

```
synctx_external_key = "{repo_id}:{normalized_path}"
```

- XWiki: 페이지 본문 하단에 HTML comment marker를 기록한다.
- OpenProject: 전용 custom field `synctx_external_key`를 우선 사용한다. custom field를 만들 수 없는 환경에서는 description marker를 fallback으로 사용한다.
- Slides: published HTML에 `meta name="synctx:source"` 값을 기록한다.

### 2.6 제한적 Outbound Feedback

내부 OpenProject에서 티켓 상태가 바뀌었거나 제한된 메타데이터가 변경된 경우, 승인된 메일 게이트웨이를 통해 외부 개발 환경에 변경 이벤트를 전달할 수 있다.
이 기능은 기본 비활성화이며, 운영 보안 검토 후 저장소별로 활성화한다.

데이터 흐름:

```mermaid
sequenceDiagram
    participant OP as OpenProject
    participant Exporter as Outbound Feedback Exporter
    participant Mail as Approved Mail Gateway
    participant Importer as External Mail Importer
    participant Git as External Git Repository

    OP-->>Exporter: 1. webhook 또는 polling으로 변경 감지
    Exporter->>Exporter: 2. synctx_external_key와 allowlist 필드 검증
    Exporter->>Exporter: 3. PII/본문/댓글/첨부 제거 및 HMAC 서명
    Exporter->>Mail: 4. signed JSON event 메일 발송
    Mail-->>Importer: 5. 외부 메일함 수신
    Importer->>Importer: 6. 서명, schema, replay 검증
    Importer->>Git: 7. Markdown 변경 branch/PR 생성
```

원칙:

- OpenProject의 전체 역동기화가 아니라 allowlist 필드의 변경 제안만 반출한다.
- 외부 Importer는 main branch에 직접 push하지 않고 PR만 생성한다.
- PR이 merge된 뒤 기존 외부→내부 Git 동기화가 OpenProject를 다시 갱신한다.
- 제목, description, comment, attachment, 사용자 실명/메일은 기본 반출 금지다.
- 상태 변경 사유가 필요하면 자유 텍스트가 아니라 사전 정의된 reason code만 보낸다.

## 3. 보안 설계

### 3.1 인증 경계

운영 환경은 Ingress 앞단의 사내 SSO/OIDC 또는 reverse proxy 인증을 기본으로 한다.
서비스는 다음 헤더를 신뢰하되, 이 헤더는 Ingress에서만 주입 가능해야 한다.

| 헤더 | 의미 |
|------|------|
| `X-Forwarded-User` | 사용자 ID |
| `X-Forwarded-Email` | 사용자 이메일 |
| `X-Forwarded-Groups` | 그룹 목록 |
| `X-Request-Id` | 요청 추적 ID |

Ingress 우회 접근은 NetworkPolicy로 차단한다.

#### 3.1.1 Docker 게이트웨이의 인증 경계

Docker Compose 배포에서는 `gateway`(nginx)가 Ingress 역할을 한다. 외부 포트는 게이트웨이만 열고, 나머지 컨테이너는 compose 네트워크 안에서만 닿는다.

- 권한 헤더(`X-Forwarded-User`, `X-Forwarded-Email`, `X-Forwarded-Groups`)는 기본적으로 빈 값으로 덮어 지운다. 게이트웨이에 직접 닿는 사용자가 관리자 그룹을 주장하지 못하게 하기 위해서다.
- 앞단 인증 프록시가 이 헤더를 넣어 줄 때만 `SYNCTX_TRUSTED_AUTH_PROXY=true`로 둔다. 신뢰 모드는 헤더를 넘기는 대신 `SYNCTX_TRUSTED_PROXY_CIDRS`에 든 출발지만 받고(nginx `allow`/`deny all`) 나머지는 403이다.
  - `SYNCTX_TRUSTED_PROXY_CIDRS`가 비어 있거나 `0.0.0.0/0`처럼 접두사 길이 0인 값이면 배포를 멈춘다. 형식이 잘못된 값(IPv4 옥텟·접두사 범위 초과)도 멈춘다.
  - `SYNCTX_BIND_ADDRESS`가 `0.0.0.0`(또는 `::`)이면 배포를 멈춘다. 게이트웨이 포트는 인증 프록시만 닿아야 한다(프록시 전용 인터페이스에 바인딩하고 그 밖의 접근은 방화벽으로 막는다).
  - **게이트웨이 접근 로그의 출발지가 Docker 브리지 게이트웨이(예: `172.18.0.1`)로 보이면 그 주소를 허용 목록에 넣지 않는다.** 그 주소는 같은 호스트의 모든 프로세스와 포트 공개 경로(루프백 바인딩, userland-proxy, Docker Desktop)가 함께 쓰므로 허용 목록으로 프록시와 직접 접속을 구분할 수 없다. 그래서 루프백(`127.0.0.1`)에 바인딩하고 호스트의 프록시를 허용하는 구성은 쓸 수 없다.
  - 동작하는 구성은 아래 둘이다.

  **구성 1. 인증 프록시가 다른 호스트에 있을 때.** 공개 포트의 DNAT는 원래 출발지를 유지하므로 프록시 IP를 그대로 허용한다.

  ```bash
  # synctx.env (Docker 서버의 프록시 쪽 인터페이스 10.0.0.5, 인증 프록시 10.0.1.20)
  SYNCTX_BIND_ADDRESS=10.0.0.5
  SYNCTX_TRUSTED_AUTH_PROXY=true
  SYNCTX_TRUSTED_PROXY_CIDRS=10.0.1.20
  ```

  방화벽에서 게이트웨이 포트(80/443 또는 `SYNCTX_HTTP_PORT`·`SYNCTX_HTTPS_PORT`)를 인증 프록시 출발지만 받게 한다. Docker가 공개한 포트는 호스트 INPUT 규칙을 거치지 않으므로 `DOCKER-USER` 체인이나 앞단 네트워크 방화벽에서 막는다.

  **구성 2. 인증 프록시를 compose 네트워크 안의 컨테이너로 띄울 때.** `SYNCTX_EXTRA_COMPOSE_FILES`로 넘기는 오버라이드 파일에서 네트워크 대역과 프록시 주소를 고정하고 그 주소만 허용한다.

  ```yaml
  # auth-proxy.override.yaml
  networks:
    default:
      ipam:
        config:
          - subnet: 172.30.0.0/24
  services:
    auth-proxy:
      image: registry.local:5000/<사내 인증 프록시 이미지>
      ports:
        - "443:8443"
      networks:
        default:
          ipv4_address: 172.30.0.10
  ```

  ```bash
  # synctx.env
  SYNCTX_EXTRA_COMPOSE_FILES=./auth-proxy.override.yaml
  SYNCTX_BIND_ADDRESS=127.0.0.1
  SYNCTX_TRUSTED_AUTH_PROXY=true
  SYNCTX_TRUSTED_PROXY_CIDRS=172.30.0.10
  ```

  사용자는 프록시 포트로만 들어온다. 프록시는 `http://gateway`로 넘긴다. 게이트웨이 공개 포트는 쓰지 않으므로 루프백에 묶어 두며, 이 포트로 들어온 요청은 출발지가 브리지 게이트웨이(`172.30.0.1`)로 보여 403이다.
- 인증 프록시가 없으면 `.synctx.yaml`에 `authz.enabled: false`를 둔다(기본값 `true`라 모든 요청이 403).

### 3.2 역할

| 역할 | 권한 |
|------|------|
| `viewer` | 저장소 목록, 문서 렌더링, 파일 다운로드 |
| `publisher` | Marp PDF/HTML 변환, 슬라이드 출판 |
| `cdn-admin` | CDN 파일 업로드, 삭제, manifest 갱신 |
| `sync-admin` | dry-run 실행, 수동 sync 실행, 실패 재처리 |
| `feedback-admin` | Outbound Feedback dry-run, 재전송, allowlist 검토 |
| `platform-admin` | 설정 변경, Secret 연결, 배포 관리 |
| `service` | 내부 서비스 간 refresh/webhook 호출 |

역할 매핑은 `.synctx.yaml`에 그룹명으로 정의한다. `role_mappings`의 키는 위 역할 이름의 하이픈을 밑줄로 바꾼 형태(`cdn_admin`)다. 하이픈으로 쓴 키는 오류 없이 무시되어 그 역할이 빈 목록이 된다.

```yaml
authz:
  group_header: X-Forwarded-Groups
  role_mappings:
    viewer: ["dev-team", "biz-team"]
    publisher: ["dev-team"]
    cdn_admin: ["platform-team"]
    sync_admin: ["platform-team"]
    feedback_admin: ["platform-team"]
    platform_admin: ["platform-admin"]
```

### 3.3 서비스 간 인증

내부 API는 별도 service token을 요구한다.

- `POST /api/repos/{id}/webhook`
- `POST /internal/slides/refresh`

토큰은 K8s Secret에서 주입하며, ConfigMap에 저장하지 않는다.

### 3.4 Secret 관리

| Secret | 사용처 | 권한 |
|--------|--------|------|
| `GIT_READ_TOKEN` | Viewer, Sync Engine, Marp | source repo read |
| `GIT_PUBLISH_TOKEN` | Marp, slides-sync | publish repo write/read |
| `GITLAB_WEBHOOK_SECRET` | Sync Webhook Receiver | GitLab push event 검증 |
| `XWIKI_CREDENTIAL` | Sync Engine | page/attachment write |
| `XWIKI_HTML_CREDENTIAL` | Sync Engine | `html_embed.enabled` 저장소 전용 XWiki 계정. page/attachment write + 대상 공간 한정 script right (`XWIKI_CREDENTIAL`과 다른 계정) |
| `OPENPROJECT_API_KEY` | Sync Engine | work package write |
| `OUTBOUND_SMTP_CREDENTIAL` | Outbound Feedback Exporter | approved mail gateway send |
| `OUTBOUND_FEEDBACK_HMAC_KEY` | Outbound Feedback Exporter, External Importer | event signature |
| `OUTBOUND_FEEDBACK_HASH_SALT` | Outbound Feedback Exporter | changed_by pseudonymization |
| `SYNCTX_SERVICE_TOKEN` | internal API | service-to-service |

모든 Secret은 env 또는 mounted file로만 전달한다. 로그에는 credential 값과 credential이 포함된 URL을 출력하지 않는다.

### 3.5 업로드 보안

CDN 업로드는 운영에서 `cdn-admin` 권한을 요구하는 방식으로 활성화한다. 운영 기본값은 `enabled: true`와 `required_role: cdn-admin`이다.

업로드 검증 규칙:

- 경로는 `library/version/file` 형식만 허용한다.
- `..`, 절대 경로, 숨김 파일, 제어 문자를 거부한다.
- 허용 확장자와 MIME type을 둘 다 검사한다.
- 동일 version 파일은 기본적으로 immutable이다. 교체는 `force=true`와 admin audit log가 필요하다.
- 업로드 후 `manifest.json`에 SHA-256을 기록한다.

### 3.6 인터랙티브 HTML 임베드 보안

- 신뢰 여부는 운영자 설정 `repositories[].html_embed.enabled`에서만 켠다. 저장소 안 `.synctx.yaml`로는 켤 수 없다.
- 합본은 `sandbox="allow-scripts"` srcdoc iframe으로만 넣는다. `allow-same-origin` 등은 붙이지 않으므로 출처가 `null`이라 Viewer·XWiki 세션에 접근할 수 없다.
- 합본 `<head>` 맨 앞에 CSP meta를 넣어 실행 중 네트워크를 `allowed_origins`로 제한한다. 저장소 목록은 최상위 `html_embed.allowed_origins`의 부분집합이어야 하고, Viewer 부모 CSP는 최상위 목록과 `font-src data:`를 포함한다.
- XWiki에는 블록당 `{{html clean="false"}}` 매크로 하나만 넣고, 매크로 본문의 `{` `}`는 모두 엔티티로 바꾼다.
- 스크립트 권한은 신뢰 저장소 전용 XWiki 계정(`repositories[].html_embed.xwiki_credential_env`)에만 대상 공간 한정으로 준다. 기본 동기화 계정에는 주지 않는다. XWiki가 작성자 권한으로 매크로를 실행하므로, 비신뢰 저장소가 본문에 직접 쓴 매크로는 기본 계정 권한으로 걸러진다. 프로그래밍 권한과 `attachment.download.whitelist` 변경은 하지 않는다.
- 잔여 위험: iframe 자신의 주소 이동(`location.href`)과 WebRTC는 CSP·sandbox로 막을 수 없다. 신뢰 저장소 지정으로만 통제한다.

## 4. 설정 스키마

`.synctx.yaml`은 Pydantic 모델로 검증한다. 부팅 시 검증 실패하면 서비스는 ready 상태가 되지 않는다.

```yaml
synctx:
  version: "1"

runtime:
  environment: production
  public_base_url: "https://synctx.internal"
  request_id_header: X-Request-Id

authz:
  enabled: true
  group_header: X-Forwarded-Groups
  role_mappings:
    viewer: ["dev-team", "biz-team"]
    publisher: ["dev-team"]
    cdn_admin: ["platform-team"]
    sync_admin: ["platform-team"]
    feedback_admin: ["platform-team"]
    platform_admin: ["platform-admin"]

html_embed:
  allowed_origins: []

repositories:
  - id: project-docs
    name: "Team Docs"
    url: "https://gitlab.internal/team/docs.git"
    default_ref: main
    auth:
      type: token
      credential_env: GIT_READ_TOKEN
    watch_paths:
      - "docs/**/*.md"
      - "issues/**/*.md"
      - "slides/**/*.md"
      - "diagrams/**/*.drawio"
    ignore_paths:
      - "docs/drafts/**"
      - "_published/**"
      - ".context/**"
    collab:
      enabled: false   # 모든 Viewer 사용자에게 공개 가능한 저장소에만 true (회의 모드·협업 연동 대상)
    html_embed:
      enabled: false
      allowed_origins: []
      max_bundle_bytes: 2097152
      xwiki_credential_env: null   # enabled: true이고 XWiki가 대상이면 필수(예: XWIKI_HTML_CREDENTIAL), 기본 동기화 계정과 달라야 함

mappings:
  - match:
      path_prefix: "docs/"
      extensions: [".md"]
    target: xwiki
    xwiki:
      space_from: directory
      page_from: filename
  - match:
      path_prefix: "issues/"
      extensions: [".md"]
    target: openproject
    openproject:
      project: example-project

targets:
  xwiki:
    base_url: "https://wiki.internal"
    wiki: xwiki
    syntax: markdown/1.2
    auth:
      type: basic
      credential_env: XWIKI_CREDENTIAL
    concurrency: 5
    timeout_seconds: 30
    retry:
      max_attempts: 3
      backoff_seconds: [1, 5, 15]
  openproject:
    base_url: "https://op.internal"
    auth:
      type: token
      credential_env: OPENPROJECT_API_KEY
    external_key_custom_field: synctx_external_key
    protected_fields: ["assignee"]
    type_map:
      task: 1
      bug: 2
      requirement: 3
    status_map:
      open: 1
      in_progress: 7
      blocked: 8
      done: 12
    priority_map:
      low: 7
      normal: 8
      high: 9
      urgent: 10

viewer:
  repo_cache_path: "/data/repos"
  max_render_size_bytes: 5242880
  cdn_base_url: "https://synctx.internal/cdn"

cdn:
  storage_path: "/data/cdn"
  max_upload_size_bytes: 50000000  # 50 MB
  upload_auth:
    enabled: true
    required_role: cdn-admin
  allowed_extensions: [".js", ".css", ".woff", ".woff2", ".ttf", ".svg", ".png", ".map"]

marp:
  publish_repo: "https://gitlab.internal/team/published-slides.git"
  publish_branch: main
  publish_base_path: "_published/slides"
  worktree_path: "/data/marp/publish-worktree"
  conversion_timeout_seconds: 120
  max_input_size_bytes: 10485760

slides:
  worktree_path: "/data/slides/worktree"
  refresh_mode: webhook_and_polling
  polling_interval_seconds: 60

sync:
  state_path: "/data/sync-state"
  lease_namespace: synctx
  schedule: "*/5 * * * *"
  on_delete: mark
  force_push_policy: full_rescan
  dry_run: false

harness:
  context_path: ".context"
  reports_path: "reports/harness"
  required_context_files:
    - system_prompt.md
    - project_state.yaml
    - glossary.md
    - decision-log.md
  blueprint_path: ".context/blueprints"
  agent_readiness:
    require_title: true
    require_summary: true
    require_decisions_section: true
    require_todo_owner: true
    check_links: true
    check_mermaid: true

outbound_feedback:
  enabled: false
  transport: email
  mode: pr_only
  change_detection: polling
  polling_interval_seconds: 300
  allowed_targets: [openproject]
  allowed_fields: [status, priority, version, due_date]
  include_subject: false
  include_description: false
  include_comments: false
  include_attachments: false
  user_identity: hashed
  require_hmac_signature: true
  replay_window_minutes: 1440
  outbound_mail:
    smtp_host: "smtp-relay.internal"
    smtp_port: 587
    credential_env: OUTBOUND_SMTP_CREDENTIAL
    from: "synctx-feedback@internal"
    to: "synctx-feedback-import@example.com"
```

`html_embed`의 출처는 `https://호스트[:포트]`만 허용하고, 저장소 목록은 최상위 목록의 부분집합이어야 한다. 어기면 서비스가 시작되지 않는다. 신뢰 저장소(`enabled: true`)는 운영자만 지정한다(3.6).

`force_push_policy` 값은 다음 중 하나다.

| 값 | 동작 |
|----|------|
| `full_rescan` | `last_successful_sha`가 현재 HEAD의 ancestor가 아니면 watch path 전체를 재평가한다. 운영 기본값 |
| `fail` | force-push를 감지하면 run을 실패 처리하고 운영자 개입을 요구한다 |
| `ignore` | force-push를 무시하고 현재 state 기준으로 계속 진행한다. 운영 사용 금지 |

`outbound_feedback`은 운영 기본값이 `enabled: false`다.
활성화 시 `allowed_fields`에 없는 필드는 메일 payload에 포함하지 않고, `include_description/comments/attachments`는 운영에서 `false`만 허용한다.
`change_detection`은 `polling` 또는 `webhook` 중 하나다. OpenProject webhook 사용 가능 여부를 운영 환경에서 검증하기 전까지 기본값은 `polling`이다.

외부 Importer는 외부망에서 동작하는 별도 서비스이므로 내부 `.synctx.yaml`의 실행 설정에 포함하지 않는다.
다만 외부 Importer에는 다음 권장 정책을 별도 설정으로 적용한다.

```yaml
external_importer_policy:
  create_pr: true
  direct_push: false
  branch_prefix: "synctx-feedback/"
  reject_unsigned_event: true
  reject_duplicate_event_id: true
```

### 4.1 운영 환경별 필수 ID 매핑 표

설정 파일의 `targets.openproject` 및 `targets.xwiki`에 들어갈 ID 값들은 운영 환경(OpenProject/XWiki 인스턴스)에 따라 다르므로 사전 확인이 필요하다.

**OpenProject 매핑 (예시)**

| 항목 | 설정 경로 | 운영 환경 ID (예시) | 설명 |
|------|-----------|-------------------|------|
| External Key | `external_key_custom_field` | `customField1` | synctx_external_key 용도로 생성한 Custom Field ID |
| Type: Task | `type_map.task` | `1` | Task 유형 ID |
| Type: Bug | `type_map.bug` | `2` | Bug 유형 ID |
| Type: Requirement | `type_map.requirement` | `3` | Requirement 유형 ID |
| Status: Open | `status_map.open` | `1` | 신규 상태 ID |
| Status: In Progress | `status_map.in_progress` | `7` | 진행 중 상태 ID |
| Status: Done | `status_map.done` | `12` | 완료 상태 ID |

**XWiki 매핑 (예시)**

| 항목 | 설정 경로 | 운영 환경 값 (예시) | 설명 |
|------|-----------|-------------------|------|
| Syntax | `targets.xwiki.syntax` | `markdown/1.2` | XWiki에 Markdown Syntax Extension이 설치되어 있어야 함 |

## 5. Front Matter 스키마

### 5.1 공통 규칙

- `synctx.target`은 `xwiki`, `openproject`, `ignore` 중 하나다.
- front matter가 없으면 path mapping을 적용한다.
- front matter와 path mapping이 충돌하면 front matter가 우선한다.
- 검증 실패 파일은 skip하고 run 결과에 `validation_error`로 기록한다.
- 필수 필드가 없을 때 자동 추론 가능한 값은 dry-run 경고를 남긴다.

### 5.2 XWiki 문서

```yaml
---
synctx:
  target: xwiki
  space: "아키텍처/백엔드"
  page: "시스템 설계"
  title: "시스템 설계"
  tags: ["architecture", "backend"]
  attachments:
    - "images/context.svg"
  delete_policy: mark
---
```

필드:

| 필드 | 필수 | 설명 |
|------|------|------|
| `space` | 선택 | 없으면 `docs/` 아래 디렉토리로 추론 |
| `page` | 선택 | 없으면 파일명으로 추론 |
| `title` | 선택 | 없으면 첫 번째 H1, 없으면 page |
| `tags` | 선택 | XWiki 태그 또는 본문 marker에 기록 |
| `attachments` | 선택 | 상대 경로 파일을 첨부파일로 업로드 |
| `delete_policy` | 선택 | `mark`, `delete`, `ignore` |

### 5.3 OpenProject 이슈

```yaml
---
synctx:
  target: openproject
  project: example-project
  type: task
  subject: "요건 분석 정리"
  status: in_progress
  priority: normal
  assignee: "hong.gildong"
  version: "Sprint 1"
  due_date: "2026-07-04"
---
```

필드:

| 필드 | 필수 | 설명 |
|------|------|------|
| `project` | 필수 | OpenProject project identifier |
| `type` | 필수 | `type_map`으로 OpenProject type id에 매핑 |
| `subject` | 선택 | 없으면 첫 번째 H1, 없으면 파일명 |
| `status` | 선택 | 없으면 `open` |
| `priority` | 선택 | 없으면 `normal` |
| `assignee` | 선택 | 사용자 매핑 테이블 또는 login/email로 해석 |
| `version` | 선택 | version name 또는 id |
| `due_date` | 선택 | ISO date |

OpenProject 중복 방지는 `synctx_external_key` custom field로 한다.

Version(스프린트) 연결의 source of truth는 Task 문서의 `synctx.version` 필드다.
Version 문서의 `issues` 목록은 Viewer의 보드 렌더링과 dry-run 검증용 manifest로만 사용하며, Sync Engine은 이 목록만 보고 Task의 OpenProject version을 할당하지 않는다.
Task가 여러 Version 문서에 동시에 포함되어도 `synctx.version` 값만 OpenProject Work Package의 version 연결에 반영한다.

### 5.4 Marp 슬라이드

```yaml
---
marp: true
theme: gaia
paginate: true
synctx:
  target: slides
  publish_path: "architecture/system-design.html"
  visibility: internal
---
```

`publish_path`는 저장소별 namespace 아래에 배치된다.

```
_published/slides/{repo_id}/{publish_path}
```

### 5.5 회의 문서

```yaml
---
synctx:
  type: meeting
  meeting:
    id: dx-ic-21-review-2026-10-08   # 필수, 저장소 안에서 유일
    title: "팀 문서 21번 검토"     # 필수
    starts_at: 2026-10-08T14:00+09:00 # 필수, 타임존 포함
    duration_minutes: 60              # 1~1440
    channel: team-docs      # 필수, 형식만 검사(매핑은 Collab Gateway)
    presenters: [sch0718]             # 선택
    cancelled: false                  # 선택
---
```

- Harness Validator가 필드 형식과 `meeting.id` 중복을 검사한다.
- Viewer 회의 모드는 `repositories[].collab.enabled: true`인 저장소의 기본 브랜치에서만 켜진다.

## 6. Sync Engine 상세 설계

### 6.1 실행 흐름

```mermaid
flowchart TD
    A["1. config 로드 및 schema 검증"] --> B["2. repo별 Lease 획득"]
    B --> C["3. repo fetch"]
    C --> D["4. state.json 로드"]
    D --> E["5. 변경 범위 계산"]
    E --> F["6. 파일별 front matter 검증"]
    F --> G["7. 대상 리소스 계획 생성"]
    G --> H{"8. dry-run?"}
    H -- Yes --> I["plan 출력 후 종료"]
    H -- No --> J["9. XWiki/OpenProject 호출"]
    J --> K["10. 파일별 결과 기록"]
    K --> L{"11. critical failure?"}
    L -- No --> M["last_successful_sha 갱신"]
    L -- Yes --> N["12. run report 저장 및 metrics 발행"]
    M --> N
    I --> O["13. Lease 반납"]
    N --> O
```

### 6.2 변경 감지

기본 명령:

```bash
git diff --name-status -M <last_successful_sha>..HEAD -- <watch_paths>
```

처리 규칙:

| 상태 | 처리 |
|------|------|
| `A` | 새 target resource 생성 |
| `M` | 기존 target resource 업데이트 |
| `D` | delete policy에 따라 mark/delete/ignore (아래 참고) |
| `R` | old resource mark 후 new resource upsert. target key가 동일하면 update |

**delete policy 대상별 동작:**

| policy | XWiki | OpenProject |
|--------|-------|-------------|
| `ignore` | 삭제 무시 | 삭제 무시 |
| `mark` | 삭제 무시 (XWiki에 mark 개념 없음) | Work Package를 `status_map.closed` 상태로 변경 |
| `delete` | XWiki 페이지 삭제 | `mark`로 fallback (Work Package 삭제 시 감사 로그·링크·히스토리가 손실되므로 의도적으로 mark 처리) |

`last_successful_sha`가 현재 HEAD의 ancestor가 아니면 force-push로 판단하고 full rescan을 수행한다.

### 6.3 idempotent upsert

XWiki:

```mermaid
sequenceDiagram
    participant Sync as Sync Engine
    participant XWiki as XWiki API

    Sync->>XWiki: 1. external marker로 기존 페이지 확인
    alt 페이지 없음
        Sync->>XWiki: 2. mapping된 URL로 GET 확인
    end
    Sync->>XWiki: 3. PUT으로 page upsert
    Sync->>Sync: 4. 첨부파일 SHA-256 비교
    opt SHA-256 다름
        Sync->>XWiki: PUT으로 첨부파일 업로드
    end
```

OpenProject:

```mermaid
sequenceDiagram
    participant Sync as Sync Engine
    participant OP as OpenProject API

    Sync->>OP: 1. custom field 'synctx_external_key'로 검색
    alt Work Package 없음
        Sync->>OP: 2. 새 Work Package 생성
    else Work Package 있음
        Sync->>OP: 3. mapped fields만 PATCH 업데이트
    end
    Note over Sync, OP: 4. 내부 수정 필드는 다음 sync에서 덮어씀 (protected_fields 제외)
    Note over Sync, OP: 5. Version 연결은 Task 문서의 synctx.version 값만 사용
```

`protected_fields`는 `.synctx.yaml`의 `targets.openproject.protected_fields`에 정의한다.
예를 들어 `assignee`가 protected이면 최초 생성 때만 설정하고 이후 내부 OpenProject 변경을 덮어쓰지 않는다.

### 6.4 실패 분류

| 분류 | 예 | 재시도 | state advance |
|------|----|--------|---------------|
| `validation_error` | front matter invalid | 아니오 | 가능, 파일은 skipped로 기록 |
| `transient_error` | 429, 5xx, timeout | 예 | 불가 |
| `auth_error` | 401, 403 | 아니오 | 불가 |
| `mapping_error` | unknown status/type | 아니오 | 불가 |
| `conflict_error` | duplicate external key | 아니오 | 불가 |

validation error는 운영자가 수정할 수 있도록 run report와 metrics에 남긴다.

### 6.5 상태 파일 예시

상태 파일은 atomic write로 갱신한다.

1. 기존 `state.json`을 `state.json.bak`으로 복사한다.
2. 새 상태를 같은 디렉토리의 임시 파일 `state.json.tmp.{pid}`에 쓴다.
3. 파일 내용을 `fsync`한다.
4. 부모 디렉토리를 `fsync`한다.
5. `os.replace(tmp, state.json)`로 rename한다.
6. rename 후 부모 디렉토리를 다시 `fsync`한다.

읽기 시 `state.json`이 JSON 파싱에 실패하면 `state.json.bak`을 읽고 `state_recovered_from_backup` 이벤트를 남긴다.

```json
{
  "schema_version": 1,
  "repo_id": "project-docs",
  "last_successful_sha": "abc1234",
  "last_run_id": "2026-06-19T10-30-00Z",
  "synced_files": {
    "docs/architecture/system-design.md": {
      "content_sha": "sha256:...",
      "target": "xwiki",
      "external_key": "project-docs:docs/architecture/system-design.md",
      "resource_id": "xwiki:아키텍처.시스템-설계",
      "last_synced_sha": "def5678",
      "last_synced_at": "2026-06-19T10:30:00Z"
    }
  },
  "pending_failures": [
    {
      "path": "issues/TASK-001.md",
      "error_type": "mapping_error",
      "message": "unknown status: review"
    }
  ]
}
```

## 7. Service API 설계

### 7.1 공통 endpoint

모든 FastAPI 서비스는 다음 endpoint를 제공한다.

| Endpoint | 용도 |
|----------|------|
| `GET /healthz` | 프로세스 생존 확인 |
| `GET /readyz` | 설정, 필수 의존성, 저장소 접근 준비 확인 |
| `GET /metrics` | Prometheus metrics |
| `GET /version` | git sha, build time, dependency manifest |

### 7.2 Viewer Service

| Method | Path | 권한 | 설명 |
|--------|------|------|------|
| GET | `/api/repos` | viewer | 접근 가능한 repo 목록 |
| GET | `/api/repos/{repo_id}/tree?ref=&path=` | viewer | Git tree 조회 |
| GET | `/api/repos/{repo_id}/files?ref=` | viewer | 볼 수 있는 파일 경로 목록(`{"files": [...]}`, 파일 찾기용). 조회 실패 502 |
| GET | `/api/repos/{repo_id}/render?ref=&path=` | viewer | Markdown HTML 렌더링 |
| GET | `/api/repos/{repo_id}/raw?ref=&path=` | viewer | 원본 파일 다운로드 |
| GET | `/api/repos/{repo_id}/download?format=html|pdf` | viewer | 변환 다운로드 |
| GET | `/api/repos/{repo_id}/meeting-export?path=&theme=` | viewer | 회의 메모 포함 HTML 틀(오프라인, 메모 없음). 회의 모드 조건 불충족 404 |
| POST | `/api/repos/{repo_id}/webhook` | service | Git push 알림 |

보안:

- `path`는 repo root 밖으로 벗어날 수 없다.
- 렌더링 최대 파일 크기를 넘으면 413을 반환한다.
- 화면 경로(`/`, `/{repo_id}`, `/{repo_id}/{path}`)의 오류(404·413·415·502·500)는 HTML 안내 화면으로, `/api/...`·`/raw/...` 경로는 JSON으로 돌려준다. HTML 안내 화면과 파일 목록 API의 502 응답에는 예외 메시지를 넣지 않는다. 그 밖의 JSON API 응답의 `detail`에 예외 메시지를 넣는 부분은 별도 보안 작업에서 다룬다.
- HTML은 sanitization 또는 신뢰 저장소 정책 중 하나를 명시한다. 운영 기본값은 신뢰 저장소 + CSP다.
- `synctx-html` 블록은 sanitizer를 거친 뒤 자리표시를 sandbox iframe으로 채운다. 본문에 직접 쓴 `<iframe>`·`<script>`는 계속 지운다. CSP는 `font-src`에 `data:`, 각 지시어에 최상위 `html_embed.allowed_origins`를 더한다.
- 회의 메모는 서버로 보내지 않는다. 브라우저 localStorage에만 저장하고, `<script>` 안 JSON은 `<`·`>`·`&`·U+2028·U+2029를 유니코드 이스케이프로 인코딩한다.

### 7.3 CDN Service

| Method | Path | 권한 | 설명 |
|--------|------|------|------|
| GET | `/cdn/{path}` | viewer | 정적 파일 서빙 |
| GET | `/api/cdn/manifest` | viewer | 라이브러리 manifest |
| POST | `/api/cdn/upload?force=false` | cdn-admin | 파일 업로드. 기존 파일 교체는 `force=true` 필요 |
| DELETE | `/api/cdn/{path}` | cdn-admin | 파일 삭제 또는 disable |

운영 기본값:

- version path는 immutable이다.
- 기존 파일이 있고 `force=false`이면 409를 반환한다.
- `force=true`는 `cdn-admin` 권한, 변경 사유, audit log를 요구한다.
- 삭제는 실제 파일 삭제 대신 manifest에서 disabled 처리한다.
- `/cdn/latest` alias는 운영 사용을 금지하고 개발 환경에서만 허용한다.
- 업로드 크기 제한: 기본 50 MB (`max_upload_size_bytes: 50000000`).

`manifest.json` 예시:

```json
{
  "schema_version": 1,
  "generated_at": "2026-06-19T10:30:00Z",
  "libraries": {
    "mermaid": {
      "10.9.1": {
        "files": {
          "mermaid.min.js": {
            "sha256": "sha256:...",
            "size": 2890123,
            "content_type": "application/javascript",
            "uploaded_by": "platform-admin",
            "uploaded_at": "2026-06-19T10:30:00Z",
            "disabled": false
          }
        }
      }
    }
  }
}
```

### 7.4 Marp Service

| Method | Path | 권한 | 설명 |
|--------|------|------|------|
| POST | `/api/marp/convert` | publisher | PDF/HTML 변환 |
| POST | `/api/marp/publish` | publisher | HTML 생성 후 publish repo push |
| GET | `/api/marp/themes` | viewer | 사용 가능 테마 |

출판 규칙:

- publish 작업은 repo별 Kubernetes Lease lock을 획득한다. Lease 이름은 `synctx-marp-publish-{repo_id}`다.
- 로컬 개발 환경에서는 publish worktree 안의 `.synctx-publish.lock` 파일 lock으로 fallback한다.
- push 실패가 non-fast-forward이면 fetch/rebase 후 1회 재시도한다.
- 같은 `publish_path`는 새 commit으로 갱신한다.
- 응답 `url`은 `/slides/<이름>.html`이다. 받은 `filename`에 `.html`이 없으면 붙이고, 저장 경로·커밋 메시지·`url`에 같은 이름을 쓴다.

### 7.5 Sync Webhook & Admin API

Sync Engine은 기본적으로 CLI와 CronJob으로 동작하며, 즉시 동기화를 위한 Webhook Receiver를 별도로 운영한다.

| Method | Path | 권한 | 설명 |
|--------|------|------|------|
| POST | `/api/sync/webhook/gitlab` | service | GitLab Push Event 수신 및 Sync Job 생성 |

Webhook Receiver 정책:

- 별도의 경량 Deployment로 동작하며, 최소 권한 원칙(Least Privilege)에 따라 Job 생성/조회 권한만 가진 Webhook 전용 ServiceAccount를 사용한다.
- GitLab `X-Gitlab-Token` 헤더를 `GITLAB_WEBHOOK_SECRET`과 비교하여 검증한다.
- Push 이벤트 수신 시 Kubernetes API를 호출하여 `synctx-sync` CronJob 기반의 일회성 Job을 생성한다.
- 이미 실행 중인 Sync Job이 있으면 새 Job을 생성하지 않고 200 OK(Ignored)를 반환한다.

수동 실행이 필요하면 `kubectl create job --from=cronjob/synctx-sync`를 사용한다.
운영 UI/API는 Phase 4 이후 추가한다.

### 7.6 Outbound Feedback Exporter

Outbound Feedback Exporter는 OpenProject 변경 중 외부 개발 흐름에 전달해도 되는 메타데이터만 메일 이벤트로 내보내는 선택 서비스다.
기본 배포는 비활성화하며, 활성화 시에도 저장소별 allowlist를 요구한다.

| Method | Path | 권한 | 설명 |
|--------|------|------|------|
| GET | `/api/feedback/events?repo_id=&since=` | feedback-admin | 최근 outbound event 조회 |
| POST | `/api/feedback/dry-run` | feedback-admin | 반출 대상 변경과 redaction 결과 미리보기 |
| POST | `/api/feedback/resend/{event_id}` | feedback-admin | 실패한 이벤트 재전송 |

운영 정책:

- `synctx_external_key`가 없는 Work Package는 반출하지 않는다.
- `allowed_fields` 외 변경은 event summary에 `ignored_fields`로만 기록하고 값은 제거한다.
- 메일 제목은 고정 prefix와 `event_id`만 포함한다.
- 메일 본문은 JSON payload와 detached HMAC signature만 포함한다.
- 동일 `event_id`는 한 번만 처리한다. 외부 Importer는 처리한 `event_id`를 저장해 replay를 차단한다.
- 외부 Importer는 Markdown 수정 branch와 PR을 생성하고, PR 본문에 원본 메일 event id와 검증 결과를 기록한다.
- HMAC key는 `key_id`로 식별한다. Exporter와 외부 Importer는 active key와 previous key를 동시에 보관하며, key rotation 기간에는 previous key로 서명된 이벤트도 검증만 허용한다.
- key rotation overlap은 `replay_window_minutes`보다 길어야 한다. 예를 들어 replay window가 1440분이면 previous key는 최소 24시간 이상 검증 가능 상태로 유지한다.
- 외부 Importer 쪽 HMAC secret은 운영자가 승인된 외부 secret store에 수동 주입한다. 메일 본문이나 Git 저장소에는 key material을 절대 포함하지 않는다.
- `changed_by`는 OpenProject 사용자 ID 또는 login을 그대로 보내지 않고, `OUTBOUND_FEEDBACK_HMAC_KEY`와 별도의 `OUTBOUND_FEEDBACK_HASH_SALT`로 HMAC-SHA256 처리한 pseudonymous identifier로 기록한다. salt 없이 단순 SHA-256을 사용하지 않는다.

이벤트 payload 예시:

```json
{
  "schema_version": 1,
  "event_id": "op-feedback-20260619-000123",
  "repo_id": "project-docs",
  "external_key": "project-docs:issues/TASK-001.md",
  "source": "openproject",
  "event_type": "work_package.updated",
  "changed_at": "2026-06-19T10:30:00+09:00",
  "changed_by": "sha256:...",
  "allowed_changes": {
    "status": {
      "from": "in_progress",
      "to": "done"
    },
    "priority": {
      "from": "normal",
      "to": "high"
    }
  },
  "ignored_fields": ["description", "comments"],
  "redaction": {
    "subject_included": false,
    "description_included": false,
    "comments_included": false,
    "attachments_included": false
  },
  "signature": {
    "alg": "HMAC-SHA256",
    "key_id": "feedback-key-2026-06",
    "value": "base64url:..."
  }
}
```

외부 Importer의 Markdown 반영 규칙:

- `external_key`로 대상 Markdown 파일을 찾는다.
- front matter의 `status`, `priority`, `version`, `due_date`만 수정한다.
- 대상 파일이 없거나 현재 front matter와 event의 `from` 값이 다르면 PR에 `synctx-feedback-conflict` label을 붙이고 자동 수정하지 않는다.
- 자유 텍스트 요건 변경은 PR 본문에 "내부에서 요건 변경 요청 감지"만 표시하고, 실제 상세 내용은 내부 OpenProject 링크를 참조하도록 한다.

## 8. 배포 설계

### 8.1 이미지와 의존성 고정

운영 이미지는 `latest` 태그를 사용하지 않는다.

| 이미지 | 태그 정책 |
|--------|-----------|
| synctx/viewer | SemVer + git sha |
| synctx/cdn | SemVer + git sha |
| synctx/marp | SemVer + git sha |
| synctx/sync | SemVer + git sha |
| jgraph/drawio | 검증 완료한 명시 버전 |
| nginx | 검증 완료한 명시 버전 |
| alpine/git | 검증 완료한 명시 버전 (Docker Compose Slides sidecar) |

`npm install -g @marp-team/marp-cli`는 빌드 시 버전을 고정한다.
외부에서 다운로드한 모든 JS/CSS/font/image tarball은 SHA-256 manifest에 기록한다.

### 8.2 K8s 기본값

모든 자체 서비스에 적용한다.

```yaml
securityContext:
  runAsNonRoot: true
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]

resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 1000m
    memory: 1Gi
```

추가 요구:

- liveness/readiness probe 필수
- NetworkPolicy로 필요한 egress만 허용
- PodDisruptionBudget는 Viewer/CDN에 적용
- ConfigMap 변경 시 rolling restart
- Secret은 chart values에 직접 넣지 않고 기존 Secret 이름을 참조

### 8.3 PVC

| PVC | 접근 | 사용처 |
|-----|------|--------|
| `repo-cache` | RWO/RWX | Viewer, Sync, Marp의 clone cache. 서비스별 분리 권장 |
| `cdn-data` | RWX | CDN 정적 파일 |
| `sync-state` | RWO | Sync Engine 상태 |
| `slides-worktree` | RWO | `/data/slides/worktree`에 mount되는 slides-sync working tree |
| `marp-work` | RWO | Marp 변환 임시 작업 |

가능하면 서비스별 PVC를 분리한다. 공유 PVC는 locking과 권한 문제가 커진다.

### 8.4 로컬 시뮬레이션 (Minikube)

개발 완료 후 실제 운영 환경과 유사한 조건에서 검증하기 위해 Minikube를 활용한 로컬 시뮬레이션을 수행한다.

**시뮬레이션 환경 구성:**
- **Minikube Cluster**: `minikube start --addons=ingress,metrics-server` 명령으로 Ingress Controller와 Metrics Server가 활성화된 클러스터 구성.
- **Minikube Docker Daemon**: `eval $(minikube docker-env)`를 통해 로컬 Docker 데몬을 Minikube와 공유하여 빠른 로컬 빌드 및 테스트를 수행. (단, 운영 환경의 내부 레지스트리 및 imagePullSecret 검증이 필요한 경우 `registry` addon을 활성화하여 별도 테스트 수행)
- **Helm 배포**: 작성된 Helm Chart를 사용하여 전체 마이크로서비스(Viewer, CDN, Marp, Draw.io, Sync Engine, Slides Server)를 `synctx` 네임스페이스에 배포.

**주요 검증 항목:**
- **Ingress 라우팅**: `/view/*`, `/cdn/*`, `/api/sync/webhook/gitlab` 등 모든 Ingress Path가 올바른 Service로 라우팅되는지 확인.
- **PVC 마운트 및 권한**: `repo-cache`, `sync-state` 등 PVC가 정상적으로 프로비저닝되고 각 Pod에 올바른 권한(RWO/RWX)으로 마운트되는지 검증. (기본 Minikube storage provisioner는 RWX를 완벽히 지원하지 않으므로, 운영 환경 수준의 RWX/NFS 동작 검증을 위해서는 NFS/CSI addon 또는 별도의 dev StorageClass 구성이 필요함)
- **Webhook & CronJob 동작**: 가상의 GitLab Push Event를 `curl`로 Ingress에 전송하여 Webhook Receiver가 정상적으로 일회성 Sync Job을 생성하는지, 그리고 CronJob 스케줄과 겹칠 때 Lease 기반 잠금이 의도대로 작동하는지 확인.
- **서비스 간 내부 통신**: Viewer가 CDN의 정적 자산을 정상적으로 로드하는지, Marp Service가 내부 API를 통해 PDF 변환을 수행하는지 점검.

### 8.5 서비스 옵션

서비스마다 켜고 끌 수 있다. 서비스 이름과 순서는 `viewer, cdn, marp, slides, drawio, sync, feedback`이고, 의존성 규칙 D1~D7과 알려진 한계는 `docs/architecture.md`의 "서비스 옵션과 의존성"을 따른다.

K8s(Helm)는 `<서비스>.enabled`로 설정한다(`feedback`만 기본 끔).

```yaml
# values 예: Draw.io를 끈 배포
drawio:
  enabled: false
```

```bash
helm upgrade synctx ./helm/synctx --namespace synctx -f values-production.yaml --set drawio.enabled=false
```

Docker Compose는 옵션 파일 `deploy/docker/synctx.env`의 `SYNCTX_SERVICES`(쉼표 구분)로 설정한다. 1단계는 `viewer, cdn, marp, slides, drawio`만 받고, `sync`·`feedback`은 "2단계" 오류로 중단한다.

```bash
# Viewer·Marp·Slides만 (Viewer가 쓰는 CDN 포함)
SYNCTX_SERVICES=viewer,cdn,marp,slides
# Draw.io 추가
SYNCTX_SERVICES=viewer,cdn,marp,slides,drawio
```

- 두 방식 모두 Viewer에 `SYNCTX_ENABLED_SERVICES`를 넘긴다. Marp가 꺼지면 Viewer가 Marp 버튼을 숨기고 `format=pdf`에 503을 돌려주며, Draw.io가 꺼지면 Draw.io 버튼을 숨긴다.
- CDN을 끄고 사내 CDN을 쓰려면 `.synctx.yaml`의 `viewer.cdn_base_url`을 그 CDN 절대 주소로 지정한다. 상대 경로로 두면 Helm 렌더(차트가 설정을 만들 때)나 Viewer 시작이 오류로 멈춘다.
- K8s에서 서비스를 꺼도 PVC(`cdn-data`, `slides-worktree`, `sync-state`)는 남는다(데이터 보호). 삭제 절차는 Runbook 12.8.

### 8.6 Docker Compose 배포

K8s 클러스터가 없는 Docker 서버를 위한 배포 방식이다. 구성과 게이트웨이 동작은 `docs/architecture.md` 10.6, 인증 경계는 3.1.1을 따른다.

#### 서버 요구사항

- Docker Compose v2.17 이상(`up --wait --wait-timeout` 사용), amd64
- 운영 서버는 폐쇄망 레지스트리(`registry.local:5000`)에서 이미지를 받는다. 반입 절차는 기존 `pull-and-save-images.sh` 흐름(외부에서 pull → tar 저장 → 폐쇄망 반입 → registry push)을 따른다.
- 서버에는 저장소 체크아웃(또는 아래 디렉터리 묶음)이 같은 상대 위치로 있어야 한다. `compose.yaml`이 `../../helm/synctx/files`(Slides nginx 설정·스크립트)와 `../../cdn-data`(CDN vendor 파일)를 마운트하고, 배포는 `scripts/docker/*.sh`로 한다. 폐쇄망에는 이미지와 함께 `deploy/docker`, `scripts/docker`, `helm/synctx/files`, `cdn-data`를 저장소와 같은 구조로 묶어(예: `git archive HEAD deploy/docker scripts/docker helm/synctx/files cdn-data`) 반입한다. 이미지 태그의 `<git-sha>`와 같은 커밋에서 묶는다.
- 레지스트리에 있어야 하는 이미지(켠 서비스만 필요하다. synctx 이미지 태그는 `0.1.0-<git-sha>`)

| 이미지 | 용도 |
|--------|------|
| `synctx/viewer` | viewer |
| `synctx/cdn` | cdn |
| `synctx/marp` | marp |
| `nginx:1.25.4-alpine` | gateway, slides |
| `alpine/git:2.43.0` | slides-git |
| `jgraph/drawio:24.2.5` | drawio |

`synctx/sync`·`synctx/feedback`은 2단계에서 쓴다.

#### 옵션 파일

| 파일 | 내용 |
|------|------|
| `deploy/docker/synctx.env` | 서비스 선택, 이미지, 포트, TLS, 신뢰 프록시 옵션 (`synctx.env.example`을 복사) |
| `deploy/docker/secrets.env` | `GIT_READ_TOKEN`, `GIT_PUBLISH_TOKEN` (`secrets.env.example`을 복사, 권한 600 권장) |
| `.synctx.yaml` | `SYNCTX_CONFIG_FILE`이 가리키는 설정 파일 |

두 env 파일과 `.synctx.yaml`, `.generated/`, `tls/`는 `deploy/docker/.gitignore`로 git에서 뺀다. 옵션 파일 안의 상대 경로는 `deploy/docker` 기준이고, `--env-file`·`--secrets-file`로 준 상대 경로는 현재 디렉터리 기준이다. 옵션 파일은 `deploy.sh`가 bash로 읽고 docker compose도 읽으므로 공백이 든 값은 큰따옴표로 감싼다(예: `SYNCTX_EXTRA_COMPOSE_FILES="a.yaml b.yaml"`).

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SYNCTX_SERVICES` | `viewer,cdn,marp,slides` | 켤 서비스(필수) |
| `SYNCTX_IMAGE_PREFIX` | `registry.local:5000/` | 이미지 접두사. 로컬에서는 빈 값 |
| `SYNCTX_IMAGE_TAG` | 없음(필수) | `0.1.0-<git-sha>`. `latest`는 거부 |
| `SYNCTX_CONFIG_FILE` | `./synctx.yaml` | 모든 서비스가 읽는 `.synctx.yaml` |
| `SYNCTX_SLIDES_REPO_URL` | 없음 | Slides 게시 저장소(slides를 켜면 필수) |
| `SYNCTX_SLIDES_BRANCH` / `SYNCTX_SLIDES_POLL_SECONDS` | `main` / `60` | 게시 브랜치, 폴링 간격 |
| `SYNCTX_BIND_ADDRESS` | `0.0.0.0` | 게이트웨이 바인딩 주소 |
| `SYNCTX_HTTP_PORT` / `SYNCTX_HTTPS_PORT` | `80` / `443` | 게이트웨이 포트 |
| `SYNCTX_TLS_DIR` / `SYNCTX_TLS_CERT` / `SYNCTX_TLS_KEY` | `./tls` / 없음 / 없음 | `SYNCTX_TLS_DIR` 아래 파일 이름. CERT·KEY는 둘 다 주거나 둘 다 비운다 |
| `SYNCTX_TRUSTED_AUTH_PROXY` / `SYNCTX_TRUSTED_PROXY_CIDRS` | `false` / 없음 | 신뢰 프록시 모드(3.1.1) |
| `SYNCTX_MARP_PROXY_TIMEOUT` | `180` | Marp 경로의 게이트웨이 응답 대기(초). 1 이상의 정수만 받는다 |
| `SYNCTX_PROJECT_NAME` | `synctx` | compose 프로젝트 이름(컨테이너·볼륨 이름 접두사) |
| `SYNCTX_EXTRA_COMPOSE_FILES` | 없음 | 사내 사정에 맞춘 compose 오버라이드 파일(공백 구분, 여러 개면 큰따옴표) |
| `SYNCTX_WAIT_TIMEOUT` | `180` | `up --wait` 대기(초) |

- TLS: CERT·KEY를 주면 443 server 블록을 만들고 80은 443으로 돌린다. `compose.tls.yaml`이 443 포트 매핑과 인증서 마운트를 더한다. 하나만 주면 중단한다.
- 토큰은 `secrets.env`에 두고 필요한 컨테이너(viewer·marp·slides-git)에만 `environment:`로 넘긴다.

#### 배포 흐름 (`scripts/docker/deploy.sh`)

```
scripts/docker/deploy.sh [--env-file 경로] [--secrets-file 경로] [--check]
```

1. 옵션 파일을 읽고 검사한다: `SYNCTX_IMAGE_TAG` 필수·`latest` 거부, `SYNCTX_SERVICES` 이름 확인(D1·D6), Slides 게시 저장소 주소(D7)와 Marp 경고(D3), `SYNCTX_CONFIG_FILE` 존재, TLS 짝, 신뢰 모드 조건, `SYNCTX_MARP_PROXY_TIMEOUT` 정수.
2. `.synctx.yaml`의 sha256을 `SYNCTX_CONFIG_HASH`로 내보낸다. viewer·cdn·marp는 이 값을 라벨 `synctx.config-hash`로 받는다.
3. 게이트웨이 설정 후보 `.generated/gateway.candidate.conf`를 만든다. `--check`이면 운영 중인 `gateway.conf`는 건드리지 않고 `.generated/gateway.check.conf`에 미리 보기를 만든 뒤 끝낸다(docker를 쓰지 않는다).
4. gateway와 같은 이미지(`<SYNCTX_IMAGE_PREFIX>nginx:1.25.4-alpine`, 없으면 pull)의 일회용 컨테이너로 후보를 `nginx -t` 검사한다(TLS면 인증서 디렉터리도 마운트). 실패하면 nginx 출력을 보여 주고 운영 중인 `gateway.conf`는 그대로 둔 채 컨테이너를 건드리지 않고 종료 코드 1이다. 통과하면 후보를 `gateway.conf`에 덮어쓴다(bind mount가 끊기지 않게 inode 유지).
5. `docker compose --profile <켜진 서비스>... up -d --wait`로 시작하고 healthy를 기다린다. 실패하면 컨테이너 로그 끝부분을 보여 주고 종료 코드 1이다.
6. 꺼진 서비스 컨테이너가 남아 있으면 지운다(볼륨은 유지).
7. gateway에서 `nginx -t` 후 `nginx -s reload`.
8. CDN이 켜져 있으면 cdn 컨테이너 안에서 `seed_cdn.py`로 vendor 파일을 채운다(`/seed`가 없거나 비어 있으면 실패).

`seed_cdn.py`는 `/seed` 아래 일반 파일만 대상으로 하고(심볼릭 링크 제외), sha256이 같으면 건너뛰며 다르면 `force=true`로 올린다. `authz.enabled`와 `cdn.upload_auth.enabled`가 켜져 있으면 `role_mappings.cdn_admin`(없으면 `platform_admin`)의 첫 그룹을 보내므로 컨테이너 안에서 직접 부르는 이 경로는 게이트웨이의 헤더 제거와 무관하다.

#### 알려진 한계

- Docker 배포는 1단계(Viewer·CDN·Marp·Slides·Draw.io)만 구현됐다. Sync·Feedback은 2단계다.
- `.drawio` 파일은 Viewer에서 415라 Draw.io 버튼이 실제로 표시되는 경로가 없다.
- D2 검사는 상대 경로만 잡는다. 절대 주소가 꺼진 자기 자신의 `/cdn`을 가리키는 경우는 알 수 없다.
- K8s Ingress와 Viewer→Marp PDF 경로는 60초 제한이 남아 있다(기존 문제). Docker 게이트웨이만 Marp 경로를 180초로 둔다.

## 9. 관측성과 운영

### 9.1 로그

모든 로그는 JSON으로 남긴다.

공통 필드:

- `timestamp`
- `level`
- `service`
- `request_id`
- `repo_id`
- `path`
- `run_id`
- `user`
- `event`
- `duration_ms`
- `result`

Secret, token, Basic Auth, signed URL은 마스킹한다.

### 9.2 Metrics

필수 metrics:

- `synctx_http_requests_total`
- `synctx_http_request_duration_seconds`
- `synctx_render_requests_total`
- `synctx_marp_convert_total`
- `synctx_marp_publish_total`
- `synctx_sync_runs_total`
- `synctx_sync_items_total`
- `synctx_sync_failures_total`
- `synctx_sync_last_success_timestamp`
- `synctx_cdn_bytes_served_total`
- `synctx_feedback_events_total`
- `synctx_feedback_redacted_fields_total`
- `synctx_feedback_mail_failures_total`
- `synctx_feedback_last_success_timestamp`
- `synctx_html_bundle_total` (synctx-html 블록 처리 결과. `result`: `ok`, `disabled`, `missing_file`, `outside_repo`, `disallowed_host`, `too_large`, `invalid_block`, `unsupported`)

### 9.3 Alert

초기 alert:

- Sync가 30분 이상 성공하지 못함
- Sync failure ratio가 최근 3회 평균 10% 초과
- XWiki/OpenProject auth error 발생
- Marp publish push 실패
- Slides Server sidecar pull 실패 또는 worktree 손상
- Outbound Feedback mail send failure 3회 이상
- Outbound Feedback redaction failure 발생
- PVC 사용량 80% 초과
- readiness failure 5분 지속
- synctx-html 합본이 허용되지 않은 외부 주소 또는 크기 초과로 거부됨 (`SynctxHtmlBundleRejected`, 저장소별 1시간 안에 1건 이상, warning)

## 10. 테스트 전략

### 10.1 테스트 레벨

| 레벨 | 대상 | 완료 기준 |
|------|------|-----------|
| Unit | renderer, path security, front matter parser, mapper | 핵심 분기 80% 이상 |
| Contract | XWiki/OpenProject fake server | upsert/delete/retry/idempotency 검증 |
| Integration | local Git repo + FastAPI TestClient | API 정상/오류 흐름 검증 |
| E2E | Kind 또는 dev cluster | Viewer 렌더링, Marp publish, Sync dry-run |
| Security | path traversal, upload validation, authz | 권한 없는 요청 403 |
| Harness | sample `.context` + issues/board/docs | context schema, blueprint, agent-readiness, board 관계 검증 |
| Outbound Feedback | fake OpenProject + fake SMTP + fake importer | redaction, signature, replay, PR 생성 검증 |
| Offline build | vendor artifact only | 외부 네트워크 없이 이미지 빌드 |
| Meeting mode | pytest(TestClient) + `node --test`(memo-core.js) | 회의 모드 판단, 스크립트 JSON 인코딩, 내보내기 본문 텍스트 일치, 메모 위치 복원·병합·탭 간 저장 |
| HTML embed | pytest(`html_bundle` 단위, Viewer TestClient, Sync fake Git) | 합본 규칙·CSP meta·매크로 이스케이프, Viewer 렌더링·CSP, Sync 다시 처리할 조건(묶음 파일 변경, 정책 변경, 누락 파일 복구, 참조 교체) |

### 10.2 운영 전 수용 기준

- 같은 sync run을 3회 반복 실행해도 외부 대상이 중복 생성되지 않는다.
- XWiki 또는 OpenProject가 1회 timeout을 반환하면 재시도 후 성공한다.
- 특정 파일 validation error가 있어도 다른 파일은 동기화된다.
- Sync 중 pod가 종료되어도 다음 실행에서 일관된 상태로 재시도된다.
- `latest` 태그가 values-production에 존재하지 않는다.
- CDN 업로드 권한 없는 사용자는 업로드할 수 없다.
- Marp publish 결과가 Git repo commit과 Slides URL에서 동일하게 확인된다.
- `synctx validate-harness`가 sample workspace의 오류를 JSON/Markdown report로 재현 가능하게 출력한다.
- Outbound Feedback 활성화 시 allowlist 밖 필드와 본문/댓글/첨부가 메일 payload에 포함되지 않는다.

## 11. 단계별 개발 체크리스트

이 단계는 운영 가능한 구현을 만들기 위한 체크리스트다.
[architecture.md](architecture.md)의 제품 로드맵과 번호를 맞추기 위해 Phase 5는 운영 준비, Phase 6은 하네스/스크럼 확장으로 둔다.

### Phase 0: 운영 설계 확정

- [x] 서비스별 책임과 데이터 원천 결정
- [x] Sync 상태 저장소와 lock 전략 결정
- [x] Marp/Slides 출판 기준 결정
- [x] `.synctx.yaml` Pydantic schema 작성
- [x] Front Matter schema와 validation error 포맷 작성
- [x] OpenProject custom field 및 status/type id 확인
- [x] XWiki Markdown Syntax 확장 설치 여부 확인

완료 기준:

- config sample이 schema validation을 통과한다.
- XWiki/OpenProject 운영 환경별 필수 id 매핑 표가 채워져 있다.

### Phase 1: 기반 패키지와 Viewer/CDN

- [x] `shared/synctx-common` 패키지 생성
- [x] config loader, auth helper, front matter parser 구현
- [x] Git manager 이식 및 path traversal 방어 테스트 작성
- [x] CDN Service 정적 서빙 구현
- [x] CDN upload validation과 manifest 구현
- [x] Viewer Service renderer 이식
- [x] Viewer API authz 적용
- [x] healthz/readyz/metrics/version endpoint 구현 (observability.py 공용 모듈)
- [x] Dockerfile과 Helm 기본 chart 작성
- [x] Helm PVC 템플릿 (cdn-pvc, slides-pvc, sync-state-pvc) 작성
- [x] Helm Service 리소스 통합 (services.yaml)
- [x] 단위 테스트: auth.py, frontmatter.py, observability.py, signer.py, redactor.py, event_store.py
- [x] CDN vendor 파일 적재 (mermaid 10.9.1, chart.js 4.4.4, highlight.js 11.9.0)

완료 기준:

- 폐쇄망 vendor 파일만으로 Viewer가 Mermaid와 highlight.js를 렌더링한다.
- 권한 없는 CDN upload가 403을 반환한다.
- `helm template`과 unit test가 통과한다.

### Phase 2: Marp/Slides/Draw.io

- [x] Marp convert API 구현
- [x] Marp publish lock 구현 (O_CREAT|O_EXCL)
- [x] publish repo commit/push 구현
- [x] slides-sync sidecar 구현
- [x] Slides Server Nginx 설정 구현
- [x] Draw.io 이미지 버전 pinning 및 Helm 배포 추가
- [x] Viewer에 Marp/Draw.io 연동 UI 추가 (front matter 기반 조건부 표시)
- [x] Dockerfile multi-stage 빌드 구현
- [x] Marp PDF 변환 통합 테스트 (convert/preview/publish/unpublish/authz, 15 tests)
- [x] Slides 출판 → Slides Server 서빙 검증 (publish + list + unpublish flow)

완료 기준:

- Markdown 슬라이드를 PDF로 다운로드할 수 있다.
- publish 후 Git commit과 Slides URL이 모두 확인된다.
- 외부 네트워크 없이 draw.io 화면이 열린다.

### Phase 3: XWiki Sync

- [x] Sync Engine CLI 구현
- [x] file-based 실행 잠금 구현 (O_CREAT|O_EXCL)
- [x] state.json atomic read/write 구현
- [x] git diff `A/M/D/R` 처리 구현 (R → D+A 분리)
- [x] XWiki REST API 클라이언트 구현 (기본 CRUD)
- [x] Mermaid SVG 변환 모듈 구현 (sync_converters/mermaid.py)
- [x] CronJob/ServiceAccount/RBAC/NetworkPolicy Helm 추가
- [x] Dockerfile multi-stage 빌드 구현
- [x] XWiki attachment upload + SHA skip 구현
- [x] partial failure 재시도 로직 구현 (TargetRetryConfig 기반 backoff)
- [x] XWiki sync 통합 테스트 (XWikiClient CRUD + SHA skip + SyncEngine retry, 25 tests)

완료 기준:

- 같은 문서를 반복 sync해도 XWiki 페이지가 중복 생성되지 않는다.
- Mermaid 문서가 SVG 첨부로 변환된다.
- 일부 실패 시 `last_successful_sha`가 전진하지 않고 다음 run에서 재시도된다.

### Phase 4: OpenProject Sync

- [x] OpenProject REST API 클라이언트 구현 (기본 CRUD)
- [x] Sync Engine에서 OpenProject 대상 라우팅 구현
- [x] OpenProject type/status/priority mapping 설정 (config.type_map/status_map/priority_map)
- [x] external key custom field 조회 구현 (filter API)
- [x] Work Package create/update 고도화 (upsert + lockVersion)
- [x] protected fields 정책 구현 (_apply_protected_fields)
- [x] version/sprint 매핑 구현 (get_version_by_name)
- [x] mapping error와 SyncReport 구현 (unknown mapping 로깅 + 실패 리포트)
- [x] user 조회 구현 (get_user_by_login)
- [x] Work Package 삭제/마킹 구현 (mark_deleted)
- [x] OpenProject 단위 테스트 (CRUD + protected fields + mark_deleted, 12 tests)

완료 기준:

- Markdown issue가 Work Package로 생성된다.
- status 변경이 idempotent하게 반영된다.
- unknown status는 다른 파일 처리를 막지 않고 report에 남는다.

### Phase 5: 운영 준비

- [x] production values 작성 (values-production.yaml)
- [x] Prometheus ServiceMonitor/PrometheusRule Helm 작성
- [x] 이미지/벤더 artifact SHA-256 manifest 작성
- [x] offline build 검증 (Makefile `build`/`save-images`/`load-images`)
- [x] Prometheus metrics scrape 연동 검증 (smoke-test.sh에 metrics 검증 포함)
- [x] 기본 alert rule 실환경 검증 (smoke-test.sh에 PrometheusRule 검증 포함)
- [x] runbook 작성
- [x] 백업/복구 절차 작성
- [x] 권한 매핑 운영 검토 (verify-authz.sh 권한 매트릭스 검증)

완료 기준:

- dev cluster에서 end-to-end smoke test가 통과한다.
- 운영 반입 artifact 목록이 고정되어 있다.
- 장애 시나리오별 runbook이 있다.

### Phase 6: 하네스 엔지니어링 및 스크럼 보드

- [x] Harness Validator 로직 구현 (synctx_common.harness)
- [x] Harness Validator CLI(`synctx validate-harness`) 구현
- [x] agent-readiness 검사 구현(제목, 요약, 결정사항, TODO owner, Mermaid)
- [x] Viewer Service 칸반 보드 렌더링 구현
- [x] `.context/` 디렉토리 규격 작성 (샘플 데이터)
- [x] `.context/blueprints/`에 문서/요건/작업 작성 표준 템플릿 작성
- [x] `reports/harness/latest.json`과 `latest.md` 출력 구현
- [x] Version 문서의 `issues` 목록 검증 구현
- [x] Task `version` 필드와 Version 문서 `issues` 목록 불일치 리포트 구현
- [x] agent-readiness 링크 유효성 검사 구현

완료 기준:

- `.context` 필수 파일이 누락되면 Harness Validator가 실패하고 수정 가능한 report를 남긴다.
- 문서가 agent-readiness 기준을 만족하지 못하면 파일 경로, 규칙 ID, 수정 제안이 report에 기록된다.
- Task의 `synctx.version`을 기준으로 OpenProject version 연결이 동기화된다.
- Version 문서의 `issues` 목록으로 Viewer 보드가 렌더링된다.
- Task version과 Version manifest 불일치가 dry-run report에 표시된다.

### Phase 7: 제한적 Outbound Feedback (선택)

- [x] Outbound Feedback Exporter redaction 구현
- [x] HMAC signature와 key rotation 구현
- [x] `changed_by` pseudonymization용 salt 관리 구현
- [x] approved SMTP relay 발송 모듈 구현
- [x] outbound event store와 replay 방지 구현
- [x] Feedback 관리 API (dry-run, resend, event 목록) 구현
- [x] Helm secret/cronjob 템플릿 작성
- [x] External Mail Importer 수신/검증 구현
- [x] Markdown front matter 변경 PR 생성 구현
- [x] conflict label과 수동 리뷰 흐름 구현
- [x] 감사 로그와 운영 runbook 작성

완료 기준:

- description/comment/attachment가 포함된 OpenProject 변경 이벤트에서도 메일 payload에는 허용 필드만 포함된다.
- HMAC signature가 틀리거나 중복 `event_id`인 메일은 외부 Importer가 거부한다.
- 외부 Importer는 main branch에 직접 push하지 않고 PR만 생성한다.
- PR merge 후 기존 외부→내부 sync로 OpenProject가 idempotent하게 갱신된다.

### Phase 8: 협업 통합 — 8.1a 회의 모드 기반

- [x] front matter 분리(일반 보기 포함)
- [x] `synctx.meeting` 검증과 Harness `meeting.id` 중복 검사
- [x] `repositories[].collab.enabled`
- [x] 회의 모드 메모·형광펜·회의 정리
- [x] `.md`·메모 포함 HTML 내보내기/가져오기
- [ ] 8.1b 근거 미리보기·근거 모음
- [ ] 8.1c 회의 문서 대시보드
- [ ] 8.2 Jitsi 내장
- [ ] 8.3 Collab Gateway
- [ ] 8.4 `/synctx` 명령과 채널

완료 기준(8.1a):

- 회의 문서를 회의 모드로 열어 메모를 남기고 새로고침해도 위치와 내용이 복원된다.
- 메모 포함 HTML을 네트워크 없이 열면 읽기 전용으로 메모가 보이고, Viewer에서 다시 가져올 수 있다.
- 메모 데이터가 서버로 전송되지 않는다.

### Phase 9: 인터랙티브 HTML 임베드

- [ ] XWiki `markdown/1.2` 안 html 매크로 실행 확인
- [ ] 설정 모델과 출처·부분집합 검사
- [ ] `html_bundle` 합본·CSP meta·이스케이프
- [ ] Viewer 렌더링·CSP·다운로드
- [ ] Sync 변환과 다시 처리할 조건
- [ ] 메트릭·Alert·Runbook
- [ ] 운영 XWiki·실제 브라우저 확인

완료 기준:

- 신뢰 저장소의 모션 그래픽이 Viewer와 XWiki 페이지 안에서 재생·토글된다.
- 묶음 파일만 바꾸거나 신뢰 설정을 바꿔도 다음 동기화에서 XWiki 페이지가 갱신된다.
- 비신뢰 저장소와 합본 실패는 안내 문구로 보이고 페이지의 나머지는 정상이다.

### 배포 옵션 (Phase 번호와 별개): Docker 배포 체크리스트

서비스 옵션과 Docker Compose 배포는 새 Phase가 아니다. Phase 번호 체계(1-9)는 그대로다.

- [ ] 옵션 검사: `scripts/docker/deploy.sh --check`가 통과하고 켜진 서비스가 의도와 같다
- [ ] 이미지 반입: 켠 서비스의 이미지가 `registry.local:5000`에 있고 `SYNCTX_IMAGE_TAG`가 `0.1.0-<git-sha>` 형식이다(`latest` 아님)
- [ ] `.synctx.yaml` 준비: 인증 프록시가 없으면 `authz.enabled: false`, CDN을 끄면 `viewer.cdn_base_url`이 절대 주소다
- [ ] TLS: 인증서를 `tls/`에 두고 HTTPS 200, HTTP 301을 확인한다
- [ ] 헤더 제거 확인: 위조한 `X-Forwarded-Groups`로 직접 요청해도 권한이 올라가지 않는다(신뢰 모드면 허용 목록 밖 출발지는 403)
- [ ] 신뢰 모드: 3.1.1의 두 구성 중 하나를 따른다(다른 호스트의 프록시는 프록시 쪽 인터페이스 바인딩 + 방화벽, 컨테이너 프록시는 고정 주소). 허용 목록에 Docker 브리지 게이트웨이 주소가 없고, 접근 로그에서 프록시 요청의 출발지가 허용한 주소인지 확인한다
- [ ] 외부 포트: 게이트웨이 외 컨테이너가 호스트 포트를 열지 않는다(`compose.sh -- ps`)
- [ ] 볼륨 백업 절차를 한 번 시험했다(Runbook 12.7)

## 12. Runbook 초안

### 12.1 Sync 실패

1. `synctx_sync_last_success_timestamp` 확인
2. 최근 run report 확인: `/data/sync-state/repos/{repo_id}/runs/`
3. `auth_error`면 Secret 만료 여부 확인
4. `mapping_error`면 `.synctx.yaml`의 map 또는 문서 front matter 수정
5. `transient_error`면 대상 서비스 상태 확인 후 Job 재실행

### 12.2 Marp publish 실패

1. Marp Service log에서 `publish_path`, `repo_id`, `commit_sha` 확인
2. publish repo non-fast-forward 여부 확인
3. worktree lock stale 여부 확인
4. 재시도해도 실패하면 publish repo 권한과 branch protection 확인

### 12.3 CDN 파일 교체

1. 같은 version 파일 교체가 필요한지 확인
2. 가능하면 새 version 디렉토리로 업로드
3. 불가피한 교체는 `force=true`로 업로드
4. manifest diff와 audit log 확인

### 12.4 Slides Server 장애

1. Slides Server readiness와 Nginx access/error log 확인
2. `slides-sync` sidecar log에서 최근 pull 시각과 commit sha 확인
3. publish repo 접근 권한과 `GIT_PUBLISH_TOKEN` 만료 여부 확인
4. worktree 손상이 의심되면 pod를 재시작하여 clean clone으로 복구
5. 특정 publish path만 404이면 Marp publish commit에 해당 파일이 포함되었는지 확인

### 12.5 Outbound Feedback 장애

1. `synctx_feedback_last_success_timestamp`와 `synctx_feedback_mail_failures_total` 확인
2. SMTP 실패면 approved mail gateway 접속, 계정 잠금, `OUTBOUND_SMTP_CREDENTIAL` 만료 여부 확인
3. redaction failure면 실패 event의 `ignored_fields`와 allowlist 설정을 확인하고, 본문/댓글/첨부가 payload에 포함되지 않았는지 검증
4. HMAC 검증 실패가 외부 Importer에서 발생하면 `key_id`, active/previous key 배포 상태, key rotation overlap이 `replay_window_minutes` 이상인지 확인
5. replay reject가 증가하면 중복 메일 발송 또는 Importer event store 손상 여부 확인
6. `changed_by`가 평문 또는 단순 SHA-256으로 기록되면 즉시 발송을 중단하고 `OUTBOUND_FEEDBACK_HASH_SALT` 설정과 redaction 테스트를 재검증
7. 복구 후 `/api/feedback/resend/{event_id}`는 redaction dry-run 결과를 확인한 뒤 `feedback-admin` 권한으로만 실행

### 12.6 synctx-html 합본 실패·XWiki 권한

1. `synctx_html_bundle_total`의 `result` 라벨로 사유를 확인한다. Sync 로그의 `synctx-html block failed` 줄에 경로와 메시지가 있다.
2. `missing_file`: 문서가 가리키는 파일을 저장소에 추가하면 다음 동기화에서 자동으로 복구된다.
3. `disallowed_host`: 외부 주소를 `allowed_origins`에 넣을지 운영자가 판단한다. 넣으려면 최상위 목록과 저장소 목록에 모두 넣는다.
4. `too_large`: 이미지를 줄이거나 `max_bundle_bytes`를 조정한다.
5. XWiki 페이지에 스크립트가 글자로 보이거나 iframe이 사라지면, 신뢰 저장소 전용 계정(`html_embed.xwiki_credential_env`)의 공간 스크립트 권한을 확인한다. 공간 `WebPreferences`에 `XWiki.XWikiGlobalRights`(`users=전용 계정`, `levels=script`, `allow=1`)가 있어야 한다. 기본 동기화 계정에는 스크립트 권한을 주지 않는다.
6. Helm에서 `existingSecret`을 쓰면 `feedback.secrets.xwikiHtmlCredentialEnabled: true`로 `XWIKI_HTML_CREDENTIAL`을 그 Secret의 `xwiki-html-credential` 키에 연결한다. 평문 값(`xwikiHtmlCredential`)은 Chart가 Secret을 만들 때만 쓴다.
7. 신뢰를 해제하거나 전용 계정을 바꾸면 다음 동기화가 다른 계정으로 게시된 문서를 모두 새 계정으로 다시 게시한다(상태 `synced_files[].publisher` 기준). 본문이 같으면 임시 버전을 한 번 거쳐 작성자를 바꾼다. 실패한 문서는 기록이 바뀌지 않아 다음 실행에서 다시 시도한다. 완료 후 페이지 기록의 작성자가 바뀌었는지 확인한다. md에서 더 이상 게시하지 않는 남은 페이지(md 삭제 후 `on_delete: mark`·`ignore`, 대상을 `openproject`로 변경)는 상태의 `xwiki_orphan: true` 기록으로 추적해 기본 계정으로 다시 저장한다. Sync 로그에서 `XWiki author of orphan page`가 들어간 줄로 성공·실패를 확인한다. 처리에 실패한 문서는 `validation_error`를 빼고 매 실행 다시 시도한다. 원본이 사라진 실패 문서는 삭제로 처리된다.

### 12.7 Docker Compose 배포·운영

1. 배포: `deploy/docker/synctx.env`, `secrets.env`, `.synctx.yaml`을 준비한 뒤 `scripts/docker/deploy.sh --check`로 옵션을 검사하고, 문제가 없으면 `scripts/docker/deploy.sh`를 실행한다. `--check`는 `.generated/gateway.check.conf`만 만들어 운영 중인 게이트웨이 설정을 건드리지 않는다.
2. 옵션 변경: `SYNCTX_SERVICES`를 고친 뒤 `deploy.sh`를 다시 실행한다. 꺼진 서비스의 컨테이너는 자동으로 지워지고 볼륨은 남는다. 게이트웨이 경로도 함께 갱신된다.
3. 설정 변경: `.synctx.yaml`만 고쳐도 `deploy.sh`를 다시 실행하면 설정 해시가 바뀐 viewer·cdn·marp만 다시 만든다(앱은 시작할 때만 설정을 읽는다).
4. 운영 명령: `scripts/docker/compose.sh -- ps`, `-- logs -f viewer`, `-- restart marp`, `-- exec cdn sh`처럼 실행한다. 직접 `docker compose up`은 쓰지 않는다(게이트웨이 설정과 설정 해시를 `deploy.sh`가 만든다).
5. 중지: `scripts/docker/down.sh`. 볼륨은 유지하며 `--volumes`를 줄 때만 지운다(CDN 업로드 파일, Viewer 저장소 캐시, Slides worktree가 사라진다).
6. 볼륨 백업: 볼륨 이름은 `<SYNCTX_PROJECT_NAME>_<볼륨>`(기본 `synctx_cdn-data`)이다.
   ```bash
   docker run --rm -v synctx_cdn-data:/data:ro -v "$PWD":/backup \
     --entrypoint tar registry.local:5000/nginx:1.25.4-alpine czf /backup/cdn-data.tgz -C /data .
   ```
   복구는 같은 방식으로 새 볼륨에 `tar xzf`한다. 볼륨 목록은 `docker volume ls`로 확인한다.
7. Viewer가 시작하자마자 종료하고 로그에 CDN 의존성 오류가 있으면(CDN이 꺼졌는데 `viewer.cdn_base_url`이 상대 경로): `SYNCTX_SERVICES`에 `cdn`을 다시 넣거나 `.synctx.yaml`의 `viewer.cdn_base_url`을 사내 CDN 절대 주소로 바꾼 뒤 `deploy.sh`를 다시 실행한다.
8. 모든 요청이 403이면 `.synctx.yaml`의 `authz.enabled`를 확인한다. 인증 프록시가 없으면 `false`, 있으면 프록시가 헤더를 넣는지와 신뢰 모드 설정(`SYNCTX_TRUSTED_PROXY_CIDRS`)을 확인한다. 신뢰 모드에서 정상 프록시가 403이면 `compose.sh -- logs gateway`로 출발지를 본다. 출발지가 Docker 브리지 게이트웨이(예: `172.18.0.1`)이면 허용하지 말고 3.1.1의 두 구성 중 하나로 바꾼다(그 주소를 허용하면 호스트의 모든 프로세스가 권한 헤더를 위조할 수 있다).
9. Marp PDF·출판이 시간 초과로 끊기면 `SYNCTX_MARP_PROXY_TIMEOUT`(기본 180초)을 확인한다. 변환 제한(기본 120초)보다 길어야 한다.

### 12.8 K8s에서 서비스를 끈 뒤 데이터 정리

1. `helm upgrade ... --set <서비스>.enabled=false`로 서비스를 끄면 Deployment·Service·Ingress 경로는 사라지지만 PVC(`cdn-data`, `slides-worktree`, `sync-state`)는 남는다(데이터 보호). 서비스를 다시 켜면 남은 PVC를 그대로 쓴다.
2. 데이터를 정말 지울 때만 운영자가 PVC를 직접 삭제한다: `kubectl -n synctx delete pvc <PVC 이름>`. 삭제 전에 백업(`scripts/backup-restore.sh`)과 StorageClass의 `reclaimPolicy`를 확인한다.
3. 켜진 서비스가 없거나(D1) CDN이 꺼졌는데 `viewer.cdn_base_url`이 상대 경로이면(D2) `helm upgrade`가 렌더 단계에서 실패한다. 메시지를 보고 옵션이나 `.synctx.yaml`을 고친다.
4. 업그레이드는 `helm upgrade ... -f values-production.yaml`처럼 values 파일을 넘겨 실행하고 `--reuse-values`는 쓰지 않는다. `--reuse-values`는 이전 릴리스 값을 그대로 써서 차트에 새로 생긴 기본값이 빠진다. `viewer`·`cdn`·`marp`·`slides`·`drawio`·`sync`는 `enabled` 키가 없으면 켜진 것으로 보므로 서비스 옵션이 없던 릴리스를 올려도 서비스가 사라지지는 않는다(`feedback`은 키가 없으면 꺼짐).

## 13. 남은 외부 검증 항목

다음은 구현 전에 실제 운영 환경에서 확인해야 한다.

- XWiki 버전과 Markdown Syntax 확장 설치 가능 여부
- XWiki REST attachment size 제한
- OpenProject API token 권한 범위
- OpenProject status/type/priority id
- OpenProject custom field 생성 권한
- OpenProject webhook 지원 여부와 Work Package update event payload 형식. webhook 미지원 또는 payload 부족 시 Outbound Feedback은 polling(CronJob)으로 구현
- GitLab mirror 지연 시간과 webhook 사용 가능 여부
- approved SMTP relay의 첨부/본문 필터링 정책, 발신 계정 감사 로그 제공 여부
- 외부 Importer가 사용할 Git provider API 권한, PR 생성 권한, branch protection 정책
- K8s StorageClass의 RWX/RWO 지원과 NFS flock/Lease 정책
- 사내 Ingress 인증 헤더와 그룹 포맷
