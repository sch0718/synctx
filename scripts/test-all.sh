#!/usr/bin/env bash
# 전체 테스트: 모든 tests 디렉터리의 pytest + Viewer JS 단위 테스트(node --test). make test가 이 스크립트를 부른다.
# 추가 인자는 pytest에 그대로 넘긴다 (예: scripts/test-all.sh -x)
set -e
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT_DIR"

PYTEST_DIRS=(shared/*/tests services/*/tests scripts/tests)
JS_TESTS=(services/viewer/tests/js/*.test.mjs)
FAILED=0

echo "=== pytest: ${PYTEST_DIRS[*]} ==="
if ! uv run --frozen --all-packages pytest "${PYTEST_DIRS[@]}" -q "$@"; then
    FAILED=1
fi
echo ""

echo "=== node --test: ${#JS_TESTS[@]} files ==="
if ! node --test "${JS_TESTS[@]}"; then
    FAILED=1
fi
echo ""

if [ "$FAILED" -eq 1 ]; then
    echo "SOME TESTS FAILED"
    exit 1
fi

echo "ALL TESTS PASSED"
