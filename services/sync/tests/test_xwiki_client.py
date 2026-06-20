"""XWiki Client 단위 테스트 (httpx mock)."""

import hashlib
import sys
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from synctx_common.config import XWikiTargetConfig, TargetAuthConfig
from targets.xwiki import XWikiClient


def _make_client(monkeypatch):
    monkeypatch.setenv("XWIKI_CRED", "admin:secret")
    config = XWikiTargetConfig(
        base_url="https://xwiki.internal",
        auth=TargetAuthConfig(type="basic", credential_env="XWIKI_CRED"),
    )
    return XWikiClient(config)


@pytest.mark.asyncio
class TestGetPage:
    async def test_found(self, monkeypatch):
        client = _make_client(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"title": "Test", "content": "body"}
        mock_resp.raise_for_status = MagicMock()
        client.client.get = AsyncMock(return_value=mock_resp)

        result = await client.get_page("MySpace", "MyPage")
        assert result["title"] == "Test"

    async def test_not_found(self, monkeypatch):
        client = _make_client(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        client.client.get = AsyncMock(return_value=mock_resp)

        result = await client.get_page("Missing", "Page")
        assert result is None


@pytest.mark.asyncio
class TestUpsertPage:
    async def test_create_new(self, monkeypatch):
        client = _make_client(monkeypatch)
        client.get_page = AsyncMock(return_value=None)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client.client.put = AsyncMock(return_value=mock_resp)

        result = await client.upsert_page("Space", "Page", "Title", "Content")
        assert result is True
        client.client.put.assert_called_once()

    async def test_update_existing(self, monkeypatch):
        client = _make_client(monkeypatch)
        client.get_page = AsyncMock(return_value={"title": "Old", "content": "Old content"})
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client.client.put = AsyncMock(return_value=mock_resp)

        result = await client.upsert_page("Space", "Page", "New Title", "New Content")
        assert result is True

    async def test_skip_unchanged(self, monkeypatch):
        client = _make_client(monkeypatch)
        client.get_page = AsyncMock(return_value={"title": "Title", "content": "Content"})

        result = await client.upsert_page("Space", "Page", "Title", "Content")
        assert result is False


@pytest.mark.asyncio
class TestDeletePage:
    async def test_delete_existing(self, monkeypatch):
        client = _make_client(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        client.client.delete = AsyncMock(return_value=mock_resp)

        result = await client.delete_page("Space", "Page")
        assert result is True

    async def test_delete_not_found(self, monkeypatch):
        client = _make_client(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        client.client.delete = AsyncMock(return_value=mock_resp)

        result = await client.delete_page("Space", "Missing")
        assert result is False


@pytest.mark.asyncio
class TestUploadAttachment:
    async def test_upload_new(self, monkeypatch):
        client = _make_client(monkeypatch)
        client.get_attachment = AsyncMock(return_value=None)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client.client.post = AsyncMock(return_value=mock_resp)

        result = await client.upload_attachment("Space", "Page", "img.svg", b"<svg/>", "image/svg+xml")
        assert result is True

    async def test_skip_if_sha_matches(self, monkeypatch):
        content = b"<svg>test</svg>"
        sha = hashlib.sha256(content).hexdigest()

        client = _make_client(monkeypatch)
        client.get_attachment = AsyncMock(return_value={"size": len(content)})

        dl_resp = MagicMock()
        dl_resp.status_code = 200
        dl_resp.content = content
        client.client.get = AsyncMock(return_value=dl_resp)

        result = await client.upload_attachment("Space", "Page", "img.svg", content, "image/svg+xml")
        assert result is False

    async def test_upload_if_sha_differs(self, monkeypatch):
        old_content = b"old"
        new_content = b"new"

        client = _make_client(monkeypatch)
        client.get_attachment = AsyncMock(return_value={"size": len(old_content)})

        dl_resp = MagicMock()
        dl_resp.status_code = 200
        dl_resp.content = old_content
        client.client.get = AsyncMock(return_value=dl_resp)

        upload_resp = MagicMock()
        upload_resp.raise_for_status = MagicMock()
        client.client.post = AsyncMock(return_value=upload_resp)

        result = await client.upload_attachment("Space", "Page", "img.svg", new_content, "image/svg+xml")
        assert result is True
