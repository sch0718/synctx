"""Sync 메트릭을 저장소별로 누적 보존하고 Pushgateway로 보낸다.

CronJob 프로세스는 실행마다 카운터가 0에서 시작하므로, 이번 실행의 증가분을
sync-state의 누적값에 더해 Prometheus가 단조 증가 카운터로 보게 한다.
"""

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from prometheus_client import CollectorRegistry, delete_from_gateway, push_to_gateway
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from synctx_common.observability import (
    html_bundle_total,
    sync_failures_total,
    sync_items_total,
    sync_last_success_timestamp,
    sync_runs_total,
)

logger = logging.getLogger(__name__)

PUSHGATEWAY_URL_ENV = "SYNCTX_PUSHGATEWAY_URL"
PUSH_JOB = "synctx-sync"
PUSH_TIMEOUT_SECONDS = 5
# 0을 담은 push 뒤 Prometheus가 한 번 이상 수집할 시간(ServiceMonitor interval 15s의 여러 배)
BASELINE_SCRAPE_GRACE_SECONDS = 60

REPO_METRICS = (
    sync_runs_total,
    sync_items_total,
    sync_failures_total,
    sync_last_success_timestamp,
    html_bundle_total,
)

SampleKey = Tuple[str, Tuple[Tuple[str, str], ...]]

# Alert가 increase()·rate()로 보는 계열. 처음 생긴 계열은 이전 값이 없어 증가로 잡히지 않으므로,
# 0을 먼저 Pushgateway에 보내고 그동안의 증가분은 그 push가 수집될 시간이 지난 뒤의 실행에서 더한다.
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


def _families() -> Dict[str, Tuple[str, str, str]]:
    """sample 이름 → (family 이름, type, 설명)."""
    names = {}
    for metric in REPO_METRICS:
        for family in metric.describe():
            sample = f"{family.name}_total" if family.type == "counter" else family.name
            names[sample] = (family.name, family.type, family.documentation)
    return names


def _snapshot(repo_id: str) -> Dict[SampleKey, float]:
    families = _families()
    values = {}
    for metric in REPO_METRICS:
        for family in metric.collect():
            for sample in family.samples:
                if sample.name in families and sample.labels.get("repo_id") == repo_id:
                    values[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
    return values


class RepoMetrics:
    """한 저장소 실행 동안의 메트릭 증가분을 sync-state 누적값에 반영한다."""

    def __init__(self, state_dir: str, repo_id: str):
        self.repo_id = repo_id
        # 상태 파일({repo_id}.json)과 이름이 겹치지 않게 하위 디렉터리에 둔다
        self.path = Path(state_dir) / "metrics" / f"{repo_id}.json"
        self._start = _snapshot(repo_id)
        self._started_at = time.time()

    def load(self) -> Dict[SampleKey, float]:
        return self._read()["samples"]

    def _read(self) -> Dict:
        """samples: 내보낼 누적값, pending: 0을 보낸 뒤 더할 증가분, pushed_at: 0을 처음 push한 시각."""
        empty = {"samples": {}, "pending": {}, "pushed_at": None}
        if not self.path.exists():
            return empty
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Failed to read sync metrics {self.path}: {e}")
            return empty
        return {
            "samples": _decode(data.get("samples", [])),
            "pending": _decode(data.get("pending", [])),
            "pushed_at": data.get("pushed_at"),
        }

    def commit(self) -> Dict[SampleKey, float]:
        """이번 실행의 증가분을 누적값에 더해 저장하고 Pushgateway로 보낼 누적값을 돌려준다."""
        families = _families()
        saved = self._read()
        totals = saved["samples"]
        pending = {}
        pushed_at = saved["pushed_at"]
        if pushed_at is not None and self._started_at - pushed_at >= BASELINE_SCRAPE_GRACE_SECONDS:
            for key, delta in saved["pending"].items():
                totals[key] = totals.get(key, 0.0) + delta
        else:
            pending.update(saved["pending"])
        for key in _zero_series(self.repo_id):
            if key not in totals:
                totals[key] = 0.0
                pending.setdefault(key, 0.0)
        for key, value in _snapshot(self.repo_id).items():
            if families[key[0]][1] == "counter":
                delta = value - self._start.get(key, 0.0)
                if key in pending:
                    pending[key] += delta
                elif delta or key not in totals:
                    totals[key] = totals.get(key, 0.0) + delta
            elif key not in self._start or value != self._start[key]:
                totals[key] = value
        # 한 번도 성공하지 못한 저장소도 SynctxSyncNoSuccess가 평가되도록 감시 시작 시각을 기준으로 둔다
        last_success = ("synctx_sync_last_success_timestamp", (("repo_id", self.repo_id),))
        totals.setdefault(last_success, self._started_at)
        self._save(totals, pending, pushed_at if pending else None)
        return totals

    def mark_pushed(self) -> None:
        """commit한 누적값이 Pushgateway에 들어갔다. 보류한 증가분은 수집 시간이 지난 뒤 더한다."""
        saved = self._read()
        if saved["pending"] and saved["pushed_at"] is None:
            self._save(saved["samples"], saved["pending"], time.time())

    def _save(
        self, totals: Dict[SampleKey, float], pending: Dict[SampleKey, float] | None = None,
        pushed_at: float | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f".tmp.{os.getpid()}")
        with open(temp_path, "w", encoding="utf-8") as f:
            data = {
                "version": 1,
                "samples": _encode(totals),
                "pending": _encode(pending or {}),
                "pushed_at": pushed_at,
            }
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        temp_path.replace(self.path)


def _decode(samples) -> Dict[SampleKey, float]:
    return {(s["name"], tuple(sorted(s["labels"].items()))): float(s["value"]) for s in samples}


def _encode(values: Dict[SampleKey, float]) -> List[Dict]:
    return [
        {"name": name, "labels": dict(labels), "value": value}
        for (name, labels), value in sorted(values.items())
    ]


class _TotalsCollector:
    def __init__(self, totals: Dict[SampleKey, float]):
        self.totals = totals

    def collect(self):
        families = _families()
        grouped: Dict[str, List[Tuple[Dict[str, str], float]]] = {}
        for (name, labels), value in sorted(self.totals.items()):
            grouped.setdefault(name, []).append((dict(labels), value))
        for name, samples in grouped.items():
            family_name, family_type, documentation = families[name]
            label_names = sorted(samples[0][0])
            cls = CounterMetricFamily if family_type == "counter" else GaugeMetricFamily
            family = cls(family_name, documentation, labels=label_names)
            for labels, value in samples:
                family.add_metric([labels[n] for n in label_names], value)
            yield family


def push_totals(url: str, repo_id: str, totals: Dict[SampleKey, float]) -> None:
    """저장소 그룹(job, repo_id)을 누적값으로 통째로 바꾼다."""
    registry = CollectorRegistry()
    registry.register(_TotalsCollector(totals))
    push_to_gateway(
        url,
        job=PUSH_JOB,
        grouping_key={"repo_id": repo_id},
        registry=registry,
        timeout=PUSH_TIMEOUT_SECONDS,
    )


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
