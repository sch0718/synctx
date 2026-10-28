import httpx
import pytest

from adapters.base import ChannelRef, SendError
from adapters.mattermost import MattermostAdapter

CHANNEL = ChannelRef(team="devteam", channel="dx")
TOKEN = "bot-secret-token"


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def adapter(responses, **kw):
    rec = Recorder(responses)
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    client = httpx.AsyncClient(transport=httpx.MockTransport(rec), base_url="https://chat.local")
    return MattermostAdapter("https://chat.local", TOKEN, client=client, sleep=sleep, **kw), rec, sleeps


def ok_channel():
    return httpx.Response(200, json={"id": "chan123"})


def ok_post():
    return httpx.Response(201, json={"id": "post1"})


@pytest.mark.asyncio
async def test_resolves_channel_then_posts():
    a, rec, _ = adapter([ok_channel(), ok_post()])
    await a.send(CHANNEL, "안녕")
    first, second = rec.requests
    assert first.method == "GET" and first.url.path == "/api/v4/teams/name/devteam/channels/name/dx"
    assert second.method == "POST" and second.url.path == "/api/v4/posts"
    assert second.read() and b'"channel_id":"chan123"' in second.read().replace(b" ", b"")
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in rec.requests)


@pytest.mark.asyncio
async def test_message_text_sent_as_json_not_urlencoded():
    import json
    a, rec, _ = adapter([ok_channel(), ok_post()])
    await a.send(CHANNEL, "한글 **본문**")
    assert json.loads(rec.requests[1].content) == {"channel_id": "chan123", "message": "한글 **본문**"}


@pytest.mark.asyncio
async def test_channel_id_cached_between_sends():
    a, rec, _ = adapter([ok_channel(), ok_post(), ok_post()])
    await a.send(CHANNEL, "a")
    await a.send(CHANNEL, "b")
    assert [r.method for r in rec.requests] == ["GET", "POST", "POST"]


@pytest.mark.asyncio
async def test_path_segments_are_url_encoded():
    a, rec, _ = adapter([ok_channel(), ok_post()])
    await a.send(ChannelRef(team="a b", channel="x/y"), "t")
    assert rec.requests[0].url.raw_path.decode().startswith("/api/v4/teams/name/a%20b/channels/name/x%2Fy")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_retries_retryable_status_then_succeeds(status):
    a, rec, sleeps = adapter([ok_channel(), httpx.Response(status), ok_post()])
    await a.send(CHANNEL, "t")
    assert len(rec.requests) == 3 and len(sleeps) == 1


@pytest.mark.asyncio
async def test_retry_after_header_respected_and_capped():
    a, _, sleeps = adapter([ok_channel(), httpx.Response(429, headers={"Retry-After": "2"}), ok_post()])
    await a.send(CHANNEL, "t")
    assert sleeps == [2]
    a, _, sleeps = adapter([ok_channel(), httpx.Response(429, headers={"Retry-After": "999"}), ok_post()])
    await a.send(CHANNEL, "t")
    assert sleeps == [30]


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts_as_retryable_error():
    a, rec, _ = adapter([ok_channel(), httpx.Response(500), httpx.Response(500), httpx.Response(500)], max_attempts=3)
    with pytest.raises(SendError) as exc:
        await a.send(CHANNEL, "t")
    assert exc.value.retryable is True and exc.value.reason == "http_5xx"
    assert len(rec.requests) == 4


@pytest.mark.asyncio
async def test_transport_error_is_retryable():
    a, _, _ = adapter([httpx.ConnectError("boom"), httpx.ConnectError("boom"), httpx.ConnectError("boom")], max_attempts=3)
    with pytest.raises(SendError) as exc:
        await a.send(CHANNEL, "t")
    assert exc.value.retryable is True and exc.value.reason == "network"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [(401, "auth"), (403, "auth"), (404, "channel_not_found"), (400, "http_4xx")])
async def test_permanent_errors_not_retried(status, reason):
    a, rec, _ = adapter([httpx.Response(status, text="detail with " + TOKEN)])
    with pytest.raises(SendError) as exc:
        await a.send(CHANNEL, "t")
    assert exc.value.retryable is False and exc.value.reason == reason
    assert len(rec.requests) == 1
    assert TOKEN not in str(exc.value)


@pytest.mark.asyncio
async def test_permanent_post_error_drops_cached_channel_id():
    a, rec, _ = adapter([ok_channel(), httpx.Response(404), ok_channel(), ok_post()])
    with pytest.raises(SendError):
        await a.send(CHANNEL, "t")
    await a.send(CHANNEL, "t")
    assert [r.method for r in rec.requests] == ["GET", "POST", "GET", "POST"]


@pytest.mark.asyncio
async def test_retryable_post_error_keeps_cached_channel_id():
    a, rec, _ = adapter([ok_channel(), httpx.Response(500), httpx.Response(500), httpx.Response(500), ok_post()],
                        max_attempts=3)
    with pytest.raises(SendError):
        await a.send(CHANNEL, "t")
    await a.send(CHANNEL, "t")
    assert [r.method for r in rec.requests] == ["GET", "POST", "POST", "POST", "POST"]


@pytest.mark.asyncio
async def test_channel_lookup_without_id_is_error():
    a, _, _ = adapter([httpx.Response(200, json={})])
    with pytest.raises(SendError) as exc:
        await a.send(CHANNEL, "t")
    assert exc.value.retryable is False


@pytest.mark.asyncio
async def test_invalid_json_from_lookup_is_error_without_leaking():
    a, _, _ = adapter([httpx.Response(200, text="<html>" + TOKEN)])
    with pytest.raises(SendError) as exc:
        await a.send(CHANNEL, "t")
    assert TOKEN not in str(exc.value)


def test_requires_token():
    with pytest.raises(ValueError):
        MattermostAdapter("https://chat.local", "")


def test_parse_command_form_decodes_korean_and_first_value():
    from adapters.mattermost import parse_command_form
    form = parse_command_form("team_domain=devteam&text=%EC%98%A4%EB%8A%98&text=second&empty=".encode())
    assert form == {"team_domain": "devteam", "text": "오늘", "empty": ""}


def test_parse_command_form_rejects_oversized_and_too_many_fields():
    from adapters.mattermost import MAX_FORM_BYTES, parse_command_form
    with pytest.raises(ValueError):
        parse_command_form(b"a=" + b"x" * MAX_FORM_BYTES)
    with pytest.raises(ValueError):
        parse_command_form("&".join(f"k{i}=v" for i in range(60)).encode())


def test_parse_command_form_tolerates_invalid_utf8():
    from adapters.mattermost import parse_command_form
    assert parse_command_form(b"text=%ff%fe")["text"]


@pytest.mark.parametrize("presented,expected,ok", [
    ("abc", "abc", True), ("abd", "abc", False), ("", "abc", False), ("abc", "", False), ("", "", False)])
def test_verify_command_token(presented, expected, ok):
    from adapters.mattermost import verify_command_token
    assert verify_command_token(presented, expected) is ok


def test_inbound_from_form_requires_core_fields_and_lowercases():
    from adapters.base import InboundCommand
    from adapters.mattermost import inbound_from_form
    full = {"team_domain": "DEVTEAM", "channel_name": "DX-Room", "user_id": "u1", "text": "오늘"}
    assert inbound_from_form(full) == InboundCommand("devteam", "dx-room", "u1", "오늘")
    assert inbound_from_form({**full, "text": ""}).text == ""
    for missing in ("team_domain", "channel_name", "user_id"):
        assert inbound_from_form({k: v for k, v in full.items() if k != missing}) is None
