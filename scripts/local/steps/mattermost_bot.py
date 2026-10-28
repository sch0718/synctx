"""로컬 Mattermost에 Collab Gateway용 봇과 /synctx 슬래시 명령을 만든다. setup-mattermost.sh가 부른다.

mmctl --local은 bot create·token generate를 지원하지 않아 관리자 로그인으로 REST API를 쓴다.
성공하면 표준 출력에 봇 토큰, 명령 토큰을 각각 한 줄씩 쓴다(다른 출력은 표준 오류).
"""

import argparse
import sys

import httpx

OK = (200, 201)


class ProvisionError(Exception):
    pass


def _check(response: httpx.Response, action: str) -> httpx.Response:
    if response.status_code not in OK:
        raise ProvisionError(f"{action} 실패(HTTP {response.status_code})")
    return response


COMMAND_TRIGGER = "synctx"
COMMAND_HINT = "오늘 | 예정 [일수] | 회의 <id> | 정리 <id> | 도움말"


def provision_command(client: httpx.Client, *, team_id: str, url: str) -> str:
    """팀에 /synctx 명령을 만들고 Mattermost가 발급한 명령 토큰을 돌려준다. 같은 주소의 명령이 있으면 그 토큰을 쓴다."""
    existing = _check(client.get("/api/v4/commands", params={"team_id": team_id, "custom_only": "true"}), "슬래시 명령 조회").json()
    for command in existing:
        if command.get("trigger") != COMMAND_TRIGGER:
            continue
        if command.get("url") == url and command.get("method") == "P" and command.get("token"):
            return command["token"]
        _check(client.delete(f"/api/v4/commands/{command['id']}"), "기존 슬래시 명령 삭제")
    body = {"team_id": team_id, "trigger": COMMAND_TRIGGER, "method": "P", "url": url, "display_name": "synctx",
            "description": "synctx 회의 조회", "auto_complete": True, "auto_complete_desc": "이 채널의 회의를 조회합니다",
            "auto_complete_hint": COMMAND_HINT}
    token = _check(client.post("/api/v4/commands", json=body), "슬래시 명령 생성").json().get("token")
    if not token:
        raise ProvisionError("슬래시 명령 응답에 token이 없습니다")
    return token


def provision_bot(client: httpx.Client, *, admin: str, password: str, team: str, channels: list[str], bot: str,
                  command_url: str, description: str = "synctx Collab Gateway") -> tuple[str, str]:
    login = _check(client.post("/api/v4/users/login", json={"login_id": admin, "password": password}), "관리자 로그인")
    session = login.headers.get("Token")
    if not session:
        raise ProvisionError("관리자 로그인 응답에 세션 토큰이 없습니다")
    client.headers["Authorization"] = f"Bearer {session}"

    team_id = _check(client.get(f"/api/v4/teams/name/{team}"), f"팀 조회({team})").json()["id"]
    channel_ids = [
        _check(client.get(f"/api/v4/teams/name/{team}/channels/name/{name}"), f"채널 조회({name})").json()["id"]
        for name in channels
    ]

    found = client.get(f"/api/v4/users/username/{bot}")
    if found.status_code == 200:
        bot_id = found.json()["id"]
    else:
        created = _check(client.post("/api/v4/bots", json={"username": bot, "display_name": bot, "description": description}),
                         "봇 생성")
        bot_id = created.json()["user_id"]

    _check(client.post(f"/api/v4/teams/{team_id}/members", json={"team_id": team_id, "user_id": bot_id}), "봇을 팀에 추가")
    for name, channel_id in zip(channels, channel_ids):
        _check(client.post(f"/api/v4/channels/{channel_id}/members", json={"user_id": bot_id}), f"봇을 채널에 추가({name})")

    issued = _check(client.post(f"/api/v4/users/{bot_id}/tokens", json={"description": "collab-gateway"}), "봇 토큰 발급")
    token = issued.json().get("token")
    if not token:
        raise ProvisionError("봇 토큰 응답에 token이 없습니다")
    return token, provision_command(client, team_id=team_id, url=command_url)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--admin", required=True)
    parser.add_argument("--password-file", required=True)
    parser.add_argument("--team", required=True)
    parser.add_argument("--bot", required=True)
    parser.add_argument("--command-url", required=True)
    parser.add_argument("channels", nargs="+")
    args = parser.parse_args(argv)
    with open(args.password_file, encoding="utf-8") as handle:
        password = handle.read().strip()
    try:
        with httpx.Client(base_url=args.base, timeout=30.0) as client:
            bot_token, command_token = provision_bot(client, admin=args.admin, password=password, team=args.team,
                                                     channels=args.channels, bot=args.bot, command_url=args.command_url)
    except (ProvisionError, httpx.HTTPError, KeyError) as exc:
        print(f"봇 준비 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(bot_token)
    print(command_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
