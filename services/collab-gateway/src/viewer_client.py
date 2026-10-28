"""Viewer의 GET /api/meetings를 서비스 토큰으로 읽는다."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

import httpx

from models import Meeting, MemoItem

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0


class ViewerError(Exception):
    """Viewer 조회 실패. 메시지에는 토큰·응답 본문을 담지 않는다."""


def _memo_items(raw: Any) -> tuple[MemoItem, ...]:
    items = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("text"), str):
            items.append(MemoItem(text=entry["text"], owner=str(entry.get("owner") or ""), due=str(entry.get("due") or "")))
    return tuple(items)


def parse_meeting(raw: Any) -> Optional[Meeting]:
    try:
        if not isinstance(raw, dict):
            return None
        presenters = raw.get("presenters")
        return Meeting(
            repo_id=str(raw["repo_id"]), repo_name=str(raw.get("repo_name") or raw["repo_id"]), id=str(raw["id"]),
            title=str(raw["title"]), path=str(raw["path"]),
            starts_at=datetime.fromisoformat(raw["starts_at"]), ends_at=datetime.fromisoformat(raw["ends_at"]),
            duration_minutes=int(raw["duration_minutes"]), channel=str(raw["channel"]),
            presenters=tuple(str(p) for p in presenters) if isinstance(presenters, list) else (),
            cancelled=bool(raw.get("cancelled", False)), url=str(raw["url"]), present_url=str(raw["present_url"]),
            decisions=_memo_items(raw.get("decisions")), actions=_memo_items(raw.get("actions")),
            memo_error=raw.get("memo_error") if isinstance(raw.get("memo_error"), str) else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


class ViewerClient:
    def __init__(self, base_url: str, token: str, *, client: Optional[httpx.AsyncClient] = None):
        if not token:
            raise ValueError("Viewer 서비스 토큰이 비어 있습니다")
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_meetings(self, since: Optional[date] = None) -> list[Meeting]:
        params = {"from": since.isoformat()} if since else None
        try:
            response = await self._client.get(f"{self._base}/api/meetings", headers=self._headers, params=params)
        except httpx.HTTPError as exc:
            raise ViewerError(f"Viewer에 연결하지 못했습니다({type(exc).__name__})") from None
        if response.status_code != 200:
            raise ViewerError(f"Viewer가 HTTP {response.status_code}로 답했습니다")
        try:
            payload = response.json()
        except ValueError:
            raise ViewerError("Viewer 응답이 JSON이 아닙니다") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("meetings"), list):
            raise ViewerError("Viewer 응답 형식이 올바르지 않습니다")
        meetings = [m for m in map(parse_meeting, payload["meetings"]) if m is not None]
        skipped = len(payload["meetings"]) - len(meetings)
        if skipped:
            logger.warning("viewer_meetings_skipped count=%d", skipped)
        return meetings
