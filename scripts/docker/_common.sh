#!/bin/bash
# _common.sh - synctx Docker Compose 배포 스크립트 공통 함수

DOCKER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${DOCKER_SCRIPT_DIR}/../.." && pwd)"
DOCKER_DIR="${PROJECT_ROOT}/deploy/docker"
COMPOSE_FILE="${DOCKER_DIR}/compose.yaml"
TLS_COMPOSE_FILE="${DOCKER_DIR}/compose.tls.yaml"

OPTIONAL_SERVICES=(viewer cdn marp slides drawio)
PHASE2_SERVICES=(sync feedback)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

print_success() { echo -e "${GREEN}✓ $1${NC}"; }
print_error()   { echo -e "${RED}✗ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠ $1${NC}"; }
print_info()    { echo -e "${BLUE}ℹ $1${NC}"; }

fail() { print_error "$1"; exit 1; }

contains() {
    local needle="$1" item
    shift
    for item in "$@"; do
        [ "$item" = "$needle" ] && return 0
    done
    return 1
}

abs_path() {
    case "$1" in
        /*) echo "$1" ;;
        *)  echo "${DOCKER_DIR}/$1" ;;
    esac
}

# 명령행으로 받은 경로는 현재 디렉터리 기준이다. bash source는 슬래시 없는 이름을 PATH에서 먼저 찾는다.
cwd_abs_path() {
    case "$1" in
        /*) echo "$1" ;;
        *)  echo "$(pwd)/$1" ;;
    esac
}

file_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

# 옵션 파일과 시크릿 파일을 읽고 경로를 정한다
load_env_files() {
    ENV_FILE="$(cwd_abs_path "$ENV_FILE")"
    SECRETS_FILE="$(cwd_abs_path "$SECRETS_FILE")"
    [ -f "$ENV_FILE" ] || fail "옵션 파일이 없습니다: ${ENV_FILE} (예시: ${DOCKER_DIR}/synctx.env.example)"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    if [ -f "$SECRETS_FILE" ]; then
        # shellcheck disable=SC1090
        source "$SECRETS_FILE"
    fi
    set +a
    [ -f "$SECRETS_FILE" ] || print_warning "시크릿 파일이 없어 Git 토큰 없이 실행합니다: ${SECRETS_FILE}"

    GENERATED_DIR="$(abs_path "${SYNCTX_GENERATED_DIR:-./.generated}")"
    GATEWAY_CONF="${GENERATED_DIR}/gateway.conf"
    export SYNCTX_GENERATED_DIR="$GENERATED_DIR"
    prepare_render_secrets
}

prepare_render_secrets() {
    local name file_name mount_name host_file target
    for name in SYNCTX_RENDER_SECRET SYNCTX_RENDER_SECRET_PREVIOUS; do
        file_name="${name}_FILE"
        mount_name="${file_name}_MOUNT"
        printf -v "$mount_name" '%s' /dev/null
        export "$mount_name"
        if [ -n "${!file_name:-}" ]; then
            [ -z "${!name:-}" ] || fail "${name}와 ${file_name}은 함께 지정할 수 없습니다"
            host_file="$(abs_path "${!file_name}")"
            [[ "$host_file" != *:* ]] || fail "${file_name} 경로에는 콜론을 쓸 수 없습니다"
            [ -f "$host_file" ] && [ -r "$host_file" ] || fail "${file_name} 파일이 없거나 읽을 수 없습니다"
            target=/run/secrets/synctx-render-secret
            [ "$name" != SYNCTX_RENDER_SECRET_PREVIOUS ] || target=/run/secrets/synctx-render-secret-previous
            printf -v "$mount_name" '%s' "$host_file"
            printf -v "$file_name" '%s' "$target"
            export "$file_name"
        fi
    done
}

# SYNCTX_SERVICES를 정해진 순서의 ENABLED 배열로 바꾼다
parse_services() {
    local raw name requested=()
    raw="$(printf '%s' "${SYNCTX_SERVICES:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
    IFS=',' read -r -a names <<< "$raw"
    for name in ${names[@]+"${names[@]}"}; do
        [ -z "$name" ] && continue
        if contains "$name" "${PHASE2_SERVICES[@]}"; then
            fail "${name}의 Docker 배포는 2단계에서 지원합니다. 지금은 ${OPTIONAL_SERVICES[*]}만 쓸 수 있습니다"
        fi
        contains "$name" "${OPTIONAL_SERVICES[@]}" || fail "알 수 없는 서비스입니다: ${name} (사용 가능: ${OPTIONAL_SERVICES[*]})"
        requested+=("$name")
    done

    ENABLED=()
    for name in "${OPTIONAL_SERVICES[@]}"; do
        if contains "$name" ${requested[@]+"${requested[@]}"}; then
            ENABLED+=("$name")
        fi
    done
    [ ${#ENABLED[@]} -gt 0 ] || fail "켜진 서비스가 없습니다. SYNCTX_SERVICES에 ${OPTIONAL_SERVICES[*]} 중 하나 이상을 넣으세요"
    SYNCTX_SERVICES="$(IFS=,; echo "${ENABLED[*]}")"
    export SYNCTX_SERVICES
}

is_enabled() {
    contains "$1" ${ENABLED[@]+"${ENABLED[@]}"}
}

# 서비스 옵션 하나에 딸린 compose 서비스 이름
compose_services_of() {
    case "$1" in
        slides) echo "slides slides-git" ;;
        *)      echo "$1" ;;
    esac
}

build_compose_args() {
    local extra
    COMPOSE_ARGS=(-f "$COMPOSE_FILE")
    if [ "${TLS_ENABLED:-false}" = true ]; then
        COMPOSE_ARGS+=(-f "$TLS_COMPOSE_FILE")
    fi
    # 사내 사정에 맞춘 오버라이드(공백 구분 경로 목록). 예: 추가 볼륨, 프록시 환경변수
    for extra in ${SYNCTX_EXTRA_COMPOSE_FILES:-}; do
        COMPOSE_ARGS+=(-f "$(abs_path "$extra")")
    done
    COMPOSE_ARGS+=(--env-file "$ENV_FILE")
    if [ -f "$SECRETS_FILE" ]; then
        COMPOSE_ARGS+=(--env-file "$SECRETS_FILE")
    fi
}
