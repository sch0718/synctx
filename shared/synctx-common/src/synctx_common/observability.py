"""Common observability: Prometheus metrics, health endpoints, HTTP instrumentation."""

from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

registry = CollectorRegistry()

viewer_jitsi_tokens_issued_total = Counter(
    "synctx_viewer_jitsi_tokens_issued_total", "Total Jitsi tokens issued", registry=registry,
)
viewer_jitsi_token_failures_total = Counter(
    "synctx_viewer_jitsi_token_failures_total", "Total Jitsi token failures", ["reason"], registry=registry,
)

# ── HTTP metrics (all services) ──
http_requests_total = Counter(
    "synctx_http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status"],
    registry=registry,
)

http_request_duration = Histogram(
    "synctx_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["service", "method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry,
)

# ── Viewer metrics ──
render_requests_total = Counter(
    "synctx_render_requests_total",
    "Total render requests",
    ["service", "repo_id", "result"],
    registry=registry,
)

html_bundle_total = Counter(
    "synctx_html_bundle_total",
    "Total synctx-html block renders",
    ["service", "repo_id", "result"],
    registry=registry,
)

html_render_total = Counter(
    "synctx_html_render_total",
    "Total HTML file renders",
    ["repo_id", "result"],
    registry=registry,
)

# ── CDN metrics ──
cdn_bytes_served_total = Counter(
    "synctx_cdn_bytes_served_total",
    "Total bytes served by CDN",
    ["library"],
    registry=registry,
)

# ── Marp metrics ──
marp_convert_total = Counter(
    "synctx_marp_convert_total",
    "Total Marp conversions",
    ["format", "result"],
    registry=registry,
)

marp_publish_total = Counter(
    "synctx_marp_publish_total",
    "Total Marp publish operations",
    ["result"],
    registry=registry,
)

# ── Sync metrics ──
sync_runs_total = Counter(
    "synctx_sync_runs_total",
    "Total sync runs",
    ["repo_id", "result"],
    registry=registry,
)

sync_items_total = Counter(
    "synctx_sync_items_total",
    "Total items processed by sync",
    ["repo_id", "target", "action"],
    registry=registry,
)

sync_failures_total = Counter(
    "synctx_sync_failures_total",
    "Total sync item failures",
    ["repo_id", "target", "error_type"],
    registry=registry,
)

sync_last_success_timestamp = Gauge(
    "synctx_sync_last_success_timestamp",
    "Timestamp of last successful sync",
    ["repo_id"],
    registry=registry,
)

# ── Feedback metrics ──
feedback_events_total = Counter(
    "synctx_feedback_events_total",
    "Total feedback events processed",
    ["result"],
    registry=registry,
)

feedback_redacted_fields_total = Counter(
    "synctx_feedback_redacted_fields_total",
    "Total fields redacted from feedback events",
    registry=registry,
)

feedback_mail_failures_total = Counter(
    "synctx_feedback_mail_failures_total",
    "Total feedback mail send failures",
    registry=registry,
)

feedback_last_success_timestamp = Gauge(
    "synctx_feedback_last_success_timestamp",
    "Timestamp of last successful feedback export",
    registry=registry,
)

# ── Collab Gateway metrics ──
collab_notifications_total = Counter(
    "synctx_collab_notifications_total",
    "Total collab notifications by kind and result",
    ["kind", "result"],
    registry=registry,
)

collab_unmapped_channel_total = Counter(
    "synctx_collab_unmapped_channel_total",
    "Meetings skipped because their channel has no mapping",
    registry=registry,
)

collab_viewer_fetch_failures_total = Counter(
    "synctx_collab_viewer_fetch_failures_total",
    "Total failures reading meetings from the Viewer",
    registry=registry,
)

collab_commands_total = Counter(
    "synctx_collab_commands_total",
    "Slash commands handled by the Collab Gateway",
    ["command", "result"],
    registry=registry,
)

collab_last_success_timestamp = Gauge(
    "synctx_collab_last_success_timestamp",
    "Timestamp of the last poll cycle that read meetings from the Viewer",
    registry=registry,
)


def _normalize_path(path: str, route=None) -> str:
    """Collapse path parameters to reduce cardinality."""
    if getattr(route, "path", None) in (
        "/render/{token}/{repo_id}/{path:path}",
        "/view/render/{token}/{repo_id}/{path:path}",
    ):
        return "/render/{token}/{repo_id}/{path}"
    if getattr(route, "path", None) in (
        "/open/{token}/{repo_id}/{path:path}",
        "/view/open/{token}/{repo_id}/{path:path}",
    ):
        return "/open/{token}/{repo_id}/{path}"
    parts = path.strip("/").split("/")
    normalized = []
    for i, part in enumerate(parts):
        if i > 0 and normalized and normalized[-1] in ("repos", "cdn", "list"):
            normalized.append("{id}")
        else:
            normalized.append(part)
    return "/" + "/".join(normalized) if normalized else "/"


def register_observability_routes(
    app, *, version: str, service_name: str = "", ready_check: Callable[[], bool] | None = None
) -> None:
    """Register health, metrics, and HTTP instrumentation middleware."""

    svc = service_name or app.title.replace("synctx-", "").replace("synctx", "unknown")

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next) -> Response:
        if request.url.path in ("/healthz", "/readyz", "/version", "/metrics"):
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        path = _normalize_path(request.url.path, request.scope.get("route"))
        http_requests_total.labels(
            service=svc, method=request.method, path=path, status=response.status_code
        ).inc()
        http_request_duration.labels(service=svc, method=request.method, path=path).observe(duration)

        return response

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        # Kubernetes readiness probe는 상태 코드만 보므로 준비 전에는 503을 돌려준다
        if ready_check and not ready_check():
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ready"}

    @app.get("/version")
    def version_endpoint():
        return {"version": version}

    @app.get("/metrics")
    def metrics():
        return PlainTextResponse(
            generate_latest(registry).decode("utf-8"),
            media_type=CONTENT_TYPE_LATEST,
        )
