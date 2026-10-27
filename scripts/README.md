# scripts/

synctx 운영 및 로컬 개발 환경을 위한 스크립트 모음.

## 디렉토리 구조

```
scripts/
├── generate-manifest.sh      # 오프라인 반입 무결성 manifest 생성
├── backup-restore.sh          # PVC 데이터 백업/복구
├── download-cdn-vendor.sh     # CDN vendor 파일 다운로드
├── verify-authz.sh            # 권한 매핑 검증
├── smoke-test.sh              # 배포 후 smoke test
├── external_importer.py       # 외부 피드백 메일 수신 → PR 생성
├── test-all.sh                # 전체 테스트(pytest + node --test), make test가 호출
├── build_motion.py            # README 소개 그림·메뉴별 도움말(그림·글) 생성
├── motion/                    # build_motion.py의 그리기 도구와 메뉴별 단계 정의
├── tests/                     # 스크립트 테스트
│   ├── test_external_importer.py
│   ├── test_helm_options.py   # Helm 서비스 옵션 렌더
│   ├── test_docker_deploy.py  # Docker 배포 검사·게이트웨이 설정
│   ├── test_run_viewer_docker.py # 단일 Viewer: 포트·볼륨·Secret·빌드 위임
│   ├── test_run_lite.py        # run.sh·소스 실행 인자 전달
│   ├── test_local_scripts.py  # scripts/local·values-local·test-all.sh 정합성
│   ├── test_seed_cdn.py       # CDN 채우기
│   ├── test_run_viewer.py     # run-viewer.sh의 로컬 설정 생성·묶음 앱
│   ├── test_motion.py         # 생성한 그림·도움말이 최신인지, SVG·문서 링크 검사
│   ├── test_prometheus_rules.py  # 렌더한 PrometheusRule을 promtool로 평가
│   └── prometheus/            # Sync·Feedback Alert 평가 케이스 (*.test.yaml)
├── docker/                    # 단일 Viewer·Docker Compose 배포 (K8s 없는 서버)
│   ├── run-viewer.sh          # run.sh --docker: 단일 Viewer·기존 스크립트로 빌드 위임
│   ├── deploy.sh              # 검사 → 게이트웨이 설정 → compose up → CDN 채우기
│   ├── down.sh                # 중지 (--volumes일 때만 볼륨 삭제)
│   ├── compose.sh             # 같은 compose 인자로 docker compose 명령 실행
│   ├── seed_cdn.py            # cdn 컨테이너 안에서 vendor 파일 업로드
│   └── _common.sh             # 공통 함수
└── local/                     # 로컬 minikube 개발 환경
    ├── run-lite.sh            # run.sh: uv workspace로 Markdown·회의 Viewer만 실행
    ├── run-viewer.sh          # Docker·minikube 없이 Viewer+Marp+CDN 파일을 한 프로세스로 실행
    ├── run_viewer.py          # run-viewer.sh가 실행하는 묶음 앱(/view, /marp, /cdn)
    ├── setup.sh               # 전체 설치 (one-shot)
    ├── verify.sh              # 환경 검증
    ├── cleanup.sh             # 환경 정리
    ├── _common.sh             # 공통 라이브러리
    └── steps/                 # setup.sh의 개별 단계
        ├── infra.sh           # minikube + ingress + cert-manager + CA
        ├── build.sh           # Docker 이미지 빌드 + minikube 로드
        ├── deploy-deps.sh     # XWiki, OpenProject, Mailpit 배포
        └── deploy-synctx.sh   # synctx Helm 배포 + CDN vendor 파일 시딩
```

## 운영 스크립트

### generate-manifest.sh

폐쇄망 반입 시 artifact의 SHA-256 무결성 검증을 위한 manifest 파일을 생성한다.
Docker 이미지 digest, CDN vendor 파일, Helm chart, Dockerfile의 해시를 수집한다.

```bash
bash scripts/generate-manifest.sh                    # manifest.sha256 생성 (기본)
bash scripts/generate-manifest.sh release.sha256      # 파일명 지정

# 검증 (폐쇄망에서)
sha256sum -c manifest.sha256
```

### backup-restore.sh

K8s PVC 데이터(CDN 파일, Sync 상태, Slides 출판물)를 백업하고 복구한다.
K8s 환경에서는 임시 Pod를 통해, 로컬에서는 직접 tar로 처리한다.

