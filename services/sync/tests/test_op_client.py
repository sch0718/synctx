"""OpenProject Client 단위 테스트 (httpx mock)."""

import sys
import os
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from synctx_common.config import OpenProjectTargetConfig, TargetAuthConfig
from targets.openproject import OpenProjectClient
from sync_engine import SyncError


def _make_client(monkeypatch):
    monkeypatch.setenv("OP_TOKEN", "test-token")
    config = OpenProjectTargetConfig(
        base_url="https://op.internal",
        auth=TargetAuthConfig(type="token", credential_env="OP_TOKEN"),
        protected_fields=["assignee", "status"],
        type_map={"task": 1},
        status_map={"open": 1, "closed": 6},
        priority_map={"high": 1},
    )
    return OpenProjectClient(config)


@pytest.mark.asyncio
class TestGetWorkPackageByExternalKey:
    async def test_found(self, monkeypatch):
        client = _make_client(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "_embedded": {"elements": [{"id": 42, "subject": "Task"}]}
        }
        client.client.get = AsyncMock(return_value=mock_resp)

        result = await client.get_work_package_by_external_key("repo:path.md")
        assert result["id"] == 42

    async def test_not_found(self, monkeypatch):
        client = _make_client(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"_embedded": {"elements": []}}
        client.client.get = AsyncMock(return_value=mock_resp)

        result = await client.get_work_package_by_external_key("repo:missing.md")
        assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,error,error_type", [
    (503, b"", httpx.HTTPStatusError, None),
    (200, b"{", SyncError, "transient_error"),
    (200, b"{}", SyncError, "transient_error"),
    (200, b'{"_embedded":{"elements":[null]}}', SyncError, "transient_error"),
    (200, b'{"_embedded":{"elements":[{"id":1},{"id":2}]}}', SyncError, "conflict_error"),
])
async def test_lookup_failure_does_not_create_work_package(monkeypatch, status, body, error, error_type):
    client = _make_client(monkeypatch)
    request = httpx.Request("GET", "https://op.internal/api/v3/work_packages")
    client.client.get = AsyncMock(return_value=httpx.Response(status, content=body, request=request))
    client.client.post = AsyncMock(return_value=MagicMock())
    client.client.patch = AsyncMock(return_value=MagicMock())

    with pytest.raises(error) as raised:
        await client.upsert_work_package("proj-1", "repo:task.md", {"subject": "New"})
    if error_type:
        assert raised.value.error_type == error_type
    client.client.post.assert_not_awaited()
    client.client.patch.assert_not_awaited()


@pytest.mark.asyncio
class TestGetVersionByName:
    async def test_found(self, monkeypatch):
        client = _make_client(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "_embedded": {"elements": [
                {"id": 1, "name": "v1.0"},
                {"id": 2, "name": "v2.0"},
            ]}
        }
        client.client.get = AsyncMock(return_value=mock_resp)

        assert await client.get_version_by_name("proj-1", "v2.0") == 2

    async def test_not_found(self, monkeypatch):
        client = _make_client(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"_embedded": {"elements": []}}
        client.client.get = AsyncMock(return_value=mock_resp)

        assert await client.get_version_by_name("proj-1", "v9.0") is None


@pytest.mark.asyncio
class TestUpsertWorkPackage:
    async def test_create_new(self, monkeypatch):
        client = _make_client(monkeypatch)
        client.get_work_package_by_external_key = AsyncMock(return_value=None)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client.client.post = AsyncMock(return_value=mock_resp)

        links = {"assignee": {"href": "/api/v3/users/10"}}
        result = await client.upsert_work_package("proj-1", "repo:task.md", {"subject": "New", "_links": links})
        assert result is True
        assert client.client.post.call_args.kwargs["json"]["_links"] == links

    @pytest.mark.parametrize("other_links", [{}, {"type": {"href": "/api/v3/types/1"}}])
    async def test_update_existing(self, monkeypatch, other_links):
        client = _make_client(monkeypatch)
        client.get_work_package_by_external_key = AsyncMock(
            return_value={"id": 42, "lockVersion": 5, "_links": {"assignee": {"href": None}}}
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client.client.patch = AsyncMock(return_value=mock_resp)

        links = {"assignee": {"href": "/api/v3/users/10"}, **other_links}
        payload = {"subject": "Updated", "status": "closed", "_links": links}
        assert await client.upsert_work_package("proj-1", "repo:task.md", payload)
        sent = client.client.patch.call_args.kwargs["json"]
        assert sent["lockVersion"] == 5
        assert sent["subject"] == "Updated" and "status" not in sent
        if other_links:
            assert sent["_links"] == other_links
        else:
            assert "_links" not in sent
        assert "assignee" in links


@pytest.mark.asyncio
class TestMarkDeleted:
    async def test_mark_existing(self, monkeypatch):
        client = _make_client(monkeypatch)
        client.get_work_package_by_external_key = AsyncMock(
            return_value={"id": 99, "lockVersion": 3}
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client.client.patch = AsyncMock(return_value=mock_resp)

        result = await client.mark_deleted("proj-1", "repo:deleted.md")
        assert result is True
        call_args = client.client.patch.call_args
        patch_payload = call_args[1]["json"]
        assert "/api/v3/statuses/6" in patch_payload["_links"]["status"]["href"]

    async def test_mark_nonexistent(self, monkeypatch):
        client = _make_client(monkeypatch)
        client.get_work_package_by_external_key = AsyncMock(return_value=None)

        result = await client.mark_deleted("proj-1", "repo:missing.md")
        assert result is False
