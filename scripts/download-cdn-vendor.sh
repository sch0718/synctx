#!/usr/bin/env bash
# docs/cdn/ 에 vendor 파일을 다운로드한다.
# 인터넷 접속 가능한 환경에서 1회 실행 후 결과물을 git commit한다.
# 폐쇄망 환경: JSDELIVR_BASE 를 내부 미러 URL로 오버라이드한다.
#
# 사용법:
#   ./download-cdn-vendor.sh
#   JSDELIVR_BASE=http://내부미러/npm ./download-cdn-vendor.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CDN_DIR="${SCRIPT_DIR}/../cdn-data"

JSDELIVR="${JSDELIVR_BASE:-https://cdn.jsdelivr.net/npm}"

# ── 버전 정의 ──────────────────────────────────────────────────
MERMAID_VER="10.9.1"
CHARTJS_VER="4.4.4"
HIGHLIGHT_VER="11.9.0"
# ───────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
CYAN='\033[0;36m'
RESET='\033[0m'

log()  { echo -e "${CYAN}[CDN]${RESET} $*"; }
ok()   { echo -e "${GREEN}[OK]${RESET}  $*"; }

download() {
  local url="$1"
  local dest="$2"
  log "$(basename "$dest")  ← $url"
  mkdir -p "$(dirname "$dest")"
  curl -fsSL --retry 3 "$url" -o "$dest"
  ok "$(basename "$dest")"
}

echo ""
echo "CDN vendor 파일 다운로드"
echo "저장 경로: ${CDN_DIR}"
echo "레지스트리: ${JSDELIVR}"
echo ""

# ── Mermaid ────────────────────────────────────────────────────
log "--- mermaid@${MERMAID_VER} ---"
download "${JSDELIVR}/mermaid@${MERMAID_VER}/dist/mermaid.min.js" \
         "${CDN_DIR}/mermaid/${MERMAID_VER}/mermaid.min.js"

# ── Chart.js ───────────────────────────────────────────────────
log "--- chart.js@${CHARTJS_VER} ---"
download "${JSDELIVR}/chart.js@${CHARTJS_VER}/dist/chart.umd.min.js" \
         "${CDN_DIR}/chart.js/${CHARTJS_VER}/chart.umd.min.js"

# ── highlight.js ───────────────────────────────────────────────
log "--- highlight.js@${HIGHLIGHT_VER} ---"
# highlight.js 패키지의 lib/index.js는 CommonJS 진입점이라 브라우저에서 동작하지 않는다.
HLJS_ASSETS="${JSDELIVR}/@highlightjs/cdn-assets@${HIGHLIGHT_VER}"
download "${HLJS_ASSETS}/highlight.min.js" \
         "${CDN_DIR}/highlight.js/${HIGHLIGHT_VER}/highlight.min.js"
for style in github github-dark monokai atom-one-light; do
  download "${HLJS_ASSETS}/styles/${style}.min.css" \
           "${CDN_DIR}/highlight.js/${HIGHLIGHT_VER}/styles/${style}.min.css"
done

# ── 편의 심링크: 최신 버전 별칭 ─────────────────────────────────
# latest/ → 실제 버전 디렉토리 심링크 (nginx가 symlink를 따르므로 동작함)
link_latest() {
  local dir="$1"
  local ver="$2"
  local latest_dir="$(dirname "${dir}")/latest"
  ln -sfn "${ver}" "${latest_dir}"
  ok "symlink: $(dirname "${dir}")/latest → ${ver}"
}

log "--- latest 심링크 생성 ---"
link_latest "${CDN_DIR}/mermaid/${MERMAID_VER}"  "${MERMAID_VER}"
link_latest "${CDN_DIR}/chart.js/${CHARTJS_VER}" "${CHARTJS_VER}"
link_latest "${CDN_DIR}/highlight.js/${HIGHLIGHT_VER}" "${HIGHLIGHT_VER}"

echo ""
echo "완료. cdn-data/ 를 git commit 하세요:"
echo "  git add cdn-data/"
echo "  git commit -m 'chore(cdn): cdn vendor 파일 추가'"
echo ""
