#!/bin/bash
# setup.sh - synctx 로컬 개발 환경 전체 설치
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

SKIP_INFRA=false
SKIP_DEPS=false
SKIP_BUILD=false
SKIP_DEPLOY=false

for arg in "$@"; do
    case "$arg" in
        --skip-infra)  SKIP_INFRA=true ;;
        --skip-deps)   SKIP_DEPS=true ;;
        --skip-build)  SKIP_BUILD=true ;;
        --skip-deploy) SKIP_DEPLOY=true ;;
        -h|--help)
            cat <<EOF
synctx 로컬 개발 환경을 설치합니다.

사용법: $0 [옵션]

옵션:
  --skip-infra    인프라(minikube, ingress) 설치 건너뜀
  --skip-deps     의존성(XWiki, OpenProject, Mailpit) 배포 건너뜀
  --skip-build    이미지 빌드 건너뜀
  --skip-deploy   synctx 서비스 배포 건너뜀
  -h, --help      도움말

환경변수:
  MINIKUBE_CPUS     minikube CPU 수 (기본 4)
  MINIKUBE_MEMORY   minikube 메모리 MB (기본 8192, Docker Desktop 할당량보다 작아야 함)
  MINIKUBE_PROFILE  minikube 프로필 (기본 minikube). 다른 프로젝트와 클러스터를 나누려면 synctx처럼 지정

단계:
  1. 인프라: minikube 시작, Ingress 활성화, /etc/hosts 설정
  2. 의존성: XWiki, OpenProject, Mailpit 배포
  3. 빌드: synctx 서비스 이미지 빌드 + minikube 로드
  4. 배포: synctx Helm 설치
EOF
            exit 0
            ;;
        *) print_error "알 수 없는 옵션: $arg"; exit 1 ;;
    esac
done

print_header "synctx 로컬 개발 환경 설치"

# 1. 인프라
if [ "$SKIP_INFRA" = false ]; then
    "${STEPS_DIR}/infra.sh"
fi

# 2. 의존성
if [ "$SKIP_DEPS" = false ]; then
    "${STEPS_DIR}/deploy-deps.sh"
fi

# 3. 빌드
if [ "$SKIP_BUILD" = false ]; then
    "${STEPS_DIR}/build.sh"
fi

# 4. 배포
if [ "$SKIP_DEPLOY" = false ]; then
    "${STEPS_DIR}/deploy-synctx.sh"
fi

# 5. 완료 가이드
print_header "설치 완료"
print_info "다음 단계:"
print_info "1. Minikube tunnel 실행 (별도 터미널):"
echo -e "   ${YELLOW}minikube tunnel -p ${MINIKUBE_PROFILE}${NC}"
print_info "2. synctx 접속:"
echo -e "   ${YELLOW}https://${SYNCTX_SUBDOMAIN}${NC}"
print_info "3. 의존성 서비스:"
echo -e "   ${YELLOW}https://${XWIKI_SUBDOMAIN}${NC}         — XWiki"
echo -e "   ${YELLOW}https://${OPENPROJECT_SUBDOMAIN}${NC}   — OpenProject (admin/admin)"
echo -e "   ${YELLOW}https://${MAIL_SUBDOMAIN}${NC}          — Mailpit"
print_info "4. 모니터링:"
echo -e "   ${YELLOW}https://${GRAFANA_SUBDOMAIN}${NC}       — Grafana"
print_info "5. 상태 확인:"
echo -e "   ${YELLOW}./scripts/local/verify.sh${NC}"
