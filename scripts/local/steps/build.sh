#!/bin/bash
# build.sh - synctx Docker 이미지 빌드 + minikube 로드
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../_common.sh"

ONLY_TARGETS=""
SKIP_TARGETS=""

usage() {
    cat <<EOF
synctx Docker 이미지 빌드 + minikube 로드

사용법: $(basename "$0") [옵션]

옵션:
  --only <targets>   빌드할 대상만 지정 (쉼표 구분)
  --skip <targets>   빌드에서 제외할 대상 (쉼표 구분)
  -h, --help         도움말

대상: ${SERVICES[*]}

예시:
  $(basename "$0")                        # 전체 빌드
  $(basename "$0") --only viewer,cdn,marp # 코어만 빌드
  $(basename "$0") --skip feedback        # Feedback 제외
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --only)  ONLY_TARGETS="$2"; shift 2 ;;
        --skip)  SKIP_TARGETS="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) print_error "알 수 없는 옵션: $1"; usage ;;
    esac
done

should_build() {
    local target="$1"
    if [ -n "$ONLY_TARGETS" ]; then
        echo ",$ONLY_TARGETS," | grep -q ",$target," && return 0 || return 1
    fi
    if [ -n "$SKIP_TARGETS" ]; then
        echo ",$SKIP_TARGETS," | grep -q ",$target," && return 1 || return 0
    fi
    return 0
}

# 사전 조건 확인
print_header "사전 조건 확인"

if ! minikube status &>/dev/null; then
    print_error "Minikube가 실행되지 않았습니다"
    exit 1
fi
print_success "Minikube 실행 확인"

if ! docker info &>/dev/null; then
    print_error "Docker가 실행되지 않았습니다"
    exit 1
fi
print_success "Docker 실행 확인"
print_info "Docker Registry: ${DOCKER_REGISTRY}"

# synctx 서비스 빌드
BUILD_FAILURES=0
for svc in "${SERVICES[@]}"; do
    if should_build "$svc"; then
        print_header "${svc} 이미지 빌드"
        if ! build_and_load_service "$svc"; then
            ((BUILD_FAILURES++))
        fi
    fi
done

if [ $BUILD_FAILURES -gt 0 ]; then
    print_error "${BUILD_FAILURES}개 서비스 빌드 실패"
    exit 1
fi

# 3rd-party 이미지 로드
print_header "3rd-party 이미지 로드"
for img in "${THIRD_PARTY_IMAGES[@]}"; do
    load_image "$img"
done

# 완료
print_header "빌드 완료"
print_info "Minikube 이미지 목록:"
minikube image ls 2>/dev/null | grep -E "${DOCKER_REGISTRY}/|drawio|mailpit" || true
