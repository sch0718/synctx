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

# 2. Ingress Controller (Helm Chart)
print_header "2. Nginx Ingress Controller"
kubectl get namespace ingress-nginx &>/dev/null
check_status $? "ingress-nginx namespace 존재"

INGRESS_PODS=$(kubectl get pods -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx -o jsonpath='{.items[*].status.phase}' 2>/dev/null)
if [[ "$INGRESS_PODS" == *"Running"* ]]; then
    print_success "Ingress Controller Pod 실행 중"
else
    print_error "Ingress Controller Pod가 실행되지 않았습니다"
    ((ERRORS++))
fi

LB_IP=$(kubectl get svc -n ingress-nginx ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
if [ -n "$LB_IP" ]; then
    print_success "LoadBalancer IP 할당됨: $LB_IP"
else
    print_warning "LoadBalancer IP 미할당 (minikube tunnel 필요)"
fi

# 3. Cert-Manager
print_header "3. Cert-Manager"
kubectl get namespace cert-manager &>/dev/null
check_status $? "cert-manager namespace 존재"

CERT_PODS=$(kubectl get pods -n cert-manager -o jsonpath='{.items[*].status.phase}' 2>/dev/null)
if [[ "$CERT_PODS" == *"Running"* ]] && [[ ! "$CERT_PODS" == *"Pending"* ]] && [[ ! "$CERT_PODS" == *"Failed"* ]]; then
    print_success "Cert-Manager Pod 실행 중"
else
    print_error "Cert-Manager Pod에 문제가 있습니다"
    ((ERRORS++))
fi

# 4. ClusterIssuer
print_header "4. ClusterIssuer"
ISSUERS=$(kubectl get clusterissuer -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)
if [ -n "$ISSUERS" ]; then
    print_success "ClusterIssuer 존재: $ISSUERS"
else
    print_error "ClusterIssuer가 존재하지 않습니다"
    ((ERRORS++))
fi

# 5. synctx 서비스
print_header "5. synctx 서비스 (${NAMESPACE})"
if kubectl get namespace "${NAMESPACE}" &>/dev/null; then
    print_success "네임스페이스 존재: ${NAMESPACE}"
    echo ""
    kubectl get pods -n "${NAMESPACE}" 2>/dev/null || true
    echo ""

    for svc in viewer cdn marp slides drawio; do
        POD_STATUS=$(kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/name=${svc}" -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
        if [ "$POD_STATUS" = "Running" ]; then
            print_success "${svc} Pod Running"
        else
            print_error "${svc} Pod 상태: ${POD_STATUS:-없음}"
            ((ERRORS++))
        fi
    done

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

# 6. Certificate 확인
print_header "6. Certificate 확인"
for ns in "${NAMESPACE}" "${DEPS_NAMESPACE}"; do
    CERTS=$(kubectl get certificate -n "$ns" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)
    if [ -n "$CERTS" ]; then
        for cert in $CERTS; do
            CERT_READY=$(kubectl get certificate "$cert" -n "$ns" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
            if [ "$CERT_READY" = "True" ]; then
                print_success "Certificate ${cert} Ready (${ns})"
            else
                print_warning "Certificate ${cert} 아직 Ready 아님 (${ns})"
            fi
        done
    fi
done

# 7. 의존성 서비스
print_header "7. 의존성 서비스 (${DEPS_NAMESPACE})"
if kubectl get namespace "${DEPS_NAMESPACE}" &>/dev/null; then
    print_success "네임스페이스 존재: ${DEPS_NAMESPACE}"
    echo ""
    kubectl get pods -n "${DEPS_NAMESPACE}" 2>/dev/null || true
    echo ""

    for dep in xwiki openproject mailpit; do
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

# 8. Monitoring
print_header "8. Monitoring Stack"
if kubectl get namespace "${MONITORING_NAMESPACE}" &>/dev/null; then
    print_success "${MONITORING_NAMESPACE} namespace 존재"

    for dep in monitoring-prometheus monitoring-grafana; do
        READY=$(kubectl get deployment "$dep" -n "${MONITORING_NAMESPACE}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)
        if [ -n "$READY" ] && [ "$READY" -gt 0 ]; then
            print_success "${dep} Running"
        else
            print_warning "${dep} 미실행"
        fi
    done
else
    print_warning "${MONITORING_NAMESPACE} namespace 없음 (모니터링 미설치)"
fi

# 9. Ingress 리소스
print_header "9. Ingress 리소스"
kubectl get ingress -A 2>/dev/null | grep -E "synctx|xwiki|openproject|mailpit|grafana" || print_warning "Ingress 리소스 없음"

# 10. /etc/hosts
print_header "10. /etc/hosts 확인"
for h in "${ALL_DOMAINS[@]}"; do
    if grep -q "$h" /etc/hosts 2>/dev/null; then
        print_success "${h} 설정됨"
    else
        print_error "${h} 미설정"
        ((ERRORS++))
    fi
done

# 11. 엔드포인트 접근 테스트 (HTTPS)
print_header "11. 엔드포인트 접근 테스트"
print_info "minikube tunnel이 실행 중이어야 합니다"

ENDPOINTS=(
    "https://${SYNCTX_SUBDOMAIN}/view/healthz"
    "https://${SYNCTX_SUBDOMAIN}/cdn/healthz"
    "https://${SYNCTX_SUBDOMAIN}/marp/healthz"
    "https://${SYNCTX_SUBDOMAIN}/slides/"
    "https://${SYNCTX_SUBDOMAIN}/drawio/"
    "https://${XWIKI_SUBDOMAIN}"
    "https://${OPENPROJECT_SUBDOMAIN}"
    "https://${MAIL_SUBDOMAIN}"
)
for endpoint in "${ENDPOINTS[@]}"; do
    HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" --connect-timeout 3 "$endpoint" 2>/dev/null || echo "000")
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
    echo ""
    print_info "브라우저에서 접속:"
    echo -e "  ${GREEN}https://${SYNCTX_SUBDOMAIN}${NC}"
    echo -e "  ${GREEN}https://${XWIKI_SUBDOMAIN}${NC}"
    echo -e "  ${GREEN}https://${OPENPROJECT_SUBDOMAIN}${NC}"
    echo -e "  ${GREEN}https://${MAIL_SUBDOMAIN}${NC}"
    echo -e "  ${GREEN}https://${GRAFANA_SUBDOMAIN}${NC}"
    exit 0
else
    print_error "${ERRORS}개 항목에서 문제 발견"
    exit 1
fi
