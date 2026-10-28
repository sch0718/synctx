import json
from datetime import datetime, timedelta, timezone

import pytest

from adapters.base import ChannelRef, SendError
from helpers import KST, api_meeting, item, meeting
from models import Meeting
from runner import MAX_FAILURES, Runner
from state import StateStore
from synctx_common.config import CollabGatewayConfig
from synctx_common.observability import (
    collab_last_success_timestamp,
    collab_notifications_total,
    collab_unmapped_channel_total,
    collab_viewer_fetch_failures_total,
)
from viewer_client import ViewerError

START = datetime(2026, 10, 15, 15, 0, tzinfo=KST)
BASE = "https://synctx.synctx.local"


def cfg(**kw):
    base = {"enabled": True, "mattermost": {"url": "https://chat.local"},
            "channels": {"dx": {"team": "devteam", "channel": "dx-room"}}, "reminders": [1440, 10]}
    base.update(kw)
    return CollabGatewayConfig.model_validate(base)


class FakeViewer:
    def __init__(self, meetings=None, error=None):
        self.meetings = meetings or []
        self.error = error
        self.calls = 0
        self.since = None

    async def fetch_meetings(self, since=None):
        self.calls += 1
        self.since = since
        if self.error:
            raise self.error
        return self.meetings


class FakeAdapter:
    def __init__(self):
        self.sent = []
        self.fail = []  # 앞에서부터 하나씩 꺼내 쓰는 예외

    async def send(self, channel, text):
        if self.fail:
            exc = self.fail.pop(0)
            if exc is not None:
                raise exc
        self.sent.append((channel, text))

    async def aclose(self):
        pass


def counter(kind, result):
    return collab_notifications_total.labels(kind=kind, result=result)._value.get()


def make(tmp_path, meetings, config=None, adapter=None, viewer=None):
    viewer = viewer or FakeViewer(meetings)
    adapter = adapter or FakeAdapter()
    runner = Runner(config or cfg(), BASE, viewer, adapter, StateStore(tmp_path))
    return runner, viewer, adapter


def at(delta):
    return START + delta


@pytest.mark.asyncio
async def test_first_cycle_far_future_sends_nothing_but_records_baseline(tmp_path):
    runner, _, adapter = make(tmp_path, [meeting()])
    report = await runner.run_cycle(at(timedelta(days=-5)))
    assert adapter.sent == [] and report.ok is True
    assert "r/m1" in json.loads((tmp_path / StateStore.FILE_NAME).read_text())["meetings"]


@pytest.mark.asyncio
async def test_reminder_sent_to_mapped_channel_once_and_survives_restart(tmp_path):
    runner, _, adapter = make(tmp_path, [meeting()])
    now = at(timedelta(minutes=-1440))
    await runner.run_cycle(at(timedelta(days=-5)))
    await runner.run_cycle(now)
    await runner.run_cycle(now + timedelta(minutes=1))
    assert len(adapter.sent) == 1
    channel, text = adapter.sent[0]
    assert channel == ChannelRef(team="devteam", channel="dx-room") and "DX 회의" in text and BASE in text
    restarted, _, adapter2 = make(tmp_path, [meeting()])
    await restarted.run_cycle(now + timedelta(minutes=2))
    assert adapter2.sent == []


@pytest.mark.asyncio
async def test_since_window_requested_from_viewer(tmp_path):
    runner, viewer, _ = make(tmp_path, [])
    await runner.run_cycle(at(timedelta(days=0)))
    assert viewer.since == (START - timedelta(days=8)).date()


@pytest.mark.asyncio
async def test_retryable_failure_is_retried_then_recorded(tmp_path):
    adapter = FakeAdapter()
    adapter.fail = [SendError("x", retryable=True, reason="http_5xx")]
    runner, _, _ = make(tmp_path, [meeting()], adapter=adapter)
    await runner.run_cycle(at(timedelta(days=-5)))
    before_failed, before_sent = counter("reminder", "failed"), counter("reminder", "sent")
    await runner.run_cycle(at(timedelta(minutes=-1440)))
    assert adapter.sent == [] and counter("reminder", "failed") == before_failed + 1
    await runner.run_cycle(at(timedelta(minutes=-1439)))
    assert len(adapter.sent) == 1 and counter("reminder", "sent") == before_sent + 1
    await runner.run_cycle(at(timedelta(minutes=-1438)))
    assert len(adapter.sent) == 1


