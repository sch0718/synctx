"""배포 옵션(SYNCTX_ENABLED_SERVICES)에 따른 Viewer 동작 테스트."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

MARP_DOC = "---\nmarp: true\n---\n# Slide\n"


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    from synctx_common.config import SynctxConfig, ViewerConfig, AuthzConfig, RepoProfile, RepoAuthConfig
    mock_config = SynctxConfig(
        authz=AuthzConfig(enabled=False),
        viewer=ViewerConfig(max_render_size_bytes=1024, cdn_base_url="/cdn"),
        repositories=[
            RepoProfile(
                id="test-repo",
                name="Test Repo",
                url="https://git.test/repo.git",
                auth=RepoAuthConfig(type="none"),
                watch_paths=["docs/"],
            )
        ],
    )
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "config", mock_config)


@pytest.fixture
def mock_repo_manager():
    manager = MagicMock()
    manager.profile = MagicMock()
    manager.profile.default_ref = "main"
    manager.head_sha_value = "abc123"
    manager.status = "ready"
    manager.file_size.return_value = 100
    return manager


@pytest.fixture
def client(mock_repo_manager, monkeypatch):
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "repo_managers", {"test-repo": mock_repo_manager})
    return TestClient(viewer_app.app, raise_server_exceptions=False)


class TestEnabledServices:
    def test_unset_means_all_enabled(self, monkeypatch):
        from app import _enabled_services, _service_enabled
        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        assert _enabled_services() is None
        assert _service_enabled("marp")

    def test_blank_means_all_enabled(self, monkeypatch):
        from app import _enabled_services, _service_enabled
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", " , ")
        assert _enabled_services() is None
        assert _service_enabled("drawio")

    def test_list_is_normalized(self, monkeypatch):
        from app import _enabled_services, _service_enabled
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", " Viewer , marp ")
        assert _enabled_services() == frozenset({"viewer", "marp"})
        assert _service_enabled("marp")
        assert not _service_enabled("drawio")


class TestToolbar:
    def test_marp_buttons_when_unset(self, client, mock_repo_manager, monkeypatch):
        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        mock_repo_manager.read_file_text.return_value = MARP_DOC
        resp = client.get("/test-repo/docs/deck.md")
        assert resp.status_code == 200
        assert "PDF (Marp)" in resp.text
        assert "출판 (Marp)" in resp.text

    def test_marp_off_hides_buttons_and_shows_download(self, client, mock_repo_manager, monkeypatch):
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer,cdn")
        mock_repo_manager.read_file_text.return_value = MARP_DOC
        resp = client.get("/test-repo/docs/deck.md")
        assert resp.status_code == 200
        assert "PDF (Marp)" not in resp.text
        assert "미리보기 (Marp)" not in resp.text
        assert "출판 (Marp)" not in resp.text
        assert 'href="/view/api/repos/test-repo/download?path=docs/deck.md"' in resp.text

    def test_drawio_button_follows_option(self, monkeypatch):
        from app import _build_toolbar
        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        assert 'href="/drawio/"' in _build_toolbar("test-repo", "a.drawio", False, {}, "")
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer,cdn")
        toolbar = _build_toolbar("test-repo", "a.drawio", False, {}, "")
        assert 'href="/drawio/"' not in toolbar
        assert "다운로드" in toolbar


class TestPdfDownload:
    def test_pdf_returns_503_when_marp_off(self, client, monkeypatch):
        import httpx
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer,cdn")
        called = []
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: called.append(1))
        resp = client.get("/api/repos/test-repo/download", params={"path": "docs/a.md", "format": "pdf"})
        assert resp.status_code == 503
        assert "Marp 서비스가 꺼져 있어" in resp.json()["detail"]
        assert called == []

    def test_pdf_calls_marp_when_enabled(self, client, monkeypatch):
        import httpx
        import app as viewer_app
        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        calls = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json):
                calls.append(url)
                return SimpleNamespace(status_code=200, content=b"%PDF-1.4")

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(viewer_app, "_prepare_pdf_markdown", lambda *args: "# A")
        resp = client.get("/api/repos/test-repo/download", params={"path": "docs/a.md", "format": "pdf"})
        assert resp.status_code == 200
        assert calls and calls[0].endswith("/api/marp/convert")


class TestStartupCheck:
    @staticmethod
    def _config(cdn_base_url):
        from synctx_common.config import SynctxConfig, ViewerConfig
        return SynctxConfig(viewer=ViewerConfig(cdn_base_url=cdn_base_url))

    def test_relative_cdn_url_with_cdn_off_raises(self, monkeypatch):
        from app import _check_service_dependencies
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer,marp")
        with pytest.raises(RuntimeError, match="cdn_base_url"):
            _check_service_dependencies(self._config("/cdn"))

    @pytest.mark.parametrize("url", ["https://cdn.internal/libs", "//cdn.internal/libs"])
    def test_absolute_cdn_url_with_cdn_off_is_ok(self, monkeypatch, url):
        from app import _check_service_dependencies
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer,marp")
        _check_service_dependencies(self._config(url))

    @pytest.mark.parametrize("enabled", [None, "viewer,cdn"])
    def test_relative_cdn_url_with_cdn_on_is_ok(self, monkeypatch, enabled):
        from app import _check_service_dependencies
        if enabled is None:
            monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        else:
            monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", enabled)
        _check_service_dependencies(self._config("/cdn"))

    def test_startup_fails_fast(self, monkeypatch):
        import app as viewer_app
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer,marp")
        with pytest.raises(RuntimeError, match="cdn_base_url"):
            with TestClient(viewer_app.app):
                pass
