import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from helpers import item, meeting
from synctx_common.config import SynctxConfig
from synctx_common.observability import collab_commands_total

TOKEN = "cmd-token-secret"
KST = timezone(timedelta(hours=9))


class FakeRunner:
    def __init__(self, meetings=None, available=True, boom=False):
        self.meetings = meetings if meetings is not None else []
        self.available = available
        self.boom = boom
        self.calls = 0

    async def run_cycle(self):
        pass

    async def current_meetings(self, now, max_age, timeout=2.0):
        self.calls += 1
        if self.boom:
            raise RuntimeError("secret internal detail")
        return self.meetings if self.available else None

    async def aclose(self):
        pass


def setup_app(monkeypatch, runner, *, enabled=True, commands=True, token=TOKEN, rate=10, channels=None):
    import app as gateway_app
    cfg = SynctxConfig.model_validate({"collab": {"gateway": {
        "enabled": enabled, "commands_enabled": commands, "command_rate_limit_per_minute": rate,
        "mattermost": {"url": "https://chat.local"},
        "channels": channels or {"dx": {"team": "devteam", "channel": "dx-room"}}}}})
    monkeypatch.setattr(gateway_app, "config", cfg)
    monkeypatch.setattr(gateway_app, "create_runner", lambda: runner)
    monkeypatch.setattr(gateway_app, "_poll_interval", lambda: 3600)
    for name in ("MATTERMOST_BOT_TOKEN", "VIEWER_SERVICE_TOKEN"):
        monkeypatch.setenv(name, "x")
    if token:
        monkeypatch.setenv("MATTERMOST_COMMAND_TOKEN", token)
    else:
        monkeypatch.delenv("MATTERMOST_COMMAND_TOKEN", raising=False)
    return gateway_app


def form(text="오늘", **kw):
    data = {"token": TOKEN, "team_domain": "devteam", "channel_name": "dx-room", "user_id": "user-abc123", "text": text}
    data.update(kw)
    return {k: v for k, v in data.items() if v is not None}


def post(client, data, **kw):
    return client.post("/api/collab/mattermost/command", content=urlencode(data).encode(),
                       headers={"Content-Type": "application/x-www-form-urlencoded"}, **kw)


def count(command, result):
    return collab_commands_total.labels(command=command, result=result)._value.get()


def today_meeting(**kw):
    return meeting(starts_at=datetime.now(KST) + timedelta(minutes=1), **kw)


@pytest.fixture
def client(monkeypatch):
    runner = FakeRunner([today_meeting(title="오늘의 회의")])
    app_module = setup_app(monkeypatch, runner)
    with TestClient(app_module.app) as c:
        c.runner = runner
        yield c


def test_valid_command_returns_ephemeral_reply(client):
    before = count("today", "ok")
    resp = post(client, form())
    body = resp.json()
    assert resp.status_code == 200 and body["response_type"] == "ephemeral"
    assert "오늘의 회의" in body["text"]
    assert count("today", "ok") == before + 1


def test_help_does_not_need_viewer(client):
    resp = post(client, form("도움말"))
    assert "/synctx" in resp.json()["text"] and client.runner.calls == 0


@pytest.mark.parametrize("token", ["wrong", "", None])
def test_bad_or_missing_token_is_401_and_not_processed(client, token):
    before = count("unknown", "unauthorized")
    resp = post(client, form(token=token))
    assert resp.status_code == 401 and "오늘의 회의" not in resp.text
    assert client.runner.calls == 0 and count("unknown", "unauthorized") == before + 1


def test_unauthorized_requests_do_not_consume_rate_limit(monkeypatch):
    app_module = setup_app(monkeypatch, FakeRunner([today_meeting()]), rate=1)
    with TestClient(app_module.app) as c:
        for _ in range(5):
            assert post(c, form(token="wrong")).status_code == 401
        assert post(c, form()).status_code == 200


def test_not_configured_token_disables_commands(monkeypatch):
    app_module = setup_app(monkeypatch, FakeRunner(), token=None)
    with TestClient(app_module.app) as c:
        assert post(c, form()).status_code == 503
        assert post(c, form(token="")).status_code == 503


def test_commands_disabled_by_config(monkeypatch):
    app_module = setup_app(monkeypatch, FakeRunner(), commands=False)
    with TestClient(app_module.app) as c:
        assert post(c, form()).status_code == 503