```bash
bash scripts/backup-restore.sh backup                 # 기본 경로에 백업 (/data/backups/YYYYMMDD-HHMMSS)
bash scripts/backup-restore.sh backup /mnt/backup     # 경로 지정
bash scripts/backup-restore.sh restore /mnt/backup/20260620-120000
bash scripts/backup-restore.sh list                   # 백업 목록 조회
```

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `SYNCTX_NAMESPACE` | `synctx` | K8s 네임스페이스 |
| `SYNCTX_BACKUP_ROOT` | `/data/backups` | 백업 저장 루트 경로 |

### download-cdn-vendor.sh

CDN 서비스에서 제공할 vendor 라이브러리(Mermaid, Chart.js, highlight.js)를 다운로드한다.
인터넷 접속 가능한 환경에서 1회 실행 후 결과물을 git commit한다.

```bash
./scripts/download-cdn-vendor.sh                                        # jsdelivr에서 다운로드
JSDELIVR_BASE=http://내부미러/npm ./scripts/download-cdn-vendor.sh      # 내부 미러 사용
```

현재 포함 버전:
- Mermaid 10.9.1
- Chart.js 4.4.4
- highlight.js 11.9.0

### verify-authz.sh

Ingress 인증 헤더(`X-Forwarded-Groups`)와 synctx role 매핑이 올바른지 검증한다.
각 서비스별 RBAC 정책(viewer, publisher, cdn-admin 등)의 허용/거부를 테스트한다.

```bash
bash scripts/verify-authz.sh                          # 기본 URL (http://synctx.internal)
bash scripts/verify-authz.sh https://synctx.synctx.local  # URL 지정
```

### smoke-test.sh

K8s 클러스터에 배포된 synctx의 전체 상태를 검증한다.
Health check, Prometheus 메트릭, 버전 엔드포인트, API 기능, Pod 상태, ServiceMonitor를 확인한다.

```bash
bash scripts/smoke-test.sh                            # 기본 URL (http://synctx.internal)
bash scripts/smoke-test.sh https://synctx.synctx.local    # URL 지정
```

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `SYNCTX_NAMESPACE` | `synctx` | K8s 네임스페이스 |

### external_importer.py

Outbound Feedback(Phase 7)의 외부망 수신 측 구현체.
HMAC 서명된 메일 이벤트를 수신하여 검증 후 Git PR을 생성한다.

```bash
cat email_body.json | python scripts/external_importer.py
```

**보안 기능:**
- HMAC-SHA256 서명 검증
- Replay attack 방지 (이벤트 ID 중복 체크 + TTL)
- 허용 필드 화이트리스트 (`status`, `priority`, `version`, `due_date`)
- 경로 traversal 방지

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `OUTBOUND_FEEDBACK_HMAC_KEY` | (필수) | HMAC 서명 키 |
| `OUTBOUND_FEEDBACK_REPLAY_WINDOW_MINUTES` | `1440` | Replay 방지 윈도우 (분) |
| `OUTBOUND_FEEDBACK_PROCESSED_EVENTS_FILE` | `.synctx-feedback/processed_events.json` | 처리된 이벤트 저장 파일 |

### build_motion.py

README 맨 위 소개 그림(`docs/readme/hero-{light,dark}.svg`)과 Viewer 메뉴별 도움말(`docs/help/*.md`, `docs/help/img/*-{light,dark}.svg`)을 만든다. 같은 사용자의 landmark·gazua 저장소와 같은 방식으로, 단계 정의 하나(`motion/help_pages.py`)에서 움직이는 그림과 글을 함께 만든다. 표준 라이브러리만 쓰고 색은 Viewer 테마 토큰(`services/viewer/src/renderer.py`)에서 읽는다.

```bash
python3 scripts/build_motion.py
```

- 화면 문구는 Viewer의 실제 문구를 쓴다. Viewer 화면이 바뀌면 `motion/help_pages.py`를 고치고 다시 만든다. 생성된 파일은 직접 고치지 않는다.
- 그림은 GitHub가 `<img>`로 보여 주므로 스크립트·외부 글꼴 없이 CSS 애니메이션만 쓰고, 동작 줄이기 설정이면 첫 장면에 멈춘다.
- `tests/test_motion.py`가 커밋된 파일이 생성 결과와 같은지, SVG가 외부 자원 없이 40KB 이하인지, README·도움말·개발 문서의 상대 링크가 살아 있는지 확인한다.

