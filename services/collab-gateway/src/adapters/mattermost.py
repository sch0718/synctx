"""Mattermost 어댑터: 봇 토큰으로 채널에 글을 올린다."""

from __future__ import annotations

import asyncio
import hmac
import logging
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qsl, quote

import httpx

from adapters.base import ChannelRef, InboundCommand, SendError

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
RETRY_AFTER_CAP_SECONDS = 30.0
BACKOFF_SECONDS = (1.0, 2.0, 4.0)


MAX_FORM_BYTES = 16 * 1024
MAX_FORM_FIELDS = 50


def parse_command_form(body: bytes) -> dict[str, str]:
    """슬래시 명령의 form 본문을 읽는다. 같은 이름이 여럿이면 첫 값만 쓰고 필드 수와 크기를 제한한다."""
    if len(body) > MAX_FORM_BYTES:
        raise ValueError("form 본문이 너무 큽니다")
    pairs = parse_qsl(body.decode("utf-8", errors="replace"), keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS)
    form: dict[str, str] = {}
    for key, value in pairs:
        form.setdefault(key, value)
    return form


def verify_command_token(presented: str, expected: str) -> bool:
    return bool(expected) and bool(presented) and hmac.compare_digest(presented.encode(), expected.encode())


def inbound_from_form(form: dict[str, str]) -> Optional[InboundCommand]:
    team, channel, user_id = form.get("team_domain", ""), form.get("channel_name", ""), form.get("user_id", "")
    if not (team and channel and user_id):
        return None
    return InboundCommand(team=team.lower(), channel=channel.lower(), user_id=user_id, text=form.get("text", ""))


class MattermostAdapter:
    def __init__(self, base_url: str, bot_token: str, *, client: Optional[httpx.AsyncClient] = None,
                 max_attempts: int = 3, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if not bot_token:
            raise ValueError("Mattermost 봇 토큰이 비어 있습니다")
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {bot_token}"}
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._channel_ids: dict[tuple[str, str], str] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    def _url(self, path: str) -> str:
        return self._base + path

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        last: Optional[SendError] = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.request(method, self._url(path), headers=self._headers, **kwargs)
            except httpx.HTTPError as exc:
                last = SendError(f"Mattermost 연결 실패({type(exc).__name__})", retryable=True, reason="network")
            else:
                status = response.status_code
                if status < 400:
                    return response
                if status in (401, 403):
                    raise SendError(f"Mattermost 인증 실패(HTTP {status})", retryable=False, reason="auth")
                if status == 404:
                    raise SendError("Mattermost에서 대상을 찾지 못했습니다(HTTP 404)", retryable=False,
                                    reason="channel_not_found")
                if status != 429 and status < 500:
                    raise SendError(f"Mattermost 요청 거부(HTTP {status})", retryable=False, reason="http_4xx")
                last = SendError(f"Mattermost 일시 오류(HTTP {status})", retryable=True,
                                 reason="rate_limited" if status == 429 else "http_5xx")
                if attempt < self._max_attempts:
                    await self._sleep(self._delay(response, attempt))
                    continue
                raise last
            if attempt < self._max_attempts:
                await self._sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
        raise last  # type: ignore[misc]

    @staticmethod
    def _delay(response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if response.status_code == 429 and value:
            try:
                return min(max(float(value), 0.0), RETRY_AFTER_CAP_SECONDS)
            except ValueError:
                pass
        return BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]

    async def _channel_id(self, channel: ChannelRef) -> str:
        cache_key = (channel.team, channel.channel)
        if cache_key in self._channel_ids:
            return self._channel_ids[cache_key]
        path = f"/api/v4/teams/name/{quote(channel.team, safe='')}/channels/name/{quote(channel.channel, safe='')}"
        response = await self._request("GET", path)
        try:
            channel_id = response.json().get("id")
        except ValueError:
            channel_id = None
        if not isinstance(channel_id, str) or not channel_id:
            raise SendError("Mattermost 채널 조회 응답에 id가 없습니다", retryable=False, reason="channel_not_found")
        self._channel_ids[cache_key] = channel_id
        return channel_id

    async def send(self, channel: ChannelRef, text: str) -> None:
        channel_id = await self._channel_id(channel)
        try:
            await self._request("POST", "/api/v4/posts", json={"channel_id": channel_id, "message": text})
        except SendError as exc:
            if not exc.retryable:
                self._channel_ids.pop((channel.team, channel.channel), None)
            raise
