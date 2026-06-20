"""Common HTTP observability endpoint registration."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.responses import PlainTextResponse


def register_observability_routes(app, *, version: str, ready_check: Callable[[], bool] | None = None) -> None:
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        ready = ready_check() if ready_check else True
        return {"status": "ready" if ready else "not_ready"}

    @app.get("/version")
    def version_endpoint():
        return {"version": version}

    @app.get("/metrics")
    def metrics():
        return PlainTextResponse("synctx_http_requests_total 0\n")
