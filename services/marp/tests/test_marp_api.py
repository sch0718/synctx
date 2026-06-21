"""Marp Service API 통합 테스트.

subprocess(marp CLI)와 GitRepoManager를 mock 처리하여
FastAPI 엔드포인트 동작을 검증한다.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import app, cleanup_file


@pytest.fixture(autouse=True)
def _disable_authz():
    import app as marp_app
    original = marp_app.config.authz.enabled
    marp_app.config.authz.enabled = False
    yield
    marp_app.config.authz.enabled = original


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestConvertPdf:
    def test_convert_returns_pdf(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("MARP_WORK_DIR", str(tmp_path))

        def fake_run(cmd, **kwargs):
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_bytes(b"%PDF-1.4 fake pdf content")
            return MagicMock(returncode=0)

        with patch("app.subprocess.run", side_effect=fake_run):
            resp = client.post("/api/marp/convert", json={"markdown": "---\nmarp: true\n---\n# Slide"})

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    def test_convert_timeout(self, client, tmp_path, monkeypatch):
        import subprocess
        monkeypatch.setenv("MARP_WORK_DIR", str(tmp_path))

        with patch("app.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="marp", timeout=120)):
            resp = client.post("/api/marp/convert", json={"markdown": "# Slide"})

        assert resp.status_code == 504

    def test_convert_cli_error(self, client, tmp_path, monkeypatch):
        import subprocess
        monkeypatch.setenv("MARP_WORK_DIR", str(tmp_path))

        with patch("app.subprocess.run", side_effect=subprocess.CalledProcessError(1, "marp", stderr=b"error")):
            resp = client.post("/api/marp/convert", json={"markdown": "# Slide"})

        assert resp.status_code == 500

    def test_convert_too_large(self, client, monkeypatch):
        monkeypatch.setattr("app.config.marp.max_input_size_bytes", 10)
        resp = client.post("/api/marp/convert", json={"markdown": "x" * 100})
        assert resp.status_code == 400


class TestPreviewHtml:
    def test_preview_returns_html(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("MARP_WORK_DIR", str(tmp_path))

        def fake_run(cmd, **kwargs):
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_text("<html><body>Preview</body></html>")
            return MagicMock(returncode=0)

        with patch("app.subprocess.run", side_effect=fake_run):
            resp = client.post("/api/marp/preview", json={"markdown": "---\nmarp: true\n---\n# Hi"})

        assert resp.status_code == 200
        assert "Preview" in resp.text

    def test_preview_cli_error(self, client, tmp_path, monkeypatch):
        import subprocess
        monkeypatch.setenv("MARP_WORK_DIR", str(tmp_path))

        with patch("app.subprocess.run", side_effect=subprocess.CalledProcessError(1, "marp", stderr=b"fail")):
            resp = client.post("/api/marp/preview", json={"markdown": "# Slide"})

        assert resp.status_code == 500


class TestPublish:
    def test_publish_success(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("MARP_WORK_DIR", str(tmp_path))
        monkeypatch.setattr("app.config.marp.publish_repo", "https://git.internal/slides.git")
        monkeypatch.setattr("app.config.marp.worktree_path", str(tmp_path / "worktree"))

        def fake_run(cmd, **kwargs):
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_text("<html>Published</html>")
            return MagicMock(returncode=0)

        mock_manager = MagicMock()
        mock_manager.ensure = AsyncMock()
        mock_manager.commit_and_push = MagicMock()

        with patch("app.subprocess.run", side_effect=fake_run), \
             patch("app.get_publish_manager", return_value=mock_manager):
            resp = client.post("/api/marp/publish", json={
                "markdown": "# Slide",
                "filename": "my-deck",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "my-deck" in data["url"]
        mock_manager.commit_and_push.assert_called_once()

    def test_publish_no_repo_configured(self, client, monkeypatch):
        monkeypatch.setattr("app.config.marp.publish_repo", "")
        resp = client.post("/api/marp/publish", json={
            "markdown": "# Slide",
            "filename": "deck",
        })
        assert resp.status_code == 500
        assert "not configured" in resp.json()["detail"]


class TestListPublished:
    def test_list_published(self, client, monkeypatch):
        monkeypatch.setattr("app.config.marp.publish_repo", "https://git.internal/slides.git")

        mock_entry = MagicMock()
        mock_entry.type = "file"
        mock_entry.name = "deck.html"
        mock_entry.path = "_published/slides/deck.html"

        mock_manager = MagicMock()
        mock_manager.ensure = AsyncMock()
        mock_manager.list_tree = MagicMock(return_value=[mock_entry])

        with patch("app.get_publish_manager", return_value=mock_manager):
            resp = client.get("/api/marp/published")

        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["path"] == "_published/slides/deck.html"


class TestUnpublish:
    def test_unpublish_success(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.marp.publish_repo", "https://git.internal/slides.git")
        monkeypatch.setattr("app.config.marp.worktree_path", str(tmp_path / "worktree"))

        slides_dir = tmp_path / "slides"
        slides_dir.mkdir()
        target_file = slides_dir / "deck.html"
        target_file.write_text("<html>content</html>")

        mock_manager = MagicMock()
        mock_manager.ensure = AsyncMock()
        mock_manager.validate_path = MagicMock(return_value="slides/deck.html")
        mock_manager.repo_dir = tmp_path
        mock_manager._run_git = MagicMock()
        mock_manager.profile = MagicMock()
        mock_manager.profile.default_ref = "main"

        with patch("app.get_publish_manager", return_value=mock_manager):
            resp = client.delete("/api/marp/published/deck.html")

        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_unpublish_not_found(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.marp.publish_repo", "https://git.internal/slides.git")
        monkeypatch.setattr("app.config.marp.worktree_path", str(tmp_path / "worktree"))

        mock_manager = MagicMock()
        mock_manager.ensure = AsyncMock()
        mock_manager.validate_path = MagicMock(return_value="slides/nonexistent.html")
        mock_manager.repo_dir = tmp_path

        with patch("app.get_publish_manager", return_value=mock_manager):
            resp = client.delete("/api/marp/published/nonexistent.html")

        assert resp.status_code == 404


class TestAuthz:
    def test_convert_blocked_without_role(self, client, _disable_authz, monkeypatch):
        import app as marp_app
        marp_app.config.authz.enabled = True
        marp_app.config.authz.role_mappings.publisher = ["publisher"]
        marp_app.config.authz.role_mappings.platform_admin = ["admin"]

        resp = client.post(
            "/api/marp/convert",
            json={"markdown": "# Slide"},
            headers={"X-Forwarded-Groups": "viewer"},
        )
        assert resp.status_code == 403

    def test_convert_allowed_with_publisher_role(self, client, _disable_authz, tmp_path, monkeypatch):
        import app as marp_app
        marp_app.config.authz.enabled = True
        marp_app.config.authz.role_mappings.publisher = ["publisher"]
        marp_app.config.authz.role_mappings.platform_admin = ["admin"]
        monkeypatch.setenv("MARP_WORK_DIR", str(tmp_path))

        def fake_run(cmd, **kwargs):
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_bytes(b"%PDF-1.4 fake")
            return MagicMock(returncode=0)

        with patch("app.subprocess.run", side_effect=fake_run):
            resp = client.post(
                "/api/marp/convert",
                json={"markdown": "# Slide"},
                headers={"X-Forwarded-Groups": "publisher"},
            )
        assert resp.status_code == 200


class TestCleanupFile:
    def test_cleanup_existing(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_text("data")
        cleanup_file(str(f))
        assert not f.exists()

    def test_cleanup_nonexistent(self, tmp_path):
        cleanup_file(str(tmp_path / "missing.pdf"))


class TestConvertHtml:
    def test_convert_html_format(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("MARP_WORK_DIR", str(tmp_path))

        async def mock_write_and_run(processed, md_path, out_path, flags, timeout):
            Path(out_path).write_text("<html><body>slides</body></html>")

        with patch("app.preprocess_mermaid", new_callable=AsyncMock, return_value=("# Slide", [])):
            with patch("app.write_and_run_marp", side_effect=mock_write_and_run):
                resp = client.post("/api/marp/convert", json={"markdown": "# Slide", "format": "html"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_convert_invalid_format(self, client):
        resp = client.post("/api/marp/convert", json={"markdown": "# Slide", "format": "pptx"})
        assert resp.status_code == 400
        assert "Unsupported format" in resp.json()["detail"]


class TestThemesAPI:
    def test_list_themes(self, client):
        resp = client.get("/api/marp/themes")
        assert resp.status_code == 200
        body = resp.json()
        assert "themes" in body
        names = [t["name"] for t in body["themes"]]
        assert "default" in names
        assert "gaia" in names
        assert "uncover" in names

    def test_themes_no_auth_required(self, client, _disable_authz):
        import app as marp_app
        marp_app.config.authz.enabled = True
        resp = client.get("/api/marp/themes")
        assert resp.status_code == 200
