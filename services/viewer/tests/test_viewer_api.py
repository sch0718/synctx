"""Viewer API 단위 테스트."""

import sys
import os
from unittest.mock import MagicMock, patch
from pathlib import PurePosixPath

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    """Viewer config 모킹."""
    from synctx_common.config import SynctxConfig, ViewerConfig, AuthzConfig, RuntimeConfig, RepoProfile, RepoAuthConfig
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
    return manager


@pytest.fixture
def client(mock_repo_manager, monkeypatch):
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "repo_managers", {"test-repo": mock_repo_manager})
    return TestClient(viewer_app.app, raise_server_exceptions=False)


class TestHealthEndpoints:
    def test_healthz(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readyz(self, client):
        resp = client.get("/readyz")
        assert resp.status_code == 200

    def test_version(self, client):
        resp = client.get("/version")
        assert resp.status_code == 200
        assert "version" in resp.json()

    def test_metrics(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "synctx_http_requests_total" in resp.text


class TestCSPHeaders:
    def test_html_response_has_csp(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/readme.md")
        assert resp.status_code == 200
        assert "Content-Security-Policy" in resp.headers
        assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_json_response_no_csp(self, client):
        resp = client.get("/api/repos")
        assert "Content-Security-Policy" not in resp.headers


class TestMaxRenderSize:
    def test_file_within_limit_renders(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Small file"
        resp = client.get("/test-repo/docs/small.md")
        assert resp.status_code == 200

    def test_file_exceeding_limit_returns_413(self, client, mock_repo_manager):
        large_content = "x" * 2000
        mock_repo_manager.read_file_text.return_value = large_content
        resp = client.get("/test-repo/docs/large.md")
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()


class TestHTMLSanitization:
    def test_script_tags_removed(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = '# Title\n\n<script>alert("xss")</script>\n\nSafe content'
        resp = client.get("/test-repo/docs/xss.md")
        assert resp.status_code == 200
        assert '<script>alert("xss")</script>' not in resp.text
        assert "Safe content" in resp.text

    def test_sanitize_function_removes_iframe(self):
        from app import _sanitize_html
        result = _sanitize_html('<div>safe</div><iframe src="evil.com"></iframe>')
        assert "<iframe" not in result
        assert "safe" in result

    def test_sanitize_function_removes_event_handlers(self):
        from app import _sanitize_html
        result = _sanitize_html('<div onload="evil()">content</div>')
        assert "onload" not in result
        assert "content" in result

    def test_sanitize_function_removes_script(self):
        from app import _sanitize_html
        result = _sanitize_html('<p>ok</p><script>bad()</script><p>also ok</p>')
        assert "<script" not in result
        assert "ok" in result


class TestAPIRoutes:
    def test_repos_list(self, client):
        resp = client.get("/api/repos")
        assert resp.status_code == 200
        body = resp.json()
        repos = body["repositories"]
        assert any(r["id"] == "test-repo" for r in repos)

    def test_repo_not_found(self, client):
        resp = client.get("/nonexistent/docs/test.md")
        assert resp.status_code == 404
