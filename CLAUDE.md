# synctx Project Instructions

## 프로젝트 개요

synctx(sync + context)는 폐쇄망 금융 환경에서 AI 기반 개발 컨텍스트를 동기화하는 마이크로서비스 플랫폼.
현재 **설계 문서 단계**이며 구현 코드는 아직 없음.

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
- **Phase 변경**: 3개 문서의 Phase 번호와 순서 일치 (1-4 코어 → 5 운영 준비 → 6 하네스/스크럼 → 7 Outbound Feedback)

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
- Front Matter: `synctx:` 네임스페이스. `type`(task/doc/report 등)과 `route`(openproject/xwiki/both)로 라우팅

## Mermaid 다이어그램 규칙

- `%%{init:...}%%` 초기화 블록이나 `classDef` 사용 금지
- README는 subgraph(External/Internal) 사용, architecture.md는 flat 노드
- 시퀀스 다이어그램 액터 순서: 좌→우 = 사용자 → FE → BE → Engine → DB

## Git 참고

- `GITHUB_TOKEN` 환경변수에 잘못된 토큰이 있을 수 있음. push 시 `GITHUB_TOKEN= git push -u origin main`으로 keyring fallback 사용
- 원격 저장소: `https://github.com/sch0718/synctx.git` (private)

## Serena MCP

이 프로젝트는 Serena MCP 서버가 활성화되어 있음.
코드 구현 단계 진입 시 `initial_instructions` → 심볼 기반 탐색/편집 도구 활용.
