#!/bin/bash
# compose.sh - deploy.sh와 같은 compose 인자로 docker compose 명령을 실행한다 (ps, logs, restart, exec 등)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

ENV_FILE="${DOCKER_DIR}/synctx.env"
SECRETS_FILE="${DOCKER_DIR}/secrets.env"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)     ENV_FILE="$2"; shift 2 ;;
        --secrets-file) SECRETS_FILE="$2"; shift 2 ;;
        --) shift; break ;;
        -h|--help)
            echo "사용법: $(basename "$0") [--env-file <경로>] [--secrets-file <경로>] -- <docker compose 인자...>"
            exit 0 ;;
        *) break ;;
    esac
done
[ $# -gt 0 ] || fail "docker compose에 넘길 명령을 지정하세요 (예: -- ps)"

load_env_files >&2
parse_services
TLS_ENABLED=false
if [ -n "${SYNCTX_TLS_CERT:-}" ] && [ -n "${SYNCTX_TLS_KEY:-}" ]; then
    TLS_ENABLED=true
    SYNCTX_TLS_DIR="$(abs_path "${SYNCTX_TLS_DIR:-./tls}")"
    export SYNCTX_TLS_DIR
fi
if [ -n "${SYNCTX_CONFIG_FILE:-}" ] && [ -f "$(abs_path "$SYNCTX_CONFIG_FILE")" ]; then
    SYNCTX_CONFIG_HASH="$(file_sha256 "$(abs_path "$SYNCTX_CONFIG_FILE")")"
    export SYNCTX_CONFIG_HASH
fi
build_compose_args
PROFILE_ARGS=()
for svc in "${ENABLED[@]}"; do
    PROFILE_ARGS+=(--profile "$svc")
done
exec docker compose "${COMPOSE_ARGS[@]}" "${PROFILE_ARGS[@]}" "$@"
