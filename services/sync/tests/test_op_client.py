"""OpenProject Client 단위 테스트 (httpx mock)."""

import sys
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from synctx_common.config import OpenProjectTargetConfig, TargetAuthConfig
from targets.openproject import OpenProjectClient


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
class TestGetUserByLogin:
    async def test_found(self, monkeypatch):
        client = _make_client(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "_embedded": {"elements": [{"id": 10, "login": "alice"}]}
        }
        client.client.get = AsyncMock(return_value=mock_resp)

        assert await client.get_user_by_login("alice") == 10

    async def test_not_found(self, monkeypatch):
        client = _make_client(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"_embedded": {"elements": []}}
        client.client.get = AsyncMock(return_value=mock_resp)

        assert await client.get_user_by_login("nobody") is None


@pytest.mark.asyncio
class TestProtectedFields:
    async def test_protected_field_preserved(self, monkeypatch):
        client = _make_client(monkeypatch)
        existing = {"assignee": "Alice", "status": "open", "subject": "Old"}
        payload = {"assignee": "Bob", "status": "closed", "subject": "New"}

        filtered = client._apply_protected_fields(existing, payload)
        assert "assignee" not in filtered
        assert "status" not in filtered
        assert filtered["subject"] == "New"

    async def test_protected_field_set_if_empty(self, monkeypatch):
        client = _make_client(monkeypatch)
        existing = {"assignee": None, "status": None}
        payload = {"assignee": "Bob", "status": "open"}

        filtered = client._apply_protected_fields(existing, payload)
        assert filtered["assignee"] == "Bob"
        assert filtered["status"] == "open"


@pytest.mark.asyncio
class TestUpsertWorkPackage:
    async def test_create_new(self, monkeypatch):
        client = _make_client(monkeypatch)
        client.get_work_package_by_external_key = AsyncMock(return_value=None)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client.client.post = AsyncMock(return_value=mock_resp)

        result = await client.upsert_work_package("proj-1", "repo:task.md", {"subject": "New"})
        assert result is True
        client.client.post.assert_called_once()

    async def test_update_existing(self, monkeypatch):
        client = _make_client(monkeypatch)
        client.get_work_package_by_external_key = AsyncMock(
            return_value={"id": 42, "lockVersion": 5, "assignee": "Alice"}
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client.client.patch = AsyncMock(return_value=mock_resp)

        result = await client.upsert_work_package("proj-1", "repo:task.md", {"subject": "Updated"})
        assert result is True
        call_args = client.client.patch.call_args
        assert call_args[1]["json"]["lockVersion"] == 5


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