## 경량 Markdown·회의 Viewer (루트 run.sh)

```bash
./run.sh --repo https://git.example/team/docs.git
./run.sh --docker --build --repo https://git.example/team/docs.git
./run.sh --docker --image synctx/viewer:0.7.0-abcdef0 --config "./my configs/viewer.yaml"
./run.sh --docker --repo https://git.example/team/docs.git --port 18100 --volume docs-cache
```

루트 `run.sh`는 첫 인자가 `--docker`면 `docker/run-viewer.sh`, 그 밖에는 `local/run-lite.sh`를 호출한다. 두 방식 모두 기존 `services/viewer/src/standalone.py`의 Markdown·회의 Viewer를 실행한다. 소스 실행은 체크아웃·uv·Git·Python 3.13 이상, Docker 실행은 Bash·Docker Engine과 standalone 코드가 들어 있는 Viewer 이미지가 필요하다. 준비된 `--image` 실행은 호스트 uv·Python·Git 없이 가능하다.

- 기본 접속은 http://127.0.0.1:18000/view/ 이다. Docker는 `--bind`·`--port`로 호스트 공개 주소, `--name`으로 컨테이너 이름, `--volume`으로 이름 있는 캐시 볼륨을 바꾼다. 기본값은 `127.0.0.1`, `18000`, `synctx-viewer`, `synctx-viewer-cache`다. 컨테이너 내부는 `0.0.0.0:8000`, 캐시는 `/data/viewer` 고정이다. Ctrl-C로 종료하면 컨테이너만 지워지고 캐시는 남는다.
- 기본 이미지는 `${DOCKER_REGISTRY:-synctx}/viewer:0.7.0-<git-sha>`. `--build`는 `local/steps/build.sh --only viewer --no-load --tag <태그>`만 호출한다. `--image`와 함께 쓸 수 없고 SemVer-SHA 태그만 허용한다(`latest` 불가). 빌드·실행은 같은 레지스트리 접두사를 쓴다. `DOCKER_REGISTRY` 없이 기본 이미지를 쓰면 `--pull never`로 로컬 이미지만 쓰고, 없으면 실행 전에 `--build`·`--image`를 안내한다.
- `--repo` 반복 또는 `--config` 중 하나를 지정한다. 상대 경로는 호출 디렉터리 기준이다. 명시적 로컬 저장소는 실제 경로로 정규화해 읽기 전용 마운트하며 상위 디렉터리는 노출하지 않는다. `file:///path/to/repo`·`file://localhost/path/to/repo`도 URL 이스케이프를 해석해 실제 디렉터리로 바꾼다. 다른 file URL 호스트와 `.git`이 파일인 linked worktree·submodule은 실행 전에 거부하므로 원격 Git URL을 대신 사용한다. Git 관리 디렉터리를 추가로 노출하지 않는다. 설정 파일도 읽기 전용이며, Docker 설정 파일의 저장소는 컨테이너에서 접근 가능한 URL을 쓴다. `--ref`·`--public-base-url`·`--refresh-interval`은 앱으로 전달한다. `--ref`는 `--repo` 전용이라 `--config`와 함께 주면 실행 전에 거부한다.
- Docker는 `GIT_READ_TOKEN`·`SYNCTX_SERVICE_TOKEN`·`SYNCTX_RENDER_SECRET`(·`_PREVIOUS`)을 변수 이름으로 전달하고 `*_FILE`은 파일을 읽기 전용 마운트하며, 추가 credential은 `--env MY_GIT_TOKEN`으로 전달한다. 값을 인자·YAML에 넣지 않는다. `--repo`는 로컬 열람용 인증 해제·회의 활성화 프로필을 만들고, `--config`의 명시적 authz·collab은 보존한다. 외부 공개는 인증 프록시·접근 제한과 실제 공개 URL을 먼저 구성한다.
- 경량 모드는 기존 vendor 자산을 `bundled`로 직접 제공하고 Marp·외부 CDN·Compose·게이트웨이를 띄우지 않는다. 회의 입장은 일반 HTTPS Markdown 링크, 메모는 브라우저 localStorage 그대로다. 일반 HTML 다운로드는 기본 렌더 HTML이며 회의 내보내기가 vendor·로컬 메모를 포함한 자체 완결형 사본이다. 기존 `meeting.channel` 필수 조건도 유지한다.

