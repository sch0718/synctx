# 의사결정 로그 (Decision Log)

## 2026-06-19: 프론트엔드 스택 결정
- **결정**: Viewer Service의 기본 렌더링은 Vanilla JS + HTML로 유지하되, 향후 복잡한 상호작용(스크럼 보드 등)이 필요한 경우 사내 `example-fe`의 React/Vite 스택을 선택적으로 도입한다.
- **사유**: 불필요한 Vue/Alpine.js 의존성을 제거하고 사내 표준 스택과 일관성을 맞추기 위함.

## 2026-10-09: 인터랙티브 HTML 임베드는 합본 + sandbox srcdoc iframe으로 한다
- **결정**: ` ```synctx-html ` 블록의 HTML 묶음을 한 파일로 합치고 CSP meta를 함께 넣어 `sandbox="allow-scripts"` srcdoc iframe으로 렌더링한다. XWiki 동기화 계정에는 대상 공간에 한정한 스크립트 권한만 준다.
- **사유**: 첨부 방식은 프로그래밍 권한을 주거나 XWiki 전체의 다운로드 정책을 풀어야 해서 채택하지 않았다. 근거는 `docs/html-embed-design.md`.

## 2026-10-16: 서비스별 배포 옵션과 Docker Compose 배포
- **결정**: Helm은 `<서비스>.enabled`로, Docker Compose는 `deploy/docker/synctx.env`의 `SYNCTX_SERVICES`로 서비스를 켜고 끈다. 서비스 사이 의존성은 규칙 D1–D8로 검사한다. 새 Phase를 만들지 않고 Phase 번호 체계(1-9)는 유지한다.
- **사유**: 필요한 서비스만 배포하는 것을 기본 사용법으로 한다. 근거는 `docs/architecture.md`의 "서비스 옵션과 의존성".

## 2026-10-22: Sync·Feedback CronJob 메트릭은 Pushgateway로 수집한다
- **결정**: 실행마다 새로 뜨는 CronJob이라 저장소별 누적값을 sync-state PVC에 두고 실행이 끝날 때 Pushgateway로 push한다. `metrics.enabled`이고 Sync나 Feedback이 켜졌는데 `sync.persistence.enabled=false`이면 Helm 렌더를 `fail`로 멈춘다(D8).
- **사유**: scrape 대상이 없는 CronJob의 Alert를 평가 가능하게 하기 위함. 근거는 `docs/production-service-design.md` 9.2·9.3.
