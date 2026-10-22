# synctx 프로젝트 컨텍스트

## 프로젝트 개요
synctx(sync + context)는 폐쇄망 금융 환경에서 AI 기반 개발 컨텍스트를 동기화하는 마이크로서비스 플랫폼이다.

## 핵심 원칙
- **단방향 동기화**: Git → XWiki/OpenProject. Git이 source of truth
- **폐쇄망 독립**: 모든 외부 의존성 vendoring, 런타임 외부 네트워크 접근 없음
- **하네스 엔지니어링**: 마크다운 문서를 LLM이 파싱 가능한 구조로 표준화

## 서비스 구성
| 서비스 | 역할 |
|--------|------|
| Viewer | 마크다운 렌더링, 파일 찾기·본문 검색, 칸반 보드, 회의 모드, 인터랙티브 HTML 임베드 |
| CDN | 정적 자산 서빙 (vendor 라이브러리) |
| Marp | 프레젠테이션 변환/출판 |
| Draw.io | 다이어그램 작성·편집 (셀프호스팅) |
| Sync Engine | Git → XWiki/OpenProject 동기화 |
| Slides Server | 출판된 슬라이드 서빙 |
| Feedback Exporter | 외부 피드백 서명 발송 (선택) |
| Pushgateway | Sync·Feedback CronJob 메트릭 수집 (메트릭 켜짐 시) |

## 문서 라우팅
Front matter의 `synctx.type`과 `synctx.route`로 동기화 대상 결정:
- `type: task` + `route: openproject` → OpenProject Work Package
- `type: doc` + `route: xwiki` → XWiki 페이지
- `route: both` → 양쪽 모두 동기화

## 에이전트 작업 지침
- 문서 작성 시 반드시 front matter에 `synctx:` 블록 포함
- TODO 항목에 `@owner` 명시 필수
- 결정사항은 `## 결정사항` 섹션에 기록
- Mermaid 다이어그램은 `%%{init:...}%%` 사용 금지
