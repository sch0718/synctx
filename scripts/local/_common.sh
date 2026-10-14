#!/bin/bash
# _common.sh - synctx 로컬 minikube 전용 스크립트 공통 라이브러리
#
# 제공 기능:
#   - 경로 설정 (SCRIPT_DIR, PROJECT_ROOT)
#   - 색상 정의 및 출력 함수
#   - 로컬 환경 상수
#   - 인프라 구성 함수 (minikube, helm repos, ingress, cert-manager, CA)
#   - Docker 이미지 빌드/로드 헬퍼
#   - Helm 배포 헬퍼
#
# 사용법:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "${SCRIPT_DIR}/_common.sh"

# ============================================================
# 경로 설정
# ============================================================
if [ -z "$SCRIPT_DIR" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [ "$(basename "$SCRIPT_DIR")" = "steps" ]; then
    LOCAL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
else
    LOCAL_DIR="${SCRIPT_DIR}"
fi
STEPS_DIR="${LOCAL_DIR}/steps"
PROJECT_ROOT="$(cd "${LOCAL_DIR}/../.." && pwd)"

# ============================================================
# 색상 정의 및 출력 함수
# ============================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

print_success() { echo -e "${GREEN}✓ $1${NC}"; }
print_error()   { echo -e "${RED}✗ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠ $1${NC}"; }
print_info()    { echo -e "${BLUE}ℹ $1${NC}"; }

# ============================================================
# 로컬 환경 상수
# ============================================================
DOMAIN="synctx.local"
NAMESPACE="synctx"
DEPS_NAMESPACE="synctx-deps"
MONITORING_NAMESPACE="monitoring"

DOCKER_REGISTRY=${DOCKER_REGISTRY:-"synctx"}
MINIKUBE_CPUS=${MINIKUBE_CPUS:-4}
MINIKUBE_MEMORY=${MINIKUBE_MEMORY:-8192}
SERVICES=(viewer cdn marp sync feedback)
THIRD_PARTY_IMAGES=(
    "nginx:1.25.4-alpine"
    "alpine/git:2.43.0"
    "jgraph/drawio:24.2.5"
)

VALUES_LOCAL_FILE="${PROJECT_ROOT}/helm/synctx/values-local.yaml"

# 서브도메인
SYNCTX_SUBDOMAIN="synctx.${DOMAIN}"
XWIKI_SUBDOMAIN="xwiki.${DOMAIN}"
OPENPROJECT_SUBDOMAIN="openproject.${DOMAIN}"
MAIL_SUBDOMAIN="mail.${DOMAIN}"
GRAFANA_SUBDOMAIN="grafana.${DOMAIN}"

ALL_DOMAINS=(
    "${SYNCTX_SUBDOMAIN}"
    "${XWIKI_SUBDOMAIN}"
    "${OPENPROJECT_SUBDOMAIN}"
    "${MAIL_SUBDOMAIN}"
)

# ============================================================
# 사전 요구사항 확인
# 사용: check_prerequisites [mkcert] [minikube] [helm]
# ============================================================
check_prerequisites() {
    local check_all=true
    local check_mkcert=false
    local check_minikube=false
    local check_helm=false

    if [ $# -gt 0 ]; then
        check_all=false
        for arg in "$@"; do
            case "$arg" in
                mkcert)   check_mkcert=true ;;
                minikube) check_minikube=true ;;
                helm)     check_helm=true ;;
            esac
        done
    fi

    if [ "$check_all" = true ] || [ "$check_mkcert" = true ]; then
        if command -v mkcert &>/dev/null; then
            print_success "mkcert 설치 확인"
        else
            print_error "mkcert가 설치되어 있지 않습니다"
            print_info "설치: brew install mkcert (macOS)"
            return 1
        fi
    fi

    if [ "$check_all" = true ] || [ "$check_minikube" = true ]; then
        if command -v minikube &>/dev/null; then
            print_success "minikube 설치 확인"
        else
            print_error "minikube가 설치되어 있지 않습니다"
            print_info "설치: brew install minikube (macOS)"
            return 1
        fi
    fi

    if [ "$check_all" = true ] || [ "$check_helm" = true ]; then
        if command -v helm &>/dev/null; then
            print_success "helm 설치 확인"
        else
            print_error "helm이 설치되어 있지 않습니다"
            print_info "설치: brew install helm (macOS)"
            return 1
        fi
    fi
}

