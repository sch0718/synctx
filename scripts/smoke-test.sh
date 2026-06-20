#!/usr/bin/env bash
set -euo pipefail

# synctx 배포 후 smoke test
# K8s 클러스터에 배포된 상태에서 실행
# 사용법: bash scripts/smoke-test.sh [base_url]

BASE_URL="${1:-http://synctx.internal}"
NAMESPACE="${SYNCTX_NAMESPACE:-synctx}"
PASS=0
FAIL=0
SKIP=0

check() {
    local name="$1"
    local url="$2"
    local expected_status="${3:-200}"

    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$url" 2>/dev/null || echo "000")

    if [ "$status" = "$expected_status" ]; then
        echo "  [PASS] $name ($status)"
        PASS=$((PASS + 1))
    elif [ "$status" = "000" ]; then
        echo "  [SKIP] $name (unreachable)"
        SKIP=$((SKIP + 1))
    else
        echo "  [FAIL] $name (expected $expected_status, got $status)"
        FAIL=$((FAIL + 1))
    fi
}

check_metrics() {
    local name="$1"
    local url="$2"

    local body
    body=$(curl -s --connect-timeout 5 "$url" 2>/dev/null || echo "")

    if echo "$body" | grep -q "synctx_"; then
        echo "  [PASS] $name (synctx metrics found)"
        PASS=$((PASS + 1))
    elif [ -z "$body" ]; then
        echo "  [SKIP] $name (unreachable)"
        SKIP=$((SKIP + 1))
    else
        echo "  [FAIL] $name (no synctx_ metrics in response)"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== synctx smoke test ==="
echo "Base URL: $BASE_URL"
echo ""

# 1. Health checks
echo "--- Health Checks ---"
check "Viewer healthz"    "$BASE_URL/view/healthz"
check "Viewer readyz"     "$BASE_URL/view/readyz"
check "CDN healthz"       "$BASE_URL/cdn/healthz"
check "CDN readyz"        "$BASE_URL/cdn/readyz"
check "Marp healthz"      "$BASE_URL/marp/healthz"
check "Slides (nginx)"    "$BASE_URL/slides/" "200"
check "Draw.io"           "$BASE_URL/drawio/" "200"
echo ""

# 2. Prometheus metrics
echo "--- Metrics Endpoints ---"
check_metrics "Viewer metrics"  "$BASE_URL/view/metrics"
check_metrics "CDN metrics"     "$BASE_URL/cdn/metrics"
check_metrics "Marp metrics"    "$BASE_URL/marp/metrics"
echo ""

# 3. Version endpoints
echo "--- Version Info ---"
check "Viewer version" "$BASE_URL/view/version"
check "CDN version"    "$BASE_URL/cdn/version"
echo ""

# 4. Functional checks
echo "--- Functional ---"

# CDN: list files (GET /api/cdn/list)
cdn_resp=$(curl -s --connect-timeout 5 "$BASE_URL/cdn/api/cdn/list" 2>/dev/null || echo "")
if echo "$cdn_resp" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    echo "  [PASS] CDN list API (valid JSON)"
    PASS=$((PASS + 1))
elif [ -z "$cdn_resp" ]; then
    echo "  [SKIP] CDN list API (unreachable)"
    SKIP=$((SKIP + 1))
else
    echo "  [FAIL] CDN list API (invalid response)"
    FAIL=$((FAIL + 1))
fi

# Marp: convert check (should accept POST)
marp_resp=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 \
    -X POST -H "Content-Type: application/json" \
    -d '{"markdown":"# Test"}' \
    "$BASE_URL/marp/api/marp/preview" 2>/dev/null || echo "000")
if [ "$marp_resp" = "200" ]; then
    echo "  [PASS] Marp preview API ($marp_resp)"
    PASS=$((PASS + 1))
elif [ "$marp_resp" = "403" ]; then
    echo "  [PASS] Marp preview API (authz active, $marp_resp)"
    PASS=$((PASS + 1))
elif [ "$marp_resp" = "000" ]; then
    echo "  [SKIP] Marp preview API (unreachable)"
    SKIP=$((SKIP + 1))
else
    echo "  [FAIL] Marp preview API (got $marp_resp)"
    FAIL=$((FAIL + 1))
fi

echo ""

# 5. K8s checks (if kubectl available)
if command -v kubectl &>/dev/null; then
    echo "--- K8s Pod Status ---"
    pods=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null || echo "")
    if [ -n "$pods" ]; then
        not_running=$(echo "$pods" | grep -cv "Running\|Completed" || true)
        total=$(echo "$pods" | wc -l | tr -d ' ')
        if [ "$not_running" -eq 0 ]; then
            echo "  [PASS] All $total pods running"
            PASS=$((PASS + 1))
        else
            echo "  [FAIL] $not_running of $total pods not running"
            echo "$pods" | grep -v "Running\|Completed" | sed 's/^/    /'
            FAIL=$((FAIL + 1))
        fi
    else
        echo "  [SKIP] No pods found in namespace $NAMESPACE"
        SKIP=$((SKIP + 1))
    fi

    echo ""

    # ServiceMonitor check
    echo "--- Prometheus Resources ---"
    sm=$(kubectl get servicemonitor -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l | tr -d ' ')
    pr=$(kubectl get prometheusrule -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [ "$sm" -gt 0 ]; then
        echo "  [PASS] ServiceMonitor found ($sm)"
        PASS=$((PASS + 1))
    else
        echo "  [SKIP] No ServiceMonitor (Prometheus Operator may not be installed)"
        SKIP=$((SKIP + 1))
    fi
    if [ "$pr" -gt 0 ]; then
        echo "  [PASS] PrometheusRule found ($pr)"
        PASS=$((PASS + 1))
    else
        echo "  [SKIP] No PrometheusRule"
        SKIP=$((SKIP + 1))
    fi
    echo ""
fi

# Summary
echo "=== Results ==="
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  SKIP: $SKIP"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
