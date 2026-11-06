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
- 설계는 Marp Service가 push 성공 후 내부 refresh API를 호출하는 것이지만, 지금은 호출하지 않고 refresh 서버도 배포하지 않는다. 출판물은 sidecar의 polling으로 반영된다.

이 결정으로 Git 이력과 서빙 상태의 기준이 분리되지 않는다.

### 2.2 동기화 상태 저장소

Sync Engine 상태는 내부 PVC에 저장한다.

```
/data/sync-state/
├── {repo_id}.json            # 저장소별 상태
├── {repo_id}.bak             # 직전 상태
├── metrics/
│   └── {repo_id}.json        # Sync 메트릭 누적값 (9.2)
├── feedback_state.json       # Outbound Feedback 마지막 처리 시각
├── feedback_events.json      # Outbound Feedback event store
└── feedback_metrics.json     # Outbound Feedback 메트릭 누적값 (9.2)
```

실행 기록은 따로 파일로 남기지 않는다. 최근 실행 id는 상태의 `last_run_id`, 실패는 `pending_failures`와 Job 로그로 본다. Lease를 쓸 수 없을 때의 파일 lock은 `/tmp/synctx-sync-{repo_id}.lock`이다.

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
- `POST /internal/slides/refresh` (설계. 지금은 배포하지 않는다)

토큰은 K8s Secret에서 주입하며, ConfigMap에 저장하지 않는다.

### 3.4 Secret 관리

| Secret | 사용처 | 권한 |
|--------|--------|------|
| `GIT_READ_TOKEN` | Viewer, Sync Engine, slides-sync, Marp(폴백) | source repo read, slides-sync의 publish repo pull. Marp는 `GIT_PUBLISH_TOKEN`이 비었을 때만 출판에 쓴다 |
| `GIT_PUBLISH_TOKEN` | Marp | publish repo write/read (출판·출판 취소·출판 목록) |
| `GITLAB_WEBHOOK_SECRET` | Sync Webhook Receiver | GitLab push event 검증 |
| `XWIKI_CREDENTIAL` | Sync Engine | page/attachment write |
| `XWIKI_HTML_CREDENTIAL` | Sync Engine | `html_embed.enabled` 저장소 전용 XWiki 계정. page/attachment write + 대상 공간 한정 script right (`XWIKI_CREDENTIAL`과 다른 계정) |
| `OPENPROJECT_API_KEY` | Sync Engine, Outbound Feedback Exporter | work package write (Exporter는 read) |
| `OUTBOUND_SMTP_CREDENTIAL` | Outbound Feedback Exporter | approved mail gateway send |
| `OUTBOUND_FEEDBACK_HMAC_KEY` | Outbound Feedback Exporter, External Importer | event signature |
| `OUTBOUND_FEEDBACK_HASH_SALT` | Outbound Feedback Exporter | changed_by pseudonymization |
| `MATTERMOST_BOT_TOKEN` | Collab Gateway | Mattermost 봇 토큰(채널 조회·글 게시) |
| `VIEWER_SERVICE_TOKEN` | Collab Gateway, Viewer | Viewer 회의·근거·검색 API 서비스 토큰(Gateway는 보내고 Viewer는 비교). 두 쪽이 같은 Secret 값을 쓴다 |
| `MATTERMOST_COMMAND_TOKEN` | Collab Gateway | 8.4 슬래시 명령 요청 검증용(선택) |
| `JITSI_APP_SECRET`·`JITSI_APP_SECRET_PREVIOUS` | Viewer / Prosody(현재 키만) | JWT 서명·검증. Helm `jitsi.jwtSecret`의 current/previous. UTF-8 32바이트 이상 |
| `JITSI_ROOM_SECRET`·`JITSI_ROOM_SECRET_PREVIOUS` | Viewer 전용 | 방 이름 HMAC. `jitsi.roomSecret` current/previous, 앱 키와 별개 |
| Jitsi 키의 `_FILE` 변형 | Viewer | 직접 값과 함께 지정 금지. Compose는 읽기 전용 마운트, Helm은 Secret 환경변수 참조 |
| `JICOFO_AUTH_PASSWORD`·`JVB_AUTH_PASSWORD` | Prosody·Jicofo·JVB | 내부 XMPP 계정. Helm `jitsi.xmppSecret`의 jicofo-password/jvb-password, Compose secrets.env의 JITSI_JICOFO_PASSWORD/JITSI_JVB_PASSWORD |
| `SYNCTX_RENDER_SECRET` | Viewer (렌더 보기) | 렌더 토큰 발급·검증. UTF-8 인코딩 뒤 32바이트 이상, 복수 인스턴스·프로세스는 공유 키 필수 |
| `SYNCTX_RENDER_SECRET_PREVIOUS` | Viewer (렌더 보기) | 검증 전용 보조 키, 32바이트 이상. 보통 이전 키이며 교체 준비 단계에는 다음 키를 미리 넣음 |
| `SYNCTX_RENDER_SECRET_FILE`·`SYNCTX_RENDER_SECRET_PREVIOUS_FILE` | Viewer (렌더 보기) | 해당 키의 mounted UTF-8 파일 경로. 직접 값과 동시 지정은 시작 오류 |
| `SYNCTX_SERVICE_TOKEN` | internal API | service-to-service. 값이 있을 때만 Viewer webhook이 `X-Service-Token` 헤더를 검사한다(현재 Helm·Compose는 주입하지 않음) |

모든 Secret은 env 또는 mounted file로만 전달한다. 로그에는 credential 값과 credential이 포함된 URL을 출력하지 않는다.

Marp가 publish repo에 접근할 때 쓰는 인증은 다음 순서로 정한다.

1. `marp.publish_auth`가 있으면 그 설정(`type`·`credential_env`·`credential_file`·`ssh_key_file`)을 그대로 쓴다. 지정한 값이 비어 있어도 다른 토큰으로 넘어가지 않는다.
2. 없으면 `GIT_PUBLISH_TOKEN`에 값이 있을 때 이 토큰으로 인증한다. Helm(`git.secrets.publishToken`, `existingSecret`의 `publish-token` 키)과 Docker Compose(`secrets.env`)가 Marp에 넣는 값이다.
3. 둘 다 없으면 0.6.0까지처럼 `repositories[0].auth`(보통 `GIT_READ_TOKEN`)로 출판하고, 호출마다 `publish_auth_fallback` WARNING 로그를 남긴다. 기존 배포가 업그레이드 직후 멈추지 않게 둔 호환 경로이며, 읽기 토큰에 쓰기 권한을 주지 않으려면 `GIT_PUBLISH_TOKEN`을 채운다. 저장소가 하나도 없으면 인증 없이 접근한다.

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
- `synctx-html` 블록의 합본은 `sandbox="allow-scripts"` srcdoc iframe으로 넣는다. `allow-same-origin` 등은 붙이지 않으므로 출처가 `null`이라 Viewer·XWiki 세션에 접근할 수 없다.
- 합본 `<head>` 맨 앞에 CSP meta를 넣어 실행 중 네트워크를 `allowed_origins`로 제한한다. 저장소 목록은 최상위 `html_embed.allowed_origins`의 부분집합이어야 하고, Viewer 부모 CSP는 최상위 목록과 `font-src data:`를 포함한다.
- XWiki에는 블록당 `{{html clean="false"}}` 매크로 하나만 넣고, 매크로 본문의 `{` `}`는 모두 엔티티로 바꾼다.
- 스크립트 권한은 신뢰 저장소 전용 XWiki 계정(`repositories[].html_embed.xwiki_credential_env`)에만 대상 공간 한정으로 준다. 기본 동기화 계정에는 주지 않는다. XWiki가 작성자 권한으로 매크로를 실행하므로, 비신뢰 저장소가 본문에 직접 쓴 매크로는 기본 계정 권한으로 걸러진다. 프로그래밍 권한과 `attachment.download.whitelist` 변경은 하지 않는다.
- 잔여 위험: iframe 자신의 주소 이동(`location.href`)과 WebRTC는 CSP·sandbox로 막을 수 없다. 신뢰 저장소 지정으로만 통제한다.

