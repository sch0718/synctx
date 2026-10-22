"""출판 저장소 인증: marp.publish_auth → GIT_PUBLISH_TOKEN → repositories[0].auth(폴백) 순서와 토큰 비노출."""

import base64
import http.server
import logging
import threading

import pytest
from fastapi.testclient import TestClient

from synctx_common.config import RepoAuthConfig, RepoProfile

PUBLISH_TOKEN = "pub-secret-7f3a9c"
READ_TOKEN = "read-secret-1b2d4e"


def _basic(token: str) -> str:
    return base64.b64encode(f"x-access-token:{token}".encode()).decode()


@pytest.fixture
def marp_app(monkeypatch, tmp_path):
    import app as marp_app

    monkeypatch.setattr(marp_app.config.authz, "enabled", False)
    monkeypatch.setattr(marp_app.config.marp, "publish_repo", "https://git.internal/slides.git")
    monkeypatch.setattr(marp_app.config.marp, "worktree_path", str(tmp_path / "worktree"))
    monkeypatch.setattr(marp_app.config.marp, "publish_auth", None)
    monkeypatch.setattr(
        marp_app.config,
        "repositories",
        [
            RepoProfile(
                id="docs",
                name="Docs",
                url="https://git.internal/docs.git",
                auth=RepoAuthConfig(type="token", credential_env="GIT_READ_TOKEN"),
            )
        ],
    )
    monkeypatch.setenv("GIT_READ_TOKEN", READ_TOKEN)
    monkeypatch.delenv("GIT_PUBLISH_TOKEN", raising=False)
    return marp_app


class TestPublishAuthSelection:
    def test_uses_publish_token_when_set(self, marp_app, monkeypatch):
        monkeypatch.setenv("GIT_PUBLISH_TOKEN", PUBLISH_TOKEN)

        manager = marp_app.get_publish_manager()

        assert manager.profile.auth == RepoAuthConfig(type="token", credential_env="GIT_PUBLISH_TOKEN")
        header = " ".join(manager._git_config_args())
        assert _basic(PUBLISH_TOKEN) in header
        assert _basic(READ_TOKEN) not in header

    def test_explicit_publish_auth_wins_over_env(self, marp_app, monkeypatch):
        monkeypatch.setenv("GIT_PUBLISH_TOKEN", PUBLISH_TOKEN)
        monkeypatch.setenv("SLIDES_PUSH_TOKEN", "custom-secret")
        explicit = RepoAuthConfig(type="token", credential_env="SLIDES_PUSH_TOKEN")
        monkeypatch.setattr(marp_app.config.marp, "publish_auth", explicit)

        manager = marp_app.get_publish_manager()

        assert manager.profile.auth == explicit
        assert _basic("custom-secret") in " ".join(manager._git_config_args())

    def test_explicit_publish_auth_does_not_fall_back(self, marp_app, monkeypatch, caplog):
        explicit = RepoAuthConfig(type="token", credential_env="SLIDES_PUSH_TOKEN")
        monkeypatch.setattr(marp_app.config.marp, "publish_auth", explicit)
        monkeypatch.delenv("SLIDES_PUSH_TOKEN", raising=False)

        with caplog.at_level(logging.WARNING):
            manager = marp_app.get_publish_manager()

        assert manager.profile.auth == explicit
        assert _basic(READ_TOKEN) not in " ".join(manager._git_config_args())
        assert "publish_auth_fallback" not in caplog.text

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_falls_back_to_first_repository_with_warning(self, marp_app, monkeypatch, caplog, value):
        if value is not None:
            monkeypatch.setenv("GIT_PUBLISH_TOKEN", value)

        with caplog.at_level(logging.WARNING):
            manager = marp_app.get_publish_manager()

        assert manager.profile.auth == RepoAuthConfig(type="token", credential_env="GIT_READ_TOKEN")
        assert "publish_auth_fallback" in caplog.text
        assert "GIT_PUBLISH_TOKEN" in caplog.text
        assert "repo_id=docs" in caplog.text
        assert READ_TOKEN not in caplog.text

    def test_no_repositories_and_no_token_means_no_auth(self, marp_app, monkeypatch, caplog):
        monkeypatch.setattr(marp_app.config, "repositories", [])

        with caplog.at_level(logging.WARNING):
            manager = marp_app.get_publish_manager()

        assert manager.profile.auth == RepoAuthConfig(type="none")
        assert "publish_auth_fallback" in caplog.text


