#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT_DIR"

SERVICES=(shared/synctx-common services/cdn services/marp services/sync services/viewer)
FAILED=0

for svc in "${SERVICES[@]}"; do
    test_dir="$svc/tests"
    if [ -d "$test_dir" ]; then
        echo "=== Testing $svc ==="
        if ! python -m pytest "$test_dir" -v --tb=short; then
            FAILED=1
        fi
        echo ""
    fi
done

if [ "$FAILED" -eq 1 ]; then
    echo "SOME TESTS FAILED"
    exit 1
fi

echo "ALL TESTS PASSED"
