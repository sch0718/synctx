import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from synctx_common.observability import (
    register_observability_routes,
    registry,
    http_requests_total,
    http_request_duration,
)


@pytest.fixture
def app():
    app = FastAPI(title="synctx-test")
    register_observability_routes(app, version="0.1.0-test", service_name="test")

    @app.get("/api/hello")
    def hello():
        return {"msg": "ok"}

    return app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestObservabilityRoutes:
    def test_healthz(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_readyz_default(self, client):
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}

    def test_readyz_with_check_passing(self):
        app = FastAPI(title="synctx-test")
        register_observability_routes(app, version="0.1.0", service_name="test", ready_check=lambda: True)
        client = TestClient(app)
        resp = client.get("/readyz")
        assert resp.json() == {"status": "ready"}

    def test_readyz_with_check_failing(self):
        app = FastAPI(title="synctx-test")
        register_observability_routes(app, version="0.1.0", service_name="test", ready_check=lambda: False)
        client = TestClient(app)
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json() == {"status": "not_ready"}

    def test_version(self, client):
        resp = client.get("/version")
        assert resp.status_code == 200
        assert resp.json() == {"version": "0.1.0-test"}

    def test_metrics(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "synctx_http_requests_total" in resp.text

    def test_metrics_format_is_prometheus(self, client):
        resp = client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"] or "openmetrics" in resp.headers["content-type"]


class TestHTTPInstrumentation:
    def test_request_increments_counter(self, client):
        before = http_requests_total.labels(
            service="test", method="GET", path="/api/hello", status=200
        )._value.get()

        client.get("/api/hello")

        after = http_requests_total.labels(
            service="test", method="GET", path="/api/hello", status=200
        )._value.get()
        assert after == before + 1

    def test_request_records_duration(self, client):
        client.get("/api/hello")
        resp = client.get("/metrics")
        assert "synctx_http_request_duration_seconds" in resp.text

    def test_health_endpoints_not_instrumented(self, client):
        before = http_requests_total.labels(
            service="test", method="GET", path="/healthz", status=200
        )._value.get()

        client.get("/healthz")

        after = http_requests_total.labels(
            service="test", method="GET", path="/healthz", status=200
        )._value.get()
        assert after == before