# ============================================================
# /etc/hosts 설정
# 사용: setup_hosts "domain1" "domain2" ...
# ============================================================
setup_hosts() {
    local domains=("$@")
    local hosts_missing=false

    for d in "${domains[@]}"; do
        if grep -q "${d}" /etc/hosts 2>/dev/null; then
            print_success "/etc/hosts에 ${d} 설정됨"
        else
            hosts_missing=true
        fi
    done

    if [ "$hosts_missing" = true ]; then
        print_warning "/etc/hosts에 누락된 항목이 있습니다"
        read -p "자동으로 추가하시겠습니까? (y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            for d in "${domains[@]}"; do
                grep -q "${d}" /etc/hosts 2>/dev/null || \
                    echo "127.0.0.1 ${d}" | sudo tee -a /etc/hosts
            done
            print_success "/etc/hosts 설정 완료"
        else
            print_warning "수동으로 추가해주세요:"
            for d in "${domains[@]}"; do
                echo -e "   ${YELLOW}sudo sh -c 'echo \"127.0.0.1 ${d}\" >> /etc/hosts'${NC}"
            done
        fi
    fi
}

# ============================================================
# Minikube 시작
# ============================================================
start_minikube() {
    if minikube status &>/dev/null; then
        print_success "Minikube가 이미 실행 중입니다"
    else
        print_info "Minikube를 시작합니다..."
        minikube start --cpus="${MINIKUBE_CPUS}" --memory="${MINIKUBE_MEMORY}" --disk-size=30g --driver=docker
        print_success "Minikube 시작 완료"
    fi
}

# ============================================================
# Helm Repository 추가
# ============================================================
setup_helm_repos() {
    helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx 2>/dev/null || true
    helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true
    helm repo update
    print_success "Helm Repository 추가 완료"
}

# ============================================================
# Nginx Ingress Controller 설치 (Helm Chart)
# ============================================================
install_ingress() {
    kubectl create namespace ingress-nginx --dry-run=client -o yaml | kubectl apply -f -

    if helm list -n ingress-nginx 2>/dev/null | grep -q ingress-nginx; then
        print_success "Ingress Controller가 이미 설치되어 있습니다"
    else
        helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
            --namespace ingress-nginx \
            --set controller.service.type=LoadBalancer \
            --set controller.ingressClassResource.default=true \
            --set controller.config.force-ssl-redirect=true \
            --set controller.config.ssl-redirect=true \
            --set controller.metrics.enabled=true \
            --set controller.replicaCount=1 \
            --version 4.8.3 \
            --wait
        print_success "Nginx Ingress Controller 설치 완료"
    fi
}

# ============================================================
# Cert-Manager 설치
# ============================================================
install_cert_manager() {
    kubectl create namespace cert-manager --dry-run=client -o yaml | kubectl apply -f -

    if helm list -n cert-manager 2>/dev/null | grep -q cert-manager; then
        print_success "Cert-Manager가 이미 설치되어 있습니다"
    else
        kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.3/cert-manager.crds.yaml

        helm upgrade --install cert-manager jetstack/cert-manager \
            --namespace cert-manager \
            --set installCRDs=false \
            --set replicaCount=1 \
            --version v1.13.3 \
            --wait
        print_success "Cert-Manager 설치 완료"
    fi
}

# ============================================================
# CA Secret 및 ClusterIssuer 설정 (mkcert)
# ============================================================
setup_ca_and_issuer() {
    if kubectl get clusterissuer ca-issuer &>/dev/null; then
        print_success "CA Issuer가 이미 존재합니다"
        return
    fi

    mkcert -install
    local ca_root
    ca_root=$(mkcert -CAROOT)

    kubectl create secret tls ca-key-pair \
        --cert="${ca_root}/rootCA.pem" \
        --key="${ca_root}/rootCA-key.pem" \
        -n cert-manager \
        --dry-run=client -o yaml | kubectl apply -f -
    print_success "CA Secret 생성 완료 (mkcert)"

    kubectl apply -f - <<'EOF'
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: ca-issuer
spec:
  ca:
    secretName: ca-key-pair
EOF
    print_success "CA ClusterIssuer 생성 완료"
}

# ============================================================
# 네임스페이스 생성
# ============================================================
ensure_namespace() {
    local ns="$1"
    kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f -
    print_success "네임스페이스 확인: ${ns}"
}

