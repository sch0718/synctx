#!/bin/bash
# infra.sh - minikube + ingress + cert-manager + CA + /etc/hosts 설정
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../_common.sh"

print_header "로컬 인프라 설정"
setup_common_infra "${ALL_DOMAINS[@]}"

ensure_namespace "${NAMESPACE}"
ensure_namespace "${DEPS_NAMESPACE}"

print_success "로컬 인프라 설정 완료"