class _RejectingGitServer(http.server.BaseHTTPRequestHandler):
    seen_auth: list[str] = []

    def do_GET(self):
        type(self).seen_auth.append(self.headers.get("Authorization", ""))
        self.send_response(403)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def rejecting_git_server():
    _RejectingGitServer.seen_auth = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RejectingGitServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/slides.git"
    finally:
        server.shutdown()
        server.server_close()


def _init_publish_worktree(marp_app, origin_url: str) -> None:
    import subprocess
    from pathlib import Path

    repo_dir = Path(marp_app.config.marp.worktree_path) / "publish-repo"
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin_url], cwd=repo_dir, check=True)


class TestPublishTokenNotLeaked:
    def test_git_failure_hides_publish_token(self, marp_app, monkeypatch, caplog, rejecting_git_server):
        from pathlib import Path
        from unittest.mock import patch

        monkeypatch.setenv("GIT_PUBLISH_TOKEN", PUBLISH_TOKEN)
        monkeypatch.setattr(marp_app.config.marp, "publish_repo", rejecting_git_server)
        monkeypatch.setenv("MARP_WORK_DIR", str(Path(marp_app.config.marp.worktree_path).parent / "work"))
        _init_publish_worktree(marp_app, rejecting_git_server)
        client = TestClient(marp_app.app, raise_server_exceptions=False)

        async def fake_marp(processed_md, md_path, output_path, extra_flags, timeout):
            Path(output_path).write_text("<html>deck</html>")

        with caplog.at_level(logging.DEBUG), patch("app.write_and_run_marp", side_effect=fake_marp):
            published = client.post("/api/marp/publish", json={"markdown": "# Slide", "filename": "deck"})
            listed = client.get("/api/marp/published")
            removed = client.delete("/api/marp/published/missing.html")

        assert published.status_code == 500
        assert published.json()["detail"] == "Failed to publish to git repository"
        assert listed.status_code == 200
        assert removed.status_code == 404
        assert _RejectingGitServer.seen_auth
        assert all(auth == f"Basic {_basic(PUBLISH_TOKEN)}" for auth in _RejectingGitServer.seen_auth)
        for resp in (published, listed, removed):
            assert PUBLISH_TOKEN not in resp.text
            assert _basic(PUBLISH_TOKEN) not in resp.text
        assert "git_command_failed" in caplog.text
        assert "Failed to publish to git" in caplog.text
        assert PUBLISH_TOKEN not in caplog.text
        assert _basic(PUBLISH_TOKEN) not in caplog.text

    def test_git_failure_exception_hides_publish_token(self, marp_app, monkeypatch, rejecting_git_server):
        import asyncio
        import subprocess

        monkeypatch.setenv("GIT_PUBLISH_TOKEN", PUBLISH_TOKEN)
        monkeypatch.setattr(marp_app.config.marp, "publish_repo", rejecting_git_server)
        manager = marp_app.get_publish_manager()

        with pytest.raises(subprocess.CalledProcessError) as excinfo:
            asyncio.run(manager.fetch())

        exc = excinfo.value
        rendered = " ".join([str(exc), repr(exc.cmd), (exc.stderr or b"").decode(errors="replace")])
        assert _RejectingGitServer.seen_auth
        assert PUBLISH_TOKEN not in rendered
        assert _basic(PUBLISH_TOKEN) not in rendered
