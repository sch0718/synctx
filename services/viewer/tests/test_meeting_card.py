from datetime import datetime, timezone

import meeting_card as mc
from synctx_common.frontmatter import MeetingFrontMatter


def _meeting(**kw):
    base = dict(id="m1", title="회의", starts_at=datetime(2026, 10, 15, 15, 0, tzinfo=mc.KST), channel="dx", duration_minutes=30)
    base.update(kw)
    return MeetingFrontMatter(**base)


def test_time_range_same_day():
    assert mc.time_range_label(datetime(2026, 10, 15, 15, 0, tzinfo=mc.KST), 30) == "2026-10-15 (목) 15:00–15:30"


def test_time_range_converts_other_offsets_to_kst():
    assert mc.time_range_label(datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc), 60) == "2026-10-15 (목) 15:00–16:00"


def test_time_range_crossing_midnight():
    assert mc.time_range_label(datetime(2026, 10, 15, 23, 30, tzinfo=mc.KST), 60) == "2026-10-15 (목) 23:30–10-16 (금) 00:30"


def test_agenda_from_toc_takes_level_two_in_order_and_unescapes():
    toc = [{"level": 1, "id": "t", "name": "T", "children": [
        {"level": 2, "id": "a", "name": "A &amp; B", "children": [{"level": 3, "id": "a1", "name": "x", "children": []}]},
        {"level": 2, "id": "b", "name": "B", "children": []}]}]
    assert mc.agenda_from_toc(toc) == [("a", "A & B"), ("b", "B")]


def test_agenda_limit():
    toc = [{"level": 2, "id": f"h{i}", "name": str(i), "children": []} for i in range(25)]
    assert len(mc.agenda_from_toc(toc)) == mc.AGENDA_LIMIT


def test_card_data_shape():
    data = mc.card_data(_meeting(presenters=["리드"], cancelled=True), [])
    assert data["starts_at_iso"] == "2026-10-15T15:00:00+09:00"
    assert data["presenters"] == ["리드"] and data["cancelled"] is True and data["agenda"] == []
    assert data["time_label"] == "2026-10-15 (목) 15:00–15:30"
