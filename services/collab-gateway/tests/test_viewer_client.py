import httpx
import pytest

from helpers import api_meeting
from viewer_client import ViewerClient, ViewerError

TOKEN = "svc-token"


def client_for(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://viewer")
    return ViewerClient("http://viewer", TOKEN, client=http)


@pytest.mark.asyncio
async def test_fetches_with_bearer_token_and_since_param():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"meetings": [api_meeting()], "repos": [], "invalid": []})

    from datetime import date
    result = await client_for(handler).fetch_meetings(since=date(2026, 10, 1))
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["url"] == "http://viewer/api/meetings?from=2026-10-01"
    assert len(result) == 1


@pytest.mark.asyncio
async def test_parses_fields():
    m = (await client_for(lambda r: httpx.Response(200, json={"meetings": [api_meeting(
        presenters=["김", "이"], decisions=[{"text": "결정", "owner": "", "due": "", "quote": ""}],
        actions=[{"text": "점검", "owner": "김", "due": "2026-10-20", "quote": ""}], memo_error="깨짐")]}))
         .fetch_meetings())[0]
    assert m.key == "r/m1" and m.title == "DX 회의" and m.presenters == ("김", "이")
    assert m.starts_at.isoformat() == "2026-10-15T15:00:00+09:00" and m.ends_at.hour == 16
    assert m.decisions[0].text == "결정" and m.actions[0].owner == "김" and m.memo_error == "깨짐"
    assert m.url == "/view/r/docs/m.md" and m.present_url.endswith("?mode=present")


@pytest.mark.asyncio
async def test_malformed_entries_are_skipped_not_fatal():
    bad = [{"id": "x"}, "string", api_meeting(starts_at="내일"), api_meeting(id="ok")]
    result = await client_for(lambda r: httpx.Response(200, json={"meetings": bad})).fetch_meetings()
    assert [m.id for m in result] == ["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(500), httpx.Response(403), httpx.Response(404),
    httpx.Response(200, text="not json"), httpx.Response(200, json=[]), httpx.Response(200, json={"meetings": "x"}),
])
async def test_errors_raise_viewer_error_without_leaking_token(response):
    with pytest.raises(ViewerError) as exc:
        await client_for(lambda r: response).fetch_meetings()
    assert TOKEN not in str(exc.value)


@pytest.mark.asyncio
async def test_network_error_raises_viewer_error():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ViewerError):
        await client_for(handler).fetch_meetings()


def test_requires_token():
    with pytest.raises(ValueError):
        ViewerClient("http://viewer", "")


@pytest.mark.asyncio
async def test_search_uses_service_token_and_discards_snippets():
    def handler(request):
        assert request.url.path == "/api/search"
        assert dict(request.url.params) == {"q": "규정", "repo": "r", "collab_only": "true"}
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.extensions["timeout"]["read"] == 2.0
        return httpx.Response(200, json={"repos": [{"id": "r", "status": "ok", "matches": [
            {"path": "docs/@all [a](b).md", "before": "비밀발췌", "match": "비밀", "after": "비밀"}]}]})

    result = await client_for(handler).search_documents("규정", {"r"})
    assert "비밀" not in repr(result)
    assert result[0].documents[0].url == "/view/r/docs/%40all%20%5Ba%5D%28b%29.md"


@pytest.mark.asyncio
async def test_evidence_url_and_title_projection():
    def handler(request):
        assert request.url.path == "/api/meetings/r/m1/evidence"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.extensions["timeout"]["read"] == 2.0
        return httpx.Response(200, json={"meeting_id": "m1", "status": "ok", "evidence": [
            {"repo_id": "rules", "path": "a(b).md", "title": "규정", "url": "/view/rules/a%28b%29.md#section",
             "html": "비밀발췌", "cites": "비밀메모"}]})

    result = await client_for(handler).fetch_evidence("r", "m1")
    assert result.documents[0].title == "규정" and result.documents[0].url == "/view/rules/a%28b%29.md#section"
    assert "비밀" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["search", "evidence"])
@pytest.mark.parametrize("response", [
    httpx.Response(500), httpx.Response(403), httpx.Response(404), httpx.Response(200, text="not json"),
    httpx.Response(200, json=[]), httpx.Response(200, json={}),
])
async def test_command_failures_are_viewer_errors(command, response):
    viewer = client_for(lambda request: response)
    with pytest.raises(ViewerError) as exc:
        if command == "search":
            await viewer.search_documents("규정", {"r"})
        else:
            await viewer.fetch_evidence("r", "m1")
    assert TOKEN not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["search", "evidence"])
async def test_command_http_timeout_is_viewer_error(command):
    def handler(request):
        raise httpx.ReadTimeout("비밀응답")

    viewer = client_for(handler)
    with pytest.raises(ViewerError) as exc:
        if command == "search":
            await viewer.search_documents("규정", {"r"})
        else:
            await viewer.fetch_evidence("r", "m1")
    assert "비밀응답" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"repos": "bad"}, {"repos": [{"id": "r", "status": "ok", "matches": "bad"}]},
    {"repos": [{"id": "r", "status": "ok", "matches": [{"path": "../escape.md"}]}]},
])
async def test_malformed_search_is_rejected(payload):
    viewer = client_for(lambda r: httpx.Response(200, json=payload))
    if isinstance(payload["repos"], list) and isinstance(payload["repos"][0]["matches"], list):
        assert not (await viewer.search_documents("규정", {"r"}))[0].documents
    else:
        with pytest.raises(ViewerError):
            await viewer.search_documents("규정", {"r"})


