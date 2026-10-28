import asyncio

import pytest
from fastapi.testclient import TestClient

from synctx_common.config import CollabGatewayConfig, SynctxConfig


def configure(monkeypatch, *, enabled=True, tokens=True):
    import app as gateway_app
    gw = CollabGatewayConfig.model_validate({
        "enabled": enabled, "mattermost": {"url": "https://chat.local"},
        "channels": {"dx": {"team": "devteam", "channel": "dx"}}})
    cfg = SynctxConfig.model_validate({"collab": {"gateway": gw.model_dump()}})
    monkeypatch.setattr(gateway_app, "config", cfg)
    for name in ("MATTERMOST_BOT_TOKEN", "VIEWER_SERVICE_TOKEN"):
        if tokens:
            monkeypatch.setenv(name, "x-" + name)
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(gateway_app, "_poll_interval", lambda: 0.01)
    return gateway_app


class FakeRunner:
    def __init__(self, fail_first=False):
        self.cycles = 0
        self.fail_first = fail_first
        self.closed = False

    async def run_cycle(self):
        self.cycles += 1
        if self.fail_first and self.cycles == 1:
            raise RuntimeError("boom")

    async def aclose(self):
        self.closed = True


def wait_for(predicate, timeout=3.0):
    import time
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.01)
    return predicate()


def test_health_ready_version_metrics(monkeypatch):
    app_module = configure(monkeypatch)
    runner = FakeRunner()
    monkeypatch.setattr(app_module, "create_runner", lambda: runner)
    with TestClient(app_module.app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").status_code == 200
        assert client.get("/version").json()["version"]
        body = client.get("/metrics").text
        assert "synctx_collab_notifications_total" in body
        assert 'kind="reminder",result="sent"' in body


def test_poll_loop_runs_repeatedly_and_stops_cleanly(monkeypatch):
    app_module = configure(monkeypatch)
    runner = FakeRunner()
    monkeypatch.setattr(app_module, "create_runner", lambda: runner)
    with TestClient(app_module.app):
        assert wait_for(lambda: runner.cycles >= 3)
    assert runner.closed is True


def test_cycle_exception_does_not_stop_loop(monkeypatch):
    app_module = configure(monkeypatch)
    runner = FakeRunner(fail_first=True)
    monkeypatch.setattr(app_module, "create_runner", lambda: runner)
    with TestClient(app_module.app):
        assert wait_for(lambda: runner.cycles >= 3)


def test_disabled_gateway_starts_idle(monkeypatch):
    app_module = configure(monkeypatch, enabled=False, tokens=False)
    created = []
    monkeypatch.setattr(app_module, "create_runner", lambda: created.append(1))
    with TestClient(app_module.app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
    assert created == []


def test_enabled_without_tokens_fails_at_startup(monkeypatch):
    app_module = configure(monkeypatch, tokens=False)
    with pytest.raises(RuntimeError) as exc:
        with TestClient(app_module.app):
            pass
    assert "MATTERMOST_BOT_TOKEN" in str(exc.value) or "VIEWER_SERVICE_TOKEN" in str(exc.value)
    assert "x-" not in str(exc.value)


def test_create_runner_wires_components(monkeypatch, tmp_path):
    app_module = configure(monkeypatch)
    monkeypatch.setenv("SYNCTX_COLLAB_STATE_PATH", str(tmp_path))
    runner = app_module.create_runner()
    assert runner._base_url == app_module.config.runtime.public_base_url
    assert str(runner._store.directory) == str(tmp_path)
    asyncio.run(runner.aclose())
