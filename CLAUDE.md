# synctx Project Instructions

## 프로젝트 개요

synctx(sync + context)는 폐쇄망 금융 환경에서 AI 기반 개발 컨텍스트를 동기화하는 마이크로서비스 플랫폼.
설계 문서와 구현 코드가 공존하는 단계.

## 문서 구조

| 문서 | 역할 |
|------|------|
| `README.md` | 서비스 소개(소개 그림, 할 수 있는 일, 시작하기, 메뉴별 도움말 표, 데이터 위치, 릴리스). 개발자용 내용은 두지 않는다 |
| `docs/development.md` | 개발 문서: 프로젝트 배경, 아키텍처 다이어그램, 서비스 테이블, 배포 방식, 서비스별 기능, 프로젝트 구조, 기술 스택 |
| `docs/help/` | Viewer 메뉴별 도움말. `scripts/build_motion.py`가 `scripts/motion/help_pages.py`로 그림·글을 생성(직접 고치지 않는다) |
| `docs/install.md` | 설치 방식 선택: 구성(전체·선택·정적)×대상(K8s·Compose·정적) 지원 범위, 프리셋, 정적 Viewer(Cloudflare Pages) 설치·롤백 |
| `docs/architecture.md` | 상세 아키텍처: 설계 결정, 서비스별 설계, 모듈 구조, Helm Chart, 개발 단계 |
| `docs/production-service-design.md` | 운영 설계: 보안, .synctx.yaml 스키마, API 스펙, 배포, 관측성, Runbook, 체크리스트 |
| `docs/reusable-assets.md` | 기존 저장소(markdown_converter, example-infra 등)에서 재사용할 자산 목록 |
| `docs/xwiki-api-reference.md` | XWiki REST API 레퍼런스 (Sync Engine 구현용) |
| `docs/html-embed-design.md` | 인터랙티브 HTML 임베드(`synctx-html` 블록, Phase 9) 설계 |
| `docs/viewer-index-design.md` | Viewer 인덱스 페이지(저장소 목록·파일 트리) 초기 설계 기록. 현행 화면은 architecture.md 3.6 |
| `docs/local-development.md` | 로컬 개발 환경(minikube, Viewer 단독 실행) |

## 문서 검토 규칙

### 3개 문서 간 일관성 필수 항목

서비스, 설정, 역할 등을 추가/변경할 때 반드시 3개 문서를 함께 갱신:

- **서비스 추가**: docs/development.md(다이어그램 + 테이블 + 프로젝트 구조), README(소개 표에 사용자에게 보이는 기능이면), architecture.md(다이어그램 + 테이블 + 모듈), production-service-design.md(설정 + API + 보안 + 메트릭 + Alert + 테스트 + Phase 체크리스트)
- **Secret 추가**: Secrets 테이블, .synctx.yaml 스키마, Runbook
- **Role 추가**: Roles 테이블, authz 설정 예시
- **Phase 변경**: 3개 문서의 Phase 번호와 순서 일치 (1-4 코어 → 5 운영 준비 → 6 하네스/스크럼 → 7 Outbound Feedback → 8 협업 통합 → 9 인터랙티브 HTML 임베드)

### 검토 출력 형식

검토 결과는 **Critical / Warning / Suggestion** 3단계로 구분.

## 핵심 설계 원칙

- **단방향 동기화**: Git이 source of truth, XWiki/OpenProject는 파생 데이터
- **폐쇄망 독립**: 모든 외부 의존성 vendoring, 런타임에 외부 네트워크 접근 없음
- **이미지 태그**: SemVer `"0.8.2-<git-sha>"` 형식, `latest` 태그 사용 금지
- **Outbound Feedback**: Phase 7 선택 기능, 양방향 동기화가 아니라 서명된 메일 이벤트 반출 + 외부 PR 생성

## 서비스 목록 (6+1+2)

Viewer, CDN, Marp, Draw.io, Sync Engine, Slides Server, Sync Webhook Receiver, Outbound Feedback Exporter(선택), Collab Gateway(선택)

보조 구성요소: Jitsi(Phase 8.2, 제3자 이미지·선택 배포(`jitsi.enabled`, 기본 꺼짐)), Pushgateway(`metrics.enabled`이고 `sync.persistence`가 켜졌으며 Sync 또는 Feedback이 켜졌을 때만 배포, Sync·Feedback CronJob 메트릭 수집)

## 설정 파일