@pytest.mark.asyncio
async def test_gives_up_after_max_failures(tmp_path):
    adapter = FakeAdapter()
    adapter.fail = [SendError("x", retryable=False, reason="auth")] * (MAX_FAILURES + 5)
    runner, _, _ = make(tmp_path, [meeting()], adapter=adapter)
    await runner.run_cycle(at(timedelta(days=-5)))
    before = counter("reminder", "gave_up")
    for i in range(MAX_FAILURES + 3):
        await runner.run_cycle(at(timedelta(minutes=-1440 + i)))
    assert counter("reminder", "gave_up") == before + 1
    consumed = MAX_FAILURES + 5 - len(adapter.fail)
    assert consumed == MAX_FAILURES


@pytest.mark.asyncio
async def test_unexpected_adapter_exception_is_failure_not_crash(tmp_path):
    adapter = FakeAdapter()
    adapter.fail = [RuntimeError("boom")]
    runner, _, _ = make(tmp_path, [meeting()], adapter=adapter)
    await runner.run_cycle(at(timedelta(days=-5)))
    report = await runner.run_cycle(at(timedelta(minutes=-1440)))
    assert report.ok is True and report.failed == 1


@pytest.mark.asyncio
async def test_failure_of_one_meeting_does_not_block_another(tmp_path):
    adapter = FakeAdapter()
    adapter.fail = [SendError("x", retryable=True, reason="network")]
    other = meeting(id="m2", title="다른 회의")
    runner, _, _ = make(tmp_path, [meeting(), other], adapter=adapter)
    await runner.run_cycle(at(timedelta(days=-5)))
    await runner.run_cycle(at(timedelta(minutes=-1440)))
    assert len(adapter.sent) == 1 and "다른 회의" in adapter.sent[0][1]


@pytest.mark.asyncio
async def test_changed_notice_retried_until_sent_and_snapshot_updated_after(tmp_path):
    adapter = FakeAdapter()
    runner, viewer, _ = make(tmp_path, [meeting()], adapter=adapter)
    await runner.run_cycle(at(timedelta(days=-5)))
    viewer.meetings = [meeting(title="새 제목")]
    adapter.fail = [SendError("x", retryable=True, reason="network")]
    await runner.run_cycle(at(timedelta(days=-4)))
    assert adapter.sent == []
    await runner.run_cycle(at(timedelta(days=-4, minutes=1)))
    assert len(adapter.sent) == 1 and "변경" in adapter.sent[0][1] and "새 제목" in adapter.sent[0][1]
    await runner.run_cycle(at(timedelta(days=-4, minutes=2)))
    assert len(adapter.sent) == 1


@pytest.mark.asyncio
async def test_cancel_flow(tmp_path):
    runner, viewer, adapter = make(tmp_path, [meeting()])
    await runner.run_cycle(at(timedelta(days=-5)))
    viewer.meetings = [meeting(cancelled=True)]
    await runner.run_cycle(at(timedelta(days=-4)))
    await runner.run_cycle(at(timedelta(days=-3)))
    assert len(adapter.sent) == 1 and "취소" in adapter.sent[0][1]


@pytest.mark.asyncio
async def test_summary_flow_only_when_enabled(tmp_path):
    m = meeting(decisions=(item("채택"),))
    runner, viewer, adapter = make(tmp_path, [meeting()], config=cfg(post_summary=True))
    await runner.run_cycle(at(timedelta(days=-5)))
    viewer.meetings = [m]
    await runner.run_cycle(at(timedelta(hours=3)))
    await runner.run_cycle(at(timedelta(hours=4)))
    assert len(adapter.sent) == 1 and "채택" in adapter.sent[0][1]
    off, v2, a2 = make(tmp_path / "off", [meeting()])
    await off.run_cycle(at(timedelta(days=-5)))
    v2.meetings = [m]
    await off.run_cycle(at(timedelta(hours=3)))
    assert a2.sent == []


@pytest.mark.asyncio
async def test_unmapped_channel_skipped_counted_and_not_persisted(tmp_path):
    runner, _, adapter = make(tmp_path, [meeting(channel="unknown")])
    before = collab_unmapped_channel_total._value.get()
    await runner.run_cycle(at(timedelta(minutes=-5)))
    await runner.run_cycle(at(timedelta(minutes=-4)))
    assert adapter.sent == [] and collab_unmapped_channel_total._value.get() == before + 2
    state = (tmp_path / StateStore.FILE_NAME)
    assert not state.exists() or "r/m1" not in json.loads(state.read_text())["meetings"]