@pytest.mark.asyncio
async def test_failed_evidence_status_is_viewer_error():
    with pytest.raises(ViewerError):
        await client_for(lambda r: httpx.Response(200, json={"status": "failed", "evidence": []})).fetch_evidence("r", "m1")


@pytest.mark.asyncio
async def test_search_scopes_before_link_validation_and_skips_invalid_items(caplog):
    payload = {"repos": [
        {"id": "other", "status": "ok", "matches": [{"path": "docs/a\\b.md"}]},
        {"id": "r", "status": "ok", "matches": [
            {"path": "docs/bad\\private.md"}, {"path": "docs/good.md"}]}]}
    result = await client_for(lambda r: httpx.Response(200, json=payload)).search_documents("규정", {"r"})
    assert [r.repo_id for r in result] == ["r"]
    assert [d.path for d in result[0].documents] == ["docs/good.md"]
    assert "viewer_search_skipped count=1" in caplog.text
    assert "private" not in caplog.text and "a\\b.md" not in caplog.text


@pytest.mark.asyncio
async def test_evidence_skips_bad_paths_and_control_anchors(caplog):
    entries = [{"repo_id": "r", "path": path, "title": "비밀제목", "url": url} for path, url in (
        ("docs/bad\\private.md", "/view/r/a.md"),
        ("docs/a.md", "/view/r/a.md#private%0Aanchor"),
        ("docs/good.md", "/view/r/good.md#section"))]
    result = await client_for(lambda r: httpx.Response(200, json={
        "meeting_id": "m1", "status": "ok", "omitted": 0, "evidence": entries})).fetch_evidence("r", "m1")
    assert [d.path for d in result.documents] == ["docs/good.md"]
    assert "viewer_evidence_skipped count=2" in caplog.text
    assert "비밀제목" not in caplog.text and "private" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("has_document", [False, True])
async def test_partial_evidence_preserves_omission_count(has_document):
    entries = [{"repo_id": "r", "path": "a.md", "title": "규정", "url": "/view/r/a.md"}] if has_document else []
    result = await client_for(lambda r: httpx.Response(200, json={
        "meeting_id": "m1", "status": "partial", "omitted": 3, "evidence": entries})).fetch_evidence("r", "m1")
    assert result.status == "partial" and result.omitted == 3
    assert len(result.documents) == int(has_document)


@pytest.mark.asyncio
async def test_search_requests_only_channel_repositories_with_five_repo_limit():
    seen = []

    def handler(request):
        repo = request.url.params.get("repo")
        seen.append(repo)
        return httpx.Response(200, json={"repos": [{"id": repo, "status": "ok", "matches": []}]})

    result = await client_for(handler).search_documents("규정", {f"r{i}" for i in range(7)})
    assert seen == [f"r{i}" for i in range(5)]
    assert {r.repo_id for r in result} == set(seen)


@pytest.mark.asyncio
async def test_empty_repo_scope_does_not_search():
    def handler(request):
        pytest.fail("빈 저장소 범위로 검색했습니다")

    assert await client_for(handler).search_documents("규정", set()) == []


def test_jitsi_metadata_is_optional_and_validated():
    from helpers import api_meeting
    from viewer_client import parse_meeting
    assert not parse_meeting(api_meeting()).jitsi_enabled
    for value in (None, {}, {'enabled': True}, {'enabled': 'true'},
                  {'enabled': True, 'joinable_from': '2026-10-15T12:00:00', 'joinable_until': '2026-10-15T14:00:00'}):
        assert not parse_meeting(api_meeting(jitsi=value)).jitsi_enabled
    meta = {'enabled': True, 'joinable_from': '2026-10-15T12:00:00+09:00', 'joinable_until': '2026-10-15T14:00:00+09:00'}
    m = parse_meeting(api_meeting(jitsi=meta))
    assert m.jitsi_enabled and m.joinable_from.isoformat() == meta['joinable_from']
    assert m.joinable_until.isoformat() == meta['joinable_until']


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "timeout", "unavailable"])
async def test_repo_level_failure_is_not_treated_as_empty_meetings(status):
    payload = {"meetings": [], "repos": [{"id": "r", "name": "저장소", "status": status, "truncated": False}], "invalid": []}
    with pytest.raises(ViewerError):
        await client_for(lambda r: httpx.Response(200, json=payload)).fetch_meetings()


@pytest.mark.asyncio
async def test_unsupported_repo_is_not_a_fetch_failure():
    payload = {"meetings": [api_meeting()], "invalid": [],
               "repos": [{"id": "r", "status": "ok", "truncated": False}, {"id": "p", "status": "unsupported", "truncated": False}]}
    assert len(await client_for(lambda r: httpx.Response(200, json=payload)).fetch_meetings()) == 1
