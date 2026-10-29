"""회의 문서 대시보드: 저장소의 회의 문서 수집, 상태 판정, 필터, 회의 메모 파일 집계."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional, Sequence

from synctx_common.frontmatter import parse_frontmatter, validate_meeting

from meeting_card import KST

logger = logging.getLogger(__name__)

CANDIDATE_QUERY = "meeting:"
CANDIDATE_PATHSPECS = (":(glob,icase)**/*.md", ":(glob,icase)**/*.markdown")
CANDIDATE_MATCH_LIMIT = 2000
GREP_TIMEOUT_SECONDS = 8.0
MAX_MEETINGS = 500
DOC_MAX_BYTES = 512 * 1024
MEMO_MAX_BYTES = 256 * 1024
MEMO_MAX_COUNT = 1000
MEMO_SUFFIX = ".memo.md"
MEMO_TEXT_MAX = 10000
MEMO_OWNER_MAX = 200
MEMO_QUOTE_MAX = 1000
MEMO_SCHEMA = 1

STATUSES = ("upcoming", "ongoing", "ended", "cancelled")
_PARTIAL_CLONE_MODES = {"blobless", "treeless"}
_MEMO_MARKER = "synctx-" + "memo-data"
_MEMO_OPEN = "<!" + "--"
_MEMO_CLOSE = "-->"
_DUE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class MemoItem:
    kind: str
    text: str
    owner: str
    due: str
    quote: str


@dataclass(frozen=True)
class MeetingEntry:
    repo_id: str
    repo_name: str
    path: str
    id: str
    title: str
    starts_at: datetime
    duration_minutes: int
    channel: str
    presenters: list[str]
    cancelled: bool
    decisions: list[MemoItem] = field(default_factory=list)
    actions: list[MemoItem] = field(default_factory=list)
    memo_error: Optional[str] = None


@dataclass(frozen=True)
class InvalidMeeting:
    repo_id: str
    repo_name: str
    path: str
    reason: str


@dataclass(frozen=True)
class RepoMeetings:
    repo_id: str
    repo_name: str
    status: str
    meetings: list[MeetingEntry] = field(default_factory=list)
    invalid: list[InvalidMeeting] = field(default_factory=list)
    truncated: bool = False


@dataclass(frozen=True)
class ActionRow:
    meeting: MeetingEntry
    item: MemoItem


_cache: dict[str, tuple[str, RepoMeetings]] = {}


def clear_cache() -> None:
    _cache.clear()


def _candidate_path(path: str) -> bool:
    if path.lower().endswith(MEMO_SUFFIX):
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return False
    return not any(part.startswith(".") for part in path.split("/"))


def memo_path_for(path: str) -> str:
    stem = path.rsplit(".", 1)[0]
    return stem + MEMO_SUFFIX


def _find_memo_data(text: str) -> Optional[str]:
    close = text.rfind(_MEMO_CLOSE)
    idx = text.rfind(_MEMO_MARKER)
    while idx != -1:
        start = idx + len(_MEMO_MARKER)
        before = idx
        while before > 0 and text[before - 1].isspace():
            before -= 1
        opened = before >= len(_MEMO_OPEN) and text[before - len(_MEMO_OPEN):before] == _MEMO_OPEN
        if opened and start < len(text) and text[start].isspace() and close >= start:
            return text[start:text.index(_MEMO_CLOSE, start)].strip()
        idx = text.rfind(_MEMO_MARKER, 0, idx) if idx else -1
    return None


def _memo_item(raw: Any) -> Optional[MemoItem]:
    if not isinstance(raw, dict) or raw.get("deleted_at"):
        return None
    kind, text = raw.get("kind"), raw.get("text")
    if kind not in ("decision", "action") or not isinstance(text, str) or not text.strip() or len(text) > MEMO_TEXT_MAX:
        return None
    owner = raw.get("owner")
    if owner is None:
        owner = ""
    if not isinstance(owner, str) or len(owner) > MEMO_OWNER_MAX:
        return None
    due = raw.get("due")
    if due is None:
        due = ""
    if not isinstance(due, str) or (due and not _DUE_RE.fullmatch(due)):
        return None
    anchor = raw.get("anchor")
    quote = anchor.get("quote") if isinstance(anchor, dict) else ""
    if not isinstance(quote, str) or len(quote) > MEMO_QUOTE_MAX:
        quote = ""
    return MemoItem(kind=kind, text=text, owner=owner, due=due, quote=quote)


def parse_memo_summary(text: str, meeting_id: str) -> tuple[list[MemoItem], list[MemoItem], Optional[str]]:
    """회의 모드 .md 내보내기의 데이터 블록에서 결정·액션을 읽는다. 읽지 못하면 사유를 함께 돌려준다."""
    if len(text.encode("utf-8")) > MEMO_MAX_BYTES:
        return [], [], "회의 메모 파일이 너무 큽니다"
    data = _find_memo_data(text)
    if data is None:
        return [], [], "회의 메모 데이터 블록을 찾지 못했습니다"
    try:
        payload = json.loads(data)
    except ValueError:
        return [], [], "회의 메모 데이터를 읽지 못했습니다"
    if not isinstance(payload, dict) or payload.get("schema") != MEMO_SCHEMA or not isinstance(payload.get("memos"), list):
        return [], [], "지원하지 않는 회의 메모 형식입니다"
    doc = payload.get("doc")
    if not isinstance(doc, dict) or doc.get("meeting_id") != meeting_id:
        return [], [], "회의 메모가 이 회의의 것이 아닙니다"
    if len(payload["memos"]) > MEMO_MAX_COUNT:
        return [], [], "회의 메모 개수가 한도를 넘었습니다"
    items = [item for item in map(_memo_item, payload["memos"]) if item is not None]
    return ([i for i in items if i.kind == "decision"], [i for i in items if i.kind == "action"], None)


def _read_text(manager: Any, path: str, resolved: str, limit: int) -> Optional[str]:
    if manager.blob_size_resolved(path, resolved) > limit:
        return None
    return manager.read_resolved(path, resolved).decode("utf-8")


def _entry_from_document(repo_id: str, repo_name: str, path: str, text: str):
    front_matter, _ = parse_frontmatter(text)
    synctx_meta = front_matter.get("synctx") if isinstance(front_matter, dict) else None
    if not isinstance(synctx_meta, dict) or synctx_meta.get("type") != "meeting":
        return None, None
    meeting, errors = validate_meeting(front_matter)
    if errors:
        return None, InvalidMeeting(repo_id, repo_name, path, errors[0])
    return MeetingEntry(repo_id=repo_id, repo_name=repo_name, path=path, id=meeting.id, title=meeting.title,
                        starts_at=meeting.starts_at, duration_minutes=meeting.duration_minutes,
                        channel=meeting.channel, presenters=list(meeting.presenters),
                        cancelled=meeting.cancelled), None


def _build(repo_id: str, repo_name: str, manager: Any, sha: str) -> RepoMeetings:
    resolved = manager.resolve_ref()
    grep = manager.grep(CANDIDATE_QUERY, None, pathspecs=CANDIDATE_PATHSPECS, path_filter=_candidate_path,
                        limit=CANDIDATE_MATCH_LIMIT, timeout=GREP_TIMEOUT_SECONDS)
    if grep.status != "ok":
        return RepoMeetings(repo_id, repo_name, grep.status)
    paths = sorted({match.path for match in grep.matches})
    truncated = grep.truncated
    known = manager.file_paths()
    meetings: list[MeetingEntry] = []
    invalid: list[InvalidMeeting] = []
    for path in paths:
        try:
            text = _read_text(manager, path, resolved, DOC_MAX_BYTES)
        except (OSError, UnicodeDecodeError, ValueError):
            invalid.append(InvalidMeeting(repo_id, repo_name, path, "문서를 읽지 못했습니다"))
            continue
        if text is None:
            invalid.append(InvalidMeeting(repo_id, repo_name, path, "문서가 너무 커서 회의 정보를 읽지 않았습니다"))
            continue
        try:
            found, bad = _entry_from_document(repo_id, repo_name, path, text)
        except (ValueError, TypeError):
            invalid.append(InvalidMeeting(repo_id, repo_name, path, "front matter를 해석하지 못했습니다"))
            continue
        if bad is not None:
            invalid.append(bad)
        if found is None:
            continue
        if len(meetings) >= MAX_MEETINGS:
            truncated = True
            break
        decisions, actions, error = [], [], None
        memo_path = memo_path_for(path)
        if memo_path in known:
            try:
                memo_text = _read_text(manager, memo_path, resolved, MEMO_MAX_BYTES)
                if memo_text is None:
                    error = "회의 메모 파일이 너무 큽니다"
                else:
                    decisions, actions, error = parse_memo_summary(memo_text, found.id)
            except (OSError, UnicodeDecodeError, ValueError):
                error = "회의 메모 파일을 읽지 못했습니다"
        meetings.append(MeetingEntry(**{**found.__dict__, "decisions": decisions, "actions": actions,
                                        "memo_error": error}))
    return RepoMeetings(repo_id, repo_name, "ok", meetings, invalid, truncated)


def collect(repo_id: str, repo_name: str, manager: Any) -> RepoMeetings:
    """저장소 기본 브랜치의 회의 문서를 모은다. 커밋이 같으면 캐시를 쓴다."""
    if manager.status != "ready":
        return RepoMeetings(repo_id, repo_name, "unavailable")
    if manager.profile.storage_mode in _PARTIAL_CLONE_MODES:
        return RepoMeetings(repo_id, repo_name, "unsupported")
    try:
        sha = manager.resolve_commit_sha()
        cached = _cache.get(repo_id)
        if cached is not None and cached[0] == sha:
            return cached[1]
        result = _build(repo_id, repo_name, manager, sha)
    except Exception as exc:
        logger.warning("meeting_index_failed repo_id=%s error=%s", repo_id, type(exc).__name__)
        return RepoMeetings(repo_id, repo_name, "failed")
    if result.status == "ok":
        _cache[repo_id] = (sha, result)
    return result


FAILED_STATUSES = ("failed", "timeout", "unavailable")


def status_of(entry: MeetingEntry, now: datetime) -> str:
    if entry.cancelled:
        return "cancelled"
    if now < entry.starts_at:
        return "upcoming"
    if now < entry.starts_at + timedelta(minutes=entry.duration_minutes):
        return "ongoing"
    return "ended"


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def filter_meetings(entries: Sequence[MeetingEntry], *, now: datetime, status: Optional[str] = None,
                    repo: Optional[str] = None, channel: Optional[str] = None, presenter: Optional[str] = None,
                    q: Optional[str] = None, start: Optional[date] = None, end: Optional[date] = None,
                    descending: bool = False) -> list[MeetingEntry]:
    needle = q.strip().lower() if q else ""
    result = []
    for item in entries:
        if status in STATUSES and status_of(item, now) != status:
            continue
        if repo and item.repo_id != repo:
            continue
        if channel and item.channel != channel:
            continue
        if presenter and presenter not in item.presenters:
            continue
        if needle and needle not in item.title.lower():
            continue
        day = item.starts_at.astimezone(KST).date()
        if (start and day < start) or (end and day > end):
            continue
        result.append(item)
    result.sort(key=lambda m: (m.starts_at, m.repo_id, m.id), reverse=descending)
    return result


def action_items(entries: Sequence[MeetingEntry], owner: Optional[str] = None) -> list[ActionRow]:
    rows = [ActionRow(meeting, item) for meeting in entries for item in meeting.actions
            if not owner or item.owner == owner]
    rows.sort(key=lambda r: (r.item.due == "", r.item.due, r.meeting.starts_at))
    return rows
