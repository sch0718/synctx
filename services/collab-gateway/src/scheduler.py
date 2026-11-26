"""회의 하나에 대해 지금 보낼 알림을 정한다(순수 함수. 보내기와 상태 저장은 runner가 한다)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from messages import KST
from models import Meeting

SUMMARY_WINDOW = timedelta(days=7)


@dataclass
class Notification:
    kind: str
    key: str
    meeting: Meeting
    changes: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class Plan:
    notifications: list[Notification]
    first: bool
    marks: dict[str, str]
    drop: list[str]
    seen_ended: bool
    snapshot: dict
    snapshot_changed: bool


def _start_iso(m: Meeting) -> str:
    return m.starts_at.astimezone(KST).isoformat()


def snapshot_of(m: Meeting) -> dict:
    return {"title": m.title, "starts_at": _start_iso(m), "duration_minutes": m.duration_minutes,
            "presenters": list(m.presenters), "cancelled": m.cancelled}


def _when(snap: dict) -> str:
    start = datetime.fromisoformat(snap["starts_at"]).astimezone(KST)
    label = start.strftime("%Y-%m-%d %H:%M:%S" if start.second else "%Y-%m-%d %H:%M")
    return f"{label} ({snap['duration_minutes']}분)"


def diff_snapshots(old: dict, new: dict) -> list[tuple[str, str, str]]:
    changes = []
    if old["title"] != new["title"]:
        changes.append(("제목", old["title"], new["title"]))
    old_start = datetime.fromisoformat(old["starts_at"]).astimezone(KST)
    new_start = datetime.fromisoformat(new["starts_at"]).astimezone(KST)
    if old_start != new_start or old["duration_minutes"] != new["duration_minutes"]:
        changes.append(("일시", _when(old), _when(new)))
    if old["presenters"] != new["presenters"]:
        changes.append(("발표자", ", ".join(old["presenters"]) or "없음", ", ".join(new["presenters"]) or "없음"))
    if old["cancelled"] != new["cancelled"]:
        changes.append(("상태", "취소" if old["cancelled"] else "진행", "취소" if new["cancelled"] else "진행"))
    return changes


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _summary_digest(m: Meeting) -> str:
    return _digest([["decision", i.text, i.owner, i.due] for i in m.decisions]
                   + [["action", i.text, i.owner, i.due] for i in m.actions])


def plan_meeting(m: Meeting, entry: Optional[dict], *, reminders: list[int], post_summary: bool, now: datetime) -> Plan:
    first = entry is None
    sent = {} if first else entry.get("sent", {})
    new = snapshot_of(m)
    old = new if first else entry.get("snapshot", new)
    ended = now >= m.ends_at
    seen_ended = ended if first else bool(entry.get("seen_ended", False))
    marks: dict[str, str] = {}
    drop: list[str] = []
    notes: list[Notification] = []

    if first and m.cancelled:
        marks["cancelled"] = "baseline"
    if not first and old["cancelled"] and not m.cancelled:
        drop.append("cancelled")
    if not first and m.cancelled and "cancelled" not in sent:
        if ended:
            marks["cancelled"] = "skipped"
        else:
            notes.append(Notification("cancelled", "cancelled", m))

    changes = diff_snapshots(old, new)
    if changes and not m.cancelled and not ended:
        key = f"changed:{_digest(new)}"
        if key not in sent:
            notes.append(Notification("changed", key, m, changes))
            drop.extend(k for k in sent if k.startswith("changed:") and k != key)

    if not m.cancelled and now < m.starts_at:
        start = _start_iso(m)
        due = [o for o in sorted(reminders, reverse=True) if now >= m.starts_at - timedelta(minutes=o)]
        unsent = [o for o in due if f"reminder:{o}:{start}" not in sent]
        if unsent:
            closest = unsent[-1]
            notes.append(Notification("reminder", f"reminder:{closest}:{start}", m))
            for offset in unsent[:-1]:
                marks[f"reminder:{offset}:{start}"] = "skipped"

    if (post_summary and ended and not m.cancelled and not seen_ended and (m.decisions or m.actions)
            and m.memo_error is None and now < m.ends_at + SUMMARY_WINDOW):
        key = f"summary:{_summary_digest(m)}"
        if key not in sent:
            notes.append(Notification("summary", key, m))

    return Plan(notifications=notes, first=first, marks=marks, drop=drop, seen_ended=seen_ended, snapshot=new,
                snapshot_changed=old != new)
