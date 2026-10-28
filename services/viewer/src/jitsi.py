from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from synctx_common.config import JitsiConfig
from synctx_common.secret_keys import SecretKeyError, load_secret_key


REASONS = {
    "disabled": "영상 회의가 꺼져 있습니다",
    "cancelled": "취소된 회의입니다",
    "too_early": "회의 시작 15분 전부터 참여할 수 있습니다",
    "ended": "회의 종료 후 1시간이 지났습니다",
}


class JitsiError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(REASONS[reason])


@dataclass(frozen=True)
class JitsiKeys:
    current: bytes
    previous: bytes | None
    room_current: bytes
    room_previous: bytes | None


def load_keys(config: JitsiConfig, environ: Mapping[str, str] | None = None) -> JitsiKeys | None:
    if not config.enabled:
        return None

    def pair(name):
        current = load_secret_key(name, environ)
        previous = load_secret_key(name + "_PREVIOUS", environ)
        if current is None:
            raise SecretKeyError(f"{name} 현재 키가 필요합니다")
        return current, previous

    current, previous = pair(config.secret_env)
    room_current, room_previous = pair(config.room_secret_env)
    if current == room_current:
        raise SecretKeyError("Jitsi 앱 키와 방 이름 키는 달라야 합니다")
    return JitsiKeys(current, previous, room_current, room_previous)


def room_name(room_secret: bytes, repo_id: str, meeting_id: str) -> str:
    if not repo_id or not meeting_id or "|" in repo_id + meeting_id:
        raise ValueError("유효하지 않은 회의 식별자입니다")
    return hmac.new(room_secret, f"{repo_id}|{meeting_id}".encode(), hashlib.sha256).hexdigest()[:32]


def join_window(meeting) -> tuple[datetime, datetime]:
    return (meeting.starts_at - timedelta(minutes=15),
            meeting.starts_at + timedelta(minutes=meeting.duration_minutes, hours=1))


def availability(config: JitsiConfig, meeting, now: datetime) -> str | None:
    if not config.enabled:
        return "disabled"
    if meeting.cancelled:
        return "cancelled"
    start, end = join_window(meeting)
    if now < start:
        return "too_early"
    if now >= end:
        return "ended"
    return None


def participant(identity: str, presenters, *, presenter_identity: str = "", authz_enabled: bool = False) -> tuple[str, bool]:
    user = identity.strip()
    presenter = presenter_identity.strip()
    moderator = authz_enabled and bool(presenter) and any(presenter.casefold() == p.strip().casefold() for p in presenters)
    name = "".join(c for c in user if not unicodedata.category(c).startswith("C"))[:80].strip()
    return name or "참석자", moderator


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def issue_token(config: JitsiConfig, keys: JitsiKeys, repo_id: str, meeting, identity: str = "", *,
                presenter_identity: str = "", authz_enabled: bool = False,
                now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    reason = availability(config, meeting, now)
    if reason:
        raise JitsiError(reason)
    _, end = join_window(meeting)
    expires = min(end, now + timedelta(hours=4))
    name, moderator = participant(identity, meeting.presenters, presenter_identity=presenter_identity, authz_enabled=authz_enabled)
    room = room_name(keys.room_current, repo_id, meeting.id)
    domain = urlsplit(config.public_url).hostname
    claims = {
        "iss": config.app_id, "aud": config.app_id, "sub": config.xmpp_domain or domain, "room": room,
        "iat": int(now.timestamp()), "nbf": int(now.timestamp()) - 10, "exp": int(expires.timestamp()),
        "context": {"user": {"name": name, "moderator": moderator}},
    }
    header = _encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = _encode(json.dumps(claims, ensure_ascii=False, separators=(",", ":")).encode())
    unsigned = f"{header}.{payload}"
    signature = _encode(hmac.new(keys.current, unsigned.encode("ascii"), hashlib.sha256).digest())
    return {"room": room, "jwt": f"{unsigned}.{signature}", "domain": domain,
            "public_url": config.public_url, "moderator": moderator,
            "expires_at": datetime.fromtimestamp(claims["exp"], timezone.utc).isoformat()}


def public_info(config: JitsiConfig, meeting) -> dict:
    start, end = join_window(meeting)
    return {"enabled": config.enabled, "joinable_from": start.isoformat(), "joinable_until": end.isoformat()}
