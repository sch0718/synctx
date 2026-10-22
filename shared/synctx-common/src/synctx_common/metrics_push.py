"""CronJob 메트릭을 실행 간 누적 보존하고 Pushgateway로 보낸다.

CronJob 프로세스는 실행마다 카운터가 0에서 시작하므로, 이번 실행의 증가분을
PVC의 누적값에 더해 Prometheus가 단조 증가 카운터로 보게 한다.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from prometheus_client import CollectorRegistry, push_to_gateway
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

logger = logging.getLogger(__name__)

PUSHGATEWAY_URL_ENV = "SYNCTX_PUSHGATEWAY_URL"
PUSH_TIMEOUT_SECONDS = 5
# 0을 담은 push 뒤 Prometheus가 한 번 이상 수집할 시간(ServiceMonitor interval 15s의 여러 배)
BASELINE_SCRAPE_GRACE_SECONDS = 60

SampleKey = Tuple[str, Tuple[Tuple[str, str], ...]]


def _families(metrics) -> Dict[str, Tuple[str, str, str]]:
    """sample 이름 → (family 이름, type, 설명)."""
    names = {}
    for metric in metrics:
        for family in metric.describe():
            sample = f"{family.name}_total" if family.type == "counter" else family.name
            names[sample] = (family.name, family.type, family.documentation)
    return names


def _snapshot(metrics, labels: Mapping[str, str]) -> Dict[SampleKey, float]:
    families = _families(metrics)
    values = {}
    for metric in metrics:
        for family in metric.collect():
            for sample in family.samples:
                if sample.name in families and all(sample.labels.get(k) == v for k, v in labels.items()):
                    values[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
    return values


class CumulativeMetrics:
    """한 실행 동안의 메트릭 증가분을 파일의 누적값에 반영한다.

    labels: 이 누적값에 넣을 sample의 라벨 조건. 다른 그룹(예: 다른 저장소)의 sample은 건너뛴다.
    zero_series: Alert가 increase()·rate()로 보는 계열. 처음 생긴 계열은 이전 값이 없어 증가로 잡히지 않으므로,
        0을 먼저 Pushgateway에 보내고 그동안의 증가분은 그 push가 수집될 시간이 지난 뒤의 실행에서 더한다.
    watch_start: 아직 값이 없으면 감시를 시작한 실행의 시각을 넣을 gauge 계열.
    """

    def __init__(
        self, path, metrics, labels: Optional[Mapping[str, str]] = None,
        zero_series: Iterable[SampleKey] = (), watch_start: Optional[SampleKey] = None,
    ):
        self.path = Path(path)
        self.metrics = tuple(metrics)
        self.labels = dict(labels or {})
        self.zero_series = list(zero_series)
        self.watch_start = watch_start
        self._start = _snapshot(self.metrics, self.labels)
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
            logger.error(f"Failed to read metrics {self.path}: {e}")
            return empty
        return {
            "samples": _decode(data.get("samples", [])),
            "pending": _decode(data.get("pending", [])),
            "pushed_at": data.get("pushed_at"),
        }

    def commit(self) -> Dict[SampleKey, float]:
        """이번 실행의 증가분을 누적값에 더해 저장하고 Pushgateway로 보낼 누적값을 돌려준다."""
        families = _families(self.metrics)
        saved = self._read()
        totals = saved["samples"]
        pending = {}
        pushed_at = saved["pushed_at"]
        if pushed_at is not None and self._started_at - pushed_at >= BASELINE_SCRAPE_GRACE_SECONDS:
            for key, delta in saved["pending"].items():
                totals[key] = totals.get(key, 0.0) + delta
        else:
            pending.update(saved["pending"])
        for key in self.zero_series:
            if key not in totals:
                totals[key] = 0.0
                pending.setdefault(key, 0.0)
        for key, value in _snapshot(self.metrics, self.labels).items():
            if families[key[0]][1] == "counter":
                delta = value - self._start.get(key, 0.0)
                if key in pending:
                    pending[key] += delta
                elif delta or key not in totals:
                    totals[key] = totals.get(key, 0.0) + delta
            elif key not in self._start or value != self._start[key]:
                totals[key] = value
        if self.watch_start is not None:
            totals.setdefault(self.watch_start, self._started_at)
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
    def __init__(self, metrics, totals: Dict[SampleKey, float]):
        self.metrics = metrics
        self.totals = totals

    def collect(self):
        families = _families(self.metrics)
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


def push_totals(
    url: str, job: str, metrics, totals: Dict[SampleKey, float],
    grouping_key: Optional[Mapping[str, str]] = None,
) -> None:
    """그룹(job, grouping_key)을 누적값으로 통째로 바꾼다."""
    registry = CollectorRegistry()
    registry.register(_TotalsCollector(tuple(metrics), totals))
    push_to_gateway(
        url,
        job=job,
        grouping_key=dict(grouping_key or {}),
        registry=registry,
        timeout=PUSH_TIMEOUT_SECONDS,
    )
