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

## 2026-10-22: Marp 출판은 출판 전용 토큰으로 인증한다
- **결정**: 출판 저장소 push 인증은 `marp.publish_auth` → `GIT_PUBLISH_TOKEN` 순으로 정한다. 둘 다 없으면 첫 저장소의 읽기 인증으로 대신하고 `publish_auth_fallback` 경고 로그를 남긴다.
- **사유**: 읽기 토큰에 쓰기 권한을 주지 않도록 권한을 나누면서, 출판 토큰이 없는 기존 배포는 멈추지 않게 하기 위함. 근거는 `docs/production-service-design.md` 3.4.

## 2026-10-27: 문서·회의용 경량 Viewer는 같은 Viewer 앱을 번들 자산으로 단독 실행한다
- **결정**: `./run.sh`(uv 소스 실행)와 `./run.sh --docker`(Viewer 컨테이너 하나)는 기존 Viewer 앱을 `standalone.py`로 감싸 `viewer.asset_source: bundled`로 띄운다. CDN·Marp·Sync·게이트웨이는 없다. `--repo` 프로필은 인증을 끄고 회의 모드를 켜며, `--config`는 명시한 인증 정책을 유지한다. 이미지는 SemVer-SHA 태그만 쓴다. 새 서비스나 Phase가 아니다.
- **사유**: 문서·회의만 필요한 팀이 minikube·Compose 없이 쓰게 하면서 렌더러·회의 코드를 둘로 나누지 않기 위함. 근거는 `docs/superpowers/specs/2026-10-24-lightweight-viewer-design.md`.

## 2026-10-28: HTML 파일 렌더 보기는 서명 토큰 URL 문서로 열고 저장소별로 따로 허용한다
- **결정**: `html_embed.enabled`와 새 `render_files`(기본 false)가 모두 true인 저장소만 `mode=render`를 연다. iframe은 `/render/{token}/…` URL 문서이며(sandbox `allow-scripts`, 렌더 화면만 CSP `frame-src 'self'`), 토큰은 저장소·ref·커밋 SHA·만료를 HMAC으로 묶는다(`SYNCTX_RENDER_SECRET`, 기본 4시간). `render_files` 저장소가 있고 Viewer 파드가 여럿이면 공유 키(`viewer.renderTokenSecret`) 없이는 Helm 렌더를 멈춘다.
- **사유**: srcdoc 합본(2026-10-09 결정)으로는 원본 HTML의 프레임·상대 링크를 따라갈 수 없다. 같은 스냅샷을 보장하면서 실행 범위 확대는 운영자가 저장소별로 따로 허용하게 하기 위함. 운영 SSO 중첩 요청 시험(C1)은 남아 있다. 근거는 `docs/html-embed-design.md` 14장.
