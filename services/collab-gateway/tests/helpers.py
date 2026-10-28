from datetime import datetime, timedelta, timezone

from models import MemoItem, Meeting

KST = timezone(timedelta(hours=9))


def meeting(**kw):
    start = kw.pop("starts_at", datetime(2026, 10, 15, 15, 0, tzinfo=KST))
    duration = kw.pop("duration_minutes", 60)
    base = dict(repo_id="r", repo_name="저장소", id="m1", title="DX 회의", path="docs/m.md", starts_at=start,
                ends_at=start + timedelta(minutes=duration), duration_minutes=duration, channel="dx",
                presenters=("김",), cancelled=False, url="/view/r/docs/m.md", present_url="/view/r/docs/m.md?mode=present",
                decisions=(), actions=(), memo_error=None)
    base.update(kw)
    return Meeting(**base)


def item(text, owner="", due=""):
    return MemoItem(text=text, owner=owner, due=due)


def api_meeting(**kw):
    base = {"repo_id": "r", "repo_name": "저장소", "path": "docs/m.md", "id": "m1", "title": "DX 회의",
            "starts_at": "2026-10-15T15:00:00+09:00", "ends_at": "2026-10-15T16:00:00+09:00", "duration_minutes": 60,
            "channel": "dx", "presenters": ["김"], "cancelled": False, "status": "upcoming",
            "url": "/view/r/docs/m.md", "present_url": "/view/r/docs/m.md?mode=present",
            "decisions": [], "actions": [], "memo_error": None}
    base.update(kw)
    return base