전체 인자·공개 URL 우선순위·캐시·빌드와 실제 환경 검증 범위는 `docs/local-development.md` 0.1과 `docs/production-service-design.md` 8.7을 따른다. fake Docker 테스트는 실제 daemon·이미지 빌드·네트워크 인증을 대신하지 않는다.

## Docker Compose 배포 (docker/)

K8s 클러스터가 없는 Docker 서버에 synctx를 배포한다. 옵션 파일 `deploy/docker/synctx.env`(`synctx.env.example`을 복사)의 `SYNCTX_SERVICES`로 켤 서비스(`viewer, cdn, marp, slides, drawio`)를 고른다. Sync·Feedback은 아직 지원하지 않는다. 옵션과 절차는 `docs/production-service-design.md` 8.6, Runbook은 12.7을 따른다.

### deploy.sh — 배포

옵션을 검사하고 켜진 서비스 경로로 게이트웨이 설정을 만든 뒤, 같은 nginx 이미지로 `nginx -t`를 통과할 때만 운영 설정에 반영하고 `docker compose up --wait`, 꺼진 서비스 정리, 게이트웨이 reload, CDN 채우기 순으로 진행한다.

```bash
./scripts/docker/deploy.sh                       # deploy/docker/synctx.env, secrets.env 사용
./scripts/docker/deploy.sh --check               # 검사와 .generated/gateway.check.conf 생성만 (docker 불필요)
./scripts/docker/deploy.sh --env-file ./prod.env --secrets-file ./prod-secrets.env
```

`--env-file`·`--secrets-file`의 상대 경로는 현재 디렉터리 기준이다. 옵션 파일 안의 상대 경로는 `deploy/docker` 기준이다.

### down.sh — 중지

```bash
./scripts/docker/down.sh                         # 컨테이너·네트워크 제거 (볼륨 유지)
./scripts/docker/down.sh --volumes               # 볼륨도 삭제 (CDN 파일, 저장소 캐시, Marp 작업 디렉터리, Slides worktree)
```

### compose.sh — compose 명령 실행

`deploy.sh`와 같은 compose 파일·옵션 파일·profile로 `docker compose` 명령을 실행한다.

```bash
./scripts/docker/compose.sh -- ps
./scripts/docker/compose.sh -- logs -f gateway
./scripts/docker/compose.sh --env-file ./prod.env -- restart viewer
```

### seed_cdn.py — CDN 채우기

`deploy.sh`가 cdn 컨테이너 안에서 `python -I -`로 실행한다. `/seed`(저장소의 `cdn-data`) 아래 파일 중 sha256이 다른 것만 CDN에 올린다. 직접 실행할 일은 없다.

## 로컬 개발 환경 (local/)

minikube 기반 로컬 개발 환경을 구성한다. 도메인은 `*.synctx.local`을 사용한다.

### run-viewer.sh — Marp를 포함한 로컬 실행

```bash
./scripts/local/run-viewer.sh                    # http://127.0.0.1:18000/view/
./scripts/local/run-viewer.sh --port 18100       # 포트 변경
./scripts/local/run-viewer.sh --config my.yaml   # 다른 .synctx.yaml 사용
./scripts/local/run-viewer.sh --values my-values.yaml --cache-dir /tmp/synctx-cache   # values 파일·캐시 위치 변경
```

`uv`(Python 3.13 이상)로 실행하며, Docker·minikube 없이 uvicorn 하나로 `/view`(Viewer), `/marp`(Marp), `/cdn`(`cdn-data/`)을 띄운다. 설정은 `helm/synctx/values-local.yaml`의 `synctxConfig.data`에서 `~/.cache/synctx/synctx.local.yaml`을 만들어 쓰고, 토큰은 `GIT_READ_TOKEN` 또는 `values-local.secret.yaml`에서 실행 시점에만 읽는다. Marp에는 `marp`·`mmdc`(npm)와 Chrome이 필요하며, 없으면 Marp를 끈 채 실행한다. 자세한 내용은 [docs/local-development.md](../docs/local-development.md) 0.2절.

### 사전 요구사항

