# 문서·회의용 경량 Viewer

## 승인된 범위

2026-10-24 대화에서 승인한 구성이다. 기존 저장소의 공통 코드를 사용하고 별도 개발 브랜치에서 구현한다.

- Markdown 탐색·본문 검색·발표 화면·회의 메모·근거 미리보기·HTML/Markdown 내보내기를 재사용한다.
- Zoom·Google Meet·Jitsi는 Markdown 안 일반 HTTPS 회의 링크로 연결한다. 영상 내장, API 연동, 회의 서버는 만들지 않는다.
- 렌더링 라이브러리는 Viewer의 기존 `static/vendor/` 사본을 웹 화면과 HTML 내보내기에서 함께 사용한다.
- Docker 없는 한 줄 실행과 동일 앱의 단일 컨테이너 실행을 제공한다.
- Sync·Feedback·CDN 서비스·Draw.io·Marp·Slides·메신저·공동 편집·자체 로그인은 경량 실행에 포함하지 않는다.
- 기존 Helm·Compose·로컬 Marp 실행의 기본값과 HTML 내보내기를 유지한다.

## 실행 계약

저장소를 clone한 뒤 `./run.sh --repo <Git URL>`로 시작한다. `uv`와 Git이 필요하고 Python/패키지는 uv가 준비한다. 여러 `--repo` 또는 `--config <.synctx.yaml>`를 받으며, 기본 브랜치는 main, 포트는 18000, 바인딩은 127.0.0.1이다. 첫 실행에 생성한 저장소 캐시는 사용자 캐시 디렉터리에 보존한다. Git 토큰은 `GIT_READ_TOKEN` 환경변수로 받으며 생성 설정에 토큰을 쓰지 않는다. Git URL에 비밀번호를 넣는 사용법은 제공하지 않는다.

`./run.sh --docker --repo <Git URL>`은 미리 빌드한 Viewer 이미지로 실행한다. `--build`를 추가하면 기존 `scripts/local/steps/build.sh --only viewer --no-load --tag <SemVer-sha>`를 사용해 빌드한다. `--image`로 명시적인 사전 빌드 이미지를 지정할 수 있다. 컨테이너는 포트 하나와 캐시 볼륨 하나만 사용하며 Gateway와 별도 CDN은 없다. 호스트 게시 주소 기본값은 127.0.0.1이다. Docker 실행은 `scripts/docker/run-viewer.sh`에 모은다.

설정 파일을 넘기면 저장소와 인증/권한 설정을 유지하면서 경량 실행에 필요한 번들 자산·캐시 경로를 적용한다. 명시적 설정과 `--repo`는 동시에 받지 않는다. 설정의 인증을 암묵적으로 끄지 않는다. 자동 생성하는 로컬 설정은 인증을 끄고 루프백에서 시작한다. 외부에서 접근 가능한 배포는 기존 인증 프록시와 HTTPS를 사용할 수 있다.

공개 주소는 `--public-base-url` → 명시적 설정의 `runtime.public_base_url` → Docker가 전달하는 `SYNCTX_VIEWER_PUBLIC_BASE_URL` → 바인딩 주소에서 유도한 로컬 주소 순서로 선택한다. 컨테이너 내부 주소와 사용자 접속 주소를 구분한다. 로컬 저장소는 절대 경로로 정규화한 뒤 캐시 ID를 만들어 서로 다른 작업 디렉터리의 같은 상대 경로가 충돌하지 않게 한다.

생성 설정 파일도 실제 설정 내용별로 분리하여 같은 캐시 디렉터리에서 여러 Viewer를 시작해도 서로의 저장소·주소 설정을 덮어쓰지 않는다. 같은 설정의 반복 실행은 기존 파일과 저장소 캐시를 재사용한다.

## 공통 앱

`services/viewer/src/standalone.py`가 CLI와 ASGI 조립을 담당한다. `/`는 `/view/`로 이동하고 기존 Viewer 앱을 `/view`에 mount한다. 하위 앱 lifespan을 직접 실행하여 초기 Git clone/fetch와 종료를 보장한다. Marp/CDN 앱이나 Helm values를 불러오지 않는다.

`ViewerConfig.asset_source`는 `cdn`(기존 기본값) 또는 `bundled`이다. bundled에서는 `/view/static/vendor/`의 기존 파일을 사용하고 CDN 서비스 의존성 검사를 생략한다. CDN 모드의 경로와 기본값은 그대로 유지한다. 내보내기의 라이브러리 로더는 바꾸지 않는다.

경량 실행에는 기본 60초의 Git 갱신을 둔다. 0이면 주기 갱신을 끈다. 초기화가 끝난 뒤 기존 비동기 `fetch`를 사용하고, 일시적인 실패가 다른 저장소나 서버를 중단하지 않게 한다. 종료 시 갱신 작업을 취소·회수한다. 별도 Sync Engine은 사용하지 않는다.

## 변경 경계

기존 Viewer 설정·자산 URL 선택·의존성 검사만 최소 수정한다. 렌더러, 검색, 회의 메모, front matter 스키마, 다른 서비스의 동작은 재작성하지 않는다. 회의 URL 전용 필드와 회의 대시보드는 추가하지 않는다. 기존 `meeting.channel` 필수 규칙도 이번에는 유지한다.

기존 vendored Mermaid와 CDN Mermaid는 다른 파일이다. 경량 화면에는 기존 내보내기와 같은 vendored 파일을 사용한다. 기존 CDN 사본을 교체하거나 중복 사본을 새로 추가하지 않는다.

## 검증

- 번들 모드에서 페이지가 같은 출처의 실제 JS/CSS를 사용하고 별도 CDN 없이 시작한다.
- 기존 CDN 모드와 Marp 서비스 옵션 테스트를 유지한다.
- 로컬 임시 Git 저장소로 clone, 검색, 회의 페이지, HTML 내보내기를 실제 HTTP로 확인한다.
- 초기화, 갱신 실패 격리, 종료 취소, 캐시 재사용을 확인한다.
- Docker wrapper의 인자 배열·토큰 전달·볼륨·포트·빌드 위임을 테스트한다.
- 실행 가능한 환경이면 기존 빌드 스크립트로 이미지를 빌드하고 단일 컨테이너도 확인한다.
- README·architecture·production-service-design·local-development를 함께 갱신한다.
