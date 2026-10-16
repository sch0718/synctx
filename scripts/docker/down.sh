#!/bin/bash
# down.sh - synctx Docker Compose 중지 (볼륨은 --volumes일 때만 지운다)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

ENV_FILE="${DOCKER_DIR}/synctx.env"
SECRETS_FILE="${DOCKER_DIR}/secrets.env"
DOWN_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)     ENV_FILE="$2"; shift 2 ;;
        --secrets-file) SECRETS_FILE="$2"; shift 2 ;;
        --volumes)      DOWN_ARGS+=(--volumes); shift ;;
        -h|--help)
            echo "사용법: $(basename "$0") [--env-file <경로>] [--secrets-file <경로>] [--volumes]"
            exit 0 ;;
        *) fail "알 수 없는 옵션: $1" ;;
    esac
done

load_env_files
build_compose_args
PROFILE_ARGS=()
for svc in "${OPTIONAL_SERVICES[@]}"; do
    PROFILE_ARGS+=(--profile "$svc")
done

print_header "synctx Docker Compose 중지"
docker compose "${COMPOSE_ARGS[@]}" -f "$TLS_COMPOSE_FILE" "${PROFILE_ARGS[@]}" down ${DOWN_ARGS[@]+"${DOWN_ARGS[@]}"}
print_success "중지 완료"
