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
    assert "알 수 없는" in post(client, form("미지원 abc")).json()["text"]
    assert count("unknown", "invalid") == before + 1


class LookupRunner(FakeRunner):
    def __init__(self, *args, error=False, delay=0, **kw):
        super().__init__(*args, **kw)
        self.lookups = []
        self.error = error
        self.delay = delay

    async def _data(self):
        import asyncio
        from viewer_client import ViewerError
        if self.error:
            raise ViewerError("비밀응답")
        if self.delay:
            await asyncio.sleep(self.delay)

    async def search_documents(self, query, repo_ids):
        from models import DocumentLink, SearchResult
        self.lookups.append(query)
        self.search_repo_ids = repo_ids
        await self._data()
        return [SearchResult("r", "ok", (DocumentLink("r", "a.md", "@all [a].md", "/view/r/a.md"),)),
                SearchResult("secret", "ok", (DocumentLink("secret", "s.md", "외부비밀", "/view/secret/s.md"),))]

    async def fetch_evidence(self, repo_id, meeting_id):
        from models import DocumentLink, EvidenceResult
        self.lookups.append((repo_id, meeting_id))
        await self._data()
        return EvidenceResult((DocumentLink("rules", "a.md", "@here [규정]", "/view/rules/a.md"),))


@pytest.mark.parametrize("text,lookup,label", [("검색 규정", "규정", "search"), ("근거 m1", ("r", "m1"), "evidence")])
def test_lookup_commands_are_ephemeral_scoped_escaped_and_counted(monkeypatch, caplog, text, lookup, label):
    runner = LookupRunner([today_meeting(id="m1")])
    app_module = setup_app(monkeypatch, runner)
    with TestClient(app_module.app) as c, caplog.at_level(logging.INFO):
        before = count(label, "ok")
        resp = post(c, form(text))
    assert resp.status_code == 200 and resp.json()["response_type"] == "ephemeral"
    assert runner.lookups == [lookup] and count(label, "ok") == before + 1
    if label == "search":
        assert runner.search_repo_ids == {"r"}
    assert "@here" not in resp.text and "@all" not in resp.text and "외부비밀" not in resp.text
    assert "https://" in resp.json()["text"] or "/view/" in resp.json()["text"]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert text not in logged and TOKEN not in logged and "user-abc123" not in logged


@pytest.mark.parametrize("text", ["검색 규정", "근거 m1"])
@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_lookup_failure_and_total_deadline(monkeypatch, text, failure):
    import time
    runner = LookupRunner([today_meeting(id="m1")], error=failure == "error", delay=1 if failure == "timeout" else 0)
    app_module = setup_app(monkeypatch, runner)
    monkeypatch.setattr(app_module, "COMMAND_TIMEOUT_SECONDS", 0.05)
    label = "search" if text.startswith("검색") else "evidence"
    with TestClient(app_module.app) as c:
        before = count(label, "unavailable")
        started = time.monotonic()
        resp = post(c, form(text))
        assert time.monotonic() - started < 0.5
    assert resp.status_code == 200 and "읽지 못했습니다" in resp.json()["text"]
    expected = "지금 검색 결과를 읽지 못했습니다" if label == "search" else "근거를 읽지 못했습니다"
    assert expected in resp.json()["text"]
    assert "비밀응답" not in resp.text and count(label, "unavailable") == before + 1


@pytest.mark.parametrize("text", ["검색 a", "검색 ab\ncd", "근거", "근거 secret"])
def test_invalid_or_out_of_scope_lookup_is_not_sent(monkeypatch, text):
    runner = LookupRunner([today_meeting(id="secret", channel="sec")])
    app_module = setup_app(monkeypatch, runner)
    with TestClient(app_module.app) as c:
        assert post(c, form(text)).status_code == 200
    assert runner.lookups == []
    if text.startswith("검색"):
        assert runner.calls == 0


@pytest.mark.parametrize("text", ["검색 규정", "근거 m1"])
def test_unmapped_lookup_skips_viewer(monkeypatch, text):
    runner = LookupRunner([today_meeting()])
    app_module = setup_app(monkeypatch, runner)
    with TestClient(app_module.app) as c:
        resp = post(c, form(text, channel_name="elsewhere"))
    assert "연결된 회의가 없습니다" in resp.json()["text"] and runner.calls == 0 and runner.lookups == []


def test_meetings_and_lookup_share_one_deadline(monkeypatch):
    import asyncio

    class SlowRunner(LookupRunner):
        async def current_meetings(self, *args):
            await asyncio.sleep(0.04)
            return self.meetings

    runner = SlowRunner([today_meeting(id="m1")], delay=0.04)
    app_module = setup_app(monkeypatch, runner)
    monkeypatch.setattr(app_module, "COMMAND_TIMEOUT_SECONDS", 0.06)
    with TestClient(app_module.app) as c:
        response = post(c, form("검색 규정"))
    assert response.status_code == 200 and "읽지 못했습니다" in response.json()["text"]
    assert runner.lookups == ["규정"]


