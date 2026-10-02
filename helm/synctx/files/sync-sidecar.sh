#!/bin/sh
# Slides Server Sidecar: 주기적으로 Git 저장소를 pull하여 최신 HTML을 가져온다.
# SIGUSR1 수신 시 즉시 pull을 수행한다.

REPO_URL="${PUBLISH_REPO_URL}"
BRANCH="${PUBLISH_BRANCH:-main}"
WORKTREE="${SLIDES_WORKTREE:-/data/slides/worktree}"
POLL_INTERVAL="${POLL_INTERVAL_SECONDS:-60}"
TRIGGER_FILE="/tmp/slides-refresh-trigger"
GENERATE_INDEX="${GENERATE_INDEX_SCRIPT:-/scripts/generate-index.sh}"
SLIDES_DIR="$WORKTREE/_published/slides"

AUTH_HEADER=""
if [ -n "$GIT_READ_TOKEN" ] && echo "$REPO_URL" | grep -q '^https://'; then
    AUTH_HEADER="Authorization: Basic $(printf 'x-access-token:%s' "$GIT_READ_TOKEN" | base64 | tr -d '\n')"
fi

git_remote() {
    if [ -n "$AUTH_HEADER" ]; then
        git -c http.extraHeader="$AUTH_HEADER" "$@"
    else
        git "$@"
    fi
}

SIDECAR_PID_FILE="/tmp/slides-sidecar.pid"
echo $$ > "$SIDECAR_PID_FILE"

echo "Starting slides-sync sidecar..."
echo "Repo: $REPO_URL"
echo "Branch: $BRANCH"
echo "Worktree: $WORKTREE"

generate_index() {
    if [ -f "$GENERATE_INDEX" ]; then
        sh "$GENERATE_INDEX" "$SLIDES_DIR" 2>&1 || echo "Index generation failed."
    fi
}

do_clone() {
    echo "Cloning repository..."
    mkdir -p "$WORKTREE"
    if git_remote clone --branch "$BRANCH" --single-branch "$REPO_URL" "$WORKTREE" 2>&1; then
        echo "Clone successful."
        generate_index
        return 0
    else
        echo "Clone failed, will retry in ${POLL_INTERVAL}s..."
        return 1
    fi
}

do_pull() {
    echo "Pulling latest changes..."
    git_remote fetch origin "$BRANCH" 2>&1 || { echo "Fetch failed, will retry later."; return 1; }
    git reset --hard "origin/$BRANCH" 2>&1 || { echo "Reset failed."; return 1; }
    echo "Pull completed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    generate_index
}

if [ -d "$WORKTREE/.git" ]; then
    git -C "$WORKTREE" remote set-url origin "$REPO_URL" 2>&1 || echo "Failed to reset remote URL."
fi

trap 'do_pull' USR1

while true; do
    if [ ! -d "$WORKTREE/.git" ]; then
        do_clone || true
    else
        cd "$WORKTREE"
        do_pull || true
    fi

    if [ -f "$TRIGGER_FILE" ]; then
        rm -f "$TRIGGER_FILE"
    fi

    sleep "$POLL_INTERVAL" &
    wait $! || true
done
