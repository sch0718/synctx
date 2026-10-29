from datetime import datetime

import messages
from helpers import KST, item, meeting

BASE = "https://synctx.synctx.local"


def test_time_label_same_day_and_cross_midnight():
    assert messages.time_label(datetime(2026, 10, 15, 15, 0, tzinfo=KST), 30) == "2026-10-15 (목) 15:00–15:30"
    assert messages.time_label(datetime(2026, 10, 15, 23, 30, tzinfo=KST), 60) == "2026-10-15 (목) 23:30–10-16 (금) 00:30"


def test_time_label_converts_to_kst():
    from datetime import timezone
    assert messages.time_label(datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc), 60) == "2026-10-15 (목) 15:00–16:00"


def test_escape_neutralizes_mentions_and_markdown():
    out = messages.escape("@channel @all [x](http://e) *b* `c` <i> # | _u_ ~s~ ![i]")
    assert "@channel" not in out and "@all" not in out
    assert "[x](" not in out and "<i>" not in out
    assert out.count("@") == 2


def test_escape_flattens_newlines_and_controls_and_truncates():
    assert "\n" not in messages.escape("a\nb\r\nc\x00d")
    long = messages.escape("가" * 500, limit=50)
    assert len(long) <= 50 and long.endswith("…")


def test_reminder_message_has_links_and_facts():
    text = messages.reminder_message(meeting(), BASE)
    assert "DX 회의" in text and "2026-10-15 (목) 15:00–16:00" in text and "김" in text and "저장소" in text
    assert f"({BASE}/view/r/docs/m.md)" in text and f"({BASE}/view/r/docs/m.md?mode=present)" in text


def test_reminder_without_presenters_omits_line():
    assert "발표자" not in messages.reminder_message(meeting(presenters=()), BASE)


def test_reminder_escapes_title_and_cannot_inject_link():
    text = messages.reminder_message(meeting(title="[클릭](http://evil) @here"), BASE)
    assert "](http://evil" not in text
    assert "@here" not in text


def test_link_target_url_encoded_chars_pass_through():
    text = messages.reminder_message(meeting(url="/view/r/%ED%95%9C.md", present_url="/view/r/%ED%95%9C.md?mode=present"), BASE)
    assert f"({BASE}/view/r/%ED%95%9C.md)" in text


def test_base_url_trailing_slash_not_doubled():
    assert "https://x//view" not in messages.reminder_message(meeting(), "https://x/")


def test_changed_message_lists_changes():
    text = messages.changed_message(meeting(), [("일시", "10-15 15:00", "10-16 10:00"), ("발표자", "김", "이")], BASE)
    assert "변경" in text and "일시" in text and "10-16 10:00" in text and "이" in text


def test_changed_message_does_not_repeat_changed_fields_below():
    m = meeting()
    text = messages.changed_message(m, [("일시", "2026-10-15 14:00 (60분)", "2026-10-15 15:00 (60분)")], BASE)
    assert text.count("- 일시:") == 1 and "→" in text
    assert "- 저장소:" in text


def test_cancelled_message():
    text = messages.cancelled_message(meeting(cancelled=True), BASE)
    assert "취소" in text and "DX 회의" in text


def test_summary_message_decisions_and_actions_with_limits():
    m = meeting(decisions=tuple(item(f"결정 {i}") for i in range(15)),
                actions=(item("점검", "김", "2026-10-20"), item("보고")))
    text = messages.summary_message(m, BASE)
    assert "결정 0" in text and "결정 9" in text and "결정 10" not in text and "외 5건" in text
    assert "점검" in text and "담당 김" in text and "기한 2026-10-20" in text and "보고" in text


def test_summary_escapes_memo_text():
    text = messages.summary_message(meeting(decisions=(item("@all 확인"),)), BASE)
    assert "@all" not in text
