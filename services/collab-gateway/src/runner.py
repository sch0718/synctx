"""한 번의 폴링 주기: Viewer에서 회의를 읽어 보낼 알림을 정하고 보내고 상태를 남긴다."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from adapters.base import ChannelRef, MessengerAdapter, SendError
import messages
from models import EvidenceResult, Meeting, SearchResult
from scheduler import Notification, plan_meeting
from gateway_state import StateStore
from synctx_common.config import CollabGatewayConfig
from synctx_common.observability import (
    collab_last_success_timestamp,
    collab_notifications_total,
    collab_unmapped_channel_total,
    collab_viewer_fetch_failures_total,
)
from viewer_client import ViewerError

logger = logging.getLogger(__name__)

MAX_FAILURES = 10
FETCH_WINDOW = timedelta(days=8)
KINDS = ("reminder", "changed", "cancelled", "summary")
RESULTS = ("sent", "failed", "gave_up")
KST = timezone(timedelta(hours=9))

for _kind in KINDS:
    for _result in RESULTS:
        collab_notifications_total.labels(kind=_kind, result=_result)


@dataclass
class CycleReport:
    ok: bool
    fetched: int = 0
    sent: int = 0
    failed: int = 0
    unmapped: int = 0


class Runner:
    def __init__(self, config: CollabGatewayConfig, public_base_url: str, viewer, adapter: MessengerAdapter,
                 store: StateStore):
        self._config = config
        self._base_url = public_base_url
        self._viewer = viewer
        self._adapter = adapter
        self._store = store
        self._warned_channels: set[str] = set()
        self._latest: Optional[tuple[list[Meeting], datetime]] = None

    def _text(self, note: Notification) -> str:
        m = note.meeting
        if note.kind == "reminder":
            return messages.reminder_message(m, self._base_url)
        if note.kind == "changed":
            return messages.changed_message(m, note.changes, self._base_url)
        if note.kind == "cancelled":
            return messages.cancelled_message(m, self._base_url)
        return messages.summary_message(m, self._base_url)

    async def _deliver(self, note: Notification, ref: ChannelRef, entry: dict, now: datetime,
                       report: CycleReport) -> None:
        sent, failures = entry["sent"], entry["failures"]
        try:
            await self._adapter.send(ref, self._text(note))
        except SendError as exc:
            reason = exc.reason
        except Exception as exc:
            reason = type(exc).__name__
        else:
            sent[note.key] = now.isoformat()
            failures.pop(note.key, None)
            collab_notifications_total.labels(kind=note.kind, result="sent").inc()
            report.sent += 1
            return
        failures[note.key] = failures.get(note.key, 0) + 1
        if failures[note.key] >= MAX_FAILURES:
            sent[note.key] = "gave_up"
            failures.pop(note.key, None)
            collab_notifications_total.labels(kind=note.kind, result="gave_up").inc()
            logger.error("collab_notification_gave_up meeting=%s kind=%s reason=%s", note.meeting.key, note.kind, reason)
        else:
            collab_notifications_total.labels(kind=note.kind, result="failed").inc()
            logger.warning("collab_notification_failed meeting=%s kind=%s reason=%s attempt=%d", note.meeting.key,
                           note.kind, reason, failures[note.key])
        report.failed += 1

    async def _process(self, m: Meeting, ref: ChannelRef, now: datetime, report: CycleReport) -> None:
        existing = self._store.get(m.key)
        plan = plan_meeting(m, existing, reminders=self._config.reminders, post_summary=self._config.post_summary,
                            now=now)
        entry = existing if existing is not None else {"snapshot": plan.snapshot, "sent": {}, "failures": {}}
        entry.setdefault("sent", {})
        entry.setdefault("failures", {})
        entry["sent"].update(plan.marks)
        for key in plan.drop:
            entry["sent"].pop(key, None)
        entry["seen_ended"] = plan.seen_ended
        changed_pending = False
        for note in plan.notifications:
            await self._deliver(note, ref, entry, now, report)
            if note.kind == "changed" and note.key not in entry["sent"]:
                changed_pending = True
            self._store.put(m.key, entry, now)
            self._store.save()
        if not changed_pending:
            entry["snapshot"] = plan.snapshot
        self._store.put(m.key, entry, now)

    @staticmethod
    def _since(now: datetime):
        return (now - FETCH_WINDOW).astimezone(KST).date()

    async def current_meetings(self, now: datetime, max_age: timedelta, timeout: float = 2.0) -> Optional[list[Meeting]]:
        """명령 응답용 회의 목록. 폴링이 읽어 둔 것이 신선하면 그것을, 아니면 짧은 기한으로 직접 읽는다(실패하면 None)."""
        if self._latest is not None and now - self._latest[1] <= max_age:
            return self._latest[0]
        try:
            meetings = await asyncio.wait_for(self._viewer.fetch_meetings(since=self._since(now)), timeout)
        except (ViewerError, asyncio.TimeoutError):
            return None
        self._latest = (meetings, now)
        return meetings

    async def search_documents(self, query: str, repo_ids: set[str]) -> list[SearchResult]:
        return await self._viewer.search_documents(query, repo_ids)

    async def fetch_evidence(self, repo_id: str, meeting_id: str) -> EvidenceResult:
        return await self._viewer.fetch_evidence(repo_id, meeting_id)

    async def run_cycle(self, now: Optional[datetime] = None) -> CycleReport:
        now = now or datetime.now(timezone.utc)
        if not self._store.acquire():
            logger.warning("collab_cycle_skipped: 다른 프로세스가 상태 파일을 쓰고 있습니다")
            return CycleReport(ok=False)
        try:
            try:
                meetings = await self._viewer.fetch_meetings(since=self._since(now))
            except ViewerError as exc:
                collab_viewer_fetch_failures_total.inc()
                logger.warning("collab_viewer_fetch_failed error=%s", exc)
                return CycleReport(ok=False)
            collab_last_success_timestamp.set(time.time())
            self._latest = (meetings, now)
            report = CycleReport(ok=True, fetched=len(meetings))
            for m in meetings:
                mapping = self._config.channels.get(m.channel)
                if mapping is None:
                    collab_unmapped_channel_total.inc()
                    report.unmapped += 1
                    if m.channel not in self._warned_channels:
                        self._warned_channels.add(m.channel)
                        logger.warning("collab_unmapped_channel channel=%s", m.channel)
                    continue
                await self._process(m, ChannelRef(mapping.team, mapping.channel), now, report)
            self._store.prune(now)
            self._store.save()
            return report
        finally:
            self._store.release()
