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
