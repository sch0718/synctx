#!/bin/bash
# verify-collab.sh - Gateway의 Mattermost 어댑터로 시험 메시지를 보내 채널에 실제로 올라왔는지 확인한다
# setup-mattermost.sh가 만든 봇 토큰을 쓰고, port-forward로 Mattermost에 붙는다. Viewer·Gateway는 필요 없다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../_common.sh"

TEAM="${MATTERMOST_TEAM:-synctx}"
CHANNEL="${1:-team-docs}"
LOCAL_PORT="${MATTERMOST_LOCAL_PORT:-18065}"
COLLAB_VALUES_FILE="${PROJECT_ROOT}/helm/synctx/values-local.collab.secret.yaml"

if [ ! -f "${COLLAB_VALUES_FILE}" ]; then
    print_error "${COLLAB_VALUES_FILE} 가 없습니다. 먼저 setup-mattermost.sh 를 실행하세요"
    exit 1
fi

print_header "Mattermost 도달 확인 (${TEAM}/${CHANNEL})"
kubectl port-forward -n "${DEPS_NAMESPACE}" svc/mattermost "${LOCAL_PORT}:8065" >/dev/null 2>&1 &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${LOCAL_PORT}/api/v4/system/ping" >/dev/null 2>&1; then break; fi
    sleep 1
done

cd "${PROJECT_ROOT}"
PYTHONPATH="${PROJECT_ROOT}/services/collab-gateway/src" \
VERIFY_BASE="http://127.0.0.1:${LOCAL_PORT}" VERIFY_TEAM="${TEAM}" VERIFY_CHANNEL="${CHANNEL}" VERIFY_VALUES="${COLLAB_VALUES_FILE}" \
uv run --frozen --all-packages python -I - <<'PY'
import asyncio, os, sys, time
sys.path.insert(0, os.environ["PYTHONPATH"])
import httpx, yaml
from adapters.base import ChannelRef
from adapters.mattermost import MattermostAdapter

base, team, channel = os.environ["VERIFY_BASE"], os.environ["VERIFY_TEAM"], os.environ["VERIFY_CHANNEL"]
token = yaml.safe_load(open(os.environ["VERIFY_VALUES"]))["collabGateway"]["secrets"]["botToken"]


async def main():
    marker = f"synctx-verify-{int(time.time())}"
    adapter = MattermostAdapter(base, token)
    await adapter.send(ChannelRef(team, channel), f":white_check_mark: {marker}")
    await adapter.aclose()
    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"}) as client:
        info = await client.get(f"/api/v4/teams/name/{team}/channels/name/{channel}")
        posts = await client.get(f"/api/v4/channels/{info.json()['id']}/posts", params={"per_page": 10})
        found = any(marker in post["message"] for post in posts.json()["posts"].values())
    print("FOUND" if found else "MISSING", marker)
    sys.exit(0 if found else 1)


asyncio.run(main())
PY
