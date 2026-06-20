#!/bin/bash
# cleanup.sh - synctx 로컬 환경 정리
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

ALL=false
KEEP_MINIKUBE=true

for arg in "$@"; do
    case "$arg" in
        --all)            ALL=true; KEEP_MINIKUBE=false ;;
        --delete-cluster) KEEP_MINIKUBE=false ;;
        -h|--help)
            cat <<EOF
synctx 로컬 환경을 정리합니다.

사용법: $0 [옵션]

옵션:
  --all               모든 것을 제거 (minikube 클러스터 포함)
  --delete-cluster    minikube 클러스터도 삭제
  -h, --help          도움말

기본 동작: Helm release + 네임스페이스만 제거 (minikube는 유지)
EOF
            exit 0
            ;;
        *) print_error "알 수 없는 옵션: $arg"; exit 1 ;;
    esac
done

print_header "synctx 로컬 환경 정리"
print_warning "이 스크립트는 설치된 synctx 구성 요소를 제거합니다."
read -p "계속하시겠습니까? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_info "취소되었습니다."
    exit 0
fi

# 1. synctx Helm release 제거
print_header "1. synctx 서비스 제거"
safe_uninstall "synctx" "${NAMESPACE}"

# 2. 의존성 제거
print_header "2. 의존성 서비스 제거"
for dep in xwiki xwiki-postgres openproject mailpit; do
    kubectl delete deployment "${dep}" -n "${DEPS_NAMESPACE}" 2>/dev/null && \
        print_success "${dep} deployment 제거" || true
done
for svc in xwiki xwiki-postgres openproject mailpit; do
    kubectl delete service "${svc}" -n "${DEPS_NAMESPACE}" 2>/dev/null || true
done
kubectl delete ingress --all -n "${DEPS_NAMESPACE}" 2>/dev/null || true
kubectl delete pvc --all -n "${DEPS_NAMESPACE}" 2>/dev/null || true
print_success "의존성 서비스 제거 완료"

# 3. 네임스페이스 제거
print_header "3. 네임스페이스 제거"
kubectl delete namespace "${NAMESPACE}" 2>/dev/null && \
    print_success "${NAMESPACE} 제거" || print_info "${NAMESPACE} 없음"
kubectl delete namespace "${DEPS_NAMESPACE}" 2>/dev/null && \
    print_success "${DEPS_NAMESPACE} 제거" || print_info "${DEPS_NAMESPACE} 없음"

# 4. minikube 클러스터
if [ "$KEEP_MINIKUBE" = false ]; then
    print_header "4. Minikube 클러스터 삭제"
    minikube delete
    print_success "Minikube 클러스터 삭제 완료"
fi

print_header "정리 완료"
