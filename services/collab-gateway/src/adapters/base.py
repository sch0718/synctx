"""메신저 어댑터 인터페이스. 첫 구현은 Mattermost다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChannelRef:
    team: str
    channel: str


@dataclass(frozen=True)
class InboundCommand:
    team: str
    channel: str
    user_id: str
    text: str


class SendError(Exception):
    """보내기 실패. 메시지에는 토큰·응답 본문을 담지 않는다."""

    def __init__(self, message: str, *, retryable: bool, reason: str):
        super().__init__(message)
        self.retryable = retryable
        self.reason = reason


class MessengerAdapter(Protocol):
    async def send(self, channel: ChannelRef, text: str) -> None: ...

    async def aclose(self) -> None: ...
