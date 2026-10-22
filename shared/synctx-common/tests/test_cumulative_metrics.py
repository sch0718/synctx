from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
from prometheus_client import CollectorRegistry, Counter, Gauge

from synctx_common import metrics_push
from synctx_common.metrics_push import CumulativeMetrics, push_totals


@pytest.fixture
def metrics():
    registry = CollectorRegistry()
    return (
        Counter("demo_events_total", "events", ["group", "result"], registry=registry),
        Counter("demo_failures_total", "failures", registry=registry),
        Gauge("demo_last_success_timestamp", "last success", registry=registry),
    )


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(metrics_push.time, "time", lambda: now[0])
    return now


FAILURES = ("demo_failures_total", ())


def test_labels_select_only_this_group(tmp_path, metrics):
    events, _, _ = metrics
    acc = CumulativeMetrics(tmp_path / "a.json", metrics, labels={"group": "a"})
    events.labels(group="a", result="ok").inc()
    events.labels(group="b", result="ok").inc()
    totals = acc.commit()
    assert {dict(labels).get("group") for name, labels in totals if name == "demo_events_total"} == {"a"}


def test_unlabelled_counter_is_zero_first_then_increase_after_grace(tmp_path, metrics, clock):
    _, failures, _ = metrics
    first = CumulativeMetrics(tmp_path / "m.json", metrics, zero_series=[FAILURES])
    failures.inc()
    assert first.commit()[FAILURES] == 0
    first.mark_pushed()

    clock[0] += 5
    assert CumulativeMetrics(tmp_path / "m.json", metrics, zero_series=[FAILURES]).commit()[FAILURES] == 0

    clock[0] += metrics_push.BASELINE_SCRAPE_GRACE_SECONDS
    run = CumulativeMetrics(tmp_path / "m.json", metrics, zero_series=[FAILURES])
    failures.inc()
    assert run.commit()[FAILURES] == 2


def test_unpushed_baseline_keeps_increase_pending(tmp_path, metrics, clock):
    _, failures, _ = metrics
    first = CumulativeMetrics(tmp_path / "m.json", metrics, zero_series=[FAILURES])
    failures.inc()
    first.commit()

    clock[0] += 3600
    assert CumulativeMetrics(tmp_path / "m.json", metrics, zero_series=[FAILURES]).commit()[FAILURES] == 0


def test_gauge_is_saved_only_when_set_and_kept_otherwise(tmp_path, metrics):
    _, _, last_success = metrics
    key = ("demo_last_success_timestamp", ())
    assert key not in CumulativeMetrics(tmp_path / "m.json", metrics).commit()

    run = CumulativeMetrics(tmp_path / "m.json", metrics)
    last_success.set(1234)
    assert run.commit()[key] == 1234
    assert CumulativeMetrics(tmp_path / "m.json", metrics).commit()[key] == 1234


def test_watch_start_fills_missing_gauge(tmp_path, metrics, clock):
    key = ("demo_last_success_timestamp", ())
    assert CumulativeMetrics(tmp_path / "m.json", metrics, watch_start=key).commit()[key] == 1000.0


class Gateway(BaseHTTPRequestHandler):
    requests = []

    def do_PUT(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
        Gateway.requests.append((self.path, body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_push_without_grouping_key_replaces_job_group(metrics):
    Gateway.requests = []
    server = HTTPServer(("127.0.0.1", 0), Gateway)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        push_totals(f"http://127.0.0.1:{server.server_port}", "demo", metrics, {FAILURES: 3.0})
    finally:
        server.shutdown()
        server.server_close()
    [(path, body)] = Gateway.requests
    assert path == "/metrics/job/demo"
    assert "demo_failures_total 3.0" in body
