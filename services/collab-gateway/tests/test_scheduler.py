from datetime import datetime, timedelta

import scheduler
from helpers import KST, item, meeting

START = datetime(2026, 10, 15, 15, 0, tzinfo=KST)
REM = [1440, 10]


def plan(m, entry=None, now=None, reminders=None, summary=False):
    return scheduler.plan_meeting(m, entry, reminders=REM if reminders is None else reminders, post_summary=summary,
                                  now=now or START - timedelta(days=3))


def entry_for(m, **kw):
    base = {"snapshot": scheduler.snapshot_of(m), "sent": {}, "failures": {}, "seen_ended": False}
    base.update(kw)
    return base


def kinds(p):
    return [n.kind for n in p.notifications]


class TestSnapshot:
    def test_fields(self):
        snap = scheduler.snapshot_of(meeting(presenters=("김", "이")))
        assert snap == {"title": "DX 회의", "starts_at": "2026-10-15T15:00:00+09:00", "duration_minutes": 60,
                        "presenters": ["김", "이"], "cancelled": False}

    def test_utc_start_normalized_to_kst(self):
        from datetime import timezone
        m = meeting(starts_at=datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc))
        assert scheduler.snapshot_of(m)["starts_at"] == "2026-10-15T15:00:00+09:00"

    def test_diff_labels(self):
        old = scheduler.snapshot_of(meeting())
        new = scheduler.snapshot_of(meeting(title="새 제목", presenters=("이",), duration_minutes=90,
                                            starts_at=START + timedelta(days=1)))
        labels = [c[0] for c in scheduler.diff_snapshots(old, new)]
        assert labels == ["제목", "일시", "발표자"]

    def test_same_instant_in_other_offset_is_not_a_change(self):
        old = scheduler.snapshot_of(meeting())
        old["starts_at"] = "2026-10-15T06:00:00+00:00"
        assert scheduler.diff_snapshots(old, scheduler.snapshot_of(meeting())) == []

    def test_old_snapshot_in_other_offset_is_shown_in_kst(self):
        old = scheduler.snapshot_of(meeting())
        old["starts_at"] = "2026-10-15T06:00:00+00:00"
        new = scheduler.snapshot_of(meeting(duration_minutes=90))
        assert scheduler.diff_snapshots(old, new) == [("일시", "2026-10-15 15:00 (60분)", "2026-10-15 15:00 (90분)")]

    def test_seconds_only_change_is_not_shown_as_identical(self):
        old = scheduler.snapshot_of(meeting())
        new = scheduler.snapshot_of(meeting(starts_at=START + timedelta(seconds=30)))
        [(label, before, after)] = scheduler.diff_snapshots(old, new)
        assert label == "일시" and before != after

    def test_diff_includes_cancel_toggle(self):
        old = scheduler.snapshot_of(meeting(cancelled=True))
        new = scheduler.snapshot_of(meeting())
        assert [c[0] for c in scheduler.diff_snapshots(old, new)] == ["상태"]


class TestFirstSight:
    def test_far_future_meeting_only_baseline(self):
        p = plan(meeting())
        assert p.notifications == [] and p.first is True

    def test_no_changed_notice_on_first_sight(self):
        assert "changed" not in kinds(plan(meeting(), now=START - timedelta(hours=2)))

    def test_cancelled_at_first_sight_marked_baseline_without_notice(self):
        p = plan(meeting(cancelled=True))
        assert p.notifications == [] and p.marks == {"cancelled": "baseline"}

    def test_ended_at_first_sight_never_posts_summary(self):
        m = meeting(decisions=(item("결정"),))
        p = plan(m, now=START + timedelta(days=1), summary=True)
        assert p.notifications == [] and p.seen_ended is True
        again = plan(m, entry_for(m, seen_ended=True), now=START + timedelta(days=2), summary=True)
        assert again.notifications == []


class TestReminders:
    def key(self, offset, m=None):
        return f"reminder:{offset}:{scheduler.snapshot_of(m or meeting())['starts_at']}"

    def test_before_first_window_nothing(self):
        m = meeting()
        assert plan(m, entry_for(m), now=START - timedelta(minutes=1441)).notifications == []

    def test_day_before_sent_once(self):
        m = meeting()
        p = plan(m, entry_for(m), now=START - timedelta(minutes=1440))
        assert kinds(p) == ["reminder"] and p.notifications[0].key == self.key(1440)

    def test_not_resent_when_key_recorded(self):
        m = meeting()
        e = entry_for(m, sent={self.key(1440): "t"})
        assert plan(m, e, now=START - timedelta(hours=5)).notifications == []

    def test_ten_minutes_before_after_day_before_sent(self):
        m = meeting()
        e = entry_for(m, sent={self.key(1440): "t"})
        p = plan(m, e, now=START - timedelta(minutes=10))
        assert [n.key for n in p.notifications] == [self.key(10)]

    def test_after_downtime_only_closest_sent_and_older_skipped(self):
        m = meeting()
        p = plan(m, entry_for(m), now=START - timedelta(minutes=5))
        assert [n.key for n in p.notifications] == [self.key(10)]
        assert p.marks == {self.key(1440): "skipped"}

    def test_no_reminder_after_start(self):
        m = meeting()
        assert plan(m, entry_for(m), now=START).notifications == []

    def test_cancelled_meeting_gets_no_reminder(self):
        m = meeting(cancelled=True)
        e = entry_for(m, sent={"cancelled": "t"})
        assert plan(m, e, now=START - timedelta(minutes=5)).notifications == []

    def test_reschedule_gets_fresh_reminder_keys(self):
        old = meeting()
        moved = meeting(starts_at=START + timedelta(days=2))
        e = entry_for(old, sent={self.key(1440, old): "t", self.key(10, old): "t"})
        p = plan(moved, e, now=START + timedelta(days=2) - timedelta(minutes=10))
        assert [n.key for n in p.notifications if n.kind == "reminder"] == [self.key(10, moved)]

    def test_empty_reminders_config(self):
        m = meeting()
        assert plan(m, entry_for(m), now=START - timedelta(minutes=5), reminders=[]).notifications == []

    def test_first_sight_inside_window_sends_closest(self):
        p = plan(meeting(), now=START - timedelta(minutes=5))
        assert kinds(p) == ["reminder"] and p.notifications[0].key == self.key(10)