@pytest.mark.parametrize("text", ["검색 규정", "근거 m1"])
def test_lookup_rate_limit_skips_additional_viewer_calls(monkeypatch, text):
    runner = LookupRunner([today_meeting(id="m1")])
    app_module = setup_app(monkeypatch, runner, rate=1)
    with TestClient(app_module.app) as c:
        assert post(c, form(text)).status_code == 200
        assert "너무 많" in post(c, form(text)).json()["text"]
    assert len(runner.lookups) == 1 and runner.calls == 1


@pytest.mark.parametrize("text,expected", [
    ("검색 규정", "지금 검색 결과를 읽지 못했습니다"),
    ("근거 m1", "근거를 읽지 못했습니다"),
    ("회의 m1", "지금 회의 정보를 읽지 못했습니다"),
])
def test_meeting_lookup_failure_uses_command_specific_message(monkeypatch, text, expected):
    app_module = setup_app(monkeypatch, FakeRunner(available=False))
    with TestClient(app_module.app) as c:
        assert expected in post(c, form(text)).json()["text"]


def test_search_passes_only_mapped_meeting_repositories(monkeypatch):
    runner = LookupRunner([today_meeting(repo_id="r"), today_meeting(repo_id="r2"),
                           today_meeting(repo_id="secret", channel="sec")])
    app_module = setup_app(monkeypatch, runner)
    with TestClient(app_module.app) as c:
        assert "문서 검색" in post(c, form("검색 규정")).json()["text"]
    assert runner.search_repo_ids == {"r", "r2"}


def test_partial_evidence_reaches_ephemeral_response(monkeypatch):
    from models import EvidenceResult

    class PartialRunner(LookupRunner):
        async def fetch_evidence(self, *args):
            return EvidenceResult((), "partial", 2)

    app_module = setup_app(monkeypatch, PartialRunner([today_meeting(id="m1")]))
    with TestClient(app_module.app) as c:
        response = post(c, form("근거 m1")).json()
    assert response["response_type"] == "ephemeral"
    assert "일부 근거를 읽지 못했습니다(2건 생략)" in response["text"]
    assert "근거가 없습니다" not in response["text"]


@pytest.mark.parametrize("slow", [False, True])
def test_repository_search_requests_share_command_deadline(monkeypatch, slow):
    import asyncio
    import httpx
    from viewer_client import ViewerClient

    seen = []

    async def handler(request):
        repo = request.url.params["repo"]
        seen.append(repo)
        if slow:
            await asyncio.sleep(0.08)
        return httpx.Response(200, json={"repos": [
            {"id": "secret", "status": "ok", "matches": [{"path": "docs/a\\b.md"}]},
            {"id": repo, "status": "ok", "matches": [{"path": "docs/a.md"}]}]})

    class HttpRunner(LookupRunner):
        def __init__(self, meetings):
            super().__init__(meetings)
            self.viewer = ViewerClient("http://viewer", "test-token", client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler)))

        async def search_documents(self, query, repo_ids):
            return await self.viewer.search_documents(query, repo_ids)

        async def aclose(self):
            await self.viewer.aclose()

    runner = HttpRunner([today_meeting(repo_id=f"r{i}") for i in range(7)]
                        + [today_meeting(repo_id="secret", channel="sec")])
    app_module = setup_app(monkeypatch, runner)
    monkeypatch.setattr(app_module, "COMMAND_TIMEOUT_SECONDS", 0.12 if slow else 2.0)
    with TestClient(app_module.app) as c:
        response = post(c, form("검색 규정")).json()
    if slow:
        assert seen == ["r0", "r1"]
        assert "지금 검색 결과를 읽지 못했습니다" in response["text"]
    else:
        assert seen == [f"r{i}" for i in range(5)]
        assert response["text"].count("\n- [") == 5
        assert "secret" not in response["text"]


def test_join_uses_channel_scope_and_is_counted(client):
    before = count('join', 'not_found')
    response = post(client, form('참여 absent'))
    assert response.status_code == 200
    assert response.json()['response_type'] == 'ephemeral'
    assert '이 채널에서' in response.json()['text']
    assert count('join', 'not_found') == before + 1
    assert all(word not in response.text.casefold() for word in ('jwt', 'room', '/jitsi'))


@pytest.mark.parametrize("text", ["참여", "join", "참여 m1"])
def test_join_command_loads_meetings_and_links_the_channel_meeting(client, text):
    body = post(client, form(text)).json()["text"]
    assert "오늘의 회의" in body and "문서 열기" in body
    assert client.runner.calls == 1