- [minikube](https://minikube.sigs.k8s.io/)
- [Docker](https://www.docker.com/)
- [Helm](https://helm.sh/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [mkcert](https://github.com/FiloSottile/mkcert) (로컬 TLS 인증서)

### setup.sh — 전체 설치

4단계(인프라 → 의존성 → 빌드 → 배포)를 순서대로 실행한다.

```bash
./scripts/local/setup.sh                # 전체 설치
./scripts/local/setup.sh --skip-infra   # 인프라 건너뜀 (이미 설치된 경우)
./scripts/local/setup.sh --skip-deps    # 의존성 건너뜀
./scripts/local/setup.sh --skip-build   # 이미지 빌드 건너뜀
./scripts/local/setup.sh --skip-deploy  # 배포 건너뜀
```

환경변수 `MINIKUBE_CPUS`(기본 4), `MINIKUBE_MEMORY`(MB, 기본 8192)로 minikube 크기를 바꾼다. `MINIKUBE_PROFILE`(기본 `minikube`)로 전용 프로필을 쓸 수 있고, `scripts/local/` 스크립트는 minikube·kubectl·helm을 그 프로필의 클러스터로 실행한다.

설치 완료 후 별도 터미널에서 `minikube tunnel -p <프로필>`을 실행해야 접속 가능하다.

### 개별 단계 실행

전체 설치 대신 필요한 단계만 실행할 수 있다.

```bash
# 인프라만 (minikube, ingress, cert-manager, CA, /etc/hosts)
./scripts/local/steps/infra.sh

# 이미지 빌드만
./scripts/local/steps/build.sh                        # 전체 빌드
./scripts/local/steps/build.sh --only viewer,cdn,marp # 특정 서비스만
./scripts/local/steps/build.sh --skip feedback        # 특정 서비스 제외
./scripts/local/steps/build.sh --tag 0.7.0-$(git rev-parse --short HEAD)  # 태그 지정 (기본 latest)
./scripts/local/steps/build.sh --no-load --tag 0.7.0-$(git rev-parse --short HEAD)  # minikube에 올리지 않고 호스트 Docker에만 (Docker 배포 확인용)

# 의존성 배포만 (XWiki, OpenProject, Mailpit)
./scripts/local/steps/deploy-deps.sh
./scripts/local/steps/deploy-deps.sh --skip-xwiki     # XWiki 제외
./scripts/local/steps/deploy-deps.sh --skip-op        # OpenProject 제외
./scripts/local/steps/deploy-deps.sh --skip-mailpit   # Mailpit 제외 (예전 이름 --skip-mailhog도 동작)
./scripts/local/steps/deploy-deps.sh --skip-images    # 이미지 pull 건너뜀

# synctx 배포만 (Helm)
./scripts/local/steps/deploy-synctx.sh
./scripts/local/steps/deploy-synctx.sh --set drawio.enabled=false   # 추가 인자는 helm upgrade에 그대로 전달
```

`deploy-synctx.sh`는 CDN Service가 없으면(`cdn.enabled=false`) CDN vendor 파일 시딩을 건너뛴다.

### verify.sh — 환경 검증

로컬 환경의 전체 상태를 11개 항목으로 검증한다.

```bash
./scripts/local/verify.sh
```

검증 항목: Minikube, Ingress Controller, Cert-Manager, ClusterIssuer,
synctx 서비스(Pod/CronJob), Certificate, 의존성 서비스, Monitoring, Ingress 리소스,
`/etc/hosts`, HTTPS 엔드포인트 접근

### cleanup.sh — 환경 정리

```bash
./scripts/local/cleanup.sh                  # Helm release + 네임스페이스만 제거 (minikube 유지)
./scripts/local/cleanup.sh --delete-cluster # minikube 클러스터도 삭제
./scripts/local/cleanup.sh --all            # --delete-cluster와 같음
```

### 로컬 환경 접속 정보

| 서비스 | URL | 비고 |
|--------|-----|------|
| synctx | `https://synctx.synctx.local` | |
| XWiki | `https://xwiki.synctx.local` | 초기 설정 필요 |
| OpenProject | `https://openproject.synctx.local` | admin/admin |
| Mailpit | `https://mail.synctx.local` | SMTP 테스트 UI |
| Grafana | `https://grafana.synctx.local` | 모니터링 |

## 테스트

```bash
# 전체 테스트: shared/*/tests, services/*/tests, scripts/tests의 pytest + Viewer JS(node --test)
make test                      # = ./scripts/test-all.sh, 추가 인자는 pytest로 (예: ./scripts/test-all.sh -x)

# external_importer 테스트
uv run pytest scripts/tests/test_external_importer.py -v

# 스크립트 테스트 전체 (helm, docker, promtool이 없으면 해당 테스트는 건너뜀)
uv run --frozen --all-packages pytest scripts/tests -q
```
