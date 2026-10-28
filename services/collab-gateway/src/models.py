"""Gateway가 Viewer 회의 API에서 읽는 값."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DocumentLink:
    repo_id: str
    path: str
    title: str
    url: str


@dataclass(frozen=True)
class SearchResult:
    repo_id: str
    status: str
    documents: tuple[DocumentLink, ...]


@dataclass(frozen=True)
class EvidenceResult:
    documents: tuple[DocumentLink, ...]
    status: str = "ok"
    omitted: int = 0


@dataclass(frozen=True)
class MemoItem:
    text: str
    owner: str
    due: str


@dataclass(frozen=True)
class Meeting:
    repo_id: str
    repo_name: str
    id: str
    title: str
    path: str
    starts_at: datetime
    ends_at: datetime
    duration_minutes: int
    channel: str
    presenters: tuple[str, ...]
    cancelled: bool
    url: str
    present_url: str
    decisions: tuple[MemoItem, ...] = ()
    actions: tuple[MemoItem, ...] = ()
    memo_error: str | None = None
    jitsi_enabled: bool = False
    joinable_from: datetime | None = None
    joinable_until: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.repo_id}/{self.id}"
