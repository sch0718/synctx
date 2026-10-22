"""Sync 메트릭을 저장소별로 누적 보존하고 Pushgateway로 보낸다."""

import json
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List

from prometheus_client import delete_from_gateway

from synctx_common import metrics_push as cumulative
from synctx_common.metrics_push import PUSHGATEWAY_URL_ENV, PUSH_TIMEOUT_SECONDS, CumulativeMetrics, SampleKey
from synctx_common.observability import (
    html_bundle_total,
    sync_failures_total,
    sync_items_total,
    sync_last_success_timestamp,
    sync_runs_total,
)

PUSH_JOB = "synctx-sync"

REPO_METRICS = (
    sync_runs_total,
    sync_items_total,
    sync_failures_total,
    sync_last_success_timestamp,
    html_bundle_total,
)

# Alert가 increase()·rate()로 보는 계열
SYNC_TARGETS = ("xwiki", "openproject")
SYNC_ACTIONS = ("A", "M", "D")
SYNC_ERROR_TYPES = ("auth_error", "transient_error", "conflict_error", "validation_error", "mapping_error", "html_bundle")
BUNDLE_REJECTIONS = ("disallowed_host", "too_large")


def _zero_series(repo_id: str) -> List[SampleKey]:
    keys = [
        ("synctx_sync_failures_total", (("error_type", error_type), ("repo_id", repo_id), ("target", target)))
        for target in SYNC_TARGETS
        for error_type in SYNC_ERROR_TYPES
    ]
    keys += [
        ("synctx_sync_items_total", (("action", action), ("repo_id", repo_id), ("target", target)))
        for target in SYNC_TARGETS
        for action in SYNC_ACTIONS
    ]
    keys += [
        ("synctx_html_bundle_total", (("repo_id", repo_id), ("result", result), ("service", "sync")))
        for result in BUNDLE_REJECTIONS
    ]
    return keys


class RepoMetrics(CumulativeMetrics):
    """한 저장소 실행 동안의 메트릭 증가분을 sync-state 누적값에 반영한다."""

    def __init__(self, state_dir: str, repo_id: str):
        self.repo_id = repo_id
        super().__init__(
            # 상태 파일({repo_id}.json)과 이름이 겹치지 않게 하위 디렉터리에 둔다
            Path(state_dir) / "metrics" / f"{repo_id}.json",
            REPO_METRICS,
            labels={"repo_id": repo_id},
            zero_series=_zero_series(repo_id),
            # 한 번도 성공하지 못한 저장소도 SynctxSyncNoSuccess가 평가되도록 감시 시작 시각을 기준으로 둔다
            watch_start=("synctx_sync_last_success_timestamp", (("repo_id", repo_id),)),
        )


def push_totals(url: str, repo_id: str, totals: Dict[SampleKey, float]) -> None:
    """저장소 그룹(job, repo_id)을 누적값으로 통째로 바꾼다."""
    cumulative.push_totals(url, PUSH_JOB, REPO_METRICS, totals, grouping_key={"repo_id": repo_id})


def prune_removed_repos(url: str, repo_ids: Iterable[str]) -> List[str]:
    """설정에서 빠진 저장소의 그룹을 지운다. 남겨 두면 마지막 성공 시각이 멈춰 SynctxSyncNoSuccess가 계속 울린다."""
    keep = set(repo_ids)
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/v1/metrics", timeout=PUSH_TIMEOUT_SECONDS) as response:
        groups = json.load(response).get("data", [])
    removed = []
    for group in groups:
        labels = group.get("labels", {})
        repo_id = labels.get("repo_id")
        if labels.get("job") == PUSH_JOB and repo_id and repo_id not in keep:
            delete_from_gateway(url, job=PUSH_JOB, grouping_key={"repo_id": repo_id}, timeout=PUSH_TIMEOUT_SECONDS)
            removed.append(repo_id)
    return removed
