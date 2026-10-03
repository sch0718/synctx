"""Front Matter 파서."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field


_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_MEETING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_STARTS_AT_EXAMPLE = "(예: 2026-10-08T14:00+09:00)"


class MeetingFrontMatter(BaseModel):
    id: str
    title: str
    starts_at: datetime
    duration_minutes: int = 60
    channel: str
    presenters: list[str] = Field(default_factory=list)
    cancelled: bool = False


class SynctxFrontMatter(BaseModel):
    type: str | None = None
    target: str | None = None
    subject: str | None = None
    status: str | None = None
    priority: str | None = None
    version: str | None = None
    due_date: str | None = None
    assignee: str | None = None
    name: str | None = None
    issues: list[str] = Field(default_factory=list)
    meeting: MeetingFrontMatter | None = None


class FrontMatter(BaseModel):
    title: str | None = None
    synctx: SynctxFrontMatter | None = None


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """마크다운 문자열에서 Front Matter를 파싱하고 본문과 분리하여 반환한다.
    
    Args:
        content: 마크다운 문자열
        
    Returns:
        (front_matter_dict, body_content)
    """
    match = _FRONT_MATTER_RE.match(content)
    if not match:
        return {}, content
        
    yaml_str = match.group(1)
    try:
        front_matter = yaml.safe_load(yaml_str)
        if not isinstance(front_matter, dict):
            front_matter = {}
    except yaml.YAMLError:
        front_matter = {}
        
    body_content = content[match.end():]
    return front_matter, body_content


def validate_frontmatter(front_matter: Dict[str, Any]) -> FrontMatter:
    return FrontMatter.model_validate(front_matter or {})


def _parse_starts_at(value: Any) -> Tuple[Optional[datetime], Optional[str]]:
    if value is None:
        return None, "starts_at이 없습니다"
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return None, f"starts_at에 시각이 없습니다{_STARTS_AT_EXAMPLE}"
    elif isinstance(value, str):
        text = value.strip()
        if "T" not in text and " " not in text:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
                return None, f"starts_at에 시각이 없습니다{_STARTS_AT_EXAMPLE}"
            return None, "starts_at 형식이 올바르지 않습니다(ISO 8601)"
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None, "starts_at 형식이 올바르지 않습니다(ISO 8601)"
    else:
        return None, "starts_at 형식이 올바르지 않습니다(ISO 8601)"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, f"starts_at에 타임존이 없습니다{_STARTS_AT_EXAMPLE}"
    return parsed, None


def validate_meeting(front_matter: Dict[str, Any]) -> Tuple[Optional[MeetingFrontMatter], List[str]]:
    """회의 문서의 synctx.meeting 블록을 검사한다. 예외 대신 한국어 오류 목록을 돌려준다."""
    synctx_meta = (front_matter or {}).get("synctx")
    raw = synctx_meta.get("meeting") if isinstance(synctx_meta, dict) else None
    if not isinstance(raw, dict):
        return None, ["synctx.meeting 블록이 없습니다"]

    errors: List[str] = []

    meeting_id = raw.get("id")
    if meeting_id is None or (isinstance(meeting_id, str) and not meeting_id.strip()):
        errors.append("meeting.id가 없습니다")
    elif not isinstance(meeting_id, str) or not _MEETING_ID_RE.match(meeting_id):
        errors.append("meeting.id 형식이 올바르지 않습니다(영문·숫자·.·_·- 128자 이하)")

    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("meeting.title이 없습니다")

    starts_at, starts_error = _parse_starts_at(raw.get("starts_at"))
    if starts_error:
        errors.append(starts_error)

    duration = raw.get("duration_minutes", 60)
    if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 1440:
        errors.append("duration_minutes는 1~1440 사이의 정수여야 합니다")

    channel = raw.get("channel")
    if channel is None or (isinstance(channel, str) and not channel.strip()):
        errors.append("meeting.channel이 없습니다")
    elif not isinstance(channel, str) or not _CHANNEL_RE.match(channel):
        errors.append("meeting.channel 형식이 올바르지 않습니다(영문 소문자·숫자·_·- 64자 이하)")

    presenters = raw.get("presenters", [])
    if not isinstance(presenters, list) or not all(isinstance(p, str) for p in presenters):
        errors.append("presenters는 문자열 목록이어야 합니다")

    cancelled = raw.get("cancelled", False)
    if not isinstance(cancelled, bool):
        errors.append("cancelled는 true 또는 false여야 합니다")

    if errors:
        return None, errors

    return MeetingFrontMatter(
        id=meeting_id,
        title=title.strip(),
        starts_at=starts_at,
        duration_minutes=duration,
        channel=channel,
        presenters=presenters,
        cancelled=cancelled,
    ), []
