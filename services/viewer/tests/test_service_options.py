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
        assert 'data-action="marp-pdf"' in resp.text
        assert "슬라이드 미리보기" in resp.text and 'data-action="marp-preview"' in resp.text
        assert 'data-action="marp-publish"' not in resp.text

    def test_marp_off_hides_buttons_and_shows_download(self, client, mock_repo_manager, monkeypatch):
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer,cdn")
        mock_repo_manager.read_file_text.return_value = MARP_DOC
        resp = client.get("/test-repo/docs/deck.md")
        assert resp.status_code == 200
        assert 'data-action="marp-pdf"' not in resp.text
        assert 'data-action="marp-preview"' not in resp.text
        assert 'data-action="marp-publish"' not in resp.text
        assert 'href="/view/api/repos/test-repo/download?path=docs/deck.md"' in resp.text
        assert "format=pdf" not in resp.text

    def test_drawio_button_follows_option(self, monkeypatch):
        from app import _build_toolbar
        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        assert 'href="/drawio/"' in _build_toolbar("test-repo", "a.drawio", False, {}, "")
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer,cdn")
        toolbar = _build_toolbar("test-repo", "a.drawio", False, {}, "")
        assert 'href="/drawio/"' not in toolbar
        assert "내려받기" in toolbar

    def test_publish_dialog_only_with_publish_repo(self, client, mock_repo_manager, monkeypatch):
        import app as viewer_app
        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        monkeypatch.setattr(viewer_app.config.marp, "publish_repo", "https://git.test/team/slides.git")
        mock_repo_manager.read_file_text.return_value = MARP_DOC
        text = client.get("/test-repo/docs/deck.md").text
        assert 'data-action="marp-publish"' in text
        assert '<dialog id="publish-dialog"' in text and "team/slides" in text
        assert "_published/slides/deck.html" in text

    @pytest.mark.parametrize("repo_url, shown", [
        ("https://publisher:secret@git.internal/slides.git", "slides"),
        ("https://token@git.internal/team/slides.git/", "team/slides"),
        ("git@git.internal:team/slides.git", "team/slides"),
    ])
    def test_publish_dialog_hides_repo_credentials(self, client, mock_repo_manager, monkeypatch, repo_url, shown):
        import app as viewer_app
        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        monkeypatch.setattr(viewer_app.config.marp, "publish_repo", repo_url)
        mock_repo_manager.read_file_text.return_value = MARP_DOC
        text = client.get("/test-repo/docs/deck.md").text
        assert f"<dd>{shown} (" in text
        assert "secret" not in text and "publisher:" not in text and "token@" not in text and "git@" not in text

    def test_meeting_doc_primary_action(self, client, mock_repo_manager):
        from app import _build_toolbar
        html = _build_toolbar("test-repo", "m.md", True, {}, "", meeting_href="/view/test-repo/m.md?mode=meeting")
        assert html.startswith('<a class="btn btn-primary" href="/view/test-repo/m.md?mode=meeting">회의 모드로 열기</a>')


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

    @staticmethod
    def _fake_marp(monkeypatch, status_code=200):
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

            async def post(self, url, json, headers=None):
                calls.append((url, headers or {}))
                return SimpleNamespace(status_code=status_code, content=b"%PDF-1.4")

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(viewer_app, "_prepare_pdf_markdown", lambda *args: "# A")
        return calls

    def test_pdf_calls_marp_when_enabled(self, client, monkeypatch):
        calls = self._fake_marp(monkeypatch)
        resp = client.get("/api/repos/test-repo/download", params={"path": "docs/a.md", "format": "pdf"})
        assert resp.status_code == 200
        assert calls and calls[0][0].endswith("/api/marp/convert")

    def test_pdf_forwards_viewer_groups_to_marp(self, client, monkeypatch):
        calls = self._fake_marp(monkeypatch)
        client.get("/api/repos/test-repo/download", params={"path": "docs/a.md", "format": "pdf"},
                   headers={"X-Forwarded-Groups": "synctx-publisher"})
        assert calls[0][1].get("X-Forwarded-Groups") == "synctx-publisher"

    def test_pdf_forbidden_by_marp_is_403(self, client, monkeypatch):
        self._fake_marp(monkeypatch, status_code=403)
        resp = client.get("/api/repos/test-repo/download", params={"path": "docs/a.md", "format": "pdf"})
        assert resp.status_code == 403


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

    def test_repository_id_shadowing_reserved_path_warns(self, monkeypatch, caplog):
        from app import _check_service_dependencies
        from synctx_common.config import SynctxConfig, ViewerConfig, RepoProfile, RepoAuthConfig
        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        cfg = SynctxConfig(
            viewer=ViewerConfig(cdn_base_url="/cdn"),
            repositories=[RepoProfile(id="search", name="Search", url="https://git.test/s.git",
                                      auth=RepoAuthConfig(type="none"))],
        )
        with caplog.at_level("WARNING"):
            _check_service_dependencies(cfg)
        assert any("search" in record.getMessage() for record in caplog.records)

    def test_startup_fails_fast(self, monkeypatch):
        import app as viewer_app
        monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer,marp")
        with pytest.raises(RuntimeError, match="cdn_base_url"):
            with TestClient(viewer_app.app):
                pass


def test_bundled_assets_do_not_require_cdn(monkeypatch):
    from app import _check_service_dependencies
    from synctx_common.config import SynctxConfig, ViewerConfig
    monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer")
    _check_service_dependencies(SynctxConfig(viewer=ViewerConfig(asset_source="bundled")))
