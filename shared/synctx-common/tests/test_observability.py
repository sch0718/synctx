import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from synctx_common.observability import register_observability_routes


@pytest.fixture
def app():
    app = FastAPI()
    register_observability_routes(app, version="0.1.0-test")
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
        app = FastAPI()
        register_observability_routes(app, version="0.1.0", ready_check=lambda: True)
        client = TestClient(app)
        resp = client.get("/readyz")
        assert resp.json() == {"status": "ready"}

    def test_readyz_with_check_failing(self):
        app = FastAPI()
        register_observability_routes(app, version="0.1.0", ready_check=lambda: False)
        client = TestClient(app)
        resp = client.get("/readyz")
        assert resp.json() == {"status": "not_ready"}

    def test_version(self, client):
        resp = client.get("/version")
        assert resp.status_code == 200
        assert resp.json() == {"version": "0.1.0-test"}

    def test_metrics(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "synctx_http_requests_total" in resp.text
