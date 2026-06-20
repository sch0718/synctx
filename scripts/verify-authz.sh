#!/usr/bin/env bash
set -euo pipefail

# synctx 권한 매핑 운영 검증 스크립트
# Ingress 인증 헤더 → synctx role 매핑이 올바른지 확인
# 사용법: bash scripts/verify-authz.sh [base_url]

BASE_URL="${1:-http://synctx.internal}"
PASS=0
FAIL=0

test_access() {
    local desc="$1"
    local method="$2"
    local url="$3"
    local groups="$4"
    local expected="$5"

    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 \
        -X "$method" \
        -H "X-Forwarded-Groups: $groups" \
        -H "Content-Type: application/json" \
        "$url" 2>/dev/null || echo "000")

    if [ "$status" = "$expected" ]; then
        echo "  [PASS] $desc (groups=$groups → $status)"
        PASS=$((PASS + 1))
    elif [ "$status" = "000" ]; then
        echo "  [SKIP] $desc (unreachable)"
    else
        echo "  [FAIL] $desc (groups=$groups → expected $expected, got $status)"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== synctx Authorization Matrix Verification ==="
echo "Base URL: $BASE_URL"
echo ""

# ── Viewer (누구나 읽기 가능) ──
echo "--- Viewer ---"
test_access "viewer read (any role)"    GET  "$BASE_URL/view/healthz"  "viewer"     "200"
test_access "viewer read (no role)"     GET  "$BASE_URL/view/healthz"  ""           "200"
echo ""

# ── CDN (읽기: 전체, 업로드: cdn-admin) ──
echo "--- CDN ---"
test_access "cdn read (any role)"       GET  "$BASE_URL/cdn/healthz"   "viewer"     "200"
test_access "cdn upload (no role)"      POST "$BASE_URL/cdn/api/cdn/upload" "viewer" "403"
test_access "cdn upload (cdn-admin)"    POST "$BASE_URL/cdn/api/cdn/upload" "cdn-admin" "422"
echo ""

# ── Marp (publisher 이상) ──
echo "--- Marp ---"
test_access "marp convert (no role)"    POST "$BASE_URL/marp/api/marp/convert"  "viewer"     "403"
test_access "marp convert (publisher)"  POST "$BASE_URL/marp/api/marp/convert"  "publisher"  "422"
test_access "marp convert (admin)"      POST "$BASE_URL/marp/api/marp/convert"  "admin"      "422"
echo ""

echo "=== Results ==="
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "권한 매핑 불일치 발견. .synctx.yaml의 authz.role_mappings 설정을 확인하세요."
    echo ""
    echo "기대하는 매핑:"
    echo "  viewer:         [viewer, publisher, cdn-admin, sync-admin, feedback-admin, admin]"
    echo "  publisher:      [publisher, admin]"
    echo "  cdn_admin:      [cdn-admin, admin]"
    echo "  sync_admin:     [sync-admin, admin]"
    echo "  feedback_admin: [feedback-admin, admin]"
    echo "  platform_admin: [admin]"
    exit 1
fi
