"""회의 정보 카드에 쓸 일시·안건 가공 함수."""

import html
from datetime import datetime, timedelta, timezone

from synctx_common.frontmatter import MeetingFrontMatter

KST = timezone(timedelta(hours=9))
AGENDA_LIMIT = 20
_WEEKDAYS = "월화수목금토일"


def to_kst(dt: datetime) -> datetime:
    return dt.astimezone(KST)


def time_range_label(starts_at: datetime, duration_minutes: int) -> str:
    start = to_kst(starts_at)
    end = start + timedelta(minutes=duration_minutes)
    head = f"{start:%Y-%m-%d} ({_WEEKDAYS[start.weekday()]}) {start:%H:%M}"
    if end.date() == start.date():
        return f"{head}–{end:%H:%M}"
    return f"{head}–{end:%m-%d} ({_WEEKDAYS[end.weekday()]}) {end:%H:%M}"


def agenda_from_toc(tokens: list[dict]) -> list[tuple[str, str]]:
    agenda: list[tuple[str, str]] = []

    def walk(items):
        for token in items:
            if len(agenda) >= AGENDA_LIMIT:
                return
            if token["level"] == 2:
                agenda.append((token["id"], html.unescape(token["name"])))
            walk(token.get("children", []))

    walk(tokens)
    return agenda


def card_data(meeting: MeetingFrontMatter, toc_tokens: list[dict], *, jitsi: dict | None = None) -> dict:
    return {
        "title": meeting.title,
        "time_label": time_range_label(meeting.starts_at, meeting.duration_minutes),
        "starts_at_iso": to_kst(meeting.starts_at).isoformat(),
        "duration_minutes": meeting.duration_minutes,
        "presenters": list(meeting.presenters),
        "channel": meeting.channel,
        "cancelled": meeting.cancelled,
        "agenda": agenda_from_toc(toc_tokens),
        "jitsi": jitsi,
    }
