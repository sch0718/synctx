# 로컬 개발 환경 (minikube)

이 문서는 synctx를 로컬 minikube 클러스터에서 실행하는 방법을 설명한다.

## 1. 운영 vs 로컬 환경 비교

| 구성 요소 | 운영 (폐쇄망) | 로컬 (minikube) |
|----------|--------------|-----------------|
| **K8s 클러스터** | 온프레미스 K8s (3+ 노드) | minikube (단일 노드, Docker 드라이버) |
| **Ingress** | 사내 Ingress Controller | minikube ingress addon |
| **Docker Registry** | `registry.internal/synctx/*` | 로컬 빌드 → `minikube image load` (`pullPolicy: Never`) |
| **Git 저장소** | 내부 GitLab 미러 | GitHub (`github.com`) |
| **XWiki** | 내부 XWiki 서버 | minikube 내 Docker 배포 (`xwiki:16-postgres-tomcat`) |
| **OpenProject** | 내부 OpenProject 서버 | minikube 내 Docker 배포 (`openproject/openproject:15`) |
| **SMTP** | 승인된 메일 게이트웨이 | Mailhog (클러스터 내, 실제 발송 없음) |
| **TLS** | 사내 CA 인증서 | 미사용 (HTTP) |
| **PVC** | NFS/Ceph StorageClass | minikube 기본 `standard` StorageClass |
| **Monitoring** | Prometheus + Grafana | 비활성화 (`metrics.enabled: false`) |
| **Feedback** | CronJob + 승인된 SMTP | CronJob + Mailhog |

## 2. 사전 요구사항

```bash
# macOS 기준
brew install minikube helm kubectl docker
```

| 도구 | 최소 버전 | 용도 |
|------|----------|------|
| minikube | 1.30+ | 로컬 K8s 클러스터 |
| helm | 3.12+ | 차트 배포 |
| kubectl | 1.28+ | K8s CLI |
| docker | 24+ | 이미지 빌드 |

minikube 리소스 권장: CPU 4코어, RAM 8GB, 디스크 30GB

## 3. 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│ minikube 클러스터                                                 │
│                                                                 │
│  ┌─ synctx 네임스페이스 ──────────────────────────────────────┐   │
│  │  Viewer   CDN   Marp   Draw.io   Slides   Sync   Feedback │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─ synctx-deps 네임스페이스 ─────────────────────────────────┐   │
│  │  XWiki + PostgreSQL    OpenProject    Mailhog              │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                 │
│  Ingress Controller (minikube addon)                            │
│    synctx.local          → synctx/viewer,cdn,marp,...           │
│    xwiki.synctx.local    → synctx-deps/xwiki                   │
│    openproject.synctx.local → synctx-deps/openproject           │
│    mail.synctx.local     → synctx-deps/mailhog                 │
└─────────────────────────────────────────────────────────────────┘
        │
   minikube tunnel (127.0.0.1)
        │
   /etc/hosts (각 host 개별 등록):
     127.0.0.1  synctx.local
     127.0.0.1  xwiki.synctx.local
     127.0.0.1  openproject.synctx.local
     127.0.0.1  mail.synctx.local
```

## 4. 빠른 시작

```bash
# 전체 설치 (인프라 → 의존성 → 빌드 → 배포)
./scripts/local/setup.sh

# 별도 터미널에서 tunnel 실행
minikube tunnel

# 상태 확인
./scripts/local/verify.sh
```

## 5. 단계별 설치

### 5.1 인프라 설정

```bash
./scripts/local/steps/infra.sh
```

수행 내용:
- minikube 시작 (미실행 시)
- Ingress addon 활성화
- `/etc/hosts`에 호스트 등록 (`synctx.local`, `xwiki.synctx.local`, `openproject.synctx.local`, `mail.synctx.local`)
- 네임스페이스 생성 (`synctx`, `synctx-deps`)

### 5.2 의존성 배포

```bash
# 전체 의존성
./scripts/local/steps/deploy-deps.sh