- `.synctx.yaml`: 동기화 설정 (저장소별). `sync:`, `harness:`, `outbound_feedback:` 블록
- Front Matter: `synctx:` 네임스페이스. `type`(task/doc/report/meeting 등)과 `route`(openproject/xwiki/both)로 라우팅. 회의 문서는 `synctx.meeting` 블록(id·title·starts_at·channel 필수)

## Mermaid 다이어그램 규칙

- `%%{init:...}%%` 초기화 블록이나 `classDef` 사용 금지
- docs/development.md는 subgraph(External/Internal) 사용, architecture.md는 flat 노드
- 시퀀스 다이어그램 액터 순서: 좌→우 = 사용자 → FE → BE → Engine → DB

## 운영 환경 컨텍스트

### 프로젝트 전제
- 폐쇄망 금융 환경에서 쓰려고 만든 개인 프로젝트
- **도메인 패턴**: 운영 `*.corp.example.com`(예시), 로컬 `*.synctx.local`

### 서버 환경
- **OS**: Oracle Linux 9 (x86_64/amd64)
- **K8s**: Kubespray 배포, CRI-O 런타임, kubeVersion >= 1.22.0
- **클러스터 구성**: 운영·개발 클러스터를 나누어 쓴다

### 이미지 / 레지스트리
- **내부 레지스트리**: `registry.local:5000` (예시, 폐쇄망 전용)
- **이미지 전송**: `pull-and-save-images.sh`로 외부에서 pull → tar 저장 → 폐쇄망 반입 → registry push
- **베이스 이미지**: `python:3.13.16-slim-trixie` (Debian 13 기반, Alpine 사용 금지). 패치 버전·배포판까지 고정하고, 올릴 때는 Dockerfile 6개와 `requires-python`을 함께 바꾼다
- **멀티 아키텍처**: Dockerfile은 플랫폼에 종속되지 않게 작성. 로컬(ARM64)과 운영(amd64)에서 동일 Dockerfile 사용. `--platform` 플래그는 빌드 스크립트에서 환경에 맞게 지정

### CI/CD
- **폐쇄망**: GitLab CI (내부)
- **외부**: GitHub (외부 개발용)

### TLS
- **운영**: Ingress TLS Secret 직접 마운트
- **로컬**: cert-manager v1.13.3 + mkcert CA → `ca-issuer` ClusterIssuer 자동 발급

### 인프라 구성 (로컬 minikube)
- **Ingress**: Nginx Ingress Controller (Helm Chart `ingress-nginx/ingress-nginx` v4.8.3)
- **TLS**: cert-manager v1.13.3 + mkcert CA
- **Monitoring**: Prometheus v2.39.1 + Grafana 9.5.20 (`monitoring` namespace)
- **Logging (운영)**: EFK Stack — Elasticsearch 7.17.3, Fluent Bit 2.1.10, Kibana 7.17.3

## 로컬 개발 (minikube)

