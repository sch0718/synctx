#!/bin/bash
# deploy-synctx.sh - synctx 메인 서비스 Helm 배포
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../_common.sh"

print_header "synctx 서비스 배포"

ensure_namespace "${NAMESPACE}"

# values-local.yaml 존재 확인
if [ ! -f "${VALUES_LOCAL_FILE}" ]; then
    print_error "values-local.yaml이 없습니다"
    print_info "먼저 생성해주세요: ${VALUES_LOCAL_FILE}"
    print_info "템플릿: scripts/local/values-deps.yaml 참고"
    exit 1
fi

# Helm install/upgrade
helm upgrade --install synctx "${PROJECT_ROOT}/helm/synctx" \
    -n "${NAMESPACE}" \
    -f "${VALUES_LOCAL_FILE}" \
    --wait --timeout 180s

print_success "synctx Helm 배포 완료"

# pullPolicy: Never 이므로 Deployment를 재시작해야 새 이미지가 적용됨
print_header "Deployment 재시작 (새 이미지 적용)"
for deploy in $(kubectl get deployments -n "${NAMESPACE}" -o name 2>/dev/null); do
    kubectl rollout restart "${deploy}" -n "${NAMESPACE}" 2>/dev/null || true
done
kubectl rollout status deployment -n "${NAMESPACE}" --timeout=120s 2>/dev/null || true
print_success "Deployment 재시작 완료"

# 상태 확인
print_header "배포 상태"
kubectl get pods -n "${NAMESPACE}" 2>/dev/null || true
echo ""
kubectl get svc -n "${NAMESPACE}" 2>/dev/null || true
echo ""
kubectl get ingress -n "${NAMESPACE}" 2>/dev/null || true