# 선택적 배포
./scripts/local/steps/deploy-deps.sh --skip-xwiki        # XWiki 제외
./scripts/local/steps/deploy-deps.sh --skip-op            # OpenProject 제외
./scripts/local/steps/deploy-deps.sh --skip-mailhog       # Mailhog 제외
./scripts/local/steps/deploy-deps.sh --skip-images        # 이미지 pull 건너뜀
```

배포되는 서비스:

| 서비스 | 이미지 | 접속 URL | 초기 계정 |
|--------|--------|----------|----------|
| XWiki | `xwiki:16-postgres-tomcat` | http://xwiki.synctx.local | 초기 설정 위저드 |
| PostgreSQL (XWiki용) | `postgres:16-alpine` | 클러스터 내부 전용 | xwiki / xwiki-local-password |
| OpenProject | `openproject/openproject:15` | http://openproject.synctx.local | admin / admin |
| Mailhog | `mailhog/mailhog:latest` | http://mail.synctx.local | 없음 (SMTP: 1025) |

### 5.3 이미지 빌드

```bash
# 전체 빌드
./scripts/local/steps/build.sh

# 선택적 빌드
./scripts/local/steps/build.sh --only viewer,cdn,marp     # 코어만
./scripts/local/steps/build.sh --skip feedback             # Feedback 제외
```

### 5.4 synctx 배포

```bash
./scripts/local/steps/deploy-synctx.sh
```

`helm/synctx/values-local.yaml`을 사용하여 배포한다.

## 6. 설정 파일

### 6.1 values-local.yaml

`helm/synctx/values-local.yaml`이 로컬 환경의 핵심 설정 파일이다.

주요 차이점 (vs 운영):
- `image.pullPolicy: Never` — minikube에 로드된 로컬 이미지 사용
- `image.tag: "latest"` — 로컬 빌드는 latest 태그
- `slides.persistence.enabled: false` — emptyDir 사용
- `feedback.secrets.create: true` — Secret 자동 생성 (운영은 existingSecret)
- `metrics.enabled: false` — Prometheus 미사용
- `synctxConfig.create: true` — ConfigMap으로 `.synctx.yaml` 주입

### 6.2 변경이 필요한 설정

| 설정 | 파일 | 설명 |
|------|------|------|
| `slides.publishRepoUrl` | values-local.yaml | Slides sidecar가 clone할 Git repo URL |
| `synctxConfig.data.repositories[].url` | values-local.yaml | Sync Engine이 동기화할 Git repo URL |
| `synctxConfig.data.targets.openproject.status_map` | values-local.yaml | OpenProject status ID 매핑 |
| `synctxConfig.data.targets.xwiki.base_url` | values-local.yaml | XWiki REST API URL (클러스터 내부) |

### 6.3 XWiki 초기 설정

1. http://xwiki.synctx.local 접속
2. 초기 설정 위저드 완료
3. **Administration → Extensions → Markdown Syntax** 확장 설치
4. REST API 활성화 확인: `GET http://xwiki.synctx.local/rest/wikis`

### 6.4 OpenProject 초기 설정

1. http://openproject.synctx.local 접속 (admin/admin)
2. 새 프로젝트 생성 (identifier: `synctx-local`)
3. **Administration → API** 에서 API token 생성
4. Status ID, Type ID, Priority ID 확인:
   ```
   GET /api/v3/statuses
   GET /api/v3/types
   GET /api/v3/priorities
   ```
5. `values-local.yaml`의 `synctxConfig.data.targets.openproject`에 `status_map`, `type_map` 업데이트
6. 생성한 API token을 `values-local.yaml`의 `feedback.secrets.opApiKey`에 설정 후 재배포:
   ```bash
   # values-local.yaml 수정 후
   ./scripts/local/steps/deploy-synctx.sh
   ```

