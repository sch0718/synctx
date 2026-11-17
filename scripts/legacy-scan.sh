#!/usr/bin/env bash
# Viewer 정적 파일(services/viewer/src/static)을 Modern Web Guidance legacy-scan으로 검사한다.
# 허용 목록(scripts/legacy-scan-allow.txt)에 없는 옛 패턴이 나오면 실패한다. 배포 전(정적 내보내기 워크플로, 릴리스 전)에 돈다.
#
#   scripts/legacy-scan.sh                 # npx로 스캔(인터넷 필요, 외부 개발 환경 전용)
#   scripts/legacy-scan.sh --input <json>  # 이미 받은 --json 결과로만 비교(시험용)
#
# 스캔 결과는 후보다. 새로 나온 항목은 규칙의 keepWhen/dropWhen으로 확인한 뒤 고치거나,
# 일부러 둔 코드면 이유와 함께 허용 목록에 올린다(CLAUDE.md 「웹 플랫폼 가이드」).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="services/viewer/src/static"
MWG_VERSION="1.0.1"
ALLOW_FILE="${LEGACY_SCAN_ALLOW:-$SCRIPT_DIR/legacy-scan-allow.txt}"

INPUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --input) INPUT="${2:?--input에 파일 경로가 필요하다}"; shift 2 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
    esac
done

command -v jq >/dev/null || { echo "jq가 필요하다" >&2; exit 2; }
[ -f "$ALLOW_FILE" ] || { echo "허용 목록이 없다: $ALLOW_FILE" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ -z "$INPUT" ]; then
    # 텔레메트리는 끈다. 버전을 고정해 같은 코드면 같은 결과가 나오게 한다.
    INPUT="$TMP/scan.json"
    (cd "$ROOT_DIR" && DISABLE_TELEMETRY=1 npx -y "modern-web-guidance@$MWG_VERSION" legacy-scan "$TARGET" --json) > "$INPUT"
fi

# 발견 항목을 「규칙 파일」별 개수로 센다.
jq -r '.guides[].rules[] | .ruleId as $r | .findings[] | "\($r) \(.file)"' "$INPUT" | sort | uniq -c \
    | awk '{print $2, $3, $1}' > "$TMP/found.txt"

# 허용 목록: 「규칙 파일 최대개수  # 이유」. 빈 줄과 # 줄은 건너뛴다.
sed -e 's/\r$//' -e 's/#.*//' -e '/^[[:space:]]*$/d' "$ALLOW_FILE" | awk '{print $1, $2, $3}' > "$TMP/allow.txt"
# 허용 목록이 잘못 적히면 통과시키지 않고 멈춘다(개수가 정수가 아님, 같은 규칙·파일이 두 번).
if ! awk 'NF != 3 || $3 !~ /^[0-9]+$/ { bad = 1 } { if (seen[$1 FS $2]++) bad = 1 } END { exit bad }' "$TMP/allow.txt"; then
    echo "허용 목록 형식 오류: $ALLOW_FILE (줄마다 「규칙 파일 개수」 한 번씩, 개수는 정수)" >&2
    exit 2
fi

NEW=0
while read -r rule file count; do
    allowed="$(awk -v r="$rule" -v f="$file" '$1 == r && $2 == f {print $3}' "$TMP/allow.txt")"
    if [ -z "$allowed" ] || [ "$count" -gt "$allowed" ]; then
        NEW=1
        echo "새 후보: $rule $file ${count}건 (허용 ${allowed:-0}건)" >&2
        jq -r --arg r "$rule" --arg f "$file" \
            '.guides[] | .guideId as $g | .rules[] | select(.ruleId == $r) | .findings[] | select(.file == $f) | "  \(.file):\(.line) \(.message) (가이드 \($g))"' "$INPUT" >&2
    fi
done < "$TMP/found.txt"

TOTAL="$(jq -r '.totalFindings' "$INPUT")"
if [ "$NEW" -eq 1 ]; then
    echo "" >&2
    echo "legacy-scan: 허용 목록 밖의 옛 패턴 후보가 있다." >&2
    echo "규칙은 'DISABLE_TELEMETRY=1 npx -y modern-web-guidance@$MWG_VERSION retrieve <guideId>'로 읽고 keepWhen/dropWhen으로 확인한다." >&2
    echo "고치거나, 일부러 둔 코드면 이유와 함께 $(basename "$ALLOW_FILE")에 올린다." >&2
    exit 1
fi
echo "legacy-scan: 통과 (후보 ${TOTAL}건, 모두 허용 목록 안)"