def test_gateway_disabled_returns_503(monkeypatch):
    app_module = setup_app(monkeypatch, FakeRunner(), enabled=False)
    with TestClient(app_module.app) as c:
        assert post(c, form()).status_code == 503


def test_oversized_body_is_413(client):
    resp = client.post("/api/collab/mattermost/command", content=b"text=" + b"x" * 20000)
    assert resp.status_code == 413


def test_missing_core_fields_is_400(client):
    for field in ("team_domain", "channel_name", "user_id"):
        assert post(client, form(**{field: None})).status_code == 400


def test_unmapped_channel_answers_without_loading_meetings(client):
    before = count("today", "no_channel")
    resp = post(client, form(channel_name="other-room"))
    assert "연결된 회의가 없습니다" in resp.json()["text"] and client.runner.calls == 0
    assert count("today", "no_channel") == before + 1


def test_team_and_channel_match_is_case_insensitive(client):
    resp = post(client, form(team_domain="DEVTEAM", channel_name="DX-Room"))
    assert "오늘의 회의" in resp.json()["text"]


def test_several_meeting_channels_can_map_to_one_chat_channel(monkeypatch):
    runner = FakeRunner([today_meeting(id="a", title="가", channel="dx"), today_meeting(id="b", title="나", channel="ops")])
    app_module = setup_app(monkeypatch, runner, channels={
        "dx": {"team": "devteam", "channel": "dx-room"}, "ops": {"team": "devteam", "channel": "dx-room"}})
    with TestClient(app_module.app) as c:
        text = post(c, form()).json()["text"]
    assert "가" in text and "나" in text


def test_other_channels_meetings_are_not_exposed(monkeypatch):
    runner = FakeRunner([today_meeting(id="a", title="우리", channel="dx"), today_meeting(id="b", title="남의", channel="sec")])
    app_module = setup_app(monkeypatch, runner)
    with TestClient(app_module.app) as c:
        text = post(c, form()).json()["text"]
    assert "우리" in text and "남의" not in text


def test_rate_limit_per_user(monkeypatch):
    app_module = setup_app(monkeypatch, FakeRunner([today_meeting()]), rate=2)
    with TestClient(app_module.app) as c:
        before = count("today", "rate_limited")
        assert "회의" in post(c, form()).json()["text"]
        post(c, form())
        limited = post(c, form())
        assert limited.status_code == 200 and "너무 많" in limited.json()["text"]
        assert count("today", "rate_limited") == before + 1
        assert "너무 많" not in post(c, form(user_id="someone-else")).json()["text"]


def test_viewer_unavailable_message(monkeypatch):
    app_module = setup_app(monkeypatch, FakeRunner(available=False))
    with TestClient(app_module.app) as c:
        before = count("today", "unavailable")
        resp = post(c, form())
        assert resp.status_code == 200 and "읽지 못했습니다" in resp.json()["text"]
        assert count("today", "unavailable") == before + 1


def test_unexpected_error_is_generic_and_counted(monkeypatch):
    app_module = setup_app(monkeypatch, FakeRunner(boom=True))
    with TestClient(app_module.app) as c:
        before = count("today", "error")
        resp = post(c, form())
        assert resp.status_code == 200 and "secret internal detail" not in resp.text
        assert "처리하지 못했습니다" in resp.json()["text"] and count("today", "error") == before + 1


def test_audit_log_has_command_and_hashed_user_but_no_text_or_token(client, caplog):
    with caplog.at_level(logging.INFO):
        post(client, form("회의 비밀스런제목"))
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "collab_command" in logged and "command=meeting" in logged and "channel=dx" in logged
    assert "user-abc123" not in logged and "비밀스런제목" not in logged and TOKEN not in logged


def test_summary_command_returns_memos(monkeypatch):
    runner = FakeRunner([today_meeting(id="m1", decisions=(item("채택됨"),))])
    app_module = setup_app(monkeypatch, runner)
    with TestClient(app_module.app) as c:
        assert "채택됨" in post(c, form("정리 m1")).json()["text"]


def test_unknown_command_is_counted_as_unknown(client):
    before = count("unknown", "invalid")
    assert "알 수 없는" in post(client, form("검색 abc")).json()["text"]
    assert count("unknown", "invalid") == before + 1