@pytest.mark.asyncio
async def test_viewer_failure_counts_and_sends_nothing(tmp_path):
    viewer = FakeViewer(error=ViewerError("down"))
    runner, _, adapter = make(tmp_path, [], viewer=viewer)
    before = collab_viewer_fetch_failures_total._value.get()
    last = collab_last_success_timestamp._value.get()
    report = await runner.run_cycle(at(timedelta(days=-1)))
    assert report.ok is False and adapter.sent == []
    assert collab_viewer_fetch_failures_total._value.get() == before + 1
    assert collab_last_success_timestamp._value.get() == last


@pytest.mark.asyncio
async def test_success_updates_last_success_timestamp(tmp_path):
    runner, _, _ = make(tmp_path, [])
    collab_last_success_timestamp.set(0)
    await runner.run_cycle(at(timedelta(days=-1)))
    assert collab_last_success_timestamp._value.get() > 0


@pytest.mark.asyncio
async def test_cycle_skipped_when_state_locked_by_other_instance(tmp_path):
    other = StateStore(tmp_path)
    assert other.acquire()
    runner, viewer, adapter = make(tmp_path, [meeting()])
    report = await runner.run_cycle(at(timedelta(minutes=-5)))
    assert report.ok is False and viewer.calls == 0 and adapter.sent == []
    other.release()
    assert (await runner.run_cycle(at(timedelta(minutes=-5)))).ok is True


@pytest.mark.asyncio
async def test_lock_released_after_cycle_even_on_error(tmp_path):
    runner, _, _ = make(tmp_path, [], viewer=FakeViewer(error=ViewerError("x")))
    await runner.run_cycle(at(timedelta(days=-1)))
    probe = StateStore(tmp_path)
    assert probe.acquire()
    probe.release()


@pytest.mark.asyncio
async def test_state_written_after_each_send(tmp_path):
    runner, _, adapter = make(tmp_path, [meeting(), meeting(id="m2")])
    await runner.run_cycle(at(timedelta(days=-5)))
    await runner.run_cycle(at(timedelta(minutes=-1440)))
    data = json.loads((tmp_path / StateStore.FILE_NAME).read_text())["meetings"]
    assert all(any(k.startswith("reminder:1440") for k in data[key]["sent"]) for key in ("r/m1", "r/m2"))


@pytest.mark.asyncio
async def test_current_meetings_uses_fresh_polled_data_without_refetch(tmp_path):
    runner, viewer, _ = make(tmp_path, [meeting()])
    now = at(timedelta(days=-5))
    await runner.run_cycle(now)
    calls = viewer.calls
    result = await runner.current_meetings(now + timedelta(seconds=30), timedelta(minutes=3))
    assert [m.id for m in result] == ["m1"] and viewer.calls == calls


@pytest.mark.asyncio
async def test_current_meetings_refetches_when_stale_or_never_polled(tmp_path):
    runner, viewer, _ = make(tmp_path, [meeting()])
    now = at(timedelta(days=-5))
    assert [m.id for m in await runner.current_meetings(now, timedelta(minutes=3))] == ["m1"]
    calls = viewer.calls
    viewer.meetings = [meeting(id="m2")]
    result = await runner.current_meetings(now + timedelta(minutes=10), timedelta(minutes=3))
    assert [m.id for m in result] == ["m2"] and viewer.calls == calls + 1
    assert viewer.since == (now + timedelta(minutes=10) - timedelta(days=8)).date()


@pytest.mark.asyncio
async def test_current_meetings_none_on_viewer_error(tmp_path):
    runner, _, _ = make(tmp_path, [], viewer=FakeViewer(error=ViewerError("down")))
    assert await runner.current_meetings(at(timedelta(days=-5)), timedelta(minutes=3)) is None


@pytest.mark.asyncio
async def test_current_meetings_none_on_timeout(tmp_path):
    import asyncio

    class SlowViewer(FakeViewer):
        async def fetch_meetings(self, since=None):
            await asyncio.sleep(1)
            return []

    runner, _, _ = make(tmp_path, [], viewer=SlowViewer())
    assert await runner.current_meetings(at(timedelta(days=-5)), timedelta(minutes=3), timeout=0.05) is None


@pytest.mark.asyncio
async def test_failed_poll_keeps_previous_snapshot_for_commands(tmp_path):
    runner, viewer, _ = make(tmp_path, [meeting()])
    now = at(timedelta(days=-5))
    await runner.run_cycle(now)
    viewer.error = ViewerError("down")
    await runner.run_cycle(now + timedelta(seconds=30))
    result = await runner.current_meetings(now + timedelta(seconds=40), timedelta(minutes=3))
    assert [m.id for m in result] == ["m1"]
