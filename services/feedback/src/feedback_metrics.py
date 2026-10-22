"""Feedback 메트릭을 sync-state에 누적 보존하고 Pushgateway로 보낸다."""

import logging
import os
from pathlib import Path

from synctx_common.metrics_push import PUSHGATEWAY_URL_ENV, CumulativeMetrics, push_totals
from synctx_common.observability import (
    feedback_events_total,
    feedback_last_success_timestamp,
    feedback_mail_failures_total,
    feedback_redacted_fields_total,
)

logger = logging.getLogger(__name__)

PUSH_JOB = "synctx-feedback"

FEEDBACK_METRICS = (
    feedback_events_total,
    feedback_redacted_fields_total,
    feedback_mail_failures_total,
    feedback_last_success_timestamp,
)

EVENT_RESULTS = ("sent", "skipped", "mail_error", "redaction_error")

# Alert가 increase()로 보는 계열
ZERO_SERIES = [
    ("synctx_feedback_events_total", (("result", result),)) for result in EVENT_RESULTS
] + [
    ("synctx_feedback_mail_failures_total", ()),
    ("synctx_feedback_redacted_fields_total", ()),
]


class FeedbackMetrics(CumulativeMetrics):
    """한 Feedback 실행 동안의 메트릭 증가분을 sync-state 누적값에 반영한다."""

    def __init__(self, state_dir):
        # Sync의 저장소별 파일(metrics/{repo_id}.json)과 겹치지 않게 Feedback 상태 파일 옆에 둔다
        super().__init__(Path(state_dir) / "feedback_metrics.json", FEEDBACK_METRICS, zero_series=ZERO_SERIES)

    def publish(self) -> None:
        """누적값을 저장하고 Pushgateway로 보낸다. 실패는 로그만 남기고 반출 결과에 영향을 주지 않는다."""
        try:
            totals = self.commit()
        except Exception as e:
            logger.error(f"Failed to save feedback metrics: {e}")
            return
        url = os.getenv(PUSHGATEWAY_URL_ENV)
        if not url:
            return
        try:
            push_totals(url, PUSH_JOB, FEEDBACK_METRICS, totals)
        except Exception as e:
            logger.warning(f"Failed to push feedback metrics to Pushgateway: {e}")
            return
        try:
            self.mark_pushed()
        except Exception as e:
            logger.error(f"Failed to record feedback metrics push: {e}")
