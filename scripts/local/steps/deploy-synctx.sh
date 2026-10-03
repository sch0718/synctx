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

# CDN PVC 시딩: cdn-data/의 vendor 파일을 업로드 API로 적재 (manifest/audit 기록 유지)
# 내용이 같으면 건너뛰고, 다르면 force=true로 덮어쓴다. latest/ 심링크는 운영 금지 alias라 제외된다.
seed_cdn_data() {
    local port="${CDN_SEED_PORT:-18081}"
    local base="http://127.0.0.1:${port}"
    local tmp_file pf_pid

    kubectl port-forward -n "${NAMESPACE}" svc/synctx-cdn "${port}:80" >/dev/null 2>&1 &
    pf_pid=$!
    tmp_file=$(mktemp)
    trap 'kill ${pf_pid} 2>/dev/null || true; wait ${pf_pid} 2>/dev/null || true; rm -f "${tmp_file}"; trap - RETURN' RETURN

    local i
    for i in $(seq 1 20); do
        curl -sf "${base}/healthz" >/dev/null 2>&1 && break
        sleep 1
    done
    if ! curl -sf "${base}/healthz" >/dev/null 2>&1; then
        print_error "CDN port-forward 연결 실패 (port ${port})"
        return 1
    fi

    local uploaded=0 skipped=0 file rel local_sha remote_status force
    while IFS= read -r file; do
        rel="${file#${PROJECT_ROOT}/cdn-data/}"
        local_sha=$(shasum -a 256 "${file}" | cut -d' ' -f1)
        remote_status=$(curl -s -o "${tmp_file}" -w '%{http_code}' "${base}/${rel}")

        if [ "${remote_status}" = "200" ]; then
            if [ "$(shasum -a 256 "${tmp_file}" | cut -d' ' -f1)" = "${local_sha}" ]; then
                skipped=$((skipped + 1))
                continue
            fi
            force=true
        else
            force=false
        fi

        if ! curl -sf -o /dev/null -X POST "${base}/api/cdn/upload?force=${force}" \
                -F "file=@${file}" -F "path=${rel}"; then
            print_error "CDN 업로드 실패: ${rel}"
            return 1
        fi
        print_info "업로드: ${rel}$([ "${force}" = true ] && echo ' (덮어쓰기)')"
        uploaded=$((uploaded + 1))
    done < <(find "${PROJECT_ROOT}/cdn-data" -type f | sort)

    print_success "CDN 시딩 완료 (업로드 ${uploaded}, 동일 ${skipped})"
}

print_header "CDN vendor 파일 시딩"
seed_cdn_data

# 상태 확인
print_header "배포 상태"
kubectl get pods -n "${NAMESPACE}" 2>/dev/null || true
echo ""
kubectl get svc -n "${NAMESPACE}" 2>/dev/null || true
echo ""
kubectl get ingress -n "${NAMESPACE}" 2>/dev/null || true