class TestCancelled:
    def test_cancel_after_seen_notifies_once(self):
        old = meeting()
        m = meeting(cancelled=True)
        p = plan(m, entry_for(old))
        assert kinds(p) == ["cancelled"] and p.notifications[0].key == "cancelled"
        again = plan(m, entry_for(old, sent={"cancelled": "t"}))
        assert again.notifications == []

    def test_cancel_after_end_marks_without_notice(self):
        old = meeting()
        p = plan(meeting(cancelled=True), entry_for(old), now=START + timedelta(days=1))
        assert p.notifications == [] and p.marks == {"cancelled": "skipped"}

    def test_cancelled_suppresses_changed(self):
        old = meeting()
        m = meeting(cancelled=True, title="다른 제목")
        assert kinds(plan(m, entry_for(old))) == ["cancelled"]

    def test_uncancel_drops_marker_and_notifies_changed(self):
        old = meeting(cancelled=True)
        m = meeting()
        p = plan(m, entry_for(old, sent={"cancelled": "t"}))
        assert p.drop == ["cancelled"] and kinds(p) == ["changed"]
        assert p.notifications[0].changes[0][0] == "상태"


class TestChanged:
    def test_title_change_notifies_with_diff(self):
        old = meeting()
        m = meeting(title="새 제목")
        p = plan(m, entry_for(old))
        n = p.notifications[0]
        assert n.kind == "changed" and n.changes == [("제목", "DX 회의", "새 제목")]
        assert n.key.startswith("changed:")

    def test_same_snapshot_no_notice(self):
        m = meeting()
        assert plan(m, entry_for(m)).notifications == []

    def test_key_deduplicates_same_new_state(self):
        old = meeting()
        m = meeting(title="새 제목")
        key = plan(m, entry_for(old)).notifications[0].key
        assert plan(m, entry_for(old, sent={key: "t"})).notifications == []

    def test_returning_to_an_earlier_state_notifies_again(self):
        a, b = meeting(title="A"), meeting(title="B")
        first = plan(b, entry_for(a)).notifications[0]
        back = plan(a, entry_for(b, sent={first.key: "t"}))
        assert kinds(back) == ["changed"] and back.drop == [first.key]
        again = plan(b, entry_for(a, sent={back.notifications[0].key: "t"}))
        assert kinds(again) == ["changed"] and again.notifications[0].key == first.key

    def test_changed_after_end_is_silent(self):
        old = meeting()
        p = plan(meeting(title="새 제목"), entry_for(old), now=START + timedelta(days=1))
        assert p.notifications == [] and p.snapshot_changed is True

    def test_start_change_also_emits_new_reminder_when_due(self):
        old = meeting()
        m = meeting(starts_at=START + timedelta(minutes=30))
        p = plan(m, entry_for(old), now=START + timedelta(minutes=25))
        assert kinds(p) == ["changed", "reminder"]


class TestSummary:
    def ended(self, **kw):
        return meeting(decisions=(item("결정"),), **kw)

    def test_posted_after_end_when_enabled(self):
        m = self.ended()
        p = plan(m, entry_for(m), now=START + timedelta(hours=2), summary=True)
        assert kinds(p) == ["summary"] and p.notifications[0].key.startswith("summary:")

    def test_disabled_by_default(self):
        m = self.ended()
        assert plan(m, entry_for(m), now=START + timedelta(hours=2)).notifications == []

    def test_not_before_end(self):
        m = self.ended()
        assert plan(m, entry_for(m), now=START + timedelta(minutes=30), summary=True).notifications == []

    def test_skipped_without_items_or_with_memo_error_or_cancelled(self):
        for m in (meeting(), self.ended(memo_error="깨짐"), self.ended(cancelled=True)):
            e = entry_for(m, sent={"cancelled": "t"} if m.cancelled else {})
            assert plan(m, e, now=START + timedelta(hours=2), summary=True).notifications == []

    def test_not_resent_for_same_content_but_resent_when_content_changes(self):
        m = self.ended()
        first = plan(m, entry_for(m), now=START + timedelta(hours=2), summary=True).notifications[0]
        assert plan(m, entry_for(m, sent={first.key: "t"}), now=START + timedelta(hours=3), summary=True).notifications == []
        edited = meeting(decisions=(item("결정"), item("추가")))
        again = plan(edited, entry_for(m, sent={first.key: "t"}), now=START + timedelta(hours=3), summary=True)
        assert kinds(again) == ["summary"] and again.notifications[0].key != first.key

    def test_expires_after_seven_days(self):
        m = self.ended()
        assert plan(m, entry_for(m), now=START + timedelta(days=9), summary=True).notifications == []


def test_summary_digest_separates_decisions_from_actions():
    as_decision = meeting(decisions=(item("점검", "김", "2026-10-20"),))
    as_action = meeting(actions=(item("점검", "김", "2026-10-20"),))
    assert scheduler._summary_digest(as_decision) != scheduler._summary_digest(as_action)
