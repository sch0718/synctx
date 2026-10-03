# synctx Project Instructions

## 프로젝트 개요

synctx(sync + context)는 폐쇄망 금융 환경에서 AI 기반 개발 컨텍스트를 동기화하는 마이크로서비스 플랫폼.
설계 문서와 구현 코드가 공존하는 단계.

## 문서 구조

| 문서 | 역할 |
|------|------|
| `README.md` | 프로젝트 개요, 아키텍처 다이어그램, 서비스 테이블, 프로젝트 구조 |
| `docs/architecture.md` | 상세 아키텍처: 설계 결정, 서비스별 설계, 모듈 구조, Helm Chart, 개발 단계 |
| `docs/production-service-design.md` | 운영 설계: 보안, .synctx.yaml 스키마, API 스펙, 배포, 관측성, Runbook, 체크리스트 |
| `docs/reusable-assets.md` | 기존 저장소(markdown_converter, example_system 등)에서 재사용할 자산 목록 |
| `docs/xwiki-api-reference.md` | XWiki REST API 레퍼런스 (Sync Engine 구현용) |

## 문서 검토 규칙

### 3개 문서 간 일관성 필수 항목

서비스, 설정, 역할 등을 추가/변경할 때 반드시 3개 문서를 함께 갱신:

- **서비스 추가**: README(다이어그램 + 테이블 + 프로젝트 구조), architecture.md(다이어그램 + 테이블 + 모듈), production-service-design.md(설정 + API + 보안 + 메트릭 + Alert + 테스트 + Phase 체크리스트)
- **Secret 추가**: Secrets 테이블, .synctx.yaml 스키마, Runbook
- **Role 추가**: Roles 테이블, authz 설정 예시
- **Phase 변경**: 3개 문서의 Phase 번호와 순서 일치 (1-4 코어 → 5 운영 준비 → 6 하네스/스크럼 → 7 Outbound Feedback → 8 협업 통합)

### 검토 출력 형식

검토 결과는 **Critical / Warning / Suggestion** 3단계로 구분.

## 핵심 설계 원칙

- **단방향 동기화**: Git이 source of truth, XWiki/OpenProject는 파생 데이터
- **폐쇄망 독립**: 모든 외부 의존성 vendoring, 런타임에 외부 네트워크 접근 없음
- **이미지 태그**: SemVer `"0.1.0-<git-sha>"` 형식, `latest` 태그 사용 금지
- **Outbound Feedback**: Phase 7 선택 기능, 양방향 동기화가 아니라 서명된 메일 이벤트 반출 + 외부 PR 생성

## 서비스 목록 (6+1+1)

Viewer, CDN, Marp, Draw.io, Sync Engine, Slides Server, Sync Webhook Receiver, Outbound Feedback Exporter(선택)

## 설정 파일

- `.synctx.yaml`: 동기화 설정 (저장소별). `sync:`, `harness:`, `outbound_feedback:` 블록
- Front Matter: `synctx:` 네임스페이스. `type`(task/doc/report/meeting 등)과 `route`(openproject/xwiki/both)로 라우팅. 회의 문서는 `synctx.meeting` 블록(id·title·starts_at·channel 필수)

## Mermaid 다이어그램 규칙

- `%%{init:...}%%` 초기화 블록이나 `classDef` 사용 금지
- README는 subgraph(External/Internal) 사용, architecture.md는 flat 노드
- 시퀀스 다이어그램 액터 순서: 좌→우 = 사용자 → FE → BE → Engine → DB

## 운영 환경 컨텍스트

### 회사 / 시스템
- **회사**: 금융사
- **시스템**: 사내 시스템 (금융 폐쇄망)
- **도메인 패턴**: 운영 `*.corp.example.com` `*.dev.example.com`, 로컬 `*.synctx.local`

### 서버 환경
- **OS**: Oracle Linux 9 (x86_64/amd64)
- **K8s**: Kubespray 배포, CRI-O 런타임, kubeVersion >= 1.22.0
- **클러스터 구성**:
  - 운영: 여러 노드
  - 개발: 여러 노드
  - 추가 클러스터

### 이미지 / 레지스트리
- **내부 레지스트리**: `registry.local:5000` (폐쇄망 전용)
- **이미지 전송**: `pull-and-save-images.sh`로 외부에서 pull → tar 저장 → 폐쇄망 반입 → registry push
- **베이스 이미지**: `python:3.11-slim` (Debian 기반, Alpine 사용 금지)
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

### 참조 프로젝트
- `example-infra`: 동일 환경의 인프라 설정 참조 프로젝트 (로컬 시뮬레이션 패턴의 원본)

## 로컬 개발 (minikube)

- **스크립트**: `scripts/local/setup.sh` (전체 설정), `scripts/local/steps/build.sh` (빌드), `scripts/local/steps/deploy-synctx.sh` (배포)
- **빌드/배포**: 직접 `docker build`/`kubectl` 명령 사용 금지 — 반드시 `scripts/local/` 스크립트 사용
- **스크립트 오류 시**: 스크립트를 우회하지 말고, 원인을 파악하여 스크립트 자체를 수정
- **네임스페이스**: `synctx` (scripts에 정의됨, `default` 네임스페이스 사용 금지)

## 구현 주의사항

- **FastAPI에서 subprocess 호출 시** `asyncio.run_in_executor`로 비동기 실행 — 동기 subprocess는 이벤트 루프를 블로킹하여 K8s liveness probe 실패 유발

## Git 참고

- `GITHUB_TOKEN` 환경변수에 잘못된 토큰이 있을 수 있음. push 시 `GITHUB_TOKEN= git push -u origin main`으로 keyring fallback 사용
- 원격 저장소: `https://github.com/sch0718/synctx.git` (private)

## Serena MCP

이 프로젝트는 Serena MCP 서버가 활성화되어 있음.
코드 구현 단계 진입 시 `initial_instructions` → 심볼 기반 탐색/편집 도구 활용.
