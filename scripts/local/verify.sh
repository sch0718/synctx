#!/bin/bash
# verify.sh - synctx 로컬 환경 검증
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

ERRORS=0

check_status() {
    if [ $1 -eq 0 ]; then
        print_success "$2"
    else
        print_error "$2"
        ((ERRORS++))
    fi
}

print_header "synctx 로컬 환경 검증"

# 1. Minikube
print_header "1. Minikube 상태"
if minikube status &>/dev/null; then
    print_success "Minikube 실행 중"
else
    print_error "Minikube가 실행되지 않았습니다"
    ((ERRORS++))
fi

# 2. Ingress
print_header "2. Ingress Controller"
if kubectl get pods -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx 2>/dev/null | grep -q Running; then
    print_success "Ingress Controller 실행 중"
else
    print_warning "Ingress Controller가 실행 중이 아닙니다"
    print_info "minikube addons enable ingress 실행 필요"
    ((ERRORS++))
fi

# 3. synctx 서비스
print_header "3. synctx 서비스 (${NAMESPACE})"
if kubectl get namespace "${NAMESPACE}" &>/dev/null; then
    print_success "네임스페이스 존재: ${NAMESPACE}"
    echo ""
    kubectl get pods -n "${NAMESPACE}" 2>/dev/null || true
    echo ""

    # Deployment 기반 서비스
    for svc in viewer cdn marp slides drawio; do
        POD_STATUS=$(kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/name=${svc}" -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
        if [ "$POD_STATUS" = "Running" ]; then
            print_success "${svc} Pod Running"
        else
            print_error "${svc} Pod 상태: ${POD_STATUS:-없음}"
            ((ERRORS++))
        fi
    done

    # CronJob 기반 서비스
    for cj in sync feedback; do
        CJ_NAME=$(kubectl get cronjob -n "${NAMESPACE}" -l "app.kubernetes.io/name=${cj}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        if [ -n "$CJ_NAME" ]; then
            LAST_SCHEDULE=$(kubectl get cronjob "$CJ_NAME" -n "${NAMESPACE}" -o jsonpath='{.status.lastScheduleTime}' 2>/dev/null)
            print_success "${cj} CronJob 존재: ${CJ_NAME} (last: ${LAST_SCHEDULE:-아직 미실행})"
        else
            print_error "${cj} CronJob 없음"
            ((ERRORS++))
        fi
    done
else
    print_error "네임스페이스 ${NAMESPACE}가 없습니다"
    ((ERRORS++))
fi

# 4. 의존성 서비스
print_header "4. 의존성 서비스 (${DEPS_NAMESPACE})"
if kubectl get namespace "${DEPS_NAMESPACE}" &>/dev/null; then
    print_success "네임스페이스 존재: ${DEPS_NAMESPACE}"
    echo ""
    kubectl get pods -n "${DEPS_NAMESPACE}" 2>/dev/null || true
    echo ""

    for dep in xwiki openproject mailhog; do
        POD_STATUS=$(kubectl get pods -n "${DEPS_NAMESPACE}" -l "app=${dep}" -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
        if [ "$POD_STATUS" = "Running" ]; then
            print_success "${dep} Pod Running"
        else
            print_error "${dep} Pod 상태: ${POD_STATUS:-없음}"
            ((ERRORS++))
        fi
    done
else
    print_error "네임스페이스 ${DEPS_NAMESPACE}가 없습니다"
    ((ERRORS++))
fi

# 5. Ingress 리소스
print_header "5. Ingress 리소스"
kubectl get ingress -A 2>/dev/null | grep -E "synctx|xwiki|openproject|mailhog" || print_warning "Ingress 리소스 없음"

# 6. /etc/hosts
print_header "6. /etc/hosts 확인"
for h in "${DOMAIN}" xwiki.synctx.local openproject.synctx.local mail.synctx.local; do
    if grep -q "$h" /etc/hosts 2>/dev/null; then
        print_success "${h} 설정됨"
    else
        print_error "${h} 미설정"
        ((ERRORS++))
    fi
done

# 7. 엔드포인트 접근 테스트
print_header "7. 엔드포인트 접근 테스트"
print_info "minikube tunnel이 실행 중이어야 합니다"

ENDPOINTS=(
    "http://${DOMAIN}/view/healthz"
    "http://${DOMAIN}/cdn/healthz"
    "http://${DOMAIN}/marp/healthz"
    "http://${DOMAIN}/slides/"
    "http://${DOMAIN}/drawio/"
    "http://xwiki.synctx.local"
    "http://openproject.synctx.local"
    "http://mail.synctx.local"
)
for endpoint in "${ENDPOINTS[@]}"; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "$endpoint" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 400 ]; then
        print_success "${endpoint} → ${HTTP_CODE}"
    elif [ "$HTTP_CODE" = "000" ]; then
        print_error "${endpoint} → 연결 불가 (tunnel 확인)"
        ((ERRORS++))
    else
        print_error "${endpoint} → ${HTTP_CODE}"
        ((ERRORS++))
    fi
done

# 결과
print_header "검증 결과"
if [ $ERRORS -eq 0 ]; then
    print_success "모든 검증 항목 통과"
    exit 0
else
    print_error "${ERRORS}개 항목에서 문제 발견"
    exit 1
fi