HTML 렌더 보기(PR #63, 상세는 `html-embed-design.md` 14장):

- 기본 소스 보기를 유지하고 `enabled`와 별도 opt-in `html_embed.render_files`(신규, 기본 false)가 모두 true인 저장소만 `mode=render`·`/render`를 연다. `enabled`만 켠 기존 저장소의 실행 범위는 확대하지 않는다. 두 플래그를 켜면 저장소 전체 `.html`·`.htm`과 모든 브랜치가 실행 대상이다. XWiki·Sync Engine은 바꾸지 않으며 Viewer·XWiki의 실효 정책 동등성은 `synctx-html` 블록에 한정한다.
- 토큰은 `v1.{exp}.{ref_b64}.{sha}.{signature}`다. ref는 `origin/` 없는 브랜치명(생략 시 기본 브랜치), UTF-8 1~255바이트를 패딩 없는 base64url(2~340자)로 싣는다. 토큰 최대 419자, exp는 선행 0 없는 양의 ASCII 숫자 1~11자리, SHA는 소문자 16진수 40자다. SHA-256 object-format(64자) 저장소는 미지원이다. 서명은 `render|v1|{repo_id}|{ref}|{sha}|{exp}`의 HMAC-SHA256 앞 16바이트를 패딩 없는 base64url로 인코딩한다. exp는 원문 숫자 문자열 그대로, ref는 디코딩 원문을 쓰고 정규형·길이·전체 일치 정규식은 `html-embed-design.md` 14.2를 따른다. `path`는 서명하지 않아 같은 저장소·SHA 안 이동을 허용한다. 권한·두 플래그를 확인한 화면 생성 함수에서 발급하며 이를 공유하는 `render_file` API 응답에도 실릴 수 있다. 토큰 전용 발급 API는 두지 않는다.
- `/render`는 `verify_viewer_access` → 토큰 정규형·서명·만료·저장소 결합 → `Sec-Fetch-Dest` → 로드된 `enabled`·`render_files` → `.html`·`.htm` → `validate_path` → SHA 존재·필요 시 ref fetch → 읽기 전 `blob_size_resolved(path, sha) > max_bundle_bytes` 검사 순서다. 모두 성공해야 200이며 합본 중 전체 크기 상한도 적용한다. `Sec-Fetch-Dest`는 `iframe`·`frame`만 허용하고 없음·`document`는 403이다. WP1의 `resolved_reader(manager, sha)`로 진입·의존 파일을 SHA 고정 읽기하며 ref를 다시 해석하지 않는다. reader의 `CalledProcessError`→`FileNotFoundError`, `ValueError`→`BundleError` 변환도 WP1 계약이다.
- 운영 Viewer는 파드 2개가 각각 저장소를 clone하며 비기본 ref·webhook fetch는 한 파드만 갱신한다. `/render`를 받은 파드에서 `cat-file -e {sha}^{commit}`이 실패하면 서명된 ref를 fetch하고 동일 SHA를 재확인한다(기본 브랜치도 실제 fetch). force-push 등으로 여전히 없으면 404·원래 화면 "새로 고침" 안내이며 최신 SHA로 대체하지 않는다.
- 설정은 Viewer import 시 `config = load_config()`로 한 번 읽는다. `enabled`·`render_files` 해제는 Viewer 재시작 후 유효 토큰에도 적용된다. Runbook 12.9의 rollout restart로 모든 파드에 반영한다.
- `/render`의 성공·오류 응답 모두 `Content-Security-Policy: sandbox allow-scripts`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`를 쓴다. `allow-same-origin`·팝업·상단 이동·폼 허용은 없다. authz를 핸들러 안에서 직접 호출하거나 `/render` 전용 예외 처리기로 감싸 authz 403·FastAPI 검증 422·404의 JSON 오류도 같은 헤더를 보장한다. 콘텐츠 형식 검사 앞의 route/응답 표식 분기로 처리하며 원문 HTML은 반환하지 않는다. 합본 실패는 사유를 이스케이프한 간단한 오류 HTML로 낸다.
- `/render`에는 `frame-ancestors`·`X-Frame-Options`를 넣지 않는다. 사용자가 제공한 Chromium spike에서 출처 `null`인 조상 아래 중첩 프레임은 `'self'`·명시 출처·`*` 모두 차단됐다. Referer도 실리지 않아 검사에 쓰지 않는다. 앞단 프록시도 이 두 헤더를 일괄 주입하지 않아야 한다.
- 외부 삽입은 토큰 비공개와 기존 authz로 통제한다. 토큰 유출 뒤 권한 있는 브라우저의 외부 iframe 요청은 허용될 수 있고 `Sec-Fetch-Dest`는 외부 삽입을 구분하지 못한다. 운영 SSO 쿠키 호환성은 아래 사전 시험을 전제로 하며 토큰은 인증을 대체하지 않는다.
- 합본 meta CSP `frame-src`는 렌더 보기만 `'self'`, 다른 합본 호출은 기본 `'none'`이다. `src` 없는 iframe은 현행도 통과한다. 완화는 초기 `src`가 있는 상대 HTML 프레임에만 적용하며 쿼리·해시를 뺀 경로 확장자와 합본기의 `resolve_path`로 검사한다. `about:blank`·`srcdoc`·`data:` 프레임은 계속 거부한다. 다른 지시어는 그대로다.
- `'self'`는 같은 출처 전체를 허용한다. `csp_middleware`는 `text/html`에만 CSP·XFO를 붙이므로 `/api` JSON·`/raw`에는 XFO가 없다. 같은 호스트의 `/cdn`·`/marp`·`/slides`·`/drawio`도 프레임에 들어갈 수 있으며 CDN은 XFO·CSP가 없고 SVG 업로드도 허용한다. 이 문서들은 sandbox는 물려받지만 합본 meta CSP의 네트워크 제한은 물려받지 않는 위험이 남는다. `config.runtime.public_base_url`(`app.py`의 `_render_download_html`·`_prepare_pdf_markdown`이 쓰는 값)로 만든 `https://host/view/render/` 경로 소스로 좁히는 대안은 결정 필요다. sandbox 문서에서의 경로 매칭은 미확인이다.
- 앱·접근 로그는 토큰 경로를 마스킹한다. 인그레스·EFK 로그에 남는 토큰의 위험은 알리고 수용하며 접근권한·보존 기간으로 관리한다. Edge·Firefox·Safari, 헤드리스 전체 화면 동작은 미확인이다. 신뢰 저장소·폐쇄망·외부 네트워크 금지와 기존 잔여 위험 통제는 유지한다.

운영 전제 / 운영 시험(운영 SSO는 미확인, PR #63 이후 남은 결정 C1): `pf`의 조상에 출처 `null`인 sandbox 문서가 있어 `Sec-Fetch-Site`가 cross-site가 되고 `SameSite=Lax`·`Strict` 인증 쿠키가 빠져 로그인으로 리다이렉트될 수 있다. 실제 운영 SSO 쿠키의 `SameSite`·`Secure` 속성으로 Viewer → 첫 sandbox 문서 → `pf` 대상의 2단계 중첩 요청과 링크 클릭을 시험하고 쿠키 송신·리다이렉트·인증 헤더를 확인한다. 로컬·무인증(`authz.enabled=false`) 사용은 이 전제와 무관하며 실제 Chromium에서 동작을 확인했다.

실패 시 아래 대안은 결정 필요이며 아직 선택하지 않았다(상세 `html-embed-design.md` 14.12).

| 대안 | 장점 | 운영 조건·단점 |
|---|---|---|
| SSO 쿠키 `SameSite=None; Secure` | 기존 Ingress 인증·"토큰은 인증을 대체하지 않는다" 원칙 유지 | HTTPS·SSO 정책 변경과 cross-site 쿠키 허용 필요. 브라우저 쿠키 차단, CSRF·외부 삽입 위험 재검토 |
| Ingress 인증의 `/view/render/` 예외 + 토큰 capability | 중첩 요청의 SSO 쿠키 의존 제거 | 토큰 소지만으로 허용해 유출 영향 확대. "토큰은 인증을 대체하지 않는다" 원칙과 앱 `/render` authz 계약 수정 필요. 위조 전달 헤더를 신뢰하지 않도록 설계해야 하며 Ingress 예외만으로는 현재 앱 authz를 통과하지 못함 |

선택 전에는 기존 authz 계약을 유지하며 운영 완료로 판정하지 않는다. 시험 통과 또는 선택한 대안의 보안·배포·시험 계약 반영이 운영 전제다.

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
    storage_mode: full   # full | shallow | blobless | treeless (shallow는 shallow_depth, blobless·treeless는 본문 검색 제외)
    collab:
      enabled: false   # 모든 Viewer 사용자에게 공개 가능한 저장소에만 true (회의 모드·협업 연동 대상)
    html_embed:
      enabled: false
      render_files: false   # enabled와 모두 true여야 Viewer 렌더 보기 허용
      allowed_origins: []
      max_bundle_bytes: 2097152
      xwiki_credential_env: null   # enabled: true이고 최상위 targets.xwiki가 있으면 Viewer 전용 저장소에도 필수(예: XWIKI_HTML_CREDENTIAL), 기본 동기화 계정과 달라야 함

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
    assignee_map:
      hong.gildong: 5
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
  asset_source: cdn                     # cdn(기본) | bundled(경량 실행에서 강제)
  repo_cache_path: "/data/repos"
  max_render_size_bytes: 5242880
  render_token_ttl_seconds: 14400        # 양의 정수, 기본 4시간
  cdn_base_url: "https://synctx.internal/cdn"
  marp_service_url: "http://synctx-marp:8000"   # PDF 다운로드 때 Viewer가 부르는 Marp 내부 주소

cdn:
  storage_path: "/data/cdn"
  max_upload_size_bytes: 50000000  # 50 MB
  upload_auth:
    enabled: true
    required_role: cdn-admin
  allowed_extensions: [".js", ".css", ".woff", ".woff2", ".ttf", ".svg", ".png", ".map"]

marp:
  publish_repo: "https://gitlab.internal/team/published-slides.git"
  # publish_auth:                  # 생략하면 GIT_PUBLISH_TOKEN → repositories[0].auth 순서 (3.4)
  #   type: token
  #   credential_env: GIT_PUBLISH_TOKEN
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

collab:                            # 플랫폼 설정(저장소별 collab.enabled와 함께 적용)
  jitsi:                           # Phase 8.2 영상 회의
    enabled: false
    public_url: "https://meet.synctx.local"  # https://호스트[:포트]만, 경로·계정·쿼리·프래그먼트 불가
    xmpp_domain: ""                # 선택, 빈 값이면 public_url 호스트명(포트 제외). JWT sub에 사용
    presenter_header: X-Forwarded-User   # 아래 허용값·authz 조건 참조
    app_id: synctx                  # 영문·숫자·점·밑줄·하이픈, 1~128자
    secret_env: JITSI_APP_SECRET     # Viewer HS256 서명·Prosody 검증에 같은 앱 키 사용
    room_secret_env: JITSI_ROOM_SECRET   # 방 이름 HMAC용 별도 키
  gateway:
    enabled: false
    adapter: mattermost            # 첫 구현. 어댑터 인터페이스만 두고 다른 메신저는 만들지 않음
    viewer_url: "http://synctx-viewer"   # 회의 목록을 읽는 Viewer 주소(http(s)://호스트[:포트], 경로·계정·쿼리 불가)
    viewer_service_token_env: VIEWER_SERVICE_TOKEN   # Gateway와 Viewer가 같은 이름의 환경변수에서 읽는다
    mattermost:
      url: "https://chat.synctx.local"        # enabled이면 필수
      bot_token_env: MATTERMOST_BOT_TOKEN
      command_token_env: MATTERMOST_COMMAND_TOKEN   # 8.4 슬래시 명령 검증용
    channels:                      # 키는 meeting.channel 형식, enabled이면 1개 이상. 매핑이 없는 채널은 건너뛴다
      team-docs: {team: devteam, channel: team-docs}
    reminders: [1440, 10]          # 시작 몇 분 전에 알릴지(1~10080, 중복 불가, 큰 값부터 정렬)
    post_summary: false            # 회의 후 결정·액션 요약 게시(옵션)
    poll_interval_seconds: 60      # 10 이상
    commands_enabled: true         # /synctx 슬래시 명령(8.4). 토큰이 없으면 이 값과 상관없이 503
    command_rate_limit_per_minute: 10   # 사용자별, 1~600
```

`collab.jitsi`는 기본으로 꺼져 있다. 켜면 HTTPS 출처·app_id·대문자/숫자/밑줄 환경변수 이름을 검사하고 앱 키와 방 이름 키의 슬롯은 겹칠 수 없다. `xmpp_domain`은 선택 호스트명이며 기본값은 public_url 호스트명(포트 제외)이다. 내장 스택에서는 jitsi.host와 같아야 한다. `presenter_header`는 `X-Forwarded-User`(기본값)·`X-Forwarded-Email`·`X-Forwarded-Preferred-Username`만 허용한다. 두 필드의 형식·허용값 검사는 Jitsi가 꺼져 있어도 적용한다.

키 값은 YAML에 넣지 않는다. 각 이름에 `_PREVIOUS`를 붙인 이전 슬롯과 현재·이전 각각의 `_FILE` 변형을 지원한다. 렌더 키와 같은 공통 로더로 UTF-8 32바이트 이상, 직접 값·파일 동시 지정 거부, 파일 끝 줄바꿈 하나(LF/CRLF) 제거를 적용한다. 두 현재 키 중 하나라도 없거나 현재·이전 키가 짧으면 Viewer 시작 오류이며 무작위 폴백은 없다. 두 현재 키는 서로 달라야 한다. 앱 키의 이전 슬롯은 Viewer가 읽기만 하고 쓰지 않는다. Viewer는 현재 앱 키로만 서명하고 Prosody는 현재 키 하나만 검증하므로 이전 슬롯의 현재 소비처는 없다(교체는 12.11). 방 이름 키의 previous도 Viewer가 읽기만 하며 현재 소비처는 없다. 방 이름은 현재 방 이름 키로 계산하므로 방 이름 키 교체 시 방도 바뀐다. Helm `jitsi.jwtSecret`·`roomSecret`이 현재·이전 슬롯을 Viewer에 주입하고 Prosody는 현재 앱 키 하나만 받는다. 공식 이미지 환경변수로 이전 앱 키 동시 검증을 보장하지 않으므로 앱 키 교체는 회의 없는 점검 시간에 Viewer·Prosody를 함께 재생성한다(12.11).

JWT의 `context.user.moderator`는 JSON boolean이다([Jitsi token_affiliation 구현](https://github.com/jitsi/jitsi-meet/blob/master/resources/prosody-plugins/mod_token_affiliation.lua)). Prosody token_affiliation이 moderator를 정한다. Prosody만 JWT를 검증하도록 Jicofo의 기존 `ENABLE_AUTH=0`과 `ENABLE_AUTO_OWNER=0`을 지정한다. A2에서는 설정 주입·키 연결을 정적 검증했을 뿐이며, 이 조합은 실제 Jitsi 기동으로 확인하지 못했다(소스 대조 기준). stable-9823 원본 템플릿이 로컬에 없어 Jicofo의 ENABLE_AUTH 처리와 web ENABLE_P2P 유효성은 확인 필요다. Viewer는 `presenter_header`로 지정한 신뢰 인증 헤더를 `presenters`와 대소문자 무시 비교한다. authz가 꺼져 있으면 moderator가 될 수 없고, 헤더가 없어도 moderator는 false다. 표시 이름용 사용자 정보가 없으면 참석자로 표시한다. 표시 이름은 제어문자 제거 후 최대 80자로 제한한다.

`collab.gateway`가 꺼져 있으면 나머지 값은 검사하지 않는다. 켜면 `mattermost.url`·`channels`가 필수이고 환경변수 이름은 대문자·숫자·밑줄만 허용한다. 토큰 값은 YAML에 넣지 않고 Secret 환경변수로만 준다.

`html_embed`의 출처는 `https://호스트[:포트]`만 허용하고, 저장소 목록은 최상위 목록의 부분집합이어야 한다. 어기면 서비스가 시작되지 않는다. 신뢰 저장소(`enabled: true`)는 운영자만 지정한다(3.6).

`repositories[].html_embed.render_files: bool = False`·`viewer.render_token_ttl_seconds`는 플랫폼 설정이다. 렌더 보기·`/render`는 `enabled`·`render_files`가 모두 true여야 한다. 저장소 안 `.synctx.yaml`로는 켤 수 없다. 현행 공통 validator는 최상위 `targets.xwiki`가 있고 저장소가 `enabled`이면 Viewer 전용 저장소에도 기본 계정과 다른 `xwiki_credential_env`를 요구한다(`config.py`의 `SynctxConfig._check_html_embed_origins`). 실제 XWiki 게시 대상에만 요구하는 방안(대상 판정·라우팅 변경 검증 필요), Viewer 설정 검사를 분리하는 방안(서비스별 설정 계약 관리 증가)은 결정 필요이며 현행 요건은 유지한다.

`SYNCTX_RENDER_SECRET`·`SYNCTX_RENDER_SECRET_PREVIOUS`는 YAML에 값으로 넣지 않고 환경변수 또는 각각의 `_FILE` 변형으로 mounted UTF-8 파일을 참조한다(3.4). 같은 키의 직접 값·파일 동시 지정, 파일 읽기 실패, 32바이트 미만 키, 이전 키만 지정은 시작 오류다. 파일은 마지막 줄바꿈 하나(LF 또는 CRLF)만 제거한다. 키 생성은 `openssl rand -base64 32`를 쓰고 출력 텍스트 자체를 UTF-8 키로 사용하며 다시 디코딩하지 않는다. 현재 키 지정이 없으면 시작 때 무작위 32바이트를 만들어 단일 인스턴스·로컬에서 쓰고 재시작하면 기존 토큰은 무효다. 단일 파드도 롤링 중 구·신 파드 무작위 키가 공존하면 간헐 실패하므로 운영 렌더는 유지되는 공유 Secret을 쓴다. 복수 인스턴스·프로세스는 공유 현재 키가 필수이고 교체 중 보조 키도 공유한다. PREVIOUS는 보통 이전 키지만 교체 준비 단계에는 다음 키를 담는 검증 전용 슬롯이다.

렌더 보기 배포: Helm `viewer.renderTokenSecret`의 `create`·`existingSecret`·`current`·`previous`와 Secret 키 `render-secret`·`render-secret-previous`를 Viewer 환경변수에 연결한다. `create`·`existingSecret` 중복은 거부하고, 공유 키 참조 없는 복수 파드는 `render_files: true` 저장소가 하나라도 있을 때만 거부한다. 렌더를 쓰지 않는 기존 운영 업그레이드에는 후자 검증을 적용하지 않는다. `values-production.yaml`에는 렌더 키 참조를 두지 않는다. 렌더를 켜는 운영 배포가 `--set viewer.renderTokenSecret.existingSecret=<Secret 이름>` 또는 별도 values로 지정하며, 지정하지 않으면 Viewer 파드는 Secret 없이 뜬다. TTL은 `synctxConfig.data`의 `viewer`에 둔다. Docker Compose·`scripts/docker/run-viewer.sh`는 두 키와 `_FILE` 변형을 전달하고 파일 경로 사용 시 해당 파일을 마운트한다. `scripts/local/run_viewer.py`·`scripts/local/run-lite.sh`(실제 생성은 `standalone.py`)는 TTL을 자동 생성 설정에 반영한다. 키를 생성 YAML에 기록하거나 `enabled`·`render_files`를 자동으로 켜지 않는다. 구현 파일별 소유권은 `html-embed-design.md` 13장 WP4를 따른다.

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
0.6.0까지 스키마에 있던 `outbound_feedback.external_importer` 블록은 어떤 코드도 읽지 않아 제거했다. 기존 `.synctx.yaml`에 남아 있어도 오류 없이 무시된다.
외부 Importer에는 다음 정책을 적용한다. 참조 구현 `scripts/external_importer.py`는 이 정책을 코드에 고정해 두었고, 실행 설정은 환경변수로만 받는다(`scripts/README.md`).

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
| `assignee` | 선택 | `targets.openproject.assignee_map`(이름 → 사용자 id)으로 해석. 표에 없으면 경고만 남기고 담당자를 넣지 않는다 |
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

Jitsi 참여 API `GET /api/meetings/{repo_id}/{meeting_id}/jitsi`는 기존 Viewer 그룹 권한만 사용하며 Bearer 서비스 토큰으로 면제하지 않는다. 협업 저장소 기본 브랜치의 검증된 단일 회의만 허용한다(없음·검증 실패·중복 id는 404). 시작 15분 전부터 종료 후 1시간이 되기 전까지 발급하고 취소 회의는 거부한다. 불가 시 409의 `detail: {reason, message}`로 `disabled`·`cancelled`·`too_early`·`ended`를 알린다. 회의 읽기 실패는 `unavailable` 사유와 503으로 반환한다. 성공 응답은 `{room, jwt, domain, public_url, moderator, expires_at}`이며 오류를 포함해 `Cache-Control: no-store`다. 방은 별도 키의 `HMAC-SHA256(repo_id|meeting_id)` 앞 32 hex, JWT는 HS256·iss/aud=app_id·sub=Jitsi 호스트·room 고정·iat·nbf=발급-10초·exp=min(회의 종료+1시간, 발급+4시간)이다. JWT·키는 로그나 Gateway 응답에 넣지 않는다. 메트릭은 `synctx_viewer_jitsi_tokens_issued_total`·`synctx_viewer_jitsi_token_failures_total{reason}`이다.

회의 목록의 각 항목에 비밀이 아닌 `jitsi: {enabled, joinable_from, joinable_until}`을 추가한다. Gateway는 필드가 없는 이전 응답을 안전하게 영상 회의 비활성으로 처리한다. 회의 카드·발표 화면은 참여 불가 사유를 텍스트로 표시하고, 내장 패널은 설정된 Jitsi의 `/external_api.js`만 사용한다. 켰을 때만 CSP의 script-src/frame-src와 camera/microphone/display-capture 권한에 해당 출처를 추가한다(connect-src 추가 없음). 껐을 때 기존 응답 헤더는 그대로다. 발표자 따라가기는 Jitsi가 확인한 moderator의 endpoint 메시지만 받으며, 문서·schema·크기·시각을 검사하고 수동 스크롤 시 끈다. 메모 앵커와 본문 DOM은 변경하지 않는다.

Viewer Bearer 서비스 토큰(`collab.gateway.viewer_service_token_env`)은 `/api/meetings`, 그 하위 `/api/meetings/{repo_id}/{meeting_id}/evidence`, `/api/search`에만 허용한다. 그 밖의 문서·원문·렌더·저장소 API는 기존 Viewer 권한을 요구한다. 검색은 서비스 토큰일 때 협업 저장소·기본 브랜치로 강제 제한하며 `ref`를 거부한다. `repo`를 생략하면 전체 협업 저장소를 검색하고, 지정한 id가 없거나 비협업이면 404다. 검색 결과는 저장소 정보와 `path`만 반환하고 줄 번호·본문 발췌는 제외한다. 근거 API도 서비스 토큰이면 참조 대상 저장소를 협업 저장소로 제한해 비협업 문서 제목·경로를 노출하지 않는다. 읽지 못하거나 설정에 없는 저장소·서비스 토큰 범위 밖의 근거는 `omitted` 건수와 `status=partial`로 알리며, 일반 Viewer 화면의 근거 동작은 유지한다.

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
    references:                       # 선택, 근거 링크의 접두어 매핑(최대 20개)
      "../사내규정/": "internal-regs:사내규정/"
---
```

- Harness Validator가 필드 형식과 `meeting.id` 중복을 검사한다.
- Viewer 회의 모드는 `repositories[].collab.enabled: true`인 저장소의 기본 브랜치에서만 켜진다.
- 회의 대시보드(Phase 8.1c)는 `collab.enabled: true` 저장소의 기본 브랜치에서 `synctx.type: meeting`이고 위 검증을 통과한 문서를 `git grep`(`meeting:`)으로 찾아 모은다. 숨김 경로와 `*.memo.md`는 후보에서 뺀다. 같은 폴더의 `<문서명>.memo.md`가 있으면 회의 모드 `.md` 내보내기의 `synctx-memo-data` 블록(스키마 1, `doc.meeting_id`가 일치)에서 삭제되지 않은 결정·액션(담당자·기한·인용)을 읽는다. 문서 512 KiB·메모 파일 256 KiB·메모 1000개·저장소당 회의 500건을 넘으면 건너뛰거나 일부만 표시하고 사유를 보인다. 메모는 브라우저 localStorage에 남고 서버는 저장하지 않으므로 대시보드에는 저장소에 커밋된 내보내기만 반영된다. 액션 완료 상태는 추적하지 않는다.
- 근거 미리보기·근거 모음(Phase 8.1b)은 검증을 통과한 `synctx.meeting`이 있는 문서에서 쓴다. 일반 보기·발표 화면에서는 회의 모드가 꺼진 저장소·기본이 아닌 ref에서도 쓰고, 회의 모드와 메모 포함 HTML(`meeting-export`)은 회의 모드가 켜진 곳에서만 열리므로 그곳에서 쓴다.
- `references`는 근거가 다른 저장소에 있을 때 쓰는 접두어 매핑이다. 키는 본문 링크 href의 원문 접두어(예: `../사내규정/`), 값은 `<저장소 id>:<저장소 루트 기준 경로 접두어>`(예: `internal-regs:사내규정/`)이며 가장 긴 키가 이긴다. 값의 경로에는 `..`·절대경로를 쓸 수 없고 20개를 넘으면 회의 정보 오류다(Harness Validator와 Viewer가 같은 규칙으로 검사). 매핑된 링크는 근거가 아니어도 `/view/<저장소>/<경로>`로 바뀐다(저장소 루트·폴더를 가리키면 `/view/<저장소>?path=<폴더>`). 같은 저장소 안 상대경로 링크는 매핑 없이 그대로 쓴다.
- 매핑이 설정에 없는 저장소를 가리키면 그 매핑만 무시하고 그 접두어로 시작하는 링크는 href 원문 그대로 둔다. 문서 머리에 "근거 저장소를 찾지 못했습니다: <id>" 안내를 한 번 보인다. 다른 저장소 근거는 그 저장소의 기본 ref에서 읽는다(매핑 값에 ref를 두지 않는다).
- 근거 원문 문서의 규약:
  - 문서명은 front matter `title:` → 없으면 첫 `#` 제목(H1) → 없으면 파일 이름(확장자 제외) 순으로 정한다. 근거 문서에는 `title:`을 두기를 권한다.
  - 링크의 `#앵커`는 먼저 원시 HTML 명시 앵커 `<a id="…"></a>`를 찾고, 없으면 Viewer가 제목에 붙이는 id(GitHub 방식 slug, 중복은 번호)로 찾는다. 명시 앵커는 조문 `aN`(예: `a1`), 대비표 행 `cN-N-N`(예: `c3-1-4`), 외부 자료 조문 `efsr-*`(예: `efsr-8-3`)처럼 문서 안에서 유일하게 둔다. 앵커가 없거나 못 찾으면 "문서 첫 부분"을 대신 보이고 위치에 안내를 붙인다.
  - 발췌 범위: 제목이면 같거나 상위 제목 직전까지, 표의 행이면 머리글 2줄과 그 행, 굵은 제목 줄(`**제9조(목적)**`·`**<표 …**`)이면 다음 제목·`---`·명시 앵커·다음 조문 줄(`**제N조`·`**제N조의M`) 직전까지, 그 밖의 줄이면 다음 빈 줄 두 개 또는 위 경계까지다. 발췌 안의 `<div class="commentary">` 해설 블록은 뺀다. 발췌 안의 `synctx-html` 블록(Viewer가 iframe으로 바꾸는 펜스, 다른 펜스 안의 예시는 제외)은 설정 줄 대신 "[인터랙티브 HTML: <title>]" 한 줄로 보이고, `title`이 없으면 `src` 파일 이름을 쓴다(`src`가 없거나 YAML 객체가 아닌 블록은 원문 그대로). HTML 본문은 "원문 열기"로 본다. 발췌 하나는 80줄·16 KiB를 넘으면 줄이고 "…(발췌가 길어 줄였습니다)"를 붙인다.

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
| `R` | old path `D`와 new path `A`로 나눠 처리한다(old 쪽은 delete policy를 따른다) |

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

1. 새 상태를 같은 디렉토리의 임시 파일 `{repo_id}.tmp.{pid}`에 쓴다.
2. 파일 내용을 `fsync`한다.
3. 기존 `{repo_id}.json`이 있으면 `{repo_id}.bak`으로 rename한다.
4. 임시 파일을 `{repo_id}.json`으로 rename한다.
5. rename 후 부모 디렉토리를 `fsync`한다.

읽기 시 `{repo_id}.json`이 없거나 JSON 파싱에 실패하면 `{repo_id}.bak`을 읽고 `state_recovered_from_backup` 로그를 남긴다.

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
| `GET /readyz` | 트래픽 받을 준비 확인. 준비 전에는 503(Viewer는 시작 때 저장소 clone·fetch가 끝나야 ready) |
| `GET /metrics` | Prometheus metrics |
| `GET /version` | 서비스 버전(`{"version": "0.7.0"}`) |

### 7.2 Viewer Service

| Method | Path | 권한 | 설명 |
|--------|------|------|------|
| GET | `/api/repos` | viewer | 접근 가능한 repo 목록 |
| GET | `/api/repos/{repo_id}/branches` | viewer | 브랜치 목록과 기본 브랜치(`{"branches", "default"}`) |
| GET | `/api/repos/{repo_id}/tree?ref=&path=` | viewer | Git tree 조회 |
| GET | `/api/repos/{repo_id}/files?ref=` | viewer | 볼 수 있는 파일 경로 목록(`{"files": [...]}`, 파일 찾기용). 조회 실패 502 |
| GET | `/api/search?q=&repo=&ref=&collab_only=` | viewer 또는 서비스 토큰 | 본문 검색(`{"query", "repos": [{"id", "name", "status", "truncated", "matches": [{"path", "line", "before", "match", "after"}]}]}`). `repo`가 없으면 모든 저장소. 검색어 2~100글자·줄바꿈 없음, `ref`는 `repo`와 함께만 쓰며 어기면 400, 모르는 저장소 404. `collab_only=true`는 협업 저장소만 검색한다. 서비스 토큰이면 이 필터를 강제하고 `ref`를 지정하면 400(빈 값 포함), 기본 브랜치만 검색한다. 지정한 `repo`가 없거나 비협업 저장소이면 404이며, `matches`는 `{"path"}`만 반환해 줄 번호·본문 발췌를 제외한다. 일반 Viewer 권한의 기존 전체 저장소·ref·발췌 응답은 유지한다. |
| GET | `/search?q=&repo=&ref=&ref_repo=` | viewer | 본문 검색 결과 HTML 페이지(`search`는 예약 경로) |
| GET | `/api/repos/{repo_id}/render?ref=&path=&theme=&mode=` | viewer | Markdown HTML 렌더링 |
| GET | `/render/{token}/{repo_id}/{path}` | viewer + 렌더 토큰 | iframe 전용 HTML 합본. 상대 링크도 같은 토큰 SHA 사용. 공개 주소는 `/view/render/…` |
| GET | `/raw/{repo_id}/{path}?ref=` | viewer | 원본 파일. png·jpg·gif·webp·svg는 그 이미지 형식, 나머지는 `application/octet-stream`. SVG에는 스크립트를 막는 CSP(`default-src 'none'; style-src 'unsafe-inline'; sandbox`)와 `nosniff` |
| GET | `/api/repos/{repo_id}/download?path=&format=raw\|html\|pdf&ref=` | viewer | 원본·변환 다운로드(기본 `raw`, `pdf`는 Marp 호출) |
| GET | `/api/repos/{repo_id}/meeting-export?path=&theme=` | viewer | 회의 메모 포함 HTML 틀(오프라인, 메모 없음, 근거 모음·말풍선 포함). 회의 모드 조건 불충족 404 |
| GET | `/api/meetings?repo=&status=&channel=&presenter=&q=&from=&to=&order=` | viewer 또는 서비스 토큰 | 회의 대시보드 데이터(서비스 토큰: `Authorization: Bearer <값>`이 `collab.gateway.viewer_service_token_env`가 가리키는 Viewer 환경변수와 상수시간 비교로 일치하면 그룹 헤더 검사를 건너뛴다. 이 면제는 회의 목록·하위 근거 목록·검색 API에만 적용하고 변수가 비어 있으면 어떤 값도 통과하지 못한다. `{"now", "repos": [{"id", "name", "status", "truncated"}], "meetings": [{"repo_id", "repo_name", "path", "id", "title", "starts_at", "ends_at", "duration_minutes", "channel", "presenters", "cancelled", "status", "url", "present_url", "decisions", "actions", "memo_error", "jitsi": {"enabled", "joinable_from", "joinable_until"}}], "invalid": [{"repo_id", "repo_name", "path", "reason"}]}`). `collab.enabled` 저장소의 기본 브랜치만, `status`는 `upcoming`·`ongoing`·`ended`·`cancelled`(그 밖은 400), `repo`를 지정했는데 없거나 collab 대상이 아니면 404, 일시는 한국 시간, `from`·`to`는 `YYYY-MM-DD`(한국 날짜, 포함) |
| GET | `/api/meetings/{repo_id}/{meeting_id}/jitsi` | viewer (서비스 토큰 면제 없음) | 단기 Jitsi 참여 JWT와 방·출처·moderator·expires_at(5.5). 협업 저장소·기본 브랜치·검증된 회의만, 없으면 404, 꺼짐·취소·시간 창 밖이면 사유 코드와 409. 모든 응답 no-store |
| GET | `/api/meetings/{repo_id}/{meeting_id}/evidence` | viewer 또는 서비스 토큰 | 협업 저장소 기본 브랜치의 검증된 회의만. 없거나 검증 실패면 404. 기존 evidence 빌더를 재사용해 `{"meeting_id", "status": "ok" 또는 "partial" 또는 "failed", "omitted": 생략 건수, "evidence": [{"title", "repo_id", "path", "url"}]}` 반환. 서비스 토큰은 참조 대상도 협업 저장소로 제한한다. 제목은 `Entry.doc`, URL은 경로·앵커로 만든 Viewer 주소. 발췌(`html`·`loc`)·인용 문맥(`cites`)·메모 본문은 제외한다. 읽기 실패·설정에 없는 저장소·서비스 토큰 범위 밖의 참조는 문서와 디코딩한 앵커별 중복을 합쳐 `omitted`로 세며, 한 건 이상이면 빈 목록이어도 `status=partial`. 렌더·빌드 전체 실패 시 200, 빈 목록과 `status=failed`, `omitted=0` |
| GET | `/meetings?tab=&repo=&channel=&presenter=&owner=&q=&from=&to=&order=` | viewer | 회의 대시보드 HTML 화면(`meetings`는 예약 경로) |
| GET | `/api/repos/{repo_id}/board/{version}` | viewer | 스크럼 보드 HTML |
| GET | `/`, `/{repo_id}?ref=&path=`, `/{repo_id}/{path}?ref=&theme=&mode=` | viewer | 화면 경로: 저장소 목록, 폴더 화면, 문서 화면(`mode=meeting` 회의 모드, `mode=present` 발표 화면, 신뢰 저장소 HTML의 `mode=render`, 새 탭 `layout=full`) |
| POST | `/api/repos/{repo_id}/webhook` | viewer + service token | Git push 알림(저장소 fetch). `SYNCTX_SERVICE_TOKEN`이 있으면 `X-Service-Token`이 같아야 한다 |

보안:

- `path`는 repo root 밖으로 벗어날 수 없다.
- 렌더링 최대 파일 크기를 넘으면 413을 반환한다.
- 본문 검색은 저장소마다 최대 50건·git 출력 2 MiB·기한 3초(ref 확인과 실행 대기 포함)이고, 동시에 도는 검색은 프로세스(파드)마다 4개까지다. 부분 복제(`blobless`·`treeless`) 저장소는 모든 파일 내용을 원격에서 받아 오게 되므로 검색하지 않고 `unsupported`로 돌려준다. 결과는 볼 수 있는 파일만, 제어 문자나 점으로 시작하는 경로 조각이 없는 것만 싣는다.
- 화면 경로(`/`, `/{repo_id}`, `/{repo_id}/{path}`)의 오류(404·413·415·502·500)는 HTML 안내 화면으로, `/api/...`·`/raw/...` 경로는 JSON으로 돌려준다. HTML 안내 화면, 파일 목록 API의 502 응답, 검색 응답(`status`만 돌려줌)에는 예외 문구와 인증 정보를 넣지 않고, 검색 경로가 새로 남기는 로그는 예외 종류만 담는다. `/api/...`·`/raw/...`의 JSON 오류 응답 `detail`은 상태별 고정 문구("File not found", "Repository refresh failed" 등)이고 예외 메시지를 담지 않으며, 이때 남기는 로그는 저장소 id와 예외 종류만 담는다. git 호출이 실패해 생기는 예외(`CalledProcessError`·`TimeoutExpired`)의 명령 목록에는 인증 헤더가 든 설정 인자(`-c http.extraHeader=…`)를 넣지 않고 URL 인증정보를 가리므로, 예외를 문자열로 만들어도 토큰이 나오지 않는다. git 호출 로그(`git_command_failed`·`fetch_ref_failed`)는 `https://user:pass@host`의 비밀번호, `https://TOKEN@host`의 사용자 정보, Authorization 헤더 값을 가린 채 남는다.
- HTML은 sanitization 또는 신뢰 저장소 정책 중 하나를 명시한다. 운영 기본값은 신뢰 저장소 + CSP다.
- 저장소의 SVG는 `/raw/`에서 `image/svg+xml`로 주되 `sandbox` CSP와 `nosniff`를 붙인다. 본문의 `<img>`로는 스크립트가 원래 실행되지 않고, 주소를 직접 열어도 sandbox 때문에 실행되지 않는다.
- `synctx-html` 블록은 sanitizer를 거친 뒤 자리표시를 sandbox iframe으로 채운다. 본문에 직접 쓴 `<iframe>`·`<script>`는 계속 지운다. CSP는 `font-src`에 `data:`, 각 지시어에 최상위 `html_embed.allowed_origins`를 더한다.
- 렌더 보기: 실제 `mode=render` 화면만 `frame-src 'self'`로 바꾸고 XFO `DENY`는 유지한다. 나머지 Viewer HTML 화면은 `frame-src 'none'`·XFO `DENY`를 유지한다. `/render`는 `scope["route"]`의 경로 함수·이름 또는 응답 표식으로 식별하고 `csp_middleware`의 CSP·XFO 덮어쓰기에서 제외해 JSON 오류에도 3.6의 헤더를 쓴다. standalone Mount 아래 `/view/render/…`와 K8s `/render/…` 양쪽을 처리하며 경로 문자열만으로 판별하지 않는다. 소스 보기의 "렌더링 / 새 탭에서 렌더링" 버튼은 `enabled`·`render_files`가 모두 true인 저장소의 HTML에만 보인다. 새 탭은 같은 `mode=render&layout=full` 화면이며 `/render`를 직접 열지 않는다.
- 렌더 보기: `/render`의 토큰·요청 대상·신뢰 거부는 403, 비HTML은 415, 경로 거부는 400, 크기 초과는 413, 파일 없음은 404다. `BundleError`는 크기 413·누락 404·그 밖의 합본 실패 422로 이스케이프한 오류 HTML을 낸다. 토큰 만료는 원래 Viewer 화면 새로 고침 안내를 준다. 루트 초과 `../`가 다른 repo_id로 풀려 서명이 불일치하면 "문서 주소 또는 토큰이 유효하지 않습니다. 원래 Viewer 화면에서 다시 여세요"로 구분한다. 오류에도 같은 sandbox·no-referrer·no-store 헤더를 붙인다. 크게 보기 래퍼·버튼은 Viewer 화면에만 넣고 `download?format=html`에는 넣지 않는다.
- 회의 메모는 서버로 보내지 않는다. 브라우저 localStorage에만 저장하고, `<script>` 안 JSON은 `<`·`>`·`&`·U+2028·U+2029를 유니코드 이스케이프로 인코딩한다.
- 회의 문서의 근거 발췌는 같은 저장소와 `synctx.meeting.references`가 가리키는 **설정에 있는 저장소**의 Git blob만 읽는다. 없는 저장소·파일, `..`으로 저장소 루트를 벗어나는 경로, 제어 문자나 점으로 시작하는 경로 조각, `viewer.max_render_size_bytes`를 넘는 파일은 근거로 쓰지 않는다. 일반 Viewer 권한은 저장소 단위가 아니라 `viewer` 역할 하나라 다른 저장소 발췌를 보여 줘도 권한이 넓어지지 않는다. 근거 API의 서비스 토큰 경로는 참조 대상도 협업 저장소로 제한하며, 범위 밖 저장소는 읽지 않는다. 한 문서에서 처리하는 근거 링크는 200개, 서로 다른 근거 파일은 50개까지다.
- 근거 발췌는 렌더링한 뒤 **발췌 전용 허용 목록 정화**를 거친다. 허용 태그(문단·강조·코드·인용·목록·표·제목·`hr`·`span`·`div`) 밖의 태그는 글자만 남기고, `script`·`style`·`iframe`·`object`·`embed`·`template`·`noscript`는 내용째 지우며, 모든 속성을 지운다(표의 숫자 `colspan`·`rowspan`만 남김). `a`는 글자만, `img`는 `[그림: alt]` 글자로 바꾸므로 `javascript:` 링크·외부 이미지·`style`이 남지 않는다. 말풍선·근거 모음 데이터는 `<script type="application/json">` 안에서 위 유니코드 이스케이프로 넣고, 메모 포함 HTML의 근거 부분에는 외부 참조(`http(s):`)가 없다.

### 7.3 CDN Service

| Method | Path | 권한 | 설명 |
|--------|------|------|------|
| GET | `/cdn/{path}` | - | 정적 파일 서빙 |
| GET | `/api/cdn/manifest` | - | 라이브러리 manifest |
| GET | `/api/cdn/list`, `/api/cdn/list/{library}` | - | 라이브러리 목록, 라이브러리의 version별 파일 수 |
| GET | `/api/cdn/admin` | - | 업로드·삭제 관리 화면(동작은 아래 cdn-admin API를 부른다) |
| POST | `/api/cdn/upload?force=false` | cdn-admin | 파일 업로드(`file`+`path` 한 개 또는 `files`+`base_path` 여러 개). 기존 파일 교체는 `force=true` 필요 |
| POST | `/api/cdn/scan` | cdn-admin | 저장소에 직접 놓인 파일을 manifest에 등록 |
| DELETE | `/api/cdn/{path}` | cdn-admin | 파일 삭제 |

조회 경로(`-`)는 CDN 앱이 역할을 검사하지 않는다. cdn-admin 검사는 `authz.enabled`와 `cdn.upload_auth.enabled`가 모두 켜졌을 때만 하고, `cdn_admin`·`platform_admin` 그룹을 받는다.

운영 기본값:

- version path는 immutable이다.
- 기존 파일이 있고 `force=false`이면 409를 반환한다.
- `force=true`는 `cdn-admin` 권한, 변경 사유, audit log를 요구한다.
- 삭제는 파일을 지우고 manifest 항목에 `disabled: true`와 `deleted_at`을 남긴다(audit log 포함).
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
| POST | `/api/marp/convert` | publisher | PDF/HTML 변환(`{"markdown", "format": "pdf"\|"html"}`) |
| POST | `/api/marp/preview` | publisher | HTML 미리보기 |
| POST | `/api/marp/publish` | publisher | HTML 생성 후 publish repo push(`{"markdown", "filename"}`) |
| GET | `/api/marp/published` | publisher | 출판된 HTML 목록 |
| DELETE | `/api/marp/published/{filename}` | publisher | 출판 취소(파일 삭제 커밋 후 push) |
| GET | `/api/marp/themes` | - | 사용 가능 테마(앱이 역할을 검사하지 않는다) |

출판 규칙:

- publish·출판 취소는 `marp.worktree_path` 안의 `.publish.lock` 파일 lock(`O_CREAT|O_EXCL`)을 잡는다. 이미 진행 중이면 409를 돌려준다. Kubernetes Lease lock은 아직 쓰지 않는다.
- 커밋한 뒤 `pull --rebase`로 원격 변경을 받고 push한다.
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

구현 상태: Webhook Receiver 서비스와 Helm 리소스(Deployment·ServiceAccount·Ingress 경로)는 아직 없다. 지금은 CronJob과 위 수동 Job으로만 동기화한다.

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

### 7.7 Collab Gateway (Phase 8.3 선택)

Collab Gateway는 회의 문서(Phase 8.1)를 Mattermost 채널과 이어 준다. 새로 만드는 데이터 저장소가 없다. 회의 정보는 Viewer의 `GET /api/meetings`에서 읽고, 검색·근거 명령은 Viewer 검색·근거 API를 추가로 읽으며(Git이 source of truth), 이미 보낸 알림만 자기 상태 파일에 둔다.

- **실행 형태**: Deployment 1개(복제본 고정, 교체 전략 `Recreate`)와 PVC 한 개(`/data/collab-gateway`). 폴링 주기는 `collab.gateway.poll_interval_seconds`(기본 60초)이다. `/healthz`·`/readyz`·`/metrics`만 열고 외부에서 들어오는 API는 없다(8.4에서 추가). Ingress 경로를 만들지 않는다.
- **읽기**: `Authorization: Bearer <VIEWER_SERVICE_TOKEN>`으로 `/api/meetings?from=<오늘-8일>`을 읽는다. 실패하면 이번 주기를 건너뛰고 `synctx_collab_viewer_fetch_failures_total`을 올린다. 형식이 잘못된 항목은 건너뛴다.
- **채널 매핑**: 회의의 `meeting.channel`을 `collab.gateway.channels`에서 `{team, channel}`로 바꾼다. 매핑이 없으면 그 회의를 건너뛰고(`synctx_collab_unmapped_channel_total`, 경고 로그는 채널당 프로세스 시작 뒤 한 번) 상태에도 남기지 않는다.
- **알림 종류**:

| 종류 | 보내는 때 | 중복 방지 키 |
|------|-----------|--------------|
| 예고 `reminder` | 시작 전 `reminders`(분)마다 한 번. 시작하지 않은 회의에서 아직 안 보낸 시점이 여럿이면(장애 복구 등) 가장 가까운 시점 하나만 보내고 나머지는 건너뜀으로 기록 | `reminder:<분>:<시작 시각>` (일시가 바뀌면 키가 달라져 다시 보낸다) |
| 변경 `changed` | 제목·일시·길이·발표자·취소 해제가 바뀌고 회의가 끝나기 전 | `changed:<새 상태 해시>` |
| 취소 `cancelled` | 알려진 회의가 취소로 바뀌고 끝나기 전 | `cancelled` (취소 해제 시 지움) |
| 요약 `summary` | `post_summary`가 켜져 있고 회의가 끝난 뒤 7일 안에 `.memo.md` 결정·액션이 있을 때(메모 오류가 있으면 제외) | `summary:<내용 해시>` (내용이 바뀌면 다시 보낸다) |

- **처음 본 회의**: 기준 상태만 저장하고 변경·취소·요약은 알리지 않는다(Gateway를 처음 켤 때 알림이 쏟아지지 않게 한다). 처음부터 취소된 회의, 이미 끝난 회의의 요약도 보내지 않는다.
- **중복 방지와 실패 처리**: 발송에 성공한 직후 상태 파일(원자적 교체, 권한 0600)에 키를 쓴다. 발송 성공과 저장 사이에 프로세스가 죽으면 같은 알림이 한 번 더 나갈 수 있다(최소 한 번 전달). 실패하면 기록하지 않고 다음 주기에 다시 시도하며, 같은 키가 연속 10번 실패하면 포기로 기록한다(`gave_up`). 변경 알림이 실패하는 동안은 기준 상태를 갱신하지 않아 다시 시도한다. 상태 파일이 읽히지 않으면 `.corrupt-<시각>`으로 옮기고 새로 시작한다(그 뒤에는 처음 본 회의 규칙이 적용된다). 마지막으로 본 지 90일이 지난 항목은 지운다. 상태 파일 잠금을 다른 프로세스가 쥐고 있으면 그 주기를 건너뛴다.
- **Mattermost 호출**: 봇 토큰으로 `GET /api/v4/teams/name/{team}/channels/name/{channel}`(채널 id 캐시) 뒤 `POST /api/v4/posts`. 연결 오류·429(`Retry-After`는 30초까지만 따름)·5xx는 호출 안에서 최대 3번 재시도하고, 401·403(인증)·404(채널 없음)·그 밖의 4xx는 재시도하지 않는다. 채널 게시가 영구 오류로 실패하면 캐시한 채널 id를 버린다. 토큰과 응답 본문은 로그·오류 메시지에 남기지 않는다.
- **메시지 안전**: 메시지에는 제목·일시·발표자·저장소 이름과 문서·발표 화면 링크만 담는다(요약 알림만 결정·액션 글을 추가). 문서에서 온 모든 글은 줄바꿈·제어 문자를 공백으로 바꾸고 마크다운 특수문자를 이스케이프하며 `@` 뒤에 폭 없는 공백을 넣어 `@channel`·`@all` 멘션과 링크 주입을 막는다. 제목·필드는 200자, 요약 항목은 300자·10건까지 보인다.
- **보안**: 토큰은 YAML이 아니라 Secret의 환경변수(`MATTERMOST_BOT_TOKEN`, `VIEWER_SERVICE_TOKEN`, `MATTERMOST_COMMAND_TOKEN`)로만 받고, 켰는데 비어 있으면 시작이 실패한다(값은 오류에 싣지 않는다). Helm NetworkPolicy는 DNS·Viewer(8000)·Mattermost 포트로만 나가게 제한한다. Viewer의 서비스 토큰은 `/api/meetings`와 그 하위 근거 목록 API, `/api/search`에만 통한다(5.5). 기존 Viewer(8000) 호출과 Secret을 그대로 사용한다.
- **운영 한계**: Docker Compose 배포는 아직 지원하지 않는다. 로컬 minikube용 Mattermost 시뮬레이션 스크립트(`deploy-deps.sh --mattermost`, `setup-mattermost.sh`, `verify-collab.sh`)가 있다. 공식 Mattermost 이미지가 amd64 전용이라 ARM64 개발 기기에서는 qemu 에뮬레이션(binfmt)이 있어야 하고, 없으면 `exec format error`로 기동하지 못한다(local-development.md 5.2). Apple Silicon의 `synctx` minikube에서 에뮬레이션으로 Mattermost 10.11.19를 띄워, 봇 토큰으로 Gateway의 Mattermost 어댑터가 올린 시험 글이 채널에서 읽히는 것까지 확인했다. 2026-10-29에는 같은 클러스터에 Viewer·Gateway 파드를 함께 배포하고, `meeting-docs`의 임시 브랜치에 올린 가상 회의로 시작 안내·변경·취소 알림이 채널에 도달하는 것과 Gateway 지표(Viewer 조회 실패 0회)를 확인했다. 요약 알림(`post_summary`, 로컬은 `values-local.yaml`에서 켠다)은 2026-10-30에 종료된 가상 회의로 Gateway 상태 파일에 `summary:` 전송 기록이 남고 실패가 0건인 것까지 확인했다(채널 표시 문구는 미확인). Alert 규칙은 `promtool test rules`로 확인한다.

#### 7.7.1 `/synctx` 명령 (Phase 8.4)

Mattermost 슬래시 명령 `/synctx`는 같은 Gateway가 `POST /api/collab/mattermost/command`로 받는다. 알림 폴링이 읽어 둔 `/api/meetings` 데이터로 회의를 찾고, 검색·근거 명령은 서비스 토큰으로 Viewer API를 추가 호출한다.

| 명령 | 응답 |
|------|------|
| `/synctx 오늘` | 이 채널에 매핑된 회의 중 오늘(한국 날짜)에 시작하는 것(시간순, 상태: 예정·진행 중·종료·취소) |
| `/synctx 예정 [일수]` | 지금부터 N일(기본 7, 1~30) 안에 진행 중이거나 시작하는 회의를 날짜별로 |
| `/synctx 회의 <id 또는 제목>` | 회의 정보(일시·상태·발표자·저장소·결정/액션 건수)와 문서·발표 화면 링크 |
| `/synctx 참여 [id]` | 채널 안 회의의 문서·발표 화면 링크와 「문서 화면의 참여 버튼으로 입장」 안내만(JWT·방 이름 없음). 생략하면 진행 중인 회의, 없으면 가장 가까운 취소되지 않은 예정 회의. id 지정은 기존 id·제목 해석 사용. 영상 회의 꺼짐·시간 창 밖·취소 사유 표시 |
| `/synctx 정리 <id 또는 제목>` | 결정사항·액션 아이템(담당·기한). `.memo.md`가 없으면 안내, 읽기 오류면 읽지 못했다는 안내 |
| `/synctx 검색 <키워드>` | 요청 채널의 회의가 속한 저장소 id를 정렬해 최대 5개까지 각각 `GET /api/search?q=&repo=<id>&collab_only=true`로 순차 호출. 경로·링크 최대 5건(중복 문서는 한 번, 본문 발췌 없음), 초과하면 `… 외 N건` 표시 |
| `/synctx 근거 <id 또는 제목>` | 채널 안 회의를 기존 id·제목 해석으로 찾고 `GET /api/meetings/{repo_id}/{meeting_id}/evidence` 호출. 근거 문서 제목·링크 최대 20건, 초과하면 `… 외 N건` 표시(원문 발췌·메모 없음). `partial`이면 목록이 비어도 "일부 근거를 읽지 못했습니다(N건 생략)"을 붙이고, 정상 응답의 빈 목록만 근거 없음으로 안내 |
| `/synctx 도움말` | 명령 목록 |

영어 별칭 `today`·`upcoming`·`meeting`·`join`·`summary`·`search`·`evidence`·`help`를 받는다.

- **인증**: form 본문의 `token`을 환경변수 `MATTERMOST_COMMAND_TOKEN`과 상수시간으로 비교한다(`mattermost.command_token_env`). 불일치·누락은 401(`synctx_collab_commands_total{result="unauthorized"}`, 본문은 읽지 않은 것처럼 처리), 토큰이 없거나 `collab.gateway.commands_enabled: false`이거나 Gateway가 꺼져 있으면 503이다(알림 기능은 그대로 동작). 필수 필드(`team_domain`·`channel_name`·`user_id`)가 없으면 400, 본문이 16 KiB를 넘거나 필드가 50개를 넘으면 413·400이다.
- **채널 범위**: 요청의 `team_domain`·`channel_name`(소문자로 비교)을 `channels`에서 거꾸로 찾아, 그 채팅 채널에 매핑된 `meeting.channel` 키들의 회의만 보인다(키가 여럿이어도 된다). 매핑이 없으면 회의 목록을 읽지 않고 "이 채널에 연결된 회의가 없습니다"만 답한다. 다른 채널 회의는 id를 알아도 "찾지 못했습니다"로 답한다. 검색은 이 목록에서 채널 키가 맞는 회의들의 저장소 id 집합(정렬 후 최대 5개)으로 요청 대상을 제한해 채널과 무관한 저장소는 검색하지 않는다. 응답도 요청 저장소로 먼저 거른 뒤 링크를 검증한다. 폴링 조회 기간(오늘-8일)·수집 상한 밖 회의만 있는 저장소는 검색 대상에 포함되지 않는다.
- **응답**: 항상 HTTP 200에 `{"response_type": "ephemeral", "text": ...}`(요청자에게만 보임)이다. 한 응답은 목록 20건·후보 5건·검색 5건·근거 20건·15000자까지며 검색·근거의 표시 상한 초과 건수는 `… 외 N건`으로 알린다. 검색·근거 항목의 경로·앵커 검증 실패(역슬래시·제어 문자 등)는 해당 항목만 생략하고, 로그에는 생략 건수만 남긴다. 회의 제목·메모·발표자·검색 경로·근거 제목은 알림과 같은 이스케이프를 거친다(멘션·링크 주입 방지). 예상하지 못한 오류는 "처리하지 못했습니다"로만 답하고 내용을 싣지 않는다.
- **응답 시간(3초 제한)**: 폴링이 마지막으로 읽은 목록이 `poll_interval_seconds × 3` 안이면 그대로 쓰고, 오래됐으면 Viewer를 직접 읽는다. 목록 조회와 저장소별 검색·근거 추가 호출 모두를 하나의 `asyncio.timeout` 안에서 합쳐 2초로 제한하고, 추가 HTTP 요청 자체도 2초 타임아웃을 둔다. 실패·타임아웃·근거 `status=failed`면 `unavailable`로 답한다. 검색은 "지금 검색 결과를 읽지 못했습니다", 근거는 "근거를 읽지 못했습니다", 기존 회의 명령은 "지금 회의 정보를 읽지 못했습니다"로 구분한다. 폴링이 한 번 실패해도 직전 목록으로 답한다.
- **입력 검증**: 검색어는 Viewer `normalize_query`와 같이 앞뒤 공백을 뺀 뒤 2~100자·줄바꿈(CR/LF)·NUL 없음으로 먼저 검증한다. 나머지 명령의 제어 문자는 공백으로 바꾸고 인자는 200자 이하, 일수는 1~30의 정수(`14`, `14일`), 제목은 부분 일치(대소문자 무시)다. id가 정확히 같은 회의가 있으면 제목 일치보다 우선하고, 여러 건이 맞으면 id와 제목 후보 5건을 보여 주며 id로 다시 입력하게 한다.
- **속도 제한**: 사용자(`user_id`)별 분당 `command_rate_limit_per_minute`(기본 10)회, 프로세스 메모리의 슬라이딩 윈도(추적 키 10000개 상한)다. 초과하면 안내만 답하고(`rate_limited`) 인증에 실패한 요청은 횟수를 쓰지 않는다.
- **감사·관측**: 로그 `collab_command command=… result=… channel=<매핑된 키> user=<사용자 id의 SHA-256 앞 12자> ms=…`만 남기고 명령 본문·토큰·사용자 id 원문은 남기지 않는다. 메트릭 `synctx_collab_commands_total{command, result}`(`command`: `today`·`upcoming`·`meeting`·`summary`·`search`·`evidence`·`help`·`unknown`, `result`: `ok`·`no_channel`·`not_found`·`ambiguous`·`invalid`·`rate_limited`·`unauthorized`·`unavailable`·`error`)와 Alert `SynctxCollabCommandUnauthorized`.
- **등록**: Mattermost가 명령 토큰을 직접 발급한다. 명령은 트리거 `synctx`, 메서드 POST, 호출 주소 `http://<Gateway Service>/api/collab/mattermost/command`로 만들고 자동 완성 힌트를 둔다. 발급된 토큰을 Secret의 `command-token`에 넣는다. Mattermost 서버는 `ServiceSettings.AllowedUntrustedInternalConnections`에 Gateway 호스트를 허용해야 사내 주소로 호출한다. 로컬은 `setup-mattermost.sh`가 REST로 등록하며 힌트에 `검색 <키워드>`·`근거 <id>`를 포함한다.
- **확인 범위**: 검색·근거는 pytest와 httpx MockTransport로 인증·범위·상한·발췌 제외·타임아웃·오류를 확인하며, 실제 Mattermost·클러스터 실행은 2026-10-29에 확인했다(검색 결과와 근거 없음 안내까지이며 근거 목록이 있는 회의는 확인하지 못했다). 기존 명령 해석·채널 범위·인증·속도 제한·응답 형식은 단위·엔드포인트 테스트로 확인했다. 슬래시 명령 등록은 로컬 Mattermost 10.11.19(amd64 에뮬레이션)에서 실제로 확인했다(이 과정에서 명령 목록 조회 경로가 `GET /api/v4/commands?team_id=&custom_only=true`임을 바로잡았다). Mattermost가 `/synctx` 실행을 Gateway로 전달해 응답이 오는 흐름도 같은 로컬 Mattermost에서 확인했다: 호스트에서 가짜 Viewer(`/api/meetings`)와 Gateway를 루프백에 띄우고(Mattermost는 `host.minikube.internal`로 호출), 관리자 REST `commands/execute`로 `도움말`·`오늘`·`예정`·`회의`·`정리`·없는 회의·미지원 명령을 실행해 모두 요청자에게만 보이는 응답을 받았고 Mattermost가 발급한 토큰이 인증을 통과했으며 제목의 `@channel`이 멘션이 되지 않았다. 로그에는 사용자 id 해시와 명령 이름·결과만 남았다. 사람이 채팅 화면에서 직접 누르는 경로, 클러스터 안 Viewer·Gateway 파드가 함께 도는 전체 흐름은 아직 확인하지 못했다.

## 8. 배포 설계

### 8.1 이미지와 의존성 고정

운영 이미지는 `latest` 태그를 사용하지 않는다.

| 이미지 | 태그 정책 |
|--------|-----------|
| synctx/viewer | SemVer + git sha |
| synctx/cdn | SemVer + git sha |
| synctx/marp | SemVer + git sha |
| synctx/sync | SemVer + git sha |
| synctx/feedback | SemVer + git sha |
| jgraph/drawio | 검증 완료한 명시 버전 |
| nginx | 검증 완료한 명시 버전 |
| alpine/git | 검증 완료한 명시 버전 (Slides sidecar, K8s·Docker Compose 공통) |
| jitsi/web·jitsi/prosody·jitsi/jicofo·jitsi/jvb | `values.yaml`의 `jitsi.imageTag`를 공유하는 고정 stable 태그, `registry.local:5000/jitsi/*` 반입, 기본 꺼짐 |
| prom/pushgateway | 검증 완료한 명시 버전 (`v1.11.1`, Sync·Feedback 메트릭 수집) |

자체 이미지 5개(viewer·cdn·marp·sync·feedback)의 Python 베이스는 `python:3.13.16-slim-trixie`(Debian 13, Alpine 미사용)로 패치 버전·배포판까지 고정한다. 올릴 때는 Dockerfile 5개와 `requires-python`을 함께 바꾼다.

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
| `repo-cache` | RWO/RWX | Viewer, Sync, Marp의 clone cache. 서비스별 분리 권장 (현재 차트에는 PVC가 없고 컨테이너 파일시스템을 쓴다) |
| `cdn-data` | RWX | CDN 정적 파일 |
| `sync-state` | RWO | Sync Engine 상태, Sync·Feedback 메트릭 누적값, Feedback 상태·event store (2.2) |
| `slides-worktree` | RWX(차트 기본값) | `/data/slides/worktree`에 mount되는 slides-sync working tree |
| `marp-work` | RWO | Marp 변환 임시 작업 (현재 차트는 PVC가 아니라 `emptyDir`) |

가능하면 서비스별 PVC를 분리한다. 공유 PVC는 locking과 권한 문제가 커진다.

### 8.4 로컬 시뮬레이션 (Minikube)

개발 완료 후 실제 운영 환경과 유사한 조건에서 검증하기 위해 Minikube를 활용한 로컬 시뮬레이션을 수행한다.

**시뮬레이션 환경 구성:**
- **Minikube Cluster**: `minikube start --addons=ingress,metrics-server` 명령으로 Ingress Controller와 Metrics Server가 활성화된 클러스터 구성. 로컬 스크립트는 `MINIKUBE_PROFILE`(기본 `minikube`, 예: `synctx`)의 클러스터로 minikube·kubectl·helm을 고정한다(`minikube -p`, `--context`, `--kube-context`). 터널은 `minikube tunnel -p <프로필>`.
- **Minikube Docker Daemon**: `eval $(minikube docker-env)`를 통해 로컬 Docker 데몬을 Minikube와 공유하여 빠른 로컬 빌드 및 테스트를 수행. (단, 운영 환경의 내부 레지스트리 및 imagePullSecret 검증이 필요한 경우 `registry` addon을 활성화하여 별도 테스트 수행)
- **Helm 배포**: 작성된 Helm Chart를 사용하여 전체 마이크로서비스(Viewer, CDN, Marp, Draw.io, Sync Engine, Slides Server)를 `synctx` 네임스페이스에 배포.

**주요 검증 항목:**
- **Ingress 라우팅**: `/view/*`, `/cdn/*`, `/api/sync/webhook/gitlab` 등 모든 Ingress Path가 올바른 Service로 라우팅되는지 확인.
- **PVC 마운트 및 권한**: `repo-cache`, `sync-state` 등 PVC가 정상적으로 프로비저닝되고 각 Pod에 올바른 권한(RWO/RWX)으로 마운트되는지 검증. (기본 Minikube storage provisioner는 RWX를 완벽히 지원하지 않으므로, 운영 환경 수준의 RWX/NFS 동작 검증을 위해서는 NFS/CSI addon 또는 별도의 dev StorageClass 구성이 필요함)
- **Webhook & CronJob 동작**: 가상의 GitLab Push Event를 `curl`로 Ingress에 전송하여 Webhook Receiver가 정상적으로 일회성 Sync Job을 생성하는지, 그리고 CronJob 스케줄과 겹칠 때 Lease 기반 잠금이 의도대로 작동하는지 확인.
- **서비스 간 내부 통신**: Viewer가 CDN의 정적 자산을 정상적으로 로드하는지, Marp Service가 내부 API를 통해 PDF 변환을 수행하는지 점검.

### 8.5 서비스 옵션

서비스마다 켜고 끌 수 있다. 서비스 이름과 순서는 `viewer, cdn, marp, slides, drawio, sync, feedback`이고, 의존성 규칙 D1~D8과 알려진 한계는 `docs/architecture.md`의 "서비스 옵션과 의존성"을 따른다.

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
- 레지스트리에 있어야 하는 이미지(켠 서비스만 필요하다. synctx 이미지 태그는 `0.7.0-<git-sha>`)

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
| `deploy/docker/secrets.env` | `GIT_READ_TOKEN`, `GIT_PUBLISH_TOKEN`, 선택 `SYNCTX_RENDER_SECRET`·`SYNCTX_RENDER_SECRET_PREVIOUS` (`secrets.env.example`을 복사, 권한 600 권장) |
| `.synctx.yaml` | `SYNCTX_CONFIG_FILE`이 가리키는 설정 파일 |

두 env 파일과 `.synctx.yaml`, `.generated/`, `tls/`는 `deploy/docker/.gitignore`로 git에서 뺀다. 옵션 파일 안의 상대 경로는 `deploy/docker` 기준이고, `--env-file`·`--secrets-file`로 준 상대 경로는 현재 디렉터리 기준이다. 옵션 파일은 `deploy.sh`가 bash로 읽고 docker compose도 읽으므로 공백이 든 값은 큰따옴표로 감싼다(예: `SYNCTX_EXTRA_COMPOSE_FILES="a.yaml b.yaml"`).

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SYNCTX_SERVICES` | `viewer,cdn,marp,slides` | 켤 서비스(필수) |
| `SYNCTX_IMAGE_PREFIX` | `registry.local:5000/` | 이미지 접두사. 로컬에서는 빈 값 |
| `SYNCTX_IMAGE_TAG` | 없음(필수) | `0.7.0-<git-sha>`. `latest`는 거부 |
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
| `SYNCTX_RENDER_SECRET_FILE` / `SYNCTX_RENDER_SECRET_PREVIOUS_FILE` | 없음 | 렌더 키 파일 경로. 같은 키의 직접 값과 함께 쓰면 중단하고, viewer에 읽기 전용으로 마운트한다 |

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

### 8.7 경량 Markdown·회의 Viewer 배포

기존 Viewer의 소스 실행·단일 컨테이너 실행 옵션이며 서비스 목록과 Phase 번호는 바꾸지 않는다. 설치 명령과 전체 인자는 local-development.md 0.1을 따른다.

```bash
# 저장소 체크아웃·uv·Git·Python 3.13 이상
./run.sh --repo https://git.example/team/docs.git
# Docker Engine. 기존 빌드 스크립트로 Viewer만 빌드
./run.sh --docker --build --repo https://git.example/team/docs.git
# 폐쇄망에 반입한 사전 빌드 Viewer 이미지
./run.sh --docker --image registry.local:5000/synctx/viewer:0.7.0-abcdef0 --config ./viewer.yaml
```

| 항목 | 동작 |
|------|------|
| 실행·의존성 | `standalone.py`에서 기존 Viewer만 `/view`로 제공. 번들 vendor 사용, 외부 CDN·Marp·Compose·게이트웨이 불필요 |
| 기본 이미지·빌드 | `${DOCKER_REGISTRY:-synctx}/viewer:0.7.0-<git-sha>`. `--build`는 `scripts/local/steps/build.sh --only viewer --no-load --tag <태그>`에 위임. `--image`와 상호 배타적. `DOCKER_REGISTRY` 없이 기본 이미지를 쓰면 `--pull never`로 로컬 이미지만 쓰고, 없으면 실행 전에 `--build`·`--image`를 안내(Docker Hub에서 받지 않음). `DOCKER_REGISTRY`나 `--image`를 주면 Docker 기본 pull 정책 |
| 반입 | 같은 커밋의 Viewer 이미지·루트 `run.sh`·`scripts/docker/run-viewer.sh`를 함께 준비. `--image`면 호스트 Python·uv·Git 없이 실행. SemVer-SHA 태그 필수, `latest` 거부 |
| 포트·캐시 | 호스트 `127.0.0.1:18000`, 컨테이너 `0.0.0.0:8000`. 기본 named volume `synctx-viewer-cache:/data/viewer`. `--bind`·`--port`·`--name`·`--volume`으로 변경 |
| 설정 | `--repo` 반복 또는 `--config`. 파일은 호출 위치 기준으로 해결하고 읽기 전용으로 마운트. 기존 설정의 명시적 authz·collab 정책 유지. `--ref`는 `--repo` 전용이며 `--config`와 함께 주면 거부(설정의 `default_ref` 사용) |
| 자산 설정 | `viewer.asset_source` 기본 `cdn`; 경량 실행은 `bundled`로 바꾸고 `SYNCTX_ENABLED_SERVICES=viewer` 고정. 저장소 캐시와 생성 YAML은 캐시 볼륨 안에 둠 |
| Git 갱신 | 시작 clone/fetch 뒤 기본 60초마다 async 갱신. `--refresh-interval 0`은 주기 갱신 끔 |
| Secret | `GIT_READ_TOKEN`·`SYNCTX_SERVICE_TOKEN`·`SYNCTX_RENDER_SECRET`·`SYNCTX_RENDER_SECRET_PREVIOUS`를 Docker 환경변수 이름으로만 전달하고, `_FILE`은 파일을 읽기 전용으로 마운트. 다른 `credential_env`는 `--env NAME`. 토큰 값을 URL·명령 인자·생성 YAML에 저장하지 않음 |
| 공개 URL | `--public-base-url` → 명시적 `runtime.public_base_url` → `SYNCTX_VIEWER_PUBLIC_BASE_URL` → 로컬 주소. Docker는 환경변수가 비면 호스트 공개 주소를 전달 |

`--repo` 생성 프로필은 로컬 열람용(`authz.enabled: false`, `collab.enabled: true`)이다. 기본 loopback에서 사용하고 외부 공개는 TLS·인증 프록시와 방화벽을 구성한 뒤 실제 공개 URL을 지정한다. 직접 보낸 권한 헤더가 신뢰되지 않도록 프록시만 Viewer에 접근하게 제한한다. `--config`는 인증을 자동으로 끄지 않으므로 authz가 켜진 설정에 프록시 헤더 없이 접근하면 403이 정상이다. Docker의 명시적 로컬 `--repo`만 실제 디렉터리에 읽기 전용으로 마운트하고 상위 디렉터리는 노출하지 않는다. 로컬 file URL은 빈 호스트 또는 `localhost`만 허용하고 URL 이스케이프(예: `%20`)를 해석한 실제 디렉터리를 마운트한다. 다른 호스트와 `.git`이 파일인 linked worktree·submodule은 Docker 실행 전에 거부하며 원격 Git URL을 안내한다. 외부 Git 관리 디렉터리를 자동 마운트하지 않는다. 컨테이너는 root로 돌고 바인드 마운트는 호스트 소유자 uid를 유지하므로(Linux), Git의 소유자 검사(dubious ownership)를 피하려고 마운트한 디렉터리마다 `GIT_CONFIG_*` 환경변수로 `safe.directory`를 등록한다. 실제 Linux Docker에서의 로컬 `--repo` clone은 아직 확인하지 않았다. 설정 파일의 저장소는 컨테이너에서 접근 가능한 Git URL을 쓴다.

회의 `synctx.meeting`의 `id`·`title`·`starts_at`·`channel` 필수 조건은 유지한다. 영상 회의는 본문의 일반 HTTPS Markdown 링크로 입장한다. 메모·형광펜은 브라우저 localStorage에 남으며 서버 캐시 볼륨이나 다른 브라우저와 동기화하지 않는다. 일반 `download?format=html`은 기본 렌더 HTML이다. `meeting-export`는 브라우저가 현재 메모를 채워 저장하는 자체 완결형 HTML 틀이며 vendor·회의 UI를 포함한다. Marp PDF·출판은 제공하지 않는다.

운영 절차:

1. 이미지·Git 접근·환경변수를 준비한 뒤 포그라운드 실행 로그와 `/view/`에서 저장소 목록을 확인한다. 잘못된 설정·저장소 URL·인증 오류는 앱 로그에서 확인한다. 스크립트가 명시적 설정 정책을 덮어써서 오류를 숨기지 않는다.
2. 기본 60초 갱신 뒤 원격 변경이 보이는지 확인한다. 갱신 실패 로그에는 저장소 ID와 예외 종류가 남으며 다음 주기에 재시도한다.
3. Ctrl-C로 중지하면 `--rm`으로 컨테이너만 제거된다. 같은 `--volume`으로 재실행해 캐시를 재사용한다. 캐시 삭제 전에 필요한 내용을 백업하고 브라우저 메모는 별도로 내보낸다.
4. 자동 테스트는 fake Docker의 명령 인자·readonly mount·이름만 전달하는 Secret·태그·빌드 위임을 검증한다. 실제 이미지 빌드와 대상 플랫폼, daemon·Git 서버·TLS·인증 프록시 연결은 해당 환경에서 확인한다. Markdown·회의 보기와 메모 포함 내보내기, 캐시 재사용, Ctrl-C 종료를 함께 확인한다.

### 8.8 정적 Viewer 내보내기 비밀 값·Access 점검 (시연용 구성)

운영 구성이 아니라 시연·열람용 정적 Viewer(Cloudflare Pages)의 비밀 값과 점검을 3.4의 운영 Secret 표와 분리해 적는다. 운영 체크리스트와 Phase 번호는 바뀌지 않는다. 설치 절차는 [설치 방식 선택](install.md)을 따른다.

| 비밀 값 | 저장 위치 | 용도 |
|---------|-----------|------|
| `GIT_READ_TOKEN` | GitHub Actions 저장소 Secrets | 대상 문서 저장소 Contents 읽기 전용(fine-grained, 만료일 설정). 설정 파일에는 `auth.credential_env` 이름만 둔다 |
| `CLOUDFLARE_API_TOKEN` | GitHub Actions 저장소 Secrets | Account → Cloudflare Pages → Edit 권한. 사전 점검(preflight)·배포(deploy) job에서만 쓰고 build job에는 넘기지 않는다 |
| `CLOUDFLARE_ACCOUNT_ID` | GitHub Actions 저장소 Secrets | 배포 대상 계정. `CLOUDFLARE_API_TOKEN`과 같은 job에서만 쓴다 |

비밀이 아닌 설정은 GitHub Actions 저장소 Variables에 둔다. 둘 다 워크플로가 읽으며 비어 있으면 배포·점검이 실패한다.

| 변수 | 내용 |
|------|------|
| `CLOUDFLARE_PAGES_PROJECT` | Pages(Direct Upload) 프로젝트 이름. `wrangler pages deploy --project-name`에 쓴다 |
| `STATIC_EXPORT_CHECK_URLS` | 접근 점검 대상 https 주소를 공백으로 구분한 목록(운영 `*.pages.dev`·미리보기·커스텀 도메인의 `/view/`). 비어 있으면 `check-access.sh`가 사용법을 출력하고 종료 코드 2로 끝난다 |
| `STATIC_EXPORT_PUBLIC` | 선택. `true`면(대소문자 무관) 공개 모드로 사전 점검 job과 배포 후 Access 점검을 건너뛴다(이때 `STATIC_EXPORT_CHECK_URLS`는 쓰지 않는다). 없거나 다른 값이면 점검한다 |

- 토큰은 환경변수로만 받고 URL·로그·산출물에 남기지 않는다. 예외는 종류(`type(exc).__name__`)만 기록하고, 산출물 검사는 토큰 값·`ghp_`·`github_pat_`·`Authorization` 패턴·`.git`을 찾는다. 워크플로는 `pull_request`로 실행하지 않는다.
- 배포 순서는 Access 앱 → 사전 점검 → 배포 → 배포 후 점검 → 주소 공유다. `scripts/static-export/check-access.sh`는 세션 없이 요청해 Access 로그인(302 등)이나 401·403이면 통과로 본다. 실패하면 워크플로가 실패하지만 올라간 배포를 자동으로 지우지는 않으며, 사람이 사이트를 먼저 내리고 Access를 고친다. 공개 모드는 Access 앱 없이 배포하며 검색 노출은 `robots.txt`·`noindex` 헤더로만 막는다(설치 문서 「공개 모드」).
- 점검 판정(`access_check.judge_access_response`)만 가짜 응답으로 시험한다. 실제 Cloudflare 계정의 Access 정책 상태, Pages 배포 삭제 권한은 확인하지 못했다.

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

`/render/` 뒤 토큰 세그먼트는 앱·uvicorn 접근 로그의 text/JSON·예외·인자형 URL 모두에서 가린다. 공개 `/view/render/`도 포함한다. 키=값 마스킹만으로는 경로 토큰을 가릴 수 없으므로 text·JSON 포매터가 최종 출력에서 `/render/` 뒤 세그먼트를 `***`로 바꾼다(`synctx_common.logging`). 인그레스·EFK에 남는 토큰은 위험을 알리고 수용한다(3.6).

### 9.2 Metrics

필수 metrics:

- `synctx_http_requests_total`
- `synctx_http_request_duration_seconds`
- `synctx_render_requests_total` (정의만 있고 아직 올리는 곳이 없다)
- `synctx_marp_convert_total` (정의만 있고 아직 올리는 곳이 없다)
- `synctx_marp_publish_total` (정의만 있고 아직 올리는 곳이 없다)
- `synctx_sync_runs_total`
- `synctx_sync_items_total`
- `synctx_sync_failures_total`
- `synctx_sync_last_success_timestamp`
- `synctx_cdn_bytes_served_total` (정의만 있고 아직 올리는 곳이 없다)
- `synctx_feedback_events_total` (`result`: `sent`, `skipped`(외부 키 없음·이미 보낸 이벤트), `mail_error`, `redaction_error`)
- `synctx_feedback_redacted_fields_total` (반출 이벤트에서 뺀 필드 수, 이벤트의 `ignored_fields` 개수 합)
- `synctx_feedback_mail_failures_total`
- `synctx_feedback_last_success_timestamp` (실패 없이 끝난 실행의 시각)
- `synctx_html_render_total{repo_id, result}` (`result`: `ok`, `denied_token`, `denied_dest`, `disabled`, `not_html`, `too_large`, `bundle_error`. `too_large`는 합본 전 `blob_size_resolved` 사전 413, `bundle_error`는 합본 중 실패·크기 초과. `/render` 결과만 집계, 토큰·SHA·경로 라벨 금지, 모르는 저장소는 `unknown`)
- `synctx_collab_notifications_total{kind, result}` (`kind`: `reminder`, `changed`, `cancelled`, `summary`. `result`: `sent`, `failed`, `gave_up`. Gateway가 0으로 먼저 만들어 둔다)
- `synctx_collab_unmapped_channel_total`
- `synctx_collab_viewer_fetch_failures_total`
- `synctx_collab_commands_total{command, result}` (`/synctx` 명령 처리 결과. 7.7.1. 0으로 먼저 만들어 둔다)
- `synctx_collab_last_success_timestamp` (Viewer에서 회의를 읽는 데 성공한 주기의 시각)
- `synctx_html_bundle_total` (synctx-html 블록 처리 결과. `result`: `ok`, `disabled`, `missing_file`, `outside_repo`, `disallowed_host`, `too_large`, `invalid_block`, `unsupported`)

HTTP 메트릭은 `scope["route"]`로 렌더 경로를 식별해 standalone `/view/render`·K8s `/render` 모두 `/render/{token}/{repo_id}/{path}` 고정 라벨로 정규화한다(`_normalize_path`). authz·경로 거부·파일 조회 실패는 기존 HTTP 상태별 메트릭으로 확인한다. 신규 렌더 Alert 임계값은 운영 확인 뒤 정한다.

수집 경로:

| 출처 | 경로 |
|------|------|
| Viewer·CDN·Marp·Collab Gateway | `synctx/metrics: "true"` 라벨이 붙은 Service의 `/metrics`를 ServiceMonitor(`synctx`)가 수집한다 |
| Sync Engine (CronJob) | 실행이 끝나면 저장소별 누적값을 Pushgateway(`synctx-pushgateway:9091`)에 push하고, ServiceMonitor(`synctx-pushgateway`, `honorLabels: true`)가 수집한다 |
| Outbound Feedback Exporter (CronJob) | 실행이 끝나면 누적값을 같은 Pushgateway에 `job="synctx-feedback"` 그룹으로 push한다 |

Sync CronJob은 실행마다 새 프로세스라 카운터가 0에서 시작한다. 그래서 다음처럼 누적값을 넘긴다.

- 이번 실행의 증가분을 sync-state의 `metrics/{repo_id}.json`에 더해 둔다(상태 파일 `{repo_id}.json`과 이름이 겹치지 않게 하위 디렉터리에 둔다). 실패한 실행도 저장하고, 마지막 성공 시각은 그 실행이 갱신하지 않았으면 이전 값을 유지한다.
- 저장과 push는 그 저장소의 Lease를 쥔 채로 한다. Webhook Job과 CronJob Job이 겹쳐도 같은 저장소의 누적값을 동시에 고치지 않는다. Lease를 잃었거나 `--dry-run`이면 저장·push하지 않는다.
- push는 `job="synctx-sync"`, `repo_id` 그룹을 누적값으로 통째로 바꾼다(HTTP PUT, 5초 제한). push 실패는 로그(`Failed to push sync metrics`)만 남기고 동기화 결과에 영향을 주지 않는다. Pushgateway가 재시작되어 값을 잃어도 다음 실행이 누적값을 다시 채운다.
- Alert가 `increase()`·`rate()`로 보는 계열(`synctx_sync_failures_total`의 대상·오류 분류 조합, `synctx_sync_items_total`의 대상·A/M/D 조합, Sync의 `synctx_html_bundle_total{result="disallowed_host"|"too_large"}`)은 처음 내보낼 때 0을 보낸다. 처음 생긴 계열은 이전 값이 없어 첫 발생이 증가로 잡히지 않기 때문이다. 그동안의 증가분은 메트릭 파일의 `pending`에 두고, 0을 담은 push가 처음 성공한 시각(`pushed_at`)에서 60초(수집 간격 15초의 여러 배)가 지난 뒤의 실행에서 더한다. 그래서 저장소의 첫 실행에서 난 오류는 다음 실행(기본 5분 뒤)에 Alert로 잡히고, push가 실패하는 동안이나 바로 이어진 재실행에서는 계속 보류된다. push 성공 기록을 저장하지 못하면 로그(`Failed to record sync metrics push`)만 남긴다. 실패와 처리 항목을 같이 보류하므로 실패율의 분모도 빠지지 않는다.
- 한 번도 성공하지 못한 저장소는 감시를 시작한 실행의 시각을 `synctx_sync_last_success_timestamp`의 기준값으로 둔다. 첫 실행부터 계속 실패해도 30분 뒤 `SynctxSyncNoSuccess`가 울린다.
- 저장소가 이미 최신이거나 바뀐 파일이 없는 실행도 성공으로 보고 `synctx_sync_last_success_timestamp`를 갱신한다.
- `--repo` 없이 전체 저장소를 도는 실행(dry-run 제외)은 끝에 `.synctx.yaml`에 없는 `repo_id`의 그룹을 Pushgateway에서 지운다. 저장소를 빼거나 id를 바꾼 뒤 남은 그룹의 마지막 성공 시각이 멈춰 `SynctxSyncNoSuccess`가 계속 울리지 않게 하기 위해서다. 메트릭 파일은 지우지 않는다(다시 넣으면 누적값이 이어진다).
- Sync 메트릭은 `metrics.enabled`이고 `sync`가 켜졌고 `sync.persistence.enabled`일 때만 Pushgateway로 보낸다. 누적값을 sync-state PVC에 두므로 `metrics.enabled`이고 `sync`가 켜졌는데 `sync.persistence.enabled=false`이면 Helm 렌더가 `fail`로 멈춘다(의존성 규칙 D8). 메트릭이 필요 없으면 `metrics.enabled=false`로 둔다. Sync에는 `SYNCTX_PUSHGATEWAY_URL`이 들어가고, Sync NetworkPolicy가 Pushgateway Pod의 9091 포트로 나가는 것을 허용한다. 이 환경변수가 없으면 누적값만 저장하고 push하지 않는다.

Outbound Feedback CronJob도 실행마다 새 프로세스이므로 같은 방식(`synctx_common.metrics_push`)을 쓴다. Sync와 다른 점은 다음과 같다.

- 누적값은 Feedback이 이미 쓰는 sync-state PVC의 `feedback_metrics.json`에 둔다(`feedback_state.json`·`feedback_events.json` 옆). Sync의 `metrics/{repo_id}.json`과 겹치지 않는다.
- 저장소 구분이 없으므로 push 그룹은 `job="synctx-feedback"` 하나다. CronJob이 `concurrencyPolicy: Forbid`이고 Webhook 같은 다른 실행 경로가 없어 Lease 없이 저장·push한다. Sync의 저장소 그룹 정리(`job="synctx-sync"`만 대상)는 이 그룹을 지우지 않는다.
- 0 기준값을 먼저 보내는 계열은 `synctx_feedback_events_total`의 네 `result`, `synctx_feedback_mail_failures_total`, `synctx_feedback_redacted_fields_total`이다. 첫 실행의 실패는 Sync와 마찬가지로 기준값 push가 수집된 뒤의 실행(기본 5분 뒤)에 Alert로 잡힌다.
- `synctx_feedback_last_success_timestamp`는 실패가 없는 실행만 갱신하고, 한 번도 성공하지 않았으면 내보내지 않는다(이 값을 보는 Alert가 없다).
- redaction 중 예외가 난 Work Package는 `result="redaction_error"`로 세고 메일을 보내지 않으며 event store에도 남기지 않는다. 실패로 보므로 마지막 처리 시각을 진행하지 않아 다음 실행에서 다시 본다. 다른 Work Package는 계속 처리한다.
- push 실패는 로그(`Failed to push feedback metrics`)만 남기고 반출 결과에 영향을 주지 않는다. `outbound_feedback.enabled: false`인 실행은 저장·push하지 않는다.
- Feedback에는 `metrics.enabled`, `feedback.enabled`, `sync.persistence.enabled`일 때 `SYNCTX_PUSHGATEWAY_URL`이 들어간다. Sync와 마찬가지로 `metrics.enabled`이고 `feedback.enabled`인데 `sync.persistence.enabled=false`이면 Helm 렌더가 `fail`로 멈춘다(D8). Pushgateway는 Sync와 Feedback 중 하나라도 push하면 배포한다. Feedback에는 NetworkPolicy가 없어 9091 egress를 따로 열지 않는다.

### 9.3 Alert

초기 alert:

- Sync가 30분 이상 성공하지 못함 (`SynctxSyncNoSuccess`)
- Sync 항목 실패율이 최근 15분 10% 초과 (`SynctxSyncHighFailureRate`)
- XWiki/OpenProject auth error 발생 (`SynctxSyncAuthError`)
- Marp publish push 실패 (`SynctxMarpPublishFailed`. `synctx_marp_publish_total`을 아직 올리지 않아 지금은 울리지 않는다)
- Slides Server sidecar 컨테이너 재시작 3회 초과 (`SynctxSlidesSidecarFailed`)
- Outbound Feedback mail send failure 30분 안에 3회 이상 (`SynctxFeedbackMailFailure`)
- Outbound Feedback redaction failure 발생 (`SynctxFeedbackRedactionFailure`)
- PVC 사용량 80% 초과 (`SynctxPVCUsageHigh`)
- readiness failure 5분 지속 (`SynctxReadinessFailure`)
- Viewer 5xx 응답 비율 5분간 5% 초과 (`SynctxViewerHighErrorRate`)
- synctx-html 합본이 허용되지 않은 외부 주소 또는 크기 초과로 거부됨 (`SynctxHtmlBundleRejected`, 저장소별 1시간 안에 1건 이상, warning)
- Jitsi(`collab.jitsi.enabled`일 때, 외부 Jitsi 포함): `sum(increase(synctx_viewer_jitsi_token_failures_total[10m])) >= 5`가 2분 지속하면 `SynctxJitsiTokenFailures`(warning). reason에는 권한·시간 창에 따른 거부도 포함되므로 사유별 계측을 먼저 확인한다.
- Collab Gateway(`collabGateway.enabled`일 때만): 발송 실패 30분 안에 5회 이상 (`SynctxCollabNotificationFailures`, warning), 발송 포기 1시간 안에 1건 이상 (`SynctxCollabNotificationGaveUp`, critical), Viewer 조회 실패 15분 안에 10회 이상 (`SynctxCollabViewerFetchFailing`, warning), 회의 조회 성공 없이 10분 초과 (`SynctxCollabStalled`, critical), 채널 매핑 누락 (`SynctxCollabUnmappedChannel`, warning), `/synctx` 명령 인증 실패 10분 안에 5회 이상 (`SynctxCollabCommandUnauthorized`, warning)

Sync Alert(성공 없음, 실패율, 인증 오류, 합본 거부)와 Outbound Feedback Alert(메일 발송 실패, redaction 실패)는 Pushgateway로 수집한 시계열로 평가한다. `scripts/tests/test_prometheus_rules.py`가 Helm이 렌더한 PrometheusRule을 `promtool test rules`로 평가한다(`scripts/tests/prometheus/*.test.yaml`). promtool이 없으면 `PROMTOOL` 환경변수로 컨테이너 명령을 준다.

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
| Outbound Feedback | fake OpenProject + fake SMTP + fake importer | redaction, signature, replay, PR 생성, OpenProject 변경 조회 필터(`<>d`)·전체 페이지 처리 검증 |
| Marp 출판 인증 | pytest(Marp TestClient) | `publish_auth` → `GIT_PUBLISH_TOKEN` → `repositories[0].auth` 순서, `publish_auth_fallback` 경고 |
| Offline build | vendor artifact only | 외부 네트워크 없이 이미지 빌드 |
| Meeting mode | pytest(TestClient) + `node --test`(memo-core.js) | 회의 모드 판단, 스크립트 JSON 인코딩, 내보내기 본문 텍스트 일치, 메모 위치 복원·병합·탭 간 저장 |
| Meeting dashboard | pytest(`meeting_index.py` 단위, 실제 Git clone, Viewer TestClient) | 후보 판정(`meeting:`·숨김 경로·`.memo.md` 제외), 검증 실패 사유, 상태 경계·시간대, 필터·정렬, 메모 파일 파서(삭제·잘못된 항목·id 불일치·크기·개수 상한), 캐시, 부분 복제·미준비 저장소, 권한(authz), 비 collab 저장소 제외, 본문 이스케이프 |
| Meeting evidence |
| Meeting evidence | pytest(`evidence.py` 단위, Viewer TestClient, frontmatter 검증) + `node --test`(evidence-core.js) | 발췌 규칙(앵커 없음·명시 앵커·제목 slug·표 행·굵은 조문 경계·앵커 못 찾음·해설 블록·`synctx-html` 블록 한 줄 안내·상한), 접두어 매핑(가장 긴 키·없는 저장소·`..` 거부), 근거 링크 판정, 발췌 정화, `references` 형식 오류, 본문 글자가 근거 없는 렌더와 같음, 메모 포함 HTML의 근거 데이터·외부 참조 없음, 7개 테마 글자 대비 |
| HTML embed | pytest(`html_bundle` 단위, Viewer TestClient, Sync fake Git) | 합본 규칙·CSP meta·매크로 이스케이프, Viewer 렌더링·CSP, Sync 다시 처리할 조건(묶음 파일 변경, 정책 변경, 누락 파일 복구, 참조 교체) |
| HTML render | WP1 단위 + WP2 TestClient·로그·메트릭 + WP3 `node --test`·DOM·실제 브라우저 + WP4 배포 검사 | ref 포함 정규형 토큰·만료·교체 키·`_FILE`·SHA reader 예외 변환, 독립 clone 파드 2개 중 한쪽만 fetch 후 다른 쪽 SHA 재확인·404, 두 플래그 opt-in·재시작 후 해제, 읽기 전 크기 검사·사전 413 라벨, `%2e%2e` 경로 400, authz 403·FastAPI 422·404 JSON 오류의 sandbox/no-referrer/no-store, standalone 경로의 CSP·메트릭·로그, 상대 `target=pf` 중첩 문서, 소스 보기 유지, iframe 재로드 없는 확대·복귀, 토큰 로그 비노출·라벨 정규화, 두 로컬·Docker 경로와 Helm 키 전달. 운영 SSO 쿠키 SameSite·Secure의 2단계 중첩 요청은 운영 시험(미완료)이며 로컬·무인증 Chromium 결과와 구분. 렌더 미사용 운영 업그레이드 회귀와 롤링 키 공존 검증 포함. Chromium spike는 설계 근거이며 구현 수용 시험은 별도(상세 `html-embed-design.md` 14.9) |
| Alert rule | `promtool test rules`(Pushgateway 모양 시계열) + Sync·Feedback fake Pushgateway | Sync·Feedback Alert 발생·미발생 시점, 실행 간 누적값, 0 기준값, push 그룹. Collab Gateway Alert 5종의 발생·미발생·해소 시점(`collab-alerts.test.yaml`, 직접 수집 시계열) |
| Collab Gateway | pytest(`services/collab-gateway/tests`: 스케줄러·상태·메시지·Mattermost 어댑터(httpx MockTransport)·Viewer 클라이언트·러너·앱·명령 해석·속도 제한·명령 엔드포인트) + pytest(`scripts/tests/test_helm_collab_gateway.py`: `helm template`) | 예고 시점·장애 복구·일시 변경·처음 본 회의, 변경·취소·요약, 중복 방지·재시작, 재시도·포기, 매핑 누락, 상태 파일 손상·잠금, 멘션·링크 이스케이프, 토큰 비노출, Helm 복제본 고정·PVC·Secret·NetworkPolicy·검증 실패, `/synctx` 채널 범위·토큰 인증·속도 제한·ephemeral 응답·감사 로그, 검색·근거 별칭·채널 저장소별 요청(최대 5개)·다른 저장소 필터 후 링크 검증·항목 단위 생략과 건수 로그·5/20건 상한과 초과 안내·partial/omitted 안내·명령별 실패 문구·발췌 제외·합산 2초 기한·Viewer 오류(MockTransport), 슬래시 명령 등록 REST(모의 전송) |
| Viewer 화면·검색 | pytest(`viewer_ui`·`search`·`linear_inline` 단위, Viewer TestClient) + `node --test`(viewer-core.js) | 상단 바·서랍·오류 안내 화면 이스케이프, 본문 검색 기한·동시 실행 제한·상태, 서비스 토큰 허용 범위·협업 필터 강제·비협업 repo 404·ref 거부·검색 경로만 반환·일반 권한 회귀·근거 API 권한/404/비협업 참조 제외/발췌 제외/partial/omitted/실패 상태, 파일 찾기·최근 문서, Markdown 인라인 렌더 결과가 python-markdown과 같음, 오류 `detail` 고정 문구·git 인증정보 가림 |
| 배포 옵션 | pytest(`scripts/tests`: `helm template`, `deploy.sh`·`compose.sh`, `run-viewer.sh`, `run-lite.sh`, `seed_cdn.py`) | 서비스 옵션과 D1·D2·D8 렌더 실패, Pushgateway 배포 조건, 게이트웨이 설정·`nginx -t`·TLS·신뢰 프록시 검사, 로컬 Viewer 설정 생성, 경량 소스·단일 컨테이너 인자·캐시·Secret·빌드 위임(fake Docker), CDN 시드 |

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
- [x] Prometheus metrics scrape 연동 검증 (smoke-test.sh에 metrics 검증 포함. Viewer·CDN·Marp 대상이며, Sync CronJob은 Phase 9에서 Pushgateway 경로를 추가했다)
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
- [x] Exporter 메트릭 계측과 Pushgateway 수집, 메일 발송 실패·redaction 실패 Alert promtool 규칙 테스트 (9.2, 9.3)

완료 기준:

- description/comment/attachment가 포함된 OpenProject 변경 이벤트에서도 메일 payload에는 허용 필드만 포함된다.
- HMAC signature가 틀리거나 중복 `event_id`인 메일은 외부 Importer가 거부한다.
- 외부 Importer는 main branch에 직접 push하지 않고 PR만 생성한다.
- PR merge 후 기존 외부→내부 sync로 OpenProject가 idempotent하게 갱신된다.

### Phase 8: 협업 통합 (회의 모드 · Jitsi · 메신저)

- [x] front matter 분리(일반 보기 포함)
- [x] `synctx.meeting` 검증과 Harness `meeting.id` 중복 검사
- [x] `repositories[].collab.enabled`
- [x] 회의 모드 메모·형광펜·회의 정리
- [x] `.md`·메모 포함 HTML 내보내기/가져오기
- [x] 회의 정보 카드·발표 화면(`?mode=present`)·회의 모드 첫 사용 안내
- [x] 8.1b 근거 미리보기·근거 모음
  - [x] front matter `synctx.meeting.references`(접두어 매핑) 검증과 저장소 간 링크 해석
  - [x] 근거 링크 판정과 발췌 규칙(앵커·제목 slug·표 행·조문 경계·해설 블록 제거·`synctx-html` 블록 한 줄 안내·상한)
  - [x] 발췌 전용 허용 목록 정화
  - [x] 링크 말풍선(포인터·키보드·Esc)과 근거 모음(인용한 곳·원문 열기)
  - [x] 메모 포함 HTML에 근거 모음·말풍선 포함
- [x] 8.1c 회의 문서 대시보드
  - [x] `GET /api/meetings`·`/meetings`(상태 탭·저장소/채널/발표자/기간/제목 필터)와 회의 수집·캐시(`meeting_index.py`)
  - [x] `<문서명>.memo.md` 결정·액션 집계(담당자·기한, 서버 저장 없음)
  - [x] 상단 바 「회의」·회의 정보 카드 「회의 대시보드로」 링크
- [ ] 8.2 Jitsi 내장
  - [x] A1: `collab.jitsi` 설정·현재/이전 키와 `_FILE` 검증, 별도 방 이름 HMAC·HS256 JWT·시간 창·취소 검사
  - [x] A1: Viewer 참여 API(기존 권한·서비스 토큰 면제 없음·404/409·no-store·메트릭), 회의 목록의 공개 참여 시간
  - [x] A1: 회의 카드·발표 화면 참여 버튼·사유 텍스트·IFrame 패널·접근성, Jitsi 출처 한정 CSP/Permissions-Policy
  - [x] A1: 발표자 따라가기(moderator 확인·메시지 검증·throttle·기존 앵커·수동 스크롤 해제), Python/Node·UI 대비 회귀 테스트
  - [x] A1: Gateway 참여/join(채널 범위·문서/발표 링크만·사유 안내), 도움말·자동완성
  - [x] A2 정적: Helm 네 워크로드·HTTPS·Secret·설정 불일치 검증, NodePort의 JVB_PORT/containerPort/Service port/nodePort 일치(기본 31000), IP만 광고·XMPP 암호 길이·태그 덮어쓰기 렌더 검사, Viewer 현재/이전 키·Prosody 현재 앱 키 연결
  - [x] A2 정적: Compose profile·게이트웨이 프록시·키 파일 전달, 로컬 선택 배포·Mattermost 자원 보호, 반입 목록
  - [x] A2 정적: test_helm_jitsi.py·test_docker_deploy.py·test_local_scripts.py와 Alert 테스트 명세 추가
  - [x] A2 런타임(2026-10-29, Apple Silicon minikube `synctx`, 네이티브 arm64): `stable-9823` 네 이미지의 태그 실존(amd64·arm64 매니페스트), 공식 이미지 기동·probe, Viewer 코드가 만든 JWT로 XMPP 입장(발표자 `owner/moderator`, 참석자 `member/participant`), 무토큰·서명 변조·다른 방 토큰 거부, Jicofo 인증 해제 상태에서 conference 생성과 JVB brewery 브리지 인식
  - [x] 이 확인에서 찾아 고친 결함: Jitsi web의 nginx는 검색 도메인을 쓰지 않아 짧은 서비스 이름(`/xmpp-websocket`의 prosody, `/colibri-ws`의 `jvb`)을 해석하지 못해 502였다. web 프록시 대상(`XMPP_BOSH_URL_BASE`·`JVB_WS_SERVER`·`JVB_WS_SERVER_ID`(web·JVB 동일)·`COLIBRI_WEBSOCKET_REGEX`)을 서비스 FQDN으로 바꿨다(클러스터 도메인은 `jitsi.clusterDomain`, 기본 `cluster.local`). 수정 뒤 `/colibri-ws`가 JVB까지 도달한다(존재하지 않는 방 요청은 JVB가 403)
  - [x] minikube Jitsi 스크립트 실행(`deploy-jitsi.sh`, Mattermost를 0으로 줄인 상태, Collab Gateway는 꺼짐)
  - [ ] A2 런타임 남은 것: 이미지 반입(`registry.local:5000`), web P2P 해제 유효성, JVB NodePort/hostPort 도달, UDP 방화벽, 외부 STUN 요청 없음 확인
  - [ ] 로컬 실제 브라우저 접속·입장, 데이터 채널(endpoint message) 발표자 따라가기, 운영 미디어 품질 검증(이번 확인은 XMPP 수준까지이며 브라우저·미디어는 하지 않았다)
- [ ] 8.3 Collab Gateway
  - [x] `collab.gateway` 설정 모델과 Viewer `/api/meetings` 서비스 토큰 인증
  - [x] `services/collab-gateway`: 예고·변경·취소·요약 알림, 상태 파일 중복 방지, Mattermost 어댑터, 채널 매핑, 메트릭
  - [x] Helm `collabGateway`(복제본 1·PVC·Secret·NetworkPolicy·검증)와 Alert 5종(promtool 규칙 테스트)
  - [x] 로컬 minikube Mattermost 시뮬레이션 스크립트(`deploy-deps.sh --mattermost`·`setup-mattermost.sh`·`verify-collab.sh`, 값 파일 `values-local.collab.secret.yaml`)
  - [x] 실제 Mattermost(10.11.19, amd64 에뮬레이션)에서 어댑터로 올린 글이 채널에 도달함을 `verify-collab.sh`로 확인
  - [x] 클러스터 안에서 Viewer·Gateway 파드가 함께 돌며 실제 회의 알림이 도달하는 전체 흐름(2026-10-29, `meeting-docs` 임시 브랜치의 가상 회의로 시작 안내·변경(제목을 되돌렸다 다시 바꾸는 경우 포함)·취소가 채널에 도달, 임시 브랜치는 삭제. 요약 알림은 2026-10-30에 전송 기록까지 확인, 채널 표시는 미확인)
  - [x] 운영 Runbook(12.10: 알림 장애, 봇·서비스 토큰 교체, 상태 파일 초기화)
  - [ ] Docker Compose 배포(제외 결정, 미구현)
- [ ] 8.4 `/synctx` 명령과 채널
  - [x] `/synctx 오늘`·`예정`·`회의`·`정리`·`도움말`(채널 범위, 토큰 인증, 속도 제한, 요청자에게만 보이는 응답, 감사 로그, 메트릭·Alert)
  - [x] 로컬 Mattermost에 슬래시 명령 등록(`setup-mattermost.sh`, Mattermost가 발급한 명령 토큰을 Secret에 사용. 실제 Mattermost에서 등록 확인)
  - [x] 검색·근거 명령(Viewer 서비스 토큰·협업 필터·근거 API, 채널 저장소별 검색 최대 5개·비협업 참조 제외·검색 발췌 제외·항목 단위 링크 검증·partial/omitted·5/20건과 초과 안내·명령별 실패 문구·합산 기한·자동완성 힌트 pytest/MockTransport 검증)
  - [x] 참여 명령(`join`, 8.2 A1 코드·단위 테스트, 실제 Jitsi 검증은 8.2 미완료 항목)
  - [x] 검색·근거 명령을 실제 Mattermost·클러스터 Viewer·Gateway로 실행 확인(2026-10-29, 검색 결과와 근거 없음 안내. 근거 목록이 있는 회의는 미확인)
  - [x] 실제 Mattermost에서 `/synctx` 실행이 Gateway로 전달돼 응답이 오는 흐름 확인(호스트의 가짜 Viewer·Gateway, `commands/execute`)
  - [x] 클러스터 안 Viewer·Gateway 파드를 거친 `/synctx` 도움말·오늘·예정·회의·참여·정리·검색·근거 실행(2026-10-29, 관리자 REST `commands/execute`)
  - [ ] 채팅 화면에서 사람이 직접 `/synctx`를 누르는 경로

완료 기준(8.1a):

- 회의 문서를 회의 모드로 열어 메모를 남기고 새로고침해도 위치와 내용이 복원된다.
- 메모 포함 HTML을 네트워크 없이 열면 읽기 전용으로 메모가 보이고, Viewer에서 다시 가져올 수 있다.
- 메모 데이터가 서버로 전송되지 않는다.

완료 기준(8.1b):

- 회의 문서에서 근거 링크에 포인터를 올리거나 포커스하면 말풍선으로 원문 발췌가 보이고, 문서 끝 근거 모음에 근거가 모인다. 본문 글자가 바뀌지 않아 기존 메모 위치가 유지된다.
- `synctx.meeting.references`로 다른 저장소의 문서를 근거로 가리킬 수 있고, 설정에 없는 저장소는 안내만 보이며 링크는 그대로 남는다.
- 메모 포함 HTML을 네트워크 없이 열어도 말풍선과 근거 모음이 동작한다(외부 참조 없음).

완료 기준(8.1c):

- `collab.enabled` 저장소의 기본 브랜치에 있는 회의 문서가 `/view/meetings`에 상태·일시순으로 모이고, 오류 문서는 사유와 함께 「확인 필요」에 보인다.
- 같은 폴더에 `<문서명>.memo.md`(회의 모드 `.md` 내보내기)를 커밋하면 결정사항·액션 아이템이 대시보드에 집계되고, 서버는 메모를 저장하지 않는다.
- `collab.enabled`가 꺼진 저장소의 회의는 목록·API에 나오지 않는다.

완료 기준(8.3):

- `meeting.channel`이 `collab.gateway.channels`에 매핑된 회의는 설정한 시점마다 한 번 Mattermost 채널에 안내가 가고, 재시작해도 같은 알림이 다시 가지 않는다(발송과 상태 저장 사이에 죽은 경우 제외).
- 회의 일시·제목·발표자 변경과 취소가 채널에 알려지고, 처음 켤 때 기존 회의에 대한 알림이 쏟아지지 않는다.
- 메시지에 담긴 문서 유래 글이 멘션이나 링크가 되지 않고, 토큰이 로그·오류에 남지 않는다.
- 실제 Mattermost에서의 도달은 `verify-collab.sh`로 확인했다(어댑터 단위). Viewer·Gateway 파드를 거친 전체 흐름은 2026-10-29에 확인했다(8.3 체크리스트 참조).

완료 기준(8.4):

- Mattermost 채널에서 `/synctx 오늘`·`예정`·`회의`·`정리`를 실행하면 그 채널에 매핑된 회의만 요청자에게만 보이는 메시지로 나오고, 다른 채널의 회의는 id를 알아도 나오지 않는다.
- 명령 토큰이 틀리면 응답하지 않고(401) 로그·메트릭에만 남으며, 사용자별 분당 호출 수가 제한된다.
- 로그에는 명령 본문·토큰·사용자 id 원문이 남지 않는다.
- 검색은 채널에 매핑된 회의의 저장소 id 최대 5개를 각각 요청해 경로·링크 최대 5건, 근거는 채널 안 회의의 제목·링크 최대 20건을 발췌 없이 답한다. 상한 초과는 `… 외 N건`, 일부 근거 생략은 `partial`·`omitted` 안내로 알린다(pytest 검증). 참여 명령(join)은 구현되어 회의 문서·발표 링크와 참여 시간 사유를 안내한다. 참여·검색·근거를 2026-10-29에 클러스터 안 파드와 실제 Mattermost로 실행해 확인했다(관리자 REST `commands/execute`. 영상 회의가 꺼져 있어 참여는 "영상 회의가 꺼져 있습니다" 안내 경로였다. 채팅 화면에서 사람이 직접 누른 경로와 근거 목록이 있는 회의는 미확인이다).

- [x] XWiki `markdown/1.2` 안 html 매크로 실행 확인 (로컬 XWiki 16.10.17에서 확인)
- [x] 설정 모델과 출처·부분집합 검사
- [x] `html_bundle` 합본·CSP meta·이스케이프
- [x] Viewer 렌더링·CSP·다운로드
- [x] Sync 변환과 다시 처리할 조건
- [x] 메트릭·Alert·Runbook
  - 계측(`synctx_html_bundle_total`)·Alert(`SynctxHtmlBundleRejected`)·Runbook(12.1, 12.6). Sync CronJob 메트릭은 저장소별 누적값을 Pushgateway로 push해 수집한다(9.2). Sync Alert는 promtool 규칙 테스트로 평가를 확인했다(9.3). 로컬 컨테이너(Pushgateway v1.11.1, Prometheus v2.39.1, `honor_labels: true` 정적 수집)에서 실제 Sync 실행 두 번(성공, XWiki 401·합본 크기 초과)으로 push → 수집 → 프로세스 간 누적 → `SynctxSyncAuthError`·`SynctxHtmlBundleRejected` firing까지 확인했다.
- [ ] 운영 XWiki·실제 브라우저 확인
  - 운영 XWiki: `markdown/1.2` 확장 설치, 동기화 전용 계정과 대상 공간 `WebPreferences`의 그 계정 한정 script right, Helm `synctxConfig.create: true`와 `synctxConfig.data` 안의 최상위 `html_embed`·`repositories[].html_embed`(`enabled`, XWiki 대상이면 `xwiki_credential_env`), Secret `feedback.secrets.xwikiHtmlCredentialEnabled: true`(`existingSecret`을 쓰면 그 Secret에 `xwiki-html-credential` 키) 뒤 재생·토글 확인
  - 실제 Chrome·Edge에서 재생과 CSP meta 차단 비교 시험(meta 있음/없음)
  - 신뢰 해제·계정 교체 때 XWiki content author가 실제로 바뀌는지
  - dev 클러스터에서 ServiceMonitor `synctx-pushgateway`(Prometheus Operator)로 `job="synctx-sync"` 계열이 수집되는지

추가 설계: HTML 렌더 보기(PR #63에서 WP1~WP4 구현)

- [x] WP1 ref 포함 `render_token`·정규형·`_FILE`·`bundle(frame_src=…)`·`resolved_reader` 예외 변환·`HtmlEmbedConfig.render_files`·`ViewerConfig.render_token_ttl_seconds`·`resolve_commit_sha`·`ensure_commit_available`와 단위 테스트
- [x] WP2 `/render`·`mode=render`·소스 보기 버튼·DOM 계약·CSP 예외·로그 마스킹·렌더 메트릭과 서버 테스트
- [x] WP3 화면 맞춤·비율·전체 화면·새 탭, `synctx-html` 블록 크게 보기·Esc·포커스 복귀와 클라이언트 테스트
- [x] WP4 WP1 설정·환경변수 이름 확정 후 Helm Secret·values-production 복수 파드 조건·Docker 키/파일 전달·두 로컬 실행 경로의 설정 생성·문서 반영과 배포 검사
- [ ] 운영 SSO 쿠키 SameSite·Secure의 2단계 중첩 요청 시험과 실패 시 대안 선택. 로컬·무인증 사용과 분리
- [x] Chromium 자동 시험(`services/viewer/tests/browser/`, 선택 실행). 2026-10-29 macOS 시스템 Google Chrome(headless, Playwright `channel="chrome"`)에서 8개 통과했다. Chrome 외 브라우저 결과는 아니다.
  - 하네스: `test_viewer_serves_synctx_html_block` — standalone `/view` 경로의 블록 버튼 동작
  - 격리·CSP: `test_iframe_origin_is_opaque`, `test_iframe_cannot_read_viewer_storage`, `test_external_requests_blocked_by_csp` — opaque origin·부모 document/저장소/쿠키 접근 거부·별도 loopback 수신 서버에 fetch/이미지 요청 미도달 및 CSP 위반 이벤트
  - 렌더 보기: `test_render_mode_runs_html_and_relative_link_same_snapshot`, `test_tampered_token_returns_403_without_execution`, `test_expired_token_recovers`, `test_block_zoom_overlay_esc_restores_focus` — 상대 `target=pf` 중첩 프레임·같은 ref/SHA·위조 거부·만료 안내 후 Viewer 새로 고침·iframe 재로드 없는 크게 보기와 Esc/포커스 복귀. 위조·만료는 설계 14.3의 403 계약이며 자동 갱신 시험은 아니다.
- [ ] 독립 캐시 파드 2개 중 한쪽만 fetch·404 복구·JSON 오류 헤더·실제 클릭 전체 화면 수용 시험, Edge·Firefox·Safari 확인(이번 브라우저 시험 범위 밖·미확인). 운영 SSO 시험도 위 별도 항목대로 미확인으로 유지

완료 기준:

- 신뢰 저장소의 모션 그래픽이 Viewer와 XWiki 페이지 안에서 재생·토글된다.
- 묶음 파일만 바꾸거나 신뢰 설정을 바꿔도 다음 동기화에서 XWiki 페이지가 갱신된다.
- 비신뢰 저장소와 합본 실패는 안내 문구로 보이고 페이지의 나머지는 정상이다.
- 추가 설계 완료 기준: HTML 기본 소스 보기를 유지하면서 신뢰 저장소의 상대 링크가 같은 토큰 SHA의 오른쪽 패널에서 열리고, 검증 실패 시 원문이 실행되지 않는다. 일반 화면·블록·Sync·XWiki의 기존 정책은 유지된다.

### 배포 옵션 (Phase 번호와 별개): Docker 배포 체크리스트

서비스 옵션, Docker Compose와 경량 Viewer 실행(8.7)은 새 Phase가 아니다. Phase 번호 체계(1-9)는 그대로다.

- [ ] 옵션 검사: `scripts/docker/deploy.sh --check`가 통과하고 켜진 서비스가 의도와 같다
- [ ] 이미지 반입: 켠 서비스의 이미지가 `registry.local:5000`에 있고 `SYNCTX_IMAGE_TAG`가 `0.7.0-<git-sha>` 형식이다(`latest` 아님)
- [ ] `.synctx.yaml` 준비: 인증 프록시가 없으면 `authz.enabled: false`, CDN을 끄면 `viewer.cdn_base_url`이 절대 주소다
- [ ] TLS: 인증서를 `tls/`에 두고 HTTPS 200, HTTP 301을 확인한다
- [ ] 헤더 제거 확인: 위조한 `X-Forwarded-Groups`로 직접 요청해도 권한이 올라가지 않는다(신뢰 모드면 허용 목록 밖 출발지는 403)
- [ ] 신뢰 모드: 3.1.1의 두 구성 중 하나를 따른다(다른 호스트의 프록시는 프록시 쪽 인터페이스 바인딩 + 방화벽, 컨테이너 프록시는 고정 주소). 허용 목록에 Docker 브리지 게이트웨이 주소가 없고, 접근 로그에서 프록시 요청의 출발지가 허용한 주소인지 확인한다
- [ ] 외부 포트: 게이트웨이 외 컨테이너가 호스트 포트를 열지 않는다(`compose.sh -- ps`)
- [ ] 볼륨 백업 절차를 한 번 시험했다(Runbook 12.7)

## 12. Runbook 초안

### 12.1 Sync 실패

1. `synctx_sync_last_success_timestamp` 확인. 계열이 없거나 모든 저장소가 함께 오래됐으면 수집 경로부터 본다.
   - Pushgateway(`synctx-pushgateway:9091/metrics`)의 `push_time_seconds{job="synctx-sync"}`가 최근인지 확인한다. 오래됐으면 CronJob이 돌고 있는지(`kubectl get cronjob,job`)와 Sync 로그의 `Failed to push sync metrics` 줄을 본다.
   - push 실패면 Pushgateway Pod 상태와 Sync NetworkPolicy의 9091 egress를 확인한다.
   - Pushgateway에는 있는데 Prometheus에 없으면 ServiceMonitor `synctx-pushgateway`가 수집 대상에 잡혔는지 확인한다.
   - 누적값은 sync-state의 `metrics/{repo_id}.json`에 있다. 파일이 깨지면 이번 실행 값부터 다시 쌓는다(Prometheus는 카운터 리셋으로 처리한다).
2. 최근 실행 확인: Sync Job 로그와 상태 파일 `/data/sync-state/{repo_id}.json`의 `last_run_id`·`pending_failures`
3. `auth_error`면 Secret 만료 여부 확인
4. `mapping_error`면 `.synctx.yaml`의 map 또는 문서 front matter 수정
5. `transient_error`면 대상 서비스 상태 확인 후 Job 재실행

### 12.2 Marp publish 실패

1. Marp Service log에서 `publish_path`, `repo_id`, `commit_sha` 확인
2. publish repo non-fast-forward 여부 확인
3. worktree lock(`marp.worktree_path`의 `.publish.lock`) stale 여부 확인. 남아 있으면 publish가 계속 409다
4. 재시도해도 실패하면 publish repo 권한과 branch protection 확인. Marp log에 `publish_auth_fallback`이 있으면 `GIT_PUBLISH_TOKEN`이 비어 읽기 토큰으로 push한 것이다(3.4)

### 12.3 CDN 파일 교체

1. 같은 version 파일 교체가 필요한지 확인
2. 가능하면 새 version 디렉토리로 업로드
3. 불가피한 교체는 `force=true`로 업로드
4. manifest diff와 audit log 확인

### 12.4 Slides Server 장애

1. Slides Server readiness와 Nginx access/error log 확인
2. `slides-sync` sidecar log에서 최근 pull 시각과 commit sha 확인
3. publish repo 접근 권한과 slides-sync가 쓰는 `GIT_READ_TOKEN` 만료 여부 확인
4. worktree 손상이 의심되면 pod를 재시작하여 clean clone으로 복구
5. 특정 publish path만 404이면 Marp publish commit에 해당 파일이 포함되었는지 확인

### 12.5 Outbound Feedback 장애

1. `synctx_feedback_last_success_timestamp`와 `synctx_feedback_mail_failures_total` 확인. 값이 없거나 멈췄으면 Pushgateway의 `push_time_seconds{job="synctx-feedback"}`가 최근인지, Feedback 로그에 `Failed to push feedback metrics` 줄이 있는지 본다
2. SMTP 실패면 approved mail gateway 접속, 계정 잠금, `OUTBOUND_SMTP_CREDENTIAL` 만료 여부 확인
3. redaction failure면 Feedback 로그의 `Failed to redact WP` 줄로 Work Package와 오류를 찾고 allowlist 설정을 확인한다. 그 Work Package는 발송·기록하지 않고 다음 실행에서 다시 보므로, 고칠 때까지 Alert가 이어진다. 본문/댓글/첨부가 payload에 포함되지 않았는지 dry-run으로 검증
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
3. 켜진 서비스가 없거나(D1), CDN이 꺼졌는데 `viewer.cdn_base_url`이 상대 경로이거나(D2), 메트릭이 켜졌는데 Sync·Feedback이 sync-state PVC 없이 켜져 있으면(D8) `helm upgrade`가 렌더 단계에서 실패한다. 메시지를 보고 옵션이나 `.synctx.yaml`을 고친다.
4. 업그레이드는 `helm upgrade ... -f values-production.yaml`처럼 values 파일을 넘겨 실행하고 `--reuse-values`는 쓰지 않는다. `--reuse-values`는 이전 릴리스 값을 그대로 써서 차트에 새로 생긴 기본값이 빠진다. `viewer`·`cdn`·`marp`·`slides`·`drawio`·`sync`는 `enabled` 키가 없으면 켜진 것으로 보므로 서비스 옵션이 없던 릴리스를 올려도 서비스가 사라지지는 않는다(`feedback`은 키가 없으면 꺼짐).

### 12.9 HTML 렌더 토큰 만료·키 교체

1. `denied_token`이 늘면 만료인지 전 인스턴스의 공유 키 불일치인지 확인한다. 키·토큰 값은 로그로 출력하지 않는다. `denied_dest`면 브라우저가 `Sec-Fetch-Dest`를 보내고 프록시가 전달하는지 확인한다. 헤더가 없으면 구형 브라우저도 403이며 검사를 완화하지 않는다.
2. 만료·무작위 키 서버 재시작 뒤에는 `/render` 주소가 아니라 원래 `/view/{repo_id}/{path}?mode=render` 화면을 새로 고친다. iframe 오류 안내에서 이 절차를 설명한다. 토큰은 화면 생성 함수에서 다시 발급하며 이를 공유하는 `render_file` API에도 나갈 수 있다. 토큰 전용 발급 API는 없다. 만료는 실행 중인 문서를 강제 종료하지 않으며 다음 링크·프레임 요청부터 실패한다.
3. 복수 인스턴스·프로세스는 `SYNCTX_RENDER_SECRET`을 공유한다. 무작위 폴백은 단일 인스턴스·로컬용이다. `SYNCTX_RENDER_SECRET_PREVIOUS`도 지정했다면 전 인스턴스에서 일치해야 한다. 설정 스키마·배포 참조는 4장을 따른다.
4. 교체 준비 단계에서는 기존 키를 현재 키로 유지하고, 새 키를 검증 후보(`SYNCTX_RENDER_SECRET_PREVIOUS`)에 넣어 전 인스턴스에 먼저 배포한다. PREVIOUS라는 이름과 달리 이 준비 단계에서만 다음 키를 담는 검증 전용 슬롯이다. 이 단계에서는 기존 키로만 발급한다. 모든 인스턴스가 두 키를 검증할 수 있는지 확인한다.
5. 그다음 현재 키를 새 키, 이전 키를 기존 키로 바꾸어 롤링 배포한다. 준비 단계 덕분에 배포 중 구·신 인스턴스 모두 두 서명을 검증한다. 새 키 발급·기존 토큰 검증을 확인한다. `services/feedback/src/signer.py`의 후보 키 검증 선례를 따르며 Feedback 키 자체는 바꾸지 않는다.
6. 기존 키로 발급하는 마지막 인스턴스의 전환 시각부터 기존 토큰의 최대 TTL(기본 4시간, 이전 설정값도 고려)이 지난 뒤 이전 키를 제거한다. 키 유출로 즉시 폐기가 필요하면 해당 키를 후보에서도 제거하고 기존 토큰 무효화·화면 새로 고침을 안내한다.
7. `enabled` 또는 `render_files`를 해제할 때는 플랫폼 설정을 반영하고 Viewer Deployment의 rollout restart를 수행한다. 로컬 minikube에서는 직접 kubectl을 실행하지 않고 `scripts/local/steps/deploy-synctx.sh`의 재시작 단계를 쓴다. `config = load_config()`가 import 때 한 번만 실행되므로 모든 파드가 새 설정으로 재시작됐는지 확인한 뒤 기존 유효 토큰도 403인지 확인한다. 이미 실행 중인 iframe은 강제로 종료하지 않는다.
8. 파드별 `cat-file -e {sha}^{commit}` 실패는 서명된 ref fetch 후 같은 SHA를 재확인한다. 여전히 없으면 404·원래 화면 새로 고침으로 복구한다. 루트 초과 상대 경로의 서명 불일치는 만료로 안내하지 않는다. 키·토큰은 출력하지 않는다.
9. 접근·앱 로그와 메트릭 라벨에 토큰이 없는지 점검한다. 인그레스·EFK 잔존 위험은 수용한 범위이며 로그 접근권한·보존 기간을 확인한다. 앞단에서 `/render`에 XFO·`frame-ancestors`를 추가했다면 중첩 프레임이 차단되므로 해당 경로 예외를 확인한다.

### 12.10 Collab Gateway 알림 장애·토큰 교체

1. `SynctxCollabNotificationFailures`·`SynctxCollabNotificationGaveUp`: Gateway 로그의 `collab_notification_failed`·`collab_notification_gave_up`의 `reason`을 본다. `auth`는 봇 토큰, `channel_not_found`는 `channels`의 team·channel 이름(또는 봇의 채널 참여), `network`·`http_5xx`는 Mattermost 접근과 NetworkPolicy(`collabGateway.networkPolicy.mattermost`)를 확인한다. 토큰·응답 본문은 로그에 없다.
2. `gave_up`으로 기록된 알림은 다시 보내지 않는다. 원인을 고친 뒤에도 이미 시작한 회의의 예고는 의미가 없으므로 그대로 둔다. 취소·변경 알림을 꼭 다시 보내야 하면 해당 회의 항목을 상태 파일에서 지운다(6번).
3. `SynctxCollabViewerFetchFailing`·`SynctxCollabStalled`: Viewer 상태와 `VIEWER_SERVICE_TOKEN`이 Gateway와 Viewer에서 같은 Secret 값인지 본다(Viewer가 403이면 두 값이 다르거나 Viewer를 재시작하지 않은 것이다).
4. `SynctxCollabUnmappedChannel`: 로그의 `collab_unmapped_channel channel=<값>`을 보고 `.synctx.yaml`의 `collab.gateway.channels`에 그 채널을 추가한다. 채널 매핑이 없는 동안 그 회의는 상태에도 남지 않으므로, 매핑을 추가하면 처음 본 회의로 취급되어 변경·취소 이력은 알리지 않는다.
5. `/synctx` 명령이 안 될 때: `SynctxCollabCommandUnauthorized`가 울리면 Mattermost의 명령 토큰과 Secret `command-token`이 같은지, Gateway를 재시작했는지 본다(Mattermost에서 명령을 지웠다 다시 만들면 토큰이 바뀐다). Mattermost에서 "Command with a trigger of 'synctx' failed"가 보이면 호출 주소·Gateway Service·`AllowedUntrustedInternalConnections`와 NetworkPolicy를 확인한다. 응답이 "지금 회의 정보를 읽지 못했습니다"면 `SynctxCollabViewerFetchFailing` 절차(3번)를 따른다. 명령 토큰 교체는 Mattermost에서 명령을 새로 만들어 값을 Secret(`command-token`)에 넣고 Gateway를 재시작한다.
6. 봇 토큰 교체: Mattermost에서 새 토큰을 만들고 Secret(`bot-token`)을 바꾼 뒤 Gateway를 재시작한다(복제본 1개, `Recreate`). 서비스 토큰을 바꿀 때는 Secret의 `viewer-service-token`을 바꾸고 Viewer와 Gateway를 둘 다 재시작한다. 상태 파일을 초기화하려면 Gateway를 내린 뒤 PVC의 `collab_gateway_state.json`을 지운다. 이후 모든 회의가 처음 본 회의로 취급되어 변경·취소·요약은 알리지 않고, 시작 전 시점이 지난 예고는 가장 가까운 것 하나만 다시 간다.
7. 상태 파일이 손상되면 `.corrupt-<시각>`으로 옮겨지고(로그 `collab_state_reset`) 새로 시작한다. 같은 PVC를 쓰는 두 프로세스가 동시에 돌면 한쪽은 `collab_cycle_skipped`를 남기고 그 주기를 건너뛴다.

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

### 12.11 Jitsi 배포·장애·키 교체

Jitsi는 선택 보조 구성요소다. 운영에 이미 Jitsi가 있으면 Helm `jitsi.enabled: false`를 유지하고 `collab.jitsi.enabled: true`, `public_url`, `app_id`와 Viewer의 `jitsi.jwtSecret.existingSecret`·`roomSecret.existingSecret`을 지정한다. 기존 Jitsi의 JWT 앱 id·키·token_affiliation 정책도 일치시킨다. 웹/API 의미와 권한은 5.5·7장의 A1 계약을 유지한다. room_secret은 Prosody·Gateway에 보내지 않는다.

자체 스택은 `jitsi.enabled`, `host`, `publicUrl`, `appId`, `ingress.tlsSecretName`, `jvb.advertisedIPs`, 세 Secret 참조를 별도 값 파일로 지정한다. 앱·방 Secret 키는 current(필수)/previous(선택), XMPP Secret 키는 jicofo-password/jvb-password다. 운영은 existingSecret을 쓰며 create/current/previous 값 파일은 로컬 시험용이고 커밋하지 않는다. `.synctx.yaml`의 public_url/app_id와 차트가 다르면 렌더를 거부한다. Helm은 secret_env/room_secret_env 이름을 따라 연결하며 `_PREVIOUS`를 제공한다. Compose는 기본 이름만 지원하고 다른 이름은 사전 검증에서 거부한다.

이미지 반입: 기준값은 `helm/synctx/values.yaml`의 `jitsi.imageTag` 한 곳이다. `values-production.yaml`은 enabled를 끈 채 네 repository만 `registry.local:5000/jitsi/*`로 덮어쓴다. `scripts/generate-manifest.sh`는 같은 태그로 네 이미지 항목을 기록한다. 이 저장소에 pull-and-save-images.sh는 없다. 현재 stable-9823은 2024년 태그이므로 반입 전 보안 패치 기준으로 태그를 다시 고른다. Docker Hub 실존·아키텍처는 미확인이다. 네 공식 이미지 모두 반입 전 `docker manifest inspect`로 실존 확인 후 대상 amd64(로컬은 arm64 지원도)를 확인하고 tar 반입·내부 registry push를 수행한다. 이번 작업에서는 네트워크·Docker 실행 없이 목록만 정적 검증했다. 실제 반입 환경에서만 이 확인 명령을 실행한다.

Compose의 Jitsi 사전 검증에는 uv와 미리 준비한 workspace Python 패키지가 필요하며 `--offline --frozen`으로 실행한다. 인터넷에서 설치하지 않으므로 배포 전에 오프라인 환경을 준비한다.

Compose: synctx.env.example·secrets.env.example을 복사해 파일 권한을 600으로 제한한다. `SYNCTX_JITSI=true`, `SYNCTX_JITSI_HOST`, `SYNCTX_JITSI_PUBLIC_URL=https://호스트`, `SYNCTX_JITSI_ADVERTISE_IPS`와 기존 TLS 인증서·키 설정을 채우고, `.synctx.yaml`의 collab.jitsi와 일치시킨다. 앱/방의 현재·이전 키는 `_FILE`로 참조하거나 secrets.env에 환경변수로 지정한다. 각 현재 키는 서로 다른 32바이트 이상이어야 한다. XMPP 암호 두 개도 각각 32바이트 이상으로 secrets.env에 지정한다. `_FILE`로 준 앱 키도 Prosody 컨테이너 환경변수 JWT_APP_SECRET으로 풀려 `docker inspect`·`scripts/docker/compose.sh -- config`에 보인다. 파일 권한(600)과 호스트 접근 통제가 필요하다. JVB에는 Jicofo 암호나 JWT 설정 등 불필요한 환경변수를 주지 않는다. Compose tmpfs는 /config·/tmp만 사용하며 /run은 덮지 않는다. `scripts/docker/deploy.sh --check`로 생성 설정과 키를 검사한 뒤 같은 스크립트로 배포한다. 기본 태그는 차트에서 읽으며 `SYNCTX_JITSI_IMAGE_TAG`로 명시적으로 고정 stable 태그를 덮어쓸 수 있다. 호스트 TLS 인증서는 Jitsi 이름도 포함해야 한다. 운영 명령은 `scripts/docker/compose.sh -- ps`·`-- logs jitsi-prosody`처럼 래퍼를 사용한다.

장애 점검:

1. 참여 버튼/토큰 오류는 `synctx_viewer_jitsi_token_failures_total{reason}`·Viewer 설정·회의 시간·권한부터 확인한다. `SynctxJitsiTokenFailures`는 10분 내 5회 이상 실패가 2분 지속할 때 발생하며 정상적인 시간 창 밖 요청도 포함한다. JWT·키·응답 본문은 로그에 남기지 않는다.
2. 웹·입장이 실패하면 HTTPS 인증서·DNS·Ingress 또는 게이트웨이·BOSH(`/http-bind`)·XMPP WebSocket(`/xmpp-websocket`)·Colibri WebSocket(`/colibri-ws/jvb/`) 연결을 점검한다. JWT_APP_ID·AUTH_TYPE=jwt·JWT_APP_SECRET 일치, JWT_ALLOW_EMPTY=0, Prosody token_affiliation과 ENABLE_AUTO_OWNER=0, Jicofo ENABLE_AUTH=0을 확인한다. 실제 참가자의 moderator 권한은 반드시 브라우저에서 검증한다.
3. 영상·음성이 없으면 브라우저에서 JVB 광고 주소로 UDP 10000(Compose/hostPort) 또는 지정 NodePort(기본 31000)에 도달하는지, NAT와 방화벽을 점검한다. NodePort에서는 JVB_PORT·containerPort·Service port·nodePort가 모두 같은 값(기본 31000)이어야 한다. 광고 주소는 쉼표 구분 IP 목록만 받고 포트·# 형식을 거부한다. stable-9823 jvb.conf는 IP와 포트를 분리하지 않는다. tcpFallback은 기본 false이며 stable-9823 템플릿에 TCP 하베스터 처리가 없어 동작하지 않는다. 별도 TURN 설계 전까지 UDP 허용을 운영 조건으로 둔다. JVB_DISABLE_STUN=1과 web 컨테이너의 ENABLE_P2P=false로 외부 STUN 의존을 끊도록 지정한다. web 변수 유효성과 실제 외부 STUN 요청 차단은 확인 필요다. 로컬 터널은 UDP 검증 근거가 아니다.
4. 공식 이미지의 s6 `/init`는 설정 생성·소유권 조정·서비스 사용자 전환을 수행하므로 root 시작을 허용한다. privileged·hostNetwork는 사용하지 않고 Helm RuntimeDefault seccomp, probe·resources·emptyDir를 둔다. emptyDir에는 재생성 가능한 설정만 있으며 재배포 시 진행 중 회의가 끊길 수 있다. 이미지 기동·probe·Java 메모리 상한은 실제 배포 확인이 필요하다.

키 교체: Viewer는 앱·방 이름 키의 previous를 읽기만 하고 쓰지 않는다. 두 이전 슬롯의 현재 소비처는 없다. 이 구성의 Prosody는 현재 JWT_APP_SECRET 하나만 검증한다. 이전 슬롯을 넣었다고 이전 JWT가 계속 통과한다고 간주하지 않는다. 회의 없는 점검 시간을 잡고 앱 current를 새 키로, previous를 기존 키로 보관한 후 Viewer와 Prosody를 함께 재생성하고 새 입장 JWT와 참가자 역할을 확인한다. 기존 JWT는 재입장이 필요하다. 외부 Jitsi도 그 검증기의 지원 범위에 맞춰 교체한다. room_secret을 바꾸면 진행 중 회의 방 이름이 바뀌므로 회의 없는 시간에 교체하고 Viewer 전 인스턴스를 함께 재생성한다. XMPP 암호 변경 시 Prosody·Jicofo·JVB도 함께 재생성한다. Secret 환경변수는 자동 갱신되지 않는다.

정적 테스트 목록: scripts/tests/test_helm_jitsi.py(기본 off·외부 Jitsi·네 워크로드·키 참조·TLS·NodePort/hostPort·오류 거부), test_docker_deploy.py(선택 profile·키 파일·호스트 프록시), test_local_scripts.py(옵션·gitignore·기본 setup 제외), scripts/tests/prometheus/jitsi-alerts.test.yaml 및 test_prometheus_rules.py(반복 실패/낮은 빈도/복구/계측 없음). Docker 실행을 제외하려면 테스트 전에 SYNCTX_TEST_NO_DOCKER=1을 지정한다(수집 시 image inspect도 생략). promtool이 없으면 평가 테스트는 skip되므로 실제 평가 완료와 구별한다.

NetworkPolicy 판단: 현재 차트의 Sync·Gateway 정책은 Viewer를 선택하지 않는다. Viewer는 토큰을 로컬 서명하고 브라우저가 public_url의 스크립트·iframe·미디어에 직접 접속하므로 Viewer→Jitsi egress는 추가하지 않는다. 클러스터 공통 기본 차단 정책이 별도로 있어도 이 기능 때문에 Viewer의 Jitsi egress를 열 필요는 없다.
