#!/bin/bash
# build.sh - Viewer를 정적 사이트(dist/)로 내보내고 산출물을 검사한다 (로컬·CI 공용)
# 사용법: scripts/static-export/build.sh [build] [--config static-export.yaml] [--out dist] [--repos a,b] [--cache-dir <경로>]
#         scripts/static-export/build.sh verify [--out dist] [--config static-export.yaml]
# 저장소 읽기 토큰은 설정의 auth.credential_env 환경변수(예: GIT_READ_TOKEN)로만 넘긴다.
# 종료 코드: 성공 0, 검사·빌드 실패 1, 설정 오류 2
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

COMMAND="build"
if [[ "${1:-}" == "build" || "${1:-}" == "verify" ]]; then
    COMMAND="$1"
    shift
fi

# 호출한 디렉터리를 바꾸지 않으므로 상대 경로(--config·--out·--cache-dir)는 호출 위치 기준이다.
PYTHONPATH="${PROJECT_ROOT}/services/viewer/src${PYTHONPATH:+:${PYTHONPATH}}" \
    exec uv run --frozen --project "${PROJECT_ROOT}" --all-packages python -m static_export "${COMMAND}" "$@"
