#!/bin/bash
# One-command standalone Viewer startup, locally or in Docker.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "--docker" ]]; then
    shift
    exec "${PROJECT_ROOT}/scripts/docker/run-viewer.sh" "$@"
fi
exec "${PROJECT_ROOT}/scripts/local/run-lite.sh" "$@"
