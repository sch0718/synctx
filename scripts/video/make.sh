#!/usr/bin/env bash
# 음성 생성, 실제 Viewer 녹화, 내레이션 영상 합성을 단계별로 실행한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORK="${TMPDIR:-/tmp}/synctx-video"
OUT="${SCRIPT_DIR}/out"
STAGE="all"
STAGE_SET=0
PROVIDER="gemini"
ONLY=""

usage() {
    cat <<'HELP'
사용법: scripts/video/make.sh [voice|record|compose|all] [옵션]
단계를 생략하면 voice → record → compose 순서로 실행합니다.
  --work 폴더             녹화용 임시 저장소·설정 위치(프로젝트 밖)
  --out 폴더              전체 결과 위치(기본: scripts/video/out)
  --provider gemini|edge   음성 공급자(기본: gemini, voice 단계만)
  --only 0|1|2|3          다시 생성할 음성 묶음 번호(0부터, voice 단계만)
준비물 설치 절차는 scripts/video/README.md를 확인하세요.
HELP
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        voice|record|compose|all)
            if [[ "$STAGE_SET" -eq 1 ]]; then
                echo "오류: 단계는 하나만 지정하세요" >&2
                exit 1
            fi
            STAGE="$1"
            STAGE_SET=1
            shift
            ;;
        --work|--out|--provider|--only)
            if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
                echo "오류: $1 뒤에 값을 지정하세요" >&2
                exit 1
            fi
            case "$1" in
                --work) WORK="$2" ;;
                --out) OUT="$2" ;;
                --provider)
                    case "$2" in
                        gemini|edge) PROVIDER="$2" ;;
                        *) echo "오류: 음성 공급자는 gemini 또는 edge여야 합니다" >&2; exit 1 ;;
                    esac
                    ;;
                --only)
                    case "$2" in
                        0|1|2|3) ONLY="$2" ;;
                        *) echo "오류: 음성 묶음 번호는 0~3이어야 합니다" >&2; exit 1 ;;
                    esac
                    ;;
            esac
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) echo "오류: 알 수 없는 인자: $1" >&2; usage >&2; exit 1 ;;
    esac
done

