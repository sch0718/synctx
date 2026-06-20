#!/bin/bash
# infra.sh - minikube + ingress + /etc/hosts 설정
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../_common.sh"

print_header "로컬 인프라 설정"

check_prerequisites
start_minikube
install_ingress
setup_hosts "${DOMAIN}"

ensure_namespace "${NAMESPACE}"
ensure_namespace "${DEPS_NAMESPACE}"

print_success "로컬 인프라 설정 완료"