# ============================================================
# 공통 인프라 설치 (Minikube + Helm Repos + Ingress + Cert-Manager + CA)
# 사용: setup_common_infra [도메인1 도메인2 ...]
# ============================================================
setup_common_infra() {
    local step=1
    local domains=("$@")

    if [ ${#domains[@]} -eq 0 ]; then
        domains=("${ALL_DOMAINS[@]}")
    fi

    print_header "${step}. 사전 요구사항 확인"
    check_prerequisites || exit 1
    ((step++))

    print_header "${step}. /etc/hosts 설정"
    if [ ${#domains[@]} -gt 0 ]; then
        setup_hosts "${domains[@]}"
    fi
    ((step++))

    print_header "${step}. Minikube 시작"
    start_minikube
    ((step++))

    print_header "${step}. Helm Repository 추가"
    setup_helm_repos
    ((step++))

    print_header "${step}. Nginx Ingress Controller 설치"
    install_ingress
    ((step++))

    print_header "${step}. Cert-Manager 설치"
    install_cert_manager
    ((step++))

    print_header "${step}. CA Secret 및 Issuer 생성"
    setup_ca_and_issuer
    ((step++))

    NEXT_STEP=$step
}

# ============================================================
# minikube 아키텍처 조회
# ============================================================
_MINIKUBE_ARCH=""
get_minikube_arch() {
    if [ -z "$_MINIKUBE_ARCH" ]; then
        local raw
        raw=$(minikube ssh 'uname -m' 2>/dev/null | tr -d '\r\n[:space:]')
        case "$raw" in
            x86_64)  _MINIKUBE_ARCH="amd64" ;;
            aarch64) _MINIKUBE_ARCH="arm64" ;;
            arm64)   _MINIKUBE_ARCH="arm64" ;;
            *)       _MINIKUBE_ARCH="amd64" ;;
        esac
    fi
    echo "$_MINIKUBE_ARCH"
}

# ============================================================
# 3rd-party 이미지 로드
# ============================================================
load_image() {
    local image="$1"
    local short_name
    short_name=$(echo "$image" | sed 's|.*/||')

    if minikube image ls 2>/dev/null | grep -q "${image}"; then
        print_success "${short_name} 이미지가 minikube에 있습니다"
        return
    fi

    print_info "${short_name} minikube에서 pull 시도..."
    if minikube image pull "${image}" 2>/dev/null; then
        print_success "${short_name} minikube pull 완료"
        return
    fi

    local arch
    arch=$(get_minikube_arch)
    print_info "${short_name} 호스트 Docker로 pull 시도 (platform=linux/${arch})..."
    if ! docker pull --platform "linux/${arch}" "${image}" 2>/dev/null; then
        print_warning "${short_name} pull 실패"
        return 1
    fi
    if ! minikube image load "${image}" 2>/dev/null; then
        print_warning "${short_name} minikube load 실패"
        return 1
    fi
    print_success "${short_name} 로드 완료"
}

# ============================================================
# synctx 서비스 이미지 빌드 + minikube 로드
# ============================================================
build_and_load_service() {
    local svc="$1"
    local dockerfile="services/${svc}/Dockerfile"
    local image_tag="${DOCKER_REGISTRY}/${svc}:latest"

    if [ ! -f "${PROJECT_ROOT}/${dockerfile}" ]; then
        print_warning "${svc} Dockerfile 없음 — 건너뜁니다"
        return 0
    fi

    print_info "[${svc}] 빌드 중..."
    if ! DOCKER_BUILDKIT=1 docker build "${PROJECT_ROOT}" \
        -f "${PROJECT_ROOT}/${dockerfile}" \
        -t "${image_tag}" >/dev/null 2>&1; then
        print_error "${svc} 빌드 실패"
        return 1
    fi
    print_success "${svc} 빌드 완료"

    eval $(minikube docker-env)
    docker rmi -f "$image_tag" 2>/dev/null || true
    eval $(minikube docker-env -u)

    minikube image load "$image_tag"
    print_success "${svc} minikube 로드 완료"
}

# ============================================================
# Helm release 제거 헬퍼
# ============================================================
safe_uninstall() {
    local release="$1"
    local namespace="$2"
    helm uninstall "$release" -n "$namespace" 2>/dev/null && \
        print_success "${release} 삭제 완료 (${namespace})" || true
}

# ============================================================
# kubectl 리소스 삭제 헬퍼 (not-found 무시)
# ============================================================
safe_delete() {
    kubectl delete "$@" --ignore-not-found 2>/dev/null || true
}

# ============================================================
# Pod Ready 대기
# ============================================================
wait_for_pods() {
    local ns="$1"
    local label="$2"
    local timeout="${3:-120}"

    print_info "${label} Pod 준비 대기 중... (timeout: ${timeout}s)"
    if kubectl wait --for=condition=ready pod -l "${label}" -n "${ns}" --timeout="${timeout}s" 2>/dev/null; then
        print_success "${label} Pod 준비 완료"
    else
        print_warning "${label} Pod가 ${timeout}s 내에 준비되지 않았습니다"
        kubectl get pods -l "${label}" -n "${ns}" 2>/dev/null
    fi
}
