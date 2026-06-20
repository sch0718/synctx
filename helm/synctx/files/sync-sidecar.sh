#!/bin/sh
# Slides Server Sidecar: 주기적으로 Git 저장소를 pull하여 최신 HTML을 가져온다.

set -e

REPO_URL="${PUBLISH_REPO_URL}"
BRANCH="${PUBLISH_BRANCH:-main}"
WORKTREE="/data/slides/worktree"
POLL_INTERVAL="${POLL_INTERVAL_SECONDS:-60}"

echo "Starting slides-sync sidecar..."
echo "Repo: $REPO_URL"
echo "Branch: $BRANCH"
echo "Worktree: $WORKTREE"

mkdir -p "$WORKTREE"

while true; do
    if [ ! -d "$WORKTREE/.git" ]; then
        echo "Cloning repository..."
        if git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$WORKTREE"; then
            echo "Clone successful."
        else
            echo "Clone failed, will retry in ${POLL_INTERVAL}s..."
            sleep "$POLL_INTERVAL"
            continue
        fi
    fi

    cd "$WORKTREE"
    echo "Pulling latest changes..."
    git pull origin "$BRANCH" || echo "Failed to pull, will retry later."

    sleep "$POLL_INTERVAL"
done
