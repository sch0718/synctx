"""채널에서 받은 /synctx 명령을 해석해 회의 정보로 답한다(메신저와 무관한 순수 함수)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

import messages
from models import Meeting

KST = timezone(timedelta(hours=9))
LINE_LIMIT = 20
CANDIDATE_LIMIT = 5
MAX_ARG_CHARS = 200
MAX_REPLY_CHARS = 15000
DEFAULT_DAYS = 7
MAX_DAYS = 30
COMMANDS = ("today", "upcoming", "meeting", "summary", "help", "unknown")
RESULTS = ("ok", "no_channel", "not_found", "ambiguous", "invalid", "rate_limited", "unauthorized", "unavailable", "error")

_ALIASES = {
    "오늘": "today", "today": "today",
    "예정": "upcoming", "upcoming": "upcoming",
    "회의": "meeting", "meeting": "meeting",
    "정리": "summary", "summary": "summary",
    "도움말": "help", "help": "help", "?": "help", "": "help",
}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f  ]+")
_DAYS_RE = re.compile(r"(\d{1,3})\s*일?")

HELP_TEXT = (
    "**/synctx 명령**\n"
    "- `/synctx 오늘` — 이 채널의 오늘 회의\n"
    "- `/synctx 예정 [일수]` — 앞으로 N일(기본 7, 최대 30) 회의\n"
    "- `/synctx 회의 <id 또는 제목>` — 회의 정보와 링크\n"
    "- `/synctx 정리 <id 또는 제목>` — 결정사항·액션 아이템\n"
    "- `/synctx 도움말`"
)


@dataclass(frozen=True)
class Reply:
    text: str
    command: str
    result: str


def parse(text: str) -> tuple[str, str]:
    flat = _CONTROL_RE.sub(" ", text or "").strip()
    head, _, rest = flat.partition(" ")
    command = _ALIASES.get(head.casefold(), "unknown")
    if command == "unknown":
        return command, flat
    return command, rest.strip()


def _status(m: Meeting, now: datetime) -> str:
    if m.cancelled:
        return "취소"
    if now < m.starts_at:
        return "예정"
    if now < m.ends_at:
        return "진행 중"
    return "종료"


def _range(m: Meeting) -> str:
    start = m.starts_at.astimezone(KST)
    end = m.ends_at.astimezone(KST)
    return f"{start:%H:%M}–{end:%H:%M}" if end.date() == start.date() else f"{start:%H:%M}–{end:%m-%d %H:%M}"


def _line(m: Meeting, now: datetime, base_url: str) -> str:
    presenters = f" · {messages.escape(', '.join(m.presenters))}" if m.presenters else ""
    link = f"[{messages.escape(m.title)}]({messages.absolute_url(base_url, m.url)})"
    return f"- {_range(m)} {link} ({_status(m, now)}){presenters}"


def _bounded(text: str) -> str:
    return text if len(text) <= MAX_REPLY_CHARS else text[: MAX_REPLY_CHARS - 1] + "…"


def _lines(meetings: Sequence[Meeting], now: datetime, base_url: str) -> list[str]:
    lines = [_line(m, now, base_url) for m in meetings[:LINE_LIMIT]]
    if len(meetings) > LINE_LIMIT:
        lines.append(f"- … 외 {len(meetings) - LINE_LIMIT}건")
    return lines


def _today(meetings: Sequence[Meeting], now: datetime, base_url: str) -> Reply:
    today = now.astimezone(KST).date()
    found = sorted((m for m in meetings if m.starts_at.astimezone(KST).date() == today), key=lambda m: m.starts_at)
    if not found:
        return Reply("오늘 이 채널에 잡힌 회의가 없습니다.", "today", "ok")
    return Reply(_bounded("\n".join([f"**오늘 회의 {len(found)}건** ({today:%Y-%m-%d})", *_lines(found, now, base_url)])),
                 "today", "ok")


def _upcoming(arg: str, meetings: Sequence[Meeting], now: datetime, base_url: str) -> Reply:
    days = DEFAULT_DAYS
    if arg:
        match = _DAYS_RE.fullmatch(arg)
        days = int(match.group(1)) if match else 0
        if not 1 <= days <= MAX_DAYS:
            return Reply(f"일수는 1~{MAX_DAYS} 사이의 정수로 입력하세요. 예: `/synctx 예정 14`", "upcoming", "invalid")
    until = now + timedelta(days=days)
    found = sorted((m for m in meetings if m.ends_at > now and m.starts_at < until), key=lambda m: m.starts_at)
    if not found:
        return Reply(f"앞으로 {days}일 안에 이 채널에 잡힌 회의가 없습니다.", "upcoming", "ok")
    lines = [f"**앞으로 {days}일 회의 {len(found)}건**"]
    current = None
    shown = found[:LINE_LIMIT]
    for m in shown:
        day = m.starts_at.astimezone(KST).date()
        if day != current:
            current = day
            lines.append(f"**{day:%m-%d}**")
        lines.append(_line(m, now, base_url))
    if len(found) > LINE_LIMIT:
        lines.append(f"- … 외 {len(found) - LINE_LIMIT}건")
    return Reply(_bounded("\n".join(lines)), "upcoming", "ok")


def _find(arg: str, meetings: Sequence[Meeting]) -> list[Meeting]:
    exact = [m for m in meetings if m.id == arg]
    if exact:
        return exact
    needle = arg.casefold()
    return [m for m in meetings if needle in m.title.casefold()]


def _resolve(command: str, arg: str, meetings: Sequence[Meeting], now: datetime, base_url: str):
    if not arg:
        return None, Reply(f"회의 id나 제목을 함께 입력하세요. 예: `/synctx {'회의' if command == 'meeting' else '정리'} <id>`",
                           command, "invalid")
    found = _find(arg, meetings)
    if not found:
        return None, Reply("이 채널에서 해당 회의를 찾지 못했습니다. `/synctx 예정`으로 목록을 확인하세요.", command, "not_found")
    if len(found) > 1:
        lines = [f"- `{messages.escape(m.id)}` {messages.escape(m.title)}" for m in found[:CANDIDATE_LIMIT]]
        more = [f"- … 외 {len(found) - CANDIDATE_LIMIT}건"] if len(found) > CANDIDATE_LIMIT else []
        text = "\n".join(["여러 회의가 맞습니다. id로 다시 입력하세요.", *lines, *more])
        return None, Reply(text, command, "ambiguous")
    return found[0], None


def _card(m: Meeting, now: datetime, base_url: str) -> Reply:
    lines = [f":calendar: **회의 정보** · [{messages.escape(m.title)}]({messages.absolute_url(base_url, m.url)})",
             f"- 일시: {messages.time_label(m.starts_at, m.duration_minutes)}",
             f"- 상태: {_status(m, now)}"]
    if m.presenters:
        lines.append(f"- 발표자: {messages.escape(', '.join(m.presenters))}")
    lines.append(f"- 저장소: {messages.escape(m.repo_name)} · id `{messages.escape(m.id)}`")
    if m.decisions or m.actions:
        lines.append(f"- 결정 {len(m.decisions)}건 · 액션 {len(m.actions)}건 (`/synctx 정리 {messages.escape(m.id)}`)")
    lines.append(f"[문서 열기]({messages.absolute_url(base_url, m.url)}) · "
                 f"[발표 화면]({messages.absolute_url(base_url, m.present_url)})")
    return Reply(_bounded("\n".join(lines)), "meeting", "ok")


def _summary(m: Meeting, base_url: str) -> Reply:
    if m.memo_error:
        return Reply(f"`{messages.escape(m.id)}`의 회의 메모 파일을 읽지 못했습니다. 문서의 `.memo.md`를 확인하세요.",
                     "summary", "ok")
    if not (m.decisions or m.actions):
        return Reply(f"`{messages.escape(m.id)}`에는 아직 기록된 결정·액션이 없습니다. 회의 모드의 .md 내보내기를 "
                     "`<문서명>.memo.md`로 저장소에 커밋하면 여기에 보입니다.", "summary", "ok")
    return Reply(_bounded(messages.summary_message(m, base_url)), "summary", "ok")


def handle(text: str, meetings: Iterable[Meeting], channel_keys: set[str], now: datetime, base_url: str) -> Reply:
    command, arg = parse(text)
    if command == "help":
        return Reply(HELP_TEXT, "help", "ok")
    if command == "unknown":
        return Reply("알 수 없는 명령입니다. `/synctx 도움말`을 확인하세요.", "unknown", "invalid")
    if len(arg) > MAX_ARG_CHARS:
        return Reply(f"입력이 너무 깁니다({MAX_ARG_CHARS}자 이하).", command, "invalid")
    if not channel_keys:
        return Reply("이 채널에 연결된 회의가 없습니다.", command, "no_channel")
    scoped = [m for m in meetings if m.channel in channel_keys]
    if command == "today":
        return _today(scoped, now, base_url)
    if command == "upcoming":
        return _upcoming(arg, scoped, now, base_url)
    found, error = _resolve(command, arg, scoped, now, base_url)
    if error is not None:
        return error
    return _card(found, now, base_url) if command == "meeting" else _summary(found, base_url)
