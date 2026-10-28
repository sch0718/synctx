"""Viewer의 회의·검색·근거 API를 서비스 토큰으로 읽는다."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional
from urllib.parse import quote, unquote

import httpx

from models import DocumentLink, EvidenceResult, Meeting, MemoItem, SearchResult

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
COMMAND_TIMEOUT_SECONDS = 2.0
SEARCH_REPO_LIMIT = 5


class ViewerError(Exception):
    """Viewer 조회 실패. 메시지에는 토큰·응답 본문을 담지 않는다."""


def _document_link(repo_id: str, path: str, title: str, anchor: str = "") -> DocumentLink:
    if not all(isinstance(value, str) for value in (repo_id, path, title, anchor)):
        raise ViewerError("Viewer 문서 형식이 올바르지 않습니다")
    if not repo_id or repo_id in (".", "..") or "/" in repo_id or not path:
        raise ViewerError("Viewer 문서 경로가 올바르지 않습니다")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise ViewerError("Viewer 문서 경로가 올바르지 않습니다")
    if any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in repo_id + path + anchor):
        raise ViewerError("Viewer 문서 경로가 올바르지 않습니다")
    url = f"/view/{quote(repo_id, safe='')}/{quote(path, safe='/')}"
    if anchor:
        url += "#" + quote(anchor, safe="")
    return DocumentLink(repo_id, path, title, url)


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
        jitsi_enabled, joinable_from, joinable_until = _jitsi_info(raw.get("jitsi"))
        return Meeting(
            repo_id=str(raw["repo_id"]), repo_name=str(raw.get("repo_name") or raw["repo_id"]), id=str(raw["id"]),
            title=str(raw["title"]), path=str(raw["path"]),
            starts_at=datetime.fromisoformat(raw["starts_at"]), ends_at=datetime.fromisoformat(raw["ends_at"]),
            duration_minutes=int(raw["duration_minutes"]), channel=str(raw["channel"]),
            presenters=tuple(str(p) for p in presenters) if isinstance(presenters, list) else (),
            cancelled=bool(raw.get("cancelled", False)), url=str(raw["url"]), present_url=str(raw["present_url"]),
            decisions=_memo_items(raw.get("decisions")), actions=_memo_items(raw.get("actions")),
            memo_error=raw.get("memo_error") if isinstance(raw.get("memo_error"), str) else None,
            jitsi_enabled=jitsi_enabled, joinable_from=joinable_from, joinable_until=joinable_until,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _jitsi_info(raw: Any) -> tuple[bool, datetime | None, datetime | None]:
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return False, None, None
    try:
        start = datetime.fromisoformat(raw["joinable_from"])
        end = datetime.fromisoformat(raw["joinable_until"])
        if start.tzinfo is not None and end.tzinfo is not None and start < end:
            return True, start, end
    except (KeyError, TypeError, ValueError):
        pass
    return False, None, None


class ViewerClient:
    def __init__(self, base_url: str, token: str, *, client: Optional[httpx.AsyncClient] = None):
        if not token:
            raise ValueError("Viewer 서비스 토큰이 비어 있습니다")
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params=None, *, timeout: float = TIMEOUT_SECONDS) -> dict:
        try:
            response = await self._client.get(f"{self._base}{path}", headers=self._headers, params=params, timeout=timeout)
        except httpx.HTTPError as exc:
            raise ViewerError(f"Viewer에 연결하지 못했습니다({type(exc).__name__})") from None
        if response.status_code != 200:
            raise ViewerError(f"Viewer가 HTTP {response.status_code}로 답했습니다")
        try:
            payload = response.json()
        except ValueError:
            raise ViewerError("Viewer 응답이 JSON이 아닙니다") from None
        if not isinstance(payload, dict):
            raise ViewerError("Viewer 응답 형식이 올바르지 않습니다")
        return payload

    async def search_documents(self, query: str, repo_ids: set[str]) -> list[SearchResult]:
        results = []
        for repo_id in sorted(repo_ids)[:SEARCH_REPO_LIMIT]:
            payload = await self._get("/api/search", {"q": query, "repo": repo_id, "collab_only": "true"},
                                      timeout=COMMAND_TIMEOUT_SECONDS)
            results.extend(self._search_results(payload, {repo_id}))
        return results

    def _search_results(self, payload: dict, repo_ids: set[str]) -> list[SearchResult]:
        try:
            if not isinstance(payload["repos"], list):
                raise ValueError
            results = []
            skipped = 0
            for repo in payload["repos"]:
                if not isinstance(repo["id"], str):
                    raise ValueError
                if repo["id"] not in repo_ids:
                    continue
                if not isinstance(repo["status"], str) or not isinstance(repo["matches"], list):
                    raise ValueError
                documents = []
                for match in repo["matches"]:
                    try:
                        documents.append(_document_link(repo["id"], match["path"], match["path"]))
                    except (ViewerError, KeyError, TypeError):
                        skipped += 1
                results.append(SearchResult(repo["id"], repo["status"], tuple(documents)))
            if skipped:
                logger.warning("viewer_search_skipped count=%d", skipped)
            return results
        except (KeyError, TypeError, ValueError):
            raise ViewerError("Viewer 검색 응답 형식이 올바르지 않습니다") from None

    async def fetch_evidence(self, repo_id: str, meeting_id: str) -> EvidenceResult:
        path = f"/api/meetings/{quote(repo_id, safe='')}/{quote(meeting_id, safe='')}/evidence"
        payload = await self._get(path, timeout=COMMAND_TIMEOUT_SECONDS)
        if payload.get("status") not in ("ok", "partial"):
            raise ViewerError("Viewer 근거를 읽지 못했습니다")
        try:
            if payload["meeting_id"] != meeting_id or not isinstance(payload["evidence"], list):
                raise ValueError
            omitted = payload.get("omitted", 0)
            if type(omitted) is not int or omitted < 0:
                raise ValueError
            links = []
            skipped = 0
            for entry in payload["evidence"]:
                try:
                    anchor = unquote(entry["url"].partition("#")[2])
                    links.append(_document_link(entry["repo_id"], entry["path"], entry["title"], anchor))
                except (ViewerError, KeyError, TypeError, AttributeError):
                    skipped += 1
            if skipped:
                logger.warning("viewer_evidence_skipped count=%d", skipped)
            return EvidenceResult(tuple(links), payload["status"], omitted)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ViewerError("Viewer 근거 응답 형식이 올바르지 않습니다") from None

    async def fetch_meetings(self, since: Optional[date] = None) -> list[Meeting]:
        params = {"from": since.isoformat()} if since else None
        payload = await self._get("/api/meetings", params)
        if not isinstance(payload.get("meetings"), list):
            raise ViewerError("Viewer 응답 형식이 올바르지 않습니다")
        meetings = [m for m in map(parse_meeting, payload["meetings"]) if m is not None]
        skipped = len(payload["meetings"]) - len(meetings)
        if skipped:
            logger.warning("viewer_meetings_skipped count=%d", skipped)
        return meetings
