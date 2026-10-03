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
├── tests/                     # 스크립트 테스트
│   └── test_external_importer.py
└── local/                     # 로컬 minikube 개발 환경
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

## 로컬 개발 환경 (local/)

minikube 기반 로컬 개발 환경을 구성한다. 도메인은 `*.synctx.local`을 사용한다.

### 사전 요구사항

- [minikube](https://minikube.sigs.k8s.io/)
- [Docker](https://www.docker.com/)
- [Helm](https://helm.sh/)
- [mkcert](https://github.com/FiloSofia92/mkcert) (로컬 TLS 인증서)

### setup.sh — 전체 설치

4단계(인프라 → 의존성 → 빌드 → 배포)를 순서대로 실행한다.

```bash
./scripts/local/setup.sh                # 전체 설치
./scripts/local/setup.sh --skip-infra   # 인프라 건너뜀 (이미 설치된 경우)
./scripts/local/setup.sh --skip-deps    # 의존성 건너뜀
./scripts/local/setup.sh --skip-build   # 이미지 빌드 건너뜀
./scripts/local/setup.sh --skip-deploy  # 배포 건너뜀
```

설치 완료 후 별도 터미널에서 `minikube tunnel`을 실행해야 접속 가능하다.

### 개별 단계 실행

전체 설치 대신 필요한 단계만 실행할 수 있다.

```bash
# 인프라만 (minikube, ingress, cert-manager, CA, /etc/hosts)
./scripts/local/steps/infra.sh

# 이미지 빌드만
./scripts/local/steps/build.sh                        # 전체 빌드
./scripts/local/steps/build.sh --only viewer,cdn,marp # 특정 서비스만
./scripts/local/steps/build.sh --skip feedback        # 특정 서비스 제외

# 의존성 배포만 (XWiki, OpenProject, Mailpit)
./scripts/local/steps/deploy-deps.sh
./scripts/local/steps/deploy-deps.sh --skip-xwiki     # XWiki 제외
./scripts/local/steps/deploy-deps.sh --skip-op        # OpenProject 제외
./scripts/local/steps/deploy-deps.sh --skip-mailhog   # Mailpit 제외
./scripts/local/steps/deploy-deps.sh --skip-images    # 이미지 pull 건너뜀

# synctx 배포만 (Helm)
./scripts/local/steps/deploy-synctx.sh
```

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
./scripts/local/cleanup.sh --all            # 모든 것 제거
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
# external_importer 테스트
uv run pytest scripts/tests/test_external_importer.py -v
```
