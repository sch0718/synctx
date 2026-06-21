#!/bin/sh
# Slides refresh HTTP server (port 8081).
# POST /internal/slides/refresh → sidecar에 SIGUSR1 전송 → 즉시 git pull 수행.
# Service token 검증: SYNCTX_SERVICE_TOKEN 환경변수가 설정되면 X-Service-Token 헤더 필수.

REFRESH_PORT="${REFRESH_PORT:-8081}"
SIDECAR_PID_FILE="/tmp/slides-sidecar.pid"
SERVICE_TOKEN="${SYNCTX_SERVICE_TOKEN:-}"

handle_request() {
    read -r request_line
    method=$(echo "$request_line" | awk '{print $1}')
    path=$(echo "$request_line" | awk '{print $2}')

    headers=""
    token=""
    content_length=0
    while IFS= read -r header; do
        header=$(echo "$header" | tr -d '\r')
        [ -z "$header" ] && break
        case "$header" in
            X-Service-Token:*|x-service-token:*)
                token=$(echo "$header" | cut -d: -f2- | sed 's/^ //')
                ;;
            Content-Length:*|content-length:*)
                content_length=$(echo "$header" | cut -d: -f2- | sed 's/^ //')
                ;;
        esac
    done

    if [ "$method" = "POST" ] && [ "$path" = "/internal/slides/refresh" ]; then
        if [ -n "$SERVICE_TOKEN" ] && [ "$token" != "$SERVICE_TOKEN" ]; then
            printf "HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n\r\n{\"error\":\"invalid service token\"}"
            return
        fi

        if [ -f "$SIDECAR_PID_FILE" ]; then
            kill -USR1 "$(cat "$SIDECAR_PID_FILE")" 2>/dev/null
            printf "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"refresh triggered\"}"
        else
            printf "HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n\r\n{\"error\":\"sidecar not running\"}"
        fi
    elif [ "$method" = "GET" ] && [ "$path" = "/healthz" ]; then
        printf "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"ok\"}"
    else
        printf "HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\n\r\n{\"error\":\"not found\"}"
    fi
}

echo "Starting refresh server on port $REFRESH_PORT..."

while true; do
    echo "$$" > "$SIDECAR_PID_FILE.server"
    handle_request | nc -l -p "$REFRESH_PORT" -q 1 2>/dev/null || true
done
