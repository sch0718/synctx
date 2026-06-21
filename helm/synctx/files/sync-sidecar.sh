#!/bin/sh
# Slides Server Sidecar: 주기적으로 Git 저장소를 pull하여 최신 HTML을 가져온다.
# SIGUSR1 수신 시 즉시 pull을 수행한다.

set -e

REPO_URL="${PUBLISH_REPO_URL}"
BRANCH="${PUBLISH_BRANCH:-main}"
WORKTREE="/data/slides/worktree"
POLL_INTERVAL="${POLL_INTERVAL_SECONDS:-60}"
TRIGGER_FILE="/tmp/slides-refresh-trigger"
GENERATE_INDEX="/scripts/generate-index.sh"
SLIDES_DIR="/data/slides/worktree/_published/slides"

SIDECAR_PID_FILE="/tmp/slides-sidecar.pid"
echo $$ > "$SIDECAR_PID_FILE"

echo "Starting slides-sync sidecar..."
echo "Repo: $REPO_URL"
echo "Branch: $BRANCH"
echo "Worktree: $WORKTREE"

if [ ! -d "$WORKTREE/.git" ]; then
    echo "Cloning repository..."
    mkdir -p "$WORKTREE"
    git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$WORKTREE"
fi

cd "$WORKTREE"

do_pull() {
    echo "Pulling latest changes..."
    git fetch origin "$BRANCH" 2>&1 || { echo "Fetch failed, will retry later."; return 1; }
    git reset --hard "origin/$BRANCH" 2>&1 || { echo "Reset failed."; return 1; }
    echo "Pull completed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [ -x "$GENERATE_INDEX" ]; then
        sh "$GENERATE_INDEX" "$SLIDES_DIR" 2>&1 || echo "Index generation failed."
    fi
}

trap 'do_pull' USR1

while true; do
    do_pull || true

    if [ -f "$TRIGGER_FILE" ]; then
        rm -f "$TRIGGER_FILE"
    fi

    sleep "$POLL_INTERVAL" &
    wait $! || true
done
