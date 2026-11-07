#!/usr/bin/env bash
# 인증 없이 보호 호스트를 요청해 막히는지 확인한다. 하나라도 막히지 않으면 비정상 종료한다.
# --mode access(기본): Cloudflare Access가 막는지. --mode login: 로그인 모드 Functions가 같은 출처 /login으로 302하는지.
# 사용법: scripts/static-export/check-access.sh [--mode access|login] https://<프로젝트>.pages.dev/view/ [...]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

exec python3 "$ROOT/services/viewer/src/static_export/access_check.py" "$@"
