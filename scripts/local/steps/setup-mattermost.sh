#!/bin/bash
# setup-mattermost.sh - 로컬 Mattermost에 팀·채널·봇을 만들고 Collab Gateway용 값 파일을 쓴다
# 먼저 deploy-deps.sh --mattermost 로 Mattermost를 띄워 둔다. 여러 번 실행해도 된다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../_common.sh"

TEAM="${MATTERMOST_TEAM:-synctx}"
BOT="synctx-bot"
ADMIN_USER="admin"
ADMIN_EMAIL="admin@local.dev"
CHANNELS=("$@")
if [ ${#CHANNELS[@]} -eq 0 ]; then
    CHANNELS=("team-docs")
fi
LOCAL_PORT="${MATTERMOST_LOCAL_PORT:-18065}"
COMMAND_URL="${SYNCTX_COMMAND_URL:-http://synctx-collab-gateway/api/collab/mattermost/command}"
ADMIN_PASSWORD_FILE="${HOME}/.cache/synctx/mattermost-admin-password"
COLLAB_VALUES_FILE="${PROJECT_ROOT}/helm/synctx/values-local.collab.secret.yaml"

mmctl_local() {
    kubectl exec -n "${DEPS_NAMESPACE}" deploy/mattermost -- mmctl --local "$@"
}

print_header "Mattermost 준비 확인"
if ! kubectl get deploy/mattermost -n "${DEPS_NAMESPACE}" &>/dev/null; then
    print_error "Mattermost가 없습니다. 먼저 deploy-deps.sh --mattermost 를 실행하세요"
    exit 1
fi
if ! kubectl rollout status deploy/mattermost -n "${DEPS_NAMESPACE}" --timeout=600s; then
    print_error "Mattermost가 10분 안에 준비되지 않았습니다 (에뮬레이션이면 느릴 수 있습니다: kubectl logs -n ${DEPS_NAMESPACE} deploy/mattermost)"
    exit 1
fi
print_success "Mattermost 준비 완료"

print_header "관리자·팀·채널"
if [ ! -f "${ADMIN_PASSWORD_FILE}" ]; then
    mkdir -p "$(dirname "${ADMIN_PASSWORD_FILE}")"
    (umask 077; printf 'Aa1-%s\n' "$(openssl rand -hex 12)" > "${ADMIN_PASSWORD_FILE}")
fi
ADMIN_PASSWORD="$(cat "${ADMIN_PASSWORD_FILE}")"

if mmctl_local user create --email "${ADMIN_EMAIL}" --username "${ADMIN_USER}" --password "${ADMIN_PASSWORD}" --system-admin >/dev/null 2>&1; then
    print_success "관리자 계정 생성: ${ADMIN_USER} (비밀번호: ${ADMIN_PASSWORD_FILE})"
else
    print_info "관리자 계정이 이미 있습니다"
fi

mmctl_local team create --name "${TEAM}" --display-name "${TEAM}" >/dev/null 2>&1 && print_success "팀 생성: ${TEAM}" || print_info "팀이 이미 있습니다: ${TEAM}"
mmctl_local team users add "${TEAM}" "${ADMIN_USER}" >/dev/null 2>&1 || true

for channel in "${CHANNELS[@]}"; do
    mmctl_local channel create --team "${TEAM}" --name "${channel}" --display-name "${channel}" >/dev/null 2>&1 \
        && print_success "채널 생성: ${channel}" || print_info "채널이 이미 있습니다: ${channel}"
    mmctl_local channel users add "${TEAM}:${channel}" "${ADMIN_USER}" >/dev/null 2>&1 || true
done

print_header "봇 계정과 토큰"
kubectl port-forward -n "${DEPS_NAMESPACE}" svc/mattermost "${LOCAL_PORT}:8065" >/dev/null 2>&1 &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${LOCAL_PORT}/api/v4/system/ping" >/dev/null 2>&1; then break; fi
    sleep 1
done

# mmctl --local은 bot create·token generate를 지원하지 않아 관리자 로그인으로 REST를 쓴다
cd "${PROJECT_ROOT}"
# 슬래시 명령의 호출 주소는 클러스터 안 Gateway Service(배포 이름 synctx)다. 토큰은 Mattermost가 발급한다
TOKENS="$(uv run --frozen --all-packages python -I "${SCRIPT_DIR}/mattermost_bot.py" \
    --base "http://127.0.0.1:${LOCAL_PORT}" --admin "${ADMIN_USER}" --password-file "${ADMIN_PASSWORD_FILE}" \
    --team "${TEAM}" --bot "${BOT}" --command-url "${COMMAND_URL}" "${CHANNELS[@]}")"
BOT_TOKEN="$(printf '%s\n' "${TOKENS}" | sed -n 1p | tr -d '[:space:]')"
COMMAND_TOKEN="$(printf '%s\n' "${TOKENS}" | sed -n 2p | tr -d '[:space:]')"
if [ -z "${BOT_TOKEN}" ] || [ -z "${COMMAND_TOKEN}" ]; then
    print_error "봇 토큰 또는 명령 토큰을 받지 못했습니다 (위 오류를 확인하세요)"
    exit 1
fi
print_success "봇 토큰과 /synctx 명령 토큰 준비"

(umask 077; cat > "${COLLAB_VALUES_FILE}" <<VALUES_EOF
# setup-mattermost.sh가 만든 로컬 전용 파일(.gitignore). 커밋하지 않는다.
collabGateway:
  enabled: true
  secrets:
    create: true
    botToken: "${BOT_TOKEN}"
    viewerServiceToken: "$(openssl rand -hex 24)"
    commandToken: "${COMMAND_TOKEN}"
VALUES_EOF
)
print_success "값 파일 작성: ${COLLAB_VALUES_FILE}"
print_info "다음: deploy-synctx.sh 로 Gateway를 배포하고, verify-collab.sh 로 Mattermost 도달을 확인합니다"
