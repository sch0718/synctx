#!/usr/bin/env bash
# 시연 저장소와 설정을 만들고 실제 Viewer의 메뉴별 영상을 녹화한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORK="${TMPDIR:-/tmp}/synctx-video"
OUT="${SCRIPT_DIR}/out"
MENUS=()
SERVER_PID=""

usage() {
    cat <<'HELP'
사용법: scripts/video/make.sh [--work 폴더] [--out 폴더] [--menu 메뉴 ...]
메뉴: browse search document meeting present evidence slides
--menu를 여러 번 주면 지정한 메뉴만, 생략하면 일곱 메뉴를 녹화합니다.
임시 저장소와 설정은 프로젝트 밖 --work 폴더에, 결과는 --out 폴더에 둡니다.
준비물 설치 절차는 scripts/video/README.md를 확인하세요.
HELP
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --work|--out|--menu)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "오류: $1 뒤에 값을 지정하세요" >&2
                exit 1
            fi
            case "$1" in
                --work) WORK="$2" ;;
                --out) OUT="$2" ;;
                --menu)
                    case "$2" in
                        browse|search|document|meeting|present|evidence|slides) MENUS+=(--menu "$2") ;;
                        *) echo "오류: 알 수 없는 메뉴: $2" >&2; exit 1 ;;
                    esac
                    ;;
            esac
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) echo "오류: 알 수 없는 옵션: $1" >&2; usage >&2; exit 1 ;;
    esac
done

cd "$PROJECT_ROOT"
if ! command -v uv >/dev/null 2>&1; then
    echo "오류: uv를 설치하고 scripts/video/README.md의 준비 절차를 먼저 실행하세요" >&2
    exit 1
fi
# 녹화 중에는 의존성이나 브라우저를 내려받지 않는다.
export UV_OFFLINE=1
PYTHON=(uv run --frozen --no-sync --all-packages --group browser python)

echo "녹화 준비물을 확인합니다"
if ! "${PYTHON[@]}" - <<'PY'
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

INSTALL = """준비 절차:
  uv sync --all-packages --group browser
  uv run --group browser playwright install ffmpeg
  brew install ffmpeg                 # 다른 운영체제에서는 해당 패키지 관리자를 사용하세요.
  npm install -g @marp-team/marp-cli @mermaid-js/mermaid-cli
시스템 Google Chrome도 설치하세요. ffmpeg에는 libx264 인코더가 필요합니다.
자세한 내용: scripts/video/README.md"""

try:
    import yaml  # 설정 파일 생성에 필요한 의존성도 미리 확인한다.
    from playwright._impl._driver import compute_driver_executable

    for tool, flag in (("git", "--version"), ("ffmpeg", "-version"),
                       ("ffprobe", "-version"), ("marp", "--version"), ("mmdc", "--version")):
        executable = shutil.which(tool)
        if not executable:
            raise RuntimeError(f"PATH에서 {tool} 실행 파일을 찾지 못했습니다")
        subprocess.run([executable, flag], check=True, capture_output=True, timeout=15)

    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], check=True,
                             capture_output=True, text=True, timeout=15).stdout
    if not re.search(r"^\s*V\S*\s+libx264\s", encoders, re.MULTILINE):
        raise RuntimeError("시스템 ffmpeg가 libx264 인코더를 지원하지 않습니다")

    # 설치 경로를 추측하지 않고 설치된 Playwright와 같은 레지스트리를 사용한다.
    node, cli = compute_driver_executable()
    package = Path(cli).parent
    script = """const fs = require('fs'), path = require('path'), root = process.argv[1];
      const bootstrap = path.join(root, 'lib/bootstrap.js');
      if (fs.existsSync(bootstrap)) require(bootstrap);
      const legacy = path.join(root, 'lib/server/registry/index.js');
      const {registry} = fs.existsSync(legacy) ? require(legacy)
        : require(path.join(root, 'lib/coreBundle.js')).registry;
      console.log(JSON.stringify(['chrome','ffmpeg'].map(name =>
        ({name, path:registry.findExecutable(name).executablePath()}))));"""
    result = subprocess.run([str(node), "-e", script, str(package)], check=True,
                            capture_output=True, text=True, timeout=15)
    for item in json.loads(result.stdout):
        executable = Path(item["path"])
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(f"{item['name']} 실행 파일이 없거나 실행 권한이 없습니다: {executable}")
        flag = "--version" if item["name"] == "chrome" else "-version"
        subprocess.run([str(executable), flag], check=True, capture_output=True, timeout=15)
except Exception as exc:
    print(f"준비물 검사 실패: {exc}\n{INSTALL}", file=sys.stderr)
    sys.exit(1)
print("준비물 확인 완료")
PY
then
    echo "의존성이 없다면 UV_OFFLINE 설정을 해제한 별도 터미널에서 README의 설치 절차를 실행하세요" >&2
    exit 1
fi

WORK="$("${PYTHON[@]}" - "$WORK" "$PROJECT_ROOT" <<'PY'
import sys
from pathlib import Path

work = Path(sys.argv[1]).expanduser().resolve()
project = Path(sys.argv[2]).resolve()
if work.is_relative_to(project) or project.is_relative_to(work):
    sys.exit("오류: --work는 프로젝트 밖의 별도 작업 폴더로 지정하세요")
work.mkdir(parents=True, exist_ok=True)
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
    sys.exit("오류: 18090 포트가 사용 중입니다. 기존 프로세스를 종료한 뒤 다시 실행하세요")
PY

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

# macOS 기본 Bash에서도 빈 배열을 안전하게 확장한다.
"${PYTHON[@]}" "${SCRIPT_DIR}/record.py" --base-url http://127.0.0.1:18090 --out "$OUT" ${MENUS[@]+"${MENUS[@]}"}
echo "영상 생성 완료: ${OUT}"
