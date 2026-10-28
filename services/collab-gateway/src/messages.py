"""Mattermost 채널에 보낼 한국어 메시지. 문서에서 온 글은 멘션·링크가 되지 않게 이스케이프한다."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from models import Meeting, MemoItem

KST = timezone(timedelta(hours=9))
_WEEKDAYS = "월화수목금토일"
TEXT_LIMIT = 200
MEMO_LIMIT = 10
MEMO_TEXT_LIMIT = 300
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f  ]+")
_MARKDOWN_RE = re.compile(r"([\\`*_~\[\]()<>#|!{}])")


def time_label(starts_at: datetime, duration_minutes: int) -> str:
    start = starts_at.astimezone(KST)
    end = start + timedelta(minutes=duration_minutes)
    head = f"{start:%Y-%m-%d} ({_WEEKDAYS[start.weekday()]}) {start:%H:%M}"
    if end.date() == start.date():
        return f"{head}–{end:%H:%M}"
    return f"{head}–{end:%m-%d} ({_WEEKDAYS[end.weekday()]}) {end:%H:%M}"


def escape(text: str, limit: int = TEXT_LIMIT) -> str:
    flat = _CONTROL_RE.sub(" ", str(text)).strip()
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return _MARKDOWN_RE.sub(r"\\\1", flat).replace("@", "@​")


def absolute_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


_url = absolute_url


def _links(m: Meeting, base_url: str) -> str:
    return f"[문서 열기]({_url(base_url, m.url)}) · [발표 화면]({_url(base_url, m.present_url)})"


def _title_link(m: Meeting, base_url: str) -> str:
    return f"[{escape(m.title)}]({_url(base_url, m.url)})"


def _facts(m: Meeting) -> list[str]:
    lines = [f"- 일시: {time_label(m.starts_at, m.duration_minutes)}"]
    if m.presenters:
        lines.append(f"- 발표자: {escape(', '.join(m.presenters))}")
    lines.append(f"- 저장소: {escape(m.repo_name)}")
    return lines


def reminder_message(m: Meeting, base_url: str) -> str:
    return "\n".join([f":calendar: **회의 안내** · {_title_link(m, base_url)}", *_facts(m), _links(m, base_url)])


def changed_message(m: Meeting, changes: list[tuple[str, str, str]], base_url: str) -> str:
    rows = [f"- {escape(label)}: {escape(old)} → {escape(new)}" for label, old, new in changes]
    return "\n".join([f":pencil2: **회의 변경** · {_title_link(m, base_url)}", *rows, *_facts(m), _links(m, base_url)])


def cancelled_message(m: Meeting, base_url: str) -> str:
    return "\n".join([f":no_entry_sign: **회의 취소** · {_title_link(m, base_url)}",
                      f"- 일시: {time_label(m.starts_at, m.duration_minutes)}", _links(m, base_url)])


def _memo_lines(items: tuple[MemoItem, ...]) -> list[str]:
    lines = []
    for memo in items[:MEMO_LIMIT]:
        extra = " · ".join(part for part in (f"담당 {escape(memo.owner)}" if memo.owner else "",
                                             f"기한 {escape(memo.due)}" if memo.due else "") if part)
        lines.append(f"- {escape(memo.text, MEMO_TEXT_LIMIT)}" + (f" ({extra})" if extra else ""))
    if len(items) > MEMO_LIMIT:
        lines.append(f"- … 외 {len(items) - MEMO_LIMIT}건")
    return lines


def summary_message(m: Meeting, base_url: str) -> str:
    lines = [f":memo: **회의 정리** · {_title_link(m, base_url)}"]
    if m.decisions:
        lines += ["**결정사항**", *_memo_lines(m.decisions)]
    if m.actions:
        lines += ["**액션 아이템**", *_memo_lines(m.actions)]
    lines.append(_links(m, base_url))
    return "\n".join(lines)
