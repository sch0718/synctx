import json
import sys, os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import exporter as exporter_module
import feedback_metrics
from exporter import FeedbackExporter
from feedback_metrics import FeedbackMetrics
from synctx_common import metrics_push as cumulative
from synctx_common.config import (
    OpenProjectTargetConfig, OutboundFeedbackConfig, SynctxConfig, TargetAuthConfig, TargetsConfig,
)

REAL_CLIENT = httpx.AsyncClient

SENT = {"id": 1, "updatedAt": "2026-10-22T00:00:00Z", "customField1": "r:a.md", "status": "done", "description": "x"}
NO_KEY = {"id": 2, "updatedAt": "2026-10-22T00:00:00Z", "status": "done"}
UNREDACTABLE = {"id": 3, "updatedAt": "2026-10-22T00:00:00Z", "customField1": "r:c.md"}
MAIL_FAILS = {"id": 4, "updatedAt": "2026-10-22T00:00:00Z", "customField1": "r:d.md", "subject": "s"}


class Gateway(BaseHTTPRequestHandler):
    requests = []

    def do_PUT(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
        Gateway.requests.append((self.path, body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def gateway(monkeypatch):
    Gateway.requests = []
    server = HTTPServer(("127.0.0.1", 0), Gateway)
    Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(feedback_metrics.PUSHGATEWAY_URL_ENV, f"http://127.0.0.1:{server.server_port}")
    yield
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(cumulative, "BASELINE_SCRAPE_GRACE_SECONDS", 0)
    monkeypatch.setenv("OUTBOUND_FEEDBACK_HASH_SALT", "salt")
    monkeypatch.setenv("OUTBOUND_FEEDBACK_HMAC_KEY", "key")
    monkeypatch.setenv("OUTBOUND_FEEDBACK_EVENT_STORE", str(tmp_path / "feedback_events.json"))
    monkeypatch.delenv(feedback_metrics.PUSHGATEWAY_URL_ENV, raising=False)

    real_redact = exporter_module.redact_work_package

    def redact(config, wp):
        if wp["id"] == UNREDACTABLE["id"]:
            raise ValueError("broken allowlist")
        return real_redact(config, wp)

    def send(config, subject, body):
        if json.loads(body)["payload"]["work_package_id"] == MAIL_FAILS["id"]:
            raise OSError("smtp down")

    monkeypatch.setattr(exporter_module, "redact_work_package", redact)
    monkeypatch.setattr(exporter_module, "send_feedback_email", send)


def make_exporter(tmp_path, monkeypatch, work_packages, enabled=True):
    def handler(request):
        return httpx.Response(200, json={"_embedded": {"elements": work_packages}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    config = SynctxConfig(
        targets=TargetsConfig(
            openproject=OpenProjectTargetConfig(
                base_url="https://op.internal",
                auth=TargetAuthConfig(type="token", credential_env="OP_TOKEN"),
                external_key_custom_field="customField1",
            )
        ),
        outbound_feedback=OutboundFeedbackConfig(enabled=enabled, allowed_fields=["status"]),
    )
    exporter = FeedbackExporter(config)
    exporter.state_file = str(tmp_path / "feedback_state.json")
    return exporter


def total(totals, name, **labels):
    return sum(v for (n, ls), v in totals.items() if n == name and all(dict(ls).get(k) == x for k, x in labels.items()))


def saved(tmp_path):
    return FeedbackMetrics(str(tmp_path)).load()


@pytest.mark.asyncio
async def test_each_outcome_is_counted_after_zero_baseline(tmp_path, monkeypatch, gateway):
    await make_exporter(tmp_path, monkeypatch, []).run()
    await make_exporter(tmp_path, monkeypatch, [SENT, NO_KEY, UNREDACTABLE, MAIL_FAILS]).run()
    await make_exporter(tmp_path, monkeypatch, []).run()

    totals = saved(tmp_path)
    for result, expected in [("sent", 1), ("skipped", 1), ("redaction_error", 1), ("mail_error", 1)]:
        assert total(totals, "synctx_feedback_events_total", result=result) == expected
    assert total(totals, "synctx_feedback_mail_failures_total") == 1
    # SENT: customField1·description, MAIL_FAILS: customField1·subject
    assert total(totals, "synctx_feedback_redacted_fields_total") == 4


@pytest.mark.asyncio
async def test_first_failures_are_pushed_as_increase_from_zero(tmp_path, monkeypatch, gateway):
    await make_exporter(tmp_path, monkeypatch, [UNREDACTABLE, MAIL_FAILS]).run()
    await make_exporter(tmp_path, monkeypatch, []).run()

    (first_path, first), (_, second) = Gateway.requests
    assert first_path == "/metrics/job/synctx-feedback"
    assert 'synctx_feedback_events_total{result="redaction_error"} 0.0' in first
    assert "synctx_feedback_mail_failures_total 0.0" in first
    assert 'synctx_feedback_events_total{result="redaction_error"} 1.0' in second
    assert "synctx_feedback_mail_failures_total 1.0" in second


@pytest.mark.asyncio
async def test_redaction_error_skips_that_work_package_and_keeps_timestamp(tmp_path, monkeypatch):
    exporter = make_exporter(tmp_path, monkeypatch, [UNREDACTABLE, SENT])
    await exporter.run()

    sent = [e["payload"]["work_package_id"] for e in exporter.event_store.list()]
    assert sent == [SENT["id"]]
    assert not (tmp_path / "feedback_state.json").exists()
    assert "synctx_feedback_last_success_timestamp" not in {n for n, _ in saved(tmp_path)}


@pytest.mark.asyncio
async def test_successful_run_records_last_success(tmp_path, monkeypatch):
    await make_exporter(tmp_path, monkeypatch, [SENT]).run()
    assert total(saved(tmp_path), "synctx_feedback_last_success_timestamp") > 0


@pytest.mark.asyncio
async def test_push_failure_does_not_fail_export(tmp_path, monkeypatch):
    monkeypatch.setenv(feedback_metrics.PUSHGATEWAY_URL_ENV, "http://127.0.0.1:9")
    await make_exporter(tmp_path, monkeypatch, [SENT]).run()
    assert (tmp_path / "feedback_state.json").exists()


@pytest.mark.asyncio
async def test_disabled_feedback_does_not_record_metrics(tmp_path, monkeypatch):
    await make_exporter(tmp_path, monkeypatch, [SENT], enabled=False).run()
    assert not (tmp_path / "feedback_metrics.json").exists()


def test_metrics_file_does_not_collide_with_sync_state(tmp_path):
    assert FeedbackMetrics(str(tmp_path)).path == tmp_path / "feedback_metrics.json"
