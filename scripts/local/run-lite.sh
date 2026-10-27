#!/bin/bash
# Standalone Markdown/meeting Viewer; preserve caller-relative arguments.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
exec uv run --frozen --project "${PROJECT_ROOT}" --package synctx-viewer --no-dev python "${PROJECT_ROOT}/services/viewer/src/standalone.py" "$@"