# 상대 경로는 실행한 위치 기준으로 해석한 뒤 프로젝트 루트로 옮긴다.
abspath() { case "$1" in /*) printf '%s\n' "$1" ;; *) printf '%s\n' "$PWD/$1" ;; esac; }
WORK="$(abspath "$WORK")"
OUT="$(abspath "$OUT")"
cd "$PROJECT_ROOT"

voice() {
    if ! command -v node >/dev/null 2>&1; then
        echo "오류: PATH에서 node를 찾지 못했습니다. Node.js 22를 설치하세요" >&2
        exit 1
    fi
    echo "내레이션 음성을 생성합니다: ${PROVIDER}"
    if [[ -n "$ONLY" ]]; then
        node "${SCRIPT_DIR}/voice.mjs" --provider "$PROVIDER" --only "$ONLY" --out "${OUT}/voice"
    else
        node "${SCRIPT_DIR}/voice.mjs" --provider "$PROVIDER" --out "${OUT}/voice"
    fi
}

# 서버 정리 trap과 오프라인 환경은 녹화 서브셸 안에서만 적용한다.
record() (
    SERVER_PID=""
    if [[ ! -f "${OUT}/voice/lines.json" ]]; then
        echo "오류: ${OUT}/voice/lines.json이 없습니다. 먼저 voice 단계를 실행하세요" >&2
        exit 1
    fi
if ! command -v uv >/dev/null 2>&1; then
    echo "오류: uv를 설치하고 scripts/video/README.md의 준비 절차를 먼저 실행하세요" >&2
    exit 1
fi
# 녹화 중에는 의존성이나 브라우저를 내려받지 않는다.
export UV_OFFLINE=1
PYTHON=(uv run --frozen --no-sync --all-packages --group browser python)

echo "녹화 준비물을 확인합니다"
# 시스템 Chrome이 없으면 녹화 시작에서 오류가 나므로 따로 검사하지 않는다.
# marp·mmdc는 없으면 Viewer가 Marp를 조용히 끄고 slides가 늦게 실패하므로 미리 본다.
for tool in git ffmpeg ffprobe marp mmdc; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "오류: PATH에서 ${tool}을(를) 찾지 못했습니다. scripts/video/README.md의 준비 절차를 먼저 실행하세요" >&2
        exit 1
    fi
done

WORK="$("${PYTHON[@]}" - "$WORK" "$PROJECT_ROOT" <<'PY'
import sys
from pathlib import Path

work = Path(sys.argv[1]).expanduser().resolve()
project = Path(sys.argv[2]).resolve()
if work.is_relative_to(project) or project.is_relative_to(work):
    sys.exit("오류: --work는 프로젝트 밖의 별도 작업 폴더로 지정하세요")
# 남의 폴더를 덮어쓰지 않도록, 없거나 비었거나 이 도구가 표식을 남긴 폴더만 쓴다.
marker = work / ".synctx-video-work"
if work.exists() and any(work.iterdir()) and not marker.is_file():
    sys.exit(f"오류: {work}는 이 도구가 만든 작업 폴더가 아닙니다. 비어 있는 새 경로를 지정하세요")
work.mkdir(parents=True, exist_ok=True)
marker.write_text("synctx 메뉴 영상 작업 폴더\n", encoding="utf-8")
print(work)
PY
)"
OUT="$("${PYTHON[@]}" - "$OUT" <<'PY'
import sys
from pathlib import Path

out = Path(sys.argv[1]).expanduser().resolve()
out.mkdir(parents=True, exist_ok=True)
print(out)
PY
)"

# 이미 사용 중인 포트의 다른 서버를 준비된 시연 서버로 오인하지 않는다.
"${PYTHON[@]}" - <<'PY'
import socket
import sys

try:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 18090))
except OSError:
    sys.exit("오류: 18090 포트가 사용 중입니다. lsof -nP -iTCP:18090 -sTCP:LISTEN 으로 찾아 종료한 뒤 다시 실행하세요")
PY

# 시연 저장소는 실행마다 이력이 새로 생긴다. 이전 실행의 저장소 캐시가 남으면
# draft 브랜치 fetch가 non-fast-forward로 거부되므로 서버 상태를 지우고 시작한다.
rm -rf "${WORK}/repos" "${WORK}/marp" "${WORK}/cache"
"${PYTHON[@]}" "${SCRIPT_DIR}/demo_repo.py" "${WORK}/demo"
"${PYTHON[@]}" - "$WORK" <<'PY'
import sys
from pathlib import Path

import yaml

work = Path(sys.argv[1])
base = "http://127.0.0.1:18090"
config = {
    "runtime": {"public_base_url": base},
    "authz": {"enabled": False},
    "viewer": {"asset_source": "bundled", "repo_cache_path": str(work / "repos"),
               "cdn_base_url": "/cdn", "marp_service_url": base + "/marp"},
    "marp": {"worktree_path": str(work / "marp")},
    "repositories": [{"id": "demo", "name": "예시 문서", "url": str(work / "demo"),
                      "default_ref": "main", "auth": {"type": "none"}, "collab": {"enabled": True}}],
}
path = work / "synctx.yaml"
path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
print(f"시연 설정: {path}")
PY

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if [[ -n "$SERVER_PID" ]]; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
        for ((attempt=0; attempt<50; attempt++)); do
            if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
            sleep 0.1
        done
        kill -KILL "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [[ $status -ne 0 ]]; then
        echo "녹화를 마치지 못했습니다. 서버 기록: ${WORK}/viewer.log" >&2
        if [[ -f "${WORK}/viewer.log" ]]; then tail -n 30 "${WORK}/viewer.log" >&2; fi
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

scripts/local/run-viewer.sh --config "${WORK}/synctx.yaml" --cache-dir "${WORK}/cache" --port 18090 \
    >"${WORK}/viewer.log" 2>&1 &
SERVER_PID=$!

"${PYTHON[@]}" - "$SERVER_PID" "${SCRIPT_DIR}/demo-docs" <<'PY'
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

pid = int(sys.argv[1])
base = "http://127.0.0.1:18090/view"
deadline = time.monotonic() + 60

def alive():
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        sys.exit("오류: Viewer 서버가 준비 중 종료되었습니다")

while time.monotonic() < deadline:
    alive()
    try:
        with urlopen(base + "/readyz", timeout=min(1, max(0.01, deadline - time.monotonic()))) as response:
            if response.status == 200:
                alive()
                break
    except OSError:
        pass
    time.sleep(0.2)
else:
    sys.exit("오류: 60초 안에 Viewer가 준비되지 않았습니다")

source = Path(sys.argv[2])
for document in sorted(source.rglob("*.md")):
    alive()
    path = document.relative_to(source).as_posix()
    try:
        with urlopen(base + "/demo/" + quote(path, safe="/"), timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
    except (OSError, RuntimeError) as exc:
        sys.exit(f"오류: 시연 문서를 열지 못했습니다: {path}: {exc}")
    print(f"시연 문서 확인: {path}")
PY

"${PYTHON[@]}" "${SCRIPT_DIR}/record.py" --base-url http://127.0.0.1:18090 --out "$OUT"
echo "녹화 완료: ${OUT}"
)

compose() {
    if ! command -v python3 >/dev/null 2>&1; then
        echo "오류: PATH에서 python3를 찾지 못했습니다" >&2
        exit 1
    fi
    echo "내레이션 영상을 합성합니다"
    python3 "${SCRIPT_DIR}/compose.py" --out "$OUT"
    echo "영상 합성 완료: ${OUT}/synctx-demo.mp4"
}

case "$STAGE" in
    voice) voice ;;
    record) record ;;
    compose) compose ;;
    all) voice; record; compose ;;
esac
