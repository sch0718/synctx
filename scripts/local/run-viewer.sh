#!/bin/bash
# run-viewer.sh - Docker 없이 로컬에서 Viewer(+Marp, CDN 파일)를 한 프로세스로 실행한다
# 사용법: scripts/local/run-viewer.sh [--port 18000] [--config <.synctx.yaml>]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"
exec uv run --frozen --all-packages python "${SCRIPT_DIR}/run_viewer.py" "$@"
