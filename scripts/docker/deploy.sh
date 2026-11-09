#!/bin/bash
# deploy.sh - synctx Docker Compose 배포
# 검사 → 게이트웨이 설정 생성·nginx 검사 → compose up → 꺼진 서비스 정리 → 게이트웨이 reload → CDN 채우기
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

ENV_FILE="${DOCKER_DIR}/synctx.env"
SECRETS_FILE="${DOCKER_DIR}/secrets.env"
CHECK_ONLY=false
TLS_ENABLED=false

usage() {
    cat <<EOF
synctx Docker Compose 배포

사용법: $(basename "$0") [옵션]

옵션:
  --env-file <경로>      옵션 파일 (기본 deploy/docker/synctx.env)
  --secrets-file <경로>  시크릿 파일 (기본 deploy/docker/secrets.env)
  --check                검사와 게이트웨이 설정 생성만 하고 배포하지 않음
  -h, --help             도움말
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)     ENV_FILE="$2"; shift 2 ;;
        --secrets-file) SECRETS_FILE="$2"; shift 2 ;;
        --check)        CHECK_ONLY=true; shift ;;
        -h|--help)      usage ;;
        *) fail "알 수 없는 옵션: $1" ;;
    esac
done

validate_options() {
    [ -n "${SYNCTX_IMAGE_TAG:-}" ] || fail "SYNCTX_IMAGE_TAG를 지정하세요 (예: 0.8.0-<git-sha>)"
    [ "$SYNCTX_IMAGE_TAG" != "latest" ] || fail "SYNCTX_IMAGE_TAG에 latest는 쓸 수 없습니다. 0.8.0-<git-sha> 형식을 쓰세요"

    parse_services
    case "${SYNCTX_JITSI:-false}" in
        true|false) ;;
        *) fail "SYNCTX_JITSI는 true 또는 false입니다" ;;
    esac
    if [ "${SYNCTX_JITSI:-false}" = true ]; then
        is_enabled viewer || fail "Jitsi 참여에는 viewer가 필요합니다"
        [[ "$SYNCTX_JITSI_IMAGE_TAG" =~ ^stable-[0-9]+(-[0-9]+)?$ ]] || fail "Jitsi 태그는 고정 stable-<번호>여야 합니다(latest 금지)"
        [[ "${SYNCTX_JITSI_HOST:-}" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || fail "SYNCTX_JITSI_HOST를 지정하세요"
        [ "${SYNCTX_JITSI_PUBLIC_URL:-}" = "https://${SYNCTX_JITSI_HOST}" ] || fail "Jitsi public URL은 https://호스트여야 합니다"
        [ -n "${SYNCTX_TLS_CERT:-}" ] && [ -n "${SYNCTX_TLS_KEY:-}" ] || fail "Jitsi에는 HTTPS TLS 설정이 필요합니다"
        [ -n "${SYNCTX_JITSI_ADVERTISE_IPS:-}" ] || fail "SYNCTX_JITSI_ADVERTISE_IPS를 지정하세요"
        [ -n "${JITSI_JICOFO_PASSWORD:-}" ] && [ -n "${JITSI_JVB_PASSWORD:-}" ] || fail "Jitsi XMPP 암호 파일(secrets.env)이 필요합니다"
        if ! uv run --project "${PROJECT_ROOT}" --offline --frozen --all-packages python "${DOCKER_SCRIPT_DIR}/validate_jitsi.py" "$(abs_path "${SYNCTX_CONFIG_FILE:-}")"; then
            fail "Jitsi 설정·키 검증 실패"
        fi
    fi

    [ -n "${SYNCTX_CONFIG_FILE:-}" ] || fail "SYNCTX_CONFIG_FILE(.synctx.yaml 경로)을 지정하세요"
    [ -f "$(abs_path "$SYNCTX_CONFIG_FILE")" ] || fail "SYNCTX_CONFIG_FILE 파일이 없습니다: $(abs_path "$SYNCTX_CONFIG_FILE")"

    if is_enabled slides; then
        [ -n "${SYNCTX_SLIDES_REPO_URL:-}" ] || fail "Slides를 켜려면 SYNCTX_SLIDES_REPO_URL(게시 저장소 주소)을 지정하세요"
        is_enabled marp || print_warning "Slides는 켜져 있지만 Marp가 꺼져 있습니다. 게시 저장소를 직접 채우지 않으면 보여 줄 슬라이드가 없습니다"
    fi
    if is_enabled viewer && ! is_enabled cdn; then
        print_info "CDN이 꺼져 있습니다. .synctx.yaml의 viewer.cdn_base_url이 내부 CDN 절대 주소여야 Viewer가 시작합니다"
    fi

    printf '%s' "${SYNCTX_MARP_PROXY_TIMEOUT:-180}" | grep -Eq '^[1-9][0-9]*$' \
        || fail "SYNCTX_MARP_PROXY_TIMEOUT는 1 이상의 정수(초)로 지정하세요 (현재 ${SYNCTX_MARP_PROXY_TIMEOUT})"

    case "${SYNCTX_TRUSTED_AUTH_PROXY:-false}" in
        true|false) ;;
        *) fail "SYNCTX_TRUSTED_AUTH_PROXY는 true 또는 false입니다 (현재 ${SYNCTX_TRUSTED_AUTH_PROXY})" ;;
    esac
    TRUSTED_PROXY_CIDRS=()
    if [ "${SYNCTX_TRUSTED_AUTH_PROXY:-false}" = true ]; then
        # 권한 헤더를 그대로 넘기면 게이트웨이에 직접 닿는 사용자가 관리자 그룹을 주장할 수 있다.
        # 그래서 신뢰 모드는 인증 프록시 출발지만 받도록 allow 목록을 강제한다.
        local cidr addr prefix octet o1 o2 o3 o4
        for cidr in $(printf '%s' "${SYNCTX_TRUSTED_PROXY_CIDRS:-}" | tr ',' ' '); do
            printf '%s' "$cidr" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$|^[0-9A-Fa-f:]+:[0-9A-Fa-f:]*(/[0-9]{1,3})?$' \
                || fail "SYNCTX_TRUSTED_PROXY_CIDRS 항목 형식이 잘못됐습니다: ${cidr}"
            addr="${cidr%%/*}"
            prefix=""
            case "$cidr" in
                */*) prefix="${cidr#*/}" ;;
            esac
            case "$addr" in
                *:*)
                    [ -z "$prefix" ] || [ $((10#$prefix)) -le 128 ] \
                        || fail "SYNCTX_TRUSTED_PROXY_CIDRS 항목 형식이 잘못됐습니다: ${cidr}"
                    ;;
                *)
                    IFS=. read -r o1 o2 o3 o4 <<< "$addr"
                    for octet in "$o1" "$o2" "$o3" "$o4"; do
                        [ $((10#$octet)) -le 255 ] || fail "SYNCTX_TRUSTED_PROXY_CIDRS 항목 형식이 잘못됐습니다: ${cidr}"
                    done
                    [ -z "$prefix" ] || [ $((10#$prefix)) -le 32 ] \
                        || fail "SYNCTX_TRUSTED_PROXY_CIDRS 항목 형식이 잘못됐습니다: ${cidr}"
                    ;;
            esac
            # 접두사 길이가 0이면 어떤 표기든 모든 주소를 뜻한다(10.0.0.0/0, 0.0.0.0/00, 0::/0 등)
            if [ -n "$prefix" ] && [ $((10#$prefix)) -eq 0 ]; then
                fail "SYNCTX_TRUSTED_PROXY_CIDRS에 모든 주소(${cidr})는 쓸 수 없습니다"
            fi
            TRUSTED_PROXY_CIDRS+=("$cidr")
        done
        [ ${#TRUSTED_PROXY_CIDRS[@]} -gt 0 ] || fail "SYNCTX_TRUSTED_AUTH_PROXY=true이면 SYNCTX_TRUSTED_PROXY_CIDRS(인증 프록시 IP·대역)를 지정해야 합니다"
        # nginx가 보는 출발지가 Docker 게이트웨이처럼 여러 클라이언트가 공유하는 주소이면 allow 목록으로
        # 프록시와 직접 접속을 구분할 수 없다. 그래서 신뢰 모드는 포트를 모든 주소에 열지 못하게 한다.
        case "${SYNCTX_BIND_ADDRESS:-0.0.0.0}" in
            0.0.0.0|::|"[::]")
                fail "신뢰 프록시 모드에서는 SYNCTX_BIND_ADDRESS를 모든 주소로 둘 수 없습니다. 인증 프록시만 닿는 인터페이스 주소로 지정하고 그 밖의 접근은 방화벽으로 막으세요 (docs/production-service-design.md 3.1.1)" ;;
        esac
    fi

    # 같은 경로의 .synctx.yaml 내용만 바뀌어도 컨테이너를 다시 만들도록 해시를 라벨로 넘긴다
    SYNCTX_CONFIG_HASH="$(file_sha256 "$(abs_path "$SYNCTX_CONFIG_FILE")")"
    export SYNCTX_CONFIG_HASH

    if [ -n "${SYNCTX_TLS_CERT:-}" ] || [ -n "${SYNCTX_TLS_KEY:-}" ]; then
        [ -n "${SYNCTX_TLS_CERT:-}" ] && [ -n "${SYNCTX_TLS_KEY:-}" ] || fail "SYNCTX_TLS_CERT와 SYNCTX_TLS_KEY는 함께 지정해야 합니다"
        local tls_dir file
        tls_dir="$(abs_path "${SYNCTX_TLS_DIR:-./tls}")"
        for file in "$SYNCTX_TLS_CERT" "$SYNCTX_TLS_KEY"; do
            [ -f "${tls_dir}/${file}" ] || fail "TLS 파일이 없습니다: ${tls_dir}/${file}"
        done
        export SYNCTX_TLS_DIR="$tls_dir"
        TLS_ENABLED=true
    fi
}

service_upstream() {
    case "$1" in
        viewer) echo "viewer:8000" ;;
        cdn)    echo "cdn:8000" ;;
        marp)   echo "marp:8000" ;;
        slides) echo "slides:80" ;;
        drawio) echo "drawio:8080" ;;
    esac
}

service_path() {
    case "$1" in
        viewer) echo "view" ;;
        *)      echo "$1" ;;
    esac
}

render_server_body() {
    cat <<'EOF'
    client_max_body_size 50m;
    # 컨테이너가 다시 떠서 IP가 바뀌어도 이어지도록 Docker DNS를 다시 조회한다
    resolver 127.0.0.11 valid=10s ipv6=off;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Request-Id $request_id;
EOF
    if [ "${SYNCTX_TRUSTED_AUTH_PROXY:-false}" != true ]; then
        cat <<'EOF'
    # 앞단 인증 프록시가 없으면 브라우저가 보낸 권한 헤더를 지운다
    proxy_set_header X-Forwarded-User "";
    proxy_set_header X-Forwarded-Email "";
    proxy_set_header X-Forwarded-Groups "";
EOF
    else
        local cidr
        echo ""
        echo "    # 신뢰 프록시 모드: 권한 헤더를 넘기므로 인증 프록시 출발지만 받는다"
        for cidr in "${TRUSTED_PROXY_CIDRS[@]}"; do
            echo "    allow ${cidr};"
        done
        echo "    deny all;"
    fi

    local svc path timeout_lines
    for svc in "${ENABLED[@]}"; do
        path="$(service_path "$svc")"
        timeout_lines=""
        if [ "$svc" = marp ]; then
            # Marp 변환 제한(conversion_timeout_seconds 기본 120초)보다 길게 둔다. nginx 기본은 60초다.
            timeout_lines="
        proxy_read_timeout ${SYNCTX_MARP_PROXY_TIMEOUT:-180}s;
        proxy_send_timeout ${SYNCTX_MARP_PROXY_TIMEOUT:-180}s;"
        fi
        # ingress-nginx의 rewrite-target /$2 와 같은 규칙
        cat <<EOF

    location ~* ^/${path}(/.*)?\$ {
        set \$synctx_upstream http://$(service_upstream "$svc");
        rewrite "(?i)^/${path}(?:/(.*))?\$" /\$1 break;
        proxy_pass \$synctx_upstream;${timeout_lines}
    }
EOF
    done
}

render_gateway_conf() {
    local target="$1" tmp https_suffix=""
    mkdir -p "$GENERATED_DIR"
    tmp="$(mktemp)"
    if [ "${SYNCTX_HTTPS_PORT:-443}" != 443 ]; then
        https_suffix=":${SYNCTX_HTTPS_PORT}"
    fi
    {
        echo "# deploy.sh가 만든 파일이다. synctx.env를 바꾼 뒤 deploy.sh를 다시 실행한다."
        if [ "$TLS_ENABLED" = true ]; then
            cat <<EOF
server {
    listen 80;
    server_name _;
    return 301 https://\$host${https_suffix}\$request_uri;
}

server {
    listen 443 ssl;
    server_name _;
    ssl_certificate /etc/nginx/tls/${SYNCTX_TLS_CERT};
    ssl_certificate_key /etc/nginx/tls/${SYNCTX_TLS_KEY};

EOF
        else
            printf 'server {\n    listen 80;\n    server_name _;\n\n'
        fi
        render_server_body
        echo "}"
        if [ "${SYNCTX_JITSI:-false}" = true ]; then
            cat <<EOF
server {
    listen 443 ssl;
    server_name ${SYNCTX_JITSI_HOST};
    ssl_certificate /etc/nginx/tls/${SYNCTX_TLS_CERT};
    ssl_certificate_key /etc/nginx/tls/${SYNCTX_TLS_KEY};
    resolver 127.0.0.11 valid=10s ipv6=off;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_http_version 1.1;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
    location / {
        set \$jitsi_upstream http://jitsi-web:80;
        proxy_pass \$jitsi_upstream;
    }
    location /colibri-ws/jvb/ {
        set \$jitsi_bridge http://jitsi-jvb:9090;
        proxy_pass \$jitsi_bridge;
    }
}
EOF
        fi
    } > "$tmp"
    cat "$tmp" > "$target"
    rm -f "$tmp"
}

# 운영 중인 게이트웨이가 읽는 설정을 바꾸기 전에 같은 nginx 이미지로 후보 설정을 검사한다.
# 깨진 설정이 들어가면 다음 재시작에서 게이트웨이가 계속 죽는다.
validate_gateway_conf() {
    local conf="$1" output
    local mounts=(-v "${conf}:/etc/nginx/conf.d/default.conf:ro")
    if [ "$TLS_ENABLED" = true ]; then
        mounts+=(-v "${SYNCTX_TLS_DIR}:/etc/nginx/tls:ro")
    fi
    if ! output="$(docker run --rm --pull missing "${mounts[@]}" "${SYNCTX_IMAGE_PREFIX-registry.local:5000/}nginx:1.25.4-alpine" nginx -t 2>&1)"; then
        echo "$output"
        fail "게이트웨이 설정이 nginx 검사를 통과하지 못했습니다. 운영 중인 설정(${GATEWAY_CONF})은 바꾸지 않았습니다 (검사한 설정: ${conf})"
    fi
    print_success "게이트웨이 설정 검사 통과"
}

print_header "synctx Docker Compose 배포"
load_env_files
validate_options
# --check는 운영 중인 게이트웨이가 bind mount한 gateway.conf를 건드리지 않고 따로 만든다
if [ "$CHECK_ONLY" = true ]; then
    CHECK_CONF="${GENERATED_DIR}/gateway.check.conf"
    render_gateway_conf "$CHECK_CONF"
    print_success "켜진 서비스: ${SYNCTX_SERVICES}"
    print_info "게이트웨이 설정(검사용): ${CHECK_CONF}"
else
    CANDIDATE_CONF="${GENERATED_DIR}/gateway.candidate.conf"
    render_gateway_conf "$CANDIDATE_CONF"
    print_success "켜진 서비스: ${SYNCTX_SERVICES}"
fi
print_info "설정 해시: ${SYNCTX_CONFIG_HASH}"

if [ "$CHECK_ONLY" = true ]; then
    print_success "검사 완료 (--check: 배포하지 않음)"
    exit 0
fi

command -v docker >/dev/null || fail "docker가 설치되어 있지 않습니다"
docker compose version >/dev/null 2>&1 || fail "docker compose v2가 필요합니다"

print_header "게이트웨이 설정 검사"
validate_gateway_conf "$CANDIDATE_CONF"
# 게이트웨이가 이 파일 하나를 bind mount하므로 inode가 바뀌지 않게 내용만 덮어쓴다
cat "$CANDIDATE_CONF" > "$GATEWAY_CONF"
rm -f "$CANDIDATE_CONF"
print_info "게이트웨이 설정: ${GATEWAY_CONF}"

build_compose_args
PROFILE_ARGS=()
for svc in "${ENABLED[@]}"; do
    PROFILE_ARGS+=(--profile "$svc")
done

[ "${SYNCTX_JITSI:-false}" != true ] || PROFILE_ARGS+=(--profile jitsi)

print_header "컨테이너 시작"
if ! docker compose "${COMPOSE_ARGS[@]}" "${PROFILE_ARGS[@]}" up -d --pull missing --wait --wait-timeout "${SYNCTX_WAIT_TIMEOUT:-180}"; then
    print_error "컨테이너가 정상 상태가 되지 않았습니다"
    docker compose "${COMPOSE_ARGS[@]}" "${PROFILE_ARGS[@]}" ps || true
    docker compose "${COMPOSE_ARGS[@]}" "${PROFILE_ARGS[@]}" logs --tail 40 || true
    exit 1
fi
print_success "컨테이너 시작 완료"

print_header "꺼진 서비스 정리"
for svc in "${OPTIONAL_SERVICES[@]}"; do
    is_enabled "$svc" && continue
    # shellcheck disable=SC2046
    docker compose "${COMPOSE_ARGS[@]}" --profile "$svc" rm -sf $(compose_services_of "$svc") >/dev/null 2>&1 || true
done
if [ "${SYNCTX_JITSI:-false}" != true ]; then
    docker compose "${COMPOSE_ARGS[@]}" --profile jitsi rm -sf jitsi-web jitsi-prosody jitsi-jicofo jitsi-jvb >/dev/null 2>&1 || true
fi
print_success "정리 완료"

print_header "게이트웨이 설정 반영"
docker compose "${COMPOSE_ARGS[@]}" exec -T gateway nginx -t
docker compose "${COMPOSE_ARGS[@]}" exec -T gateway nginx -s reload
print_success "게이트웨이 reload 완료"

if is_enabled cdn; then
    print_header "CDN vendor 파일 채우기"
    docker compose "${COMPOSE_ARGS[@]}" "${PROFILE_ARGS[@]}" exec -T cdn python -I - < "${DOCKER_SCRIPT_DIR}/seed_cdn.py"
fi

print_header "배포 완료"
docker compose "${COMPOSE_ARGS[@]}" "${PROFILE_ARGS[@]}" ps