- **스크립트**: `scripts/local/setup.sh` (전체 설정), `scripts/local/steps/build.sh` (빌드), `scripts/local/steps/deploy-synctx.sh` (배포)
- **빌드/배포**: 직접 `docker build`/`kubectl` 명령 사용 금지 — 반드시 `scripts/local/` 스크립트 사용
- **스크립트 오류 시**: 스크립트를 우회하지 말고, 원인을 파악하여 스크립트 자체를 수정
- **네임스페이스**: `synctx` (scripts에 정의됨, `default` 네임스페이스 사용 금지)
- **minikube 프로필**: `MINIKUBE_PROFILE=synctx`처럼 전용 클러스터를 쓸 수 있다. 스크립트가 minikube·kubectl·helm을 그 프로필로 고정한다(터널은 `minikube tunnel -p <프로필>`)
- **Viewer만 로컬 실행**: `./run.sh --repo <Git 주소>`(문서·회의 Viewer만, `uv`, 번들 자산, http://127.0.0.1:18000/view/). Marp 미리보기까지는 `scripts/local/run-viewer.sh`(Docker·minikube 없이 `/view`·`/marp`·`/cdn`을 한 프로세스로). 설정은 values-local.yaml에서 만들어 `~/.cache/synctx/`에 둔다
- **단일 Viewer 컨테이너**: `./run.sh --docker`(= `scripts/docker/run-viewer.sh`)로 띄운다. 직접 `docker run` 금지 — 태그·토큰·렌더 키 전달을 스크립트가 맞춘다
- **Docker Compose 배포**: `scripts/docker/deploy.sh`(옵션 파일 `deploy/docker/synctx.env`), 중지 `scripts/docker/down.sh`, 운영 명령 `scripts/docker/compose.sh -- <명령>`. 직접 `docker compose up` 금지 — 게이트웨이 설정과 설정 해시를 deploy.sh가 만든다

## 구현 주의사항

- **FastAPI에서 subprocess 호출 시** `asyncio.run_in_executor`로 비동기 실행 — 동기 subprocess는 이벤트 루프를 블로킹하여 K8s liveness probe 실패 유발

## Git 참고

- `GITHUB_TOKEN` 환경변수에 잘못된 토큰이 있을 수 있음. push 시 `GITHUB_TOKEN= git push -u origin main`으로 keyring fallback 사용
- 원격 저장소: `https://github.com/sch0718/synctx.git` (private)

## 웹 플랫폼 가이드 (Modern Web Guidance)

HTML·CSS·브라우저 JS를 만들거나 고칠 때는 Chrome·Edge·Firefox·MDN 팀이 관리하는 [Modern Web Guidance](https://github.com/GoogleChrome/modern-web-guidance) 스킬을 쓴다. `.claude/settings.json`이 플러그인 `modern-web-guidance@googlechrome`을 켠다(처음 열 때 마켓플레이스 신뢰를 묻는다). 서버·파이프라인·빌드 설정·시험 코드 작업에는 쓰지 않는다.

- **대상:** Viewer 정적 파일(`services/viewer/src/static/`)과 Viewer가 내보내는 HTML·CSS. Python 서비스, Helm, 스크립트는 대상이 아니다.
- **개발:** 화면·CSS·클라이언트 JS 작업을 시작할 때 먼저 가이드를 찾아 읽고 구현한다. 직접 만들거나 라이브러리를 더하기 전에 같은 일을 하는 웹 표준 기능(`<dialog>`, popover, anchor positioning, `light-dark()`, `field-sizing` 등)이 있는지 확인한다. 가이드는 바닐라 HTML/CSS/JS 기준이므로 바닐라 JS(Viewer)에 맞게 옮기되 최신 기능은 버리지 않는다. 검색어는 기능을 뜻하는 영어 2~5단어로 쓰고, 기능이 여럿이면 나눠 찾는다.

  ```sh
  DISABLE_TELEMETRY=1 npx -y modern-web-guidance@latest search "light dismiss dialog"
  DISABLE_TELEMETRY=1 npx -y modern-web-guidance@latest retrieve "<id>[,<id>...]"
  ```

- **점검:** 화면을 넓게 바꾼 PR, 릴리스 전, 정기 점검 때 `legacy-scan`으로 옛 패턴을 찾는다. 결과는 후보일 뿐이므로 항목마다 규칙의 `keepWhen`/`dropWhen`으로 확인하고, 일부러 둔 대체 코드·외부(vendor) 코드·비슷해 보이기만 하는 코드는 고치지 않는다. 확인한 것만 고치고 같은 경로를 다시 스캔한다. 스캔 규칙은 전체 가이드의 약 20%에만 있으므로 0건이 곧 최신이라는 뜻은 아니다. 기능 하나를 고칠 때는 스캔하지 않고 위 「개발」 순서를 따른다.

  ```sh
  DISABLE_TELEMETRY=1 npx -y modern-web-guidance@latest legacy-scan <경로> --json
  ```

- **브라우저 지원:** Baseline Widely available 기능은 대체 코드 없이 쓴다. 그보다 새 기능은 가이드의 대체 방법을 따르되, 폴리필 패키지보다 기능 감지와 짧은 대체 코드를 먼저 고른다. Origin Trial·변동성 기능(`.mwgrc`의 `allowOriginTrials`·`allowVolatileFeatures`)은 켜지 않는다.
- **한글 입력:** Enter로 저장·제출하는 입력은 IME 조합 중(`event.isComposing`)이면 무시한다(가이드 `ime-safe-enter-submit`). 조합 중 Enter를 처리하면 마지막 글자가 빠지거나 두 번 저장된다.
- **텔레메트리:** 이 CLI는 기본으로 검색어와 가이드 ID를 Google에 보낸다. 명령 앞에 늘 `DISABLE_TELEMETRY=1`을 붙이고, 검색어에는 일반적인 UI 기능 이름만 쓰며 저장소 내용을 넣지 않는다.
- **폐쇄망:** 이 도구는 외부(GitHub) 개발 환경에서만 쓰는 개발 도구다. 이미지·vendoring·런타임에 넣지 않는다. 폐쇄망 사내 브라우저 버전이 확인되지 않았으므로 Baseline Newly available 기능은 대체 코드 없이 쓰지 않는다.
