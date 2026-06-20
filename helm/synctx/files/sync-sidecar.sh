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

if [ ! -d "$WORKTREE/.git" ]; then
    echo "Cloning repository..."
    mkdir -p "$WORKTREE"
    git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$WORKTREE"
fi

cd "$WORKTREE"

while true; do
    echo "Pulling latest changes..."
    git pull origin "$BRANCH" || echo "Failed to pull, will retry later."
    
    # Nginx에 reload 신호를 보낼 수도 있으나, 정적 파일이므로 파일 시스템 변경만으로 충분함.
    
    sleep "$POLL_INTERVAL"
done
