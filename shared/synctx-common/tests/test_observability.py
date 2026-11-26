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

    def test_requests_use_bounded_path_labels(self, app, client):
        @app.get("/docs/{path:path}")
        def docs(path: str):
            return {"path": path}

        matched = http_requests_total.labels(service="test", method="GET", path="/docs/{path:path}", status=200)
        unmatched = http_requests_total.labels(service="test", method="GET", path="unmatched", status=404)
        before = matched._value.get(), unmatched._value.get()
        for name in ("one", "two"):
            assert client.get(f"/docs/{name}.md").status_code == 200
            assert client.get(f"/missing/{name}").status_code == 404
        assert (matched._value.get(), unmatched._value.get()) == (before[0] + 2, before[1] + 2)

    def test_mounted_app_is_not_labelled_unmatched(self, app, client, tmp_path):
        from starlette.staticfiles import StaticFiles

        (tmp_path / "a.txt").write_text("x")
        app.mount("/static", StaticFiles(directory=tmp_path))
        mount = http_requests_total.labels(service="test", method="GET", path="mount", status=200)
        before = mount._value.get()
        assert client.get("/static/a.txt").status_code == 200
        assert mount._value.get() == before + 1

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


@pytest.mark.parametrize("prefix", ["", "/view"])
@pytest.mark.parametrize("status", [200, 403, 404, 422])
def test_render_http_metrics_use_route_template_for_mounted_and_root_apps(prefix, status):
    from fastapi.responses import JSONResponse

    viewer = FastAPI()
    register_observability_routes(viewer, version="test", service_name="render-test")

    @viewer.get("/render/{token}/{repo_id}/{path:path}")
    def render_html(token: str, repo_id: str, path: str):
        return JSONResponse({"result": "test"}, status_code=status)

    root = FastAPI()
    root.mount(prefix or "/", viewer)
    counter = http_requests_total.labels(service="render-test", method="GET",
                                         path="/render/{token}/{repo_id}/{path}", status=status)
    before = counter._value.get()
    client = TestClient(root)
    for token, repo, path in [("secret-token-a", "repo1", "docs/a.html"), ("secret-token-b", "repo2", "deep/path/b.htm")]:
        assert client.get(f"{prefix}/render/{token}/{repo}/{path}").status_code == status
    assert counter._value.get() == before + 2
    samples = [sample for family in http_requests_total.collect() for sample in family.samples
               if sample.labels.get("service") == "render-test"]
    assert all(sample.labels["path"] == "/render/{token}/{repo_id}/{path}" for sample in samples)
    assert "secret-token" not in repr(samples)


def test_render_path_normalization_uses_route_instead_of_url():
    from types import SimpleNamespace
    from synctx_common.observability import _normalize_path

    assert _normalize_path(SimpleNamespace(path="/render/{token}/{repo_id}/{path:path}")) == "/render/{token}/{repo_id}/{path}"
    assert _normalize_path(SimpleNamespace(path="/view/render/{token}/{repo_id}/{path:path}")) == "/render/{token}/{repo_id}/{path}"
    assert _normalize_path(SimpleNamespace(path="/{repo_id}/{path:path}")) == "/{repo_id}/{path:path}"
    assert _normalize_path(SimpleNamespace(path="/open/{token}/{repo_id}/{path:path}")) == "/open/{token}/{repo_id}/{path}"
    assert _normalize_path(SimpleNamespace(path="/view/open/{token}/{repo_id}/{path:path}")) == "/open/{token}/{repo_id}/{path}"


def test_html_render_metric_has_only_bounded_result_and_repo_labels():
    from synctx_common.observability import html_render_total

    assert html_render_total._labelnames == ("repo_id", "result")
    metric = html_render_total.labels(repo_id="unknown", result="denied_token")
    before = metric._value.get()
    metric.inc()
    assert metric._value.get() == before + 1