### 6.5 Sync dry-run 모드

로컬 기본 설정은 `sync.dry_run: true`로 되어 있어, Sync Engine이 XWiki/OpenProject에 **실제 쓰기를 하지 않고** 동기화 계획만 출력한다.
실제 upsert 경로를 검증하려면 `synctxConfig.data`에서 `sync.dry_run: false`로 변경 후 재배포한다.

```yaml
# values-local.yaml → synctxConfig.data 내부
sync:
  on_delete: mark
  dry_run: false    # 실제 XWiki/OpenProject 쓰기 활성화
```

### 6.6 Slides 테스트용 Git repo

```bash
# GitHub에 빈 repo 생성 후
git init synctx-slides-test
cd synctx-slides-test
echo "# Test Slides" > README.md
git add . && git commit -m "init"
git remote add origin https://github.com/<user>/synctx-slides-test.git
git push -u origin main
```

`values-local.yaml`의 `slides.publishRepoUrl`을 이 repo URL로 설정.

## 7. 일상 작업

### 코드 수정 후 재배포

```bash
# 특정 서비스만 빌드 + 재배포
./scripts/local/steps/build.sh --only viewer
kubectl rollout restart deployment synctx-viewer -n synctx

# 전체 재배포
./scripts/local/steps/build.sh
helm upgrade synctx helm/synctx -n synctx -f helm/synctx/values-local.yaml
```

### 로그 확인

```bash
# 특정 서비스 로그
kubectl logs -f -l app.kubernetes.io/name=viewer -n synctx
kubectl logs -f -l app.kubernetes.io/name=sync -n synctx

# 의존성 로그
kubectl logs -f -l app=xwiki -n synctx-deps
kubectl logs -f -l app=openproject -n synctx-deps
```

### Sync Engine 수동 실행

```bash
# CronJob에서 수동 Job 생성
kubectl create job --from=cronjob/synctx-sync synctx-sync-manual -n synctx

# 결과 확인
kubectl logs job/synctx-sync-manual -n synctx
```

### Mailhog 메일 확인

http://mail.synctx.local 에서 Feedback Exporter가 발송한 메일을 확인할 수 있다.

## 8. 스크립트 목록

| 스크립트 | 용도 |
|---------|------|
| `scripts/local/setup.sh` | 전체 환경 설치 (원스텝) |
| `scripts/local/verify.sh` | 환경 검증 |
| `scripts/local/cleanup.sh` | 환경 정리 |
| `scripts/local/steps/infra.sh` | 인프라 설정 (minikube, ingress) |
| `scripts/local/steps/build.sh` | 이미지 빌드 + minikube 로드 |
| `scripts/local/steps/deploy-deps.sh` | 의존성 배포 (XWiki, OpenProject, Mailhog) |
| `scripts/local/steps/deploy-synctx.sh` | synctx Helm 배포 |

## 9. 문제 해결

### minikube tunnel이 동작하지 않을 때

```bash
# tunnel 프로세스 확인
ps aux | grep "minikube tunnel"

# 재시작
sudo kill $(pgrep -f "minikube tunnel")
minikube tunnel
```

### 이미지 pull 실패 (ImagePullBackOff)

로컬 빌드 이미지는 `pullPolicy: Never`여야 한다. `values-local.yaml` 확인.

```bash
# minikube 내 이미지 목록 확인
minikube image ls | grep synctx
```

### XWiki/OpenProject가 느리게 시작될 때

첫 실행 시 초기화에 2~5분 소요될 수 있다.

```bash
# 상태 확인
kubectl get pods -n synctx-deps -w
```

### PVC 데이터 초기화

```bash
kubectl delete pvc --all -n synctx-deps
kubectl delete pvc --all -n synctx
# 재배포
./scripts/local/steps/deploy-deps.sh --skip-images
./scripts/local/steps/deploy-synctx.sh
```
