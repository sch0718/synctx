"""Standalone runtime keeps the existing viewer and bundled browser assets."""

import asyncio
import importlib
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from synctx_common.config import ViewerConfig


def test_asset_source_is_validated_and_defaults_to_cdn():
    assert ViewerConfig().asset_source == "cdn"
    assert ViewerConfig(asset_source="bundled").asset_source == "bundled"
    with pytest.raises(ValueError):
        ViewerConfig(asset_source="internet")


def runtime():
    return importlib.import_module("standalone")


def test_repeated_repos_generate_unique_ids_and_environment_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_READ_TOKEN", "secret-never-write")
    args = runtime().parse_args([
        "--repo", "https://git.example/a/docs.git", "--repo", "https://git.example/b/docs.git",
        "--repo", "https://git.example/a/docs.git", "--cache-dir", str(tmp_path),
    ])
    path = runtime().prepare_config(args)
    data = yaml.safe_load(path.read_text())
    repos = data["repositories"]
    assert len({repo["id"] for repo in repos}) == 3
    assert all(repo["auth"] == {"type": "token", "credential_env": "GIT_READ_TOKEN"} for repo in repos)
    assert "secret-never-write" not in path.read_text()
    assert data["authz"] == {"enabled": False}
    assert data["viewer"]["asset_source"] == "bundled"
    assert data["runtime"]["public_base_url"] == "http://127.0.0.1:18000"


def test_explicit_config_keeps_authz_and_repository_settings(tmp_path):
    original = tmp_path / "input.yaml"
    data = {"authz": {"enabled": True, "role_mappings": {"viewer": ["readers"]}},
            "repositories": [{"id": "mine", "name": "Mine", "url": "https://git.example/docs.git",
                              "default_ref": "release", "auth": {"type": "token", "credential_env": "CUSTOM_TOKEN"}}]}
    original.write_text(yaml.safe_dump(data))
    before = original.read_bytes()
    args = runtime().parse_args(["--config", str(original), "--cache-dir", str(tmp_path / "cache")])
    generated = yaml.safe_load(runtime().prepare_config(args).read_text())
    assert generated["authz"] == data["authz"]
    assert generated["repositories"] == data["repositories"]
    assert original.read_bytes() == before


@pytest.mark.parametrize("argv", [[], ["--repo", "a", "--config", "b"],
    ["--repo", "https://user:password@git.example/repo.git"],
    ["--repo", "https://token@git.example/repo.git"],
    ["--repo", "a", "--port", "0"], ["--repo", "a", "--port", "65536"],
    ["--repo", "a", "--refresh-interval", "-1"], ["--repo", "a", "--refresh-interval", "nan"],
    ["--repo", "a", "--host", "https://example.com"],
    ["--config", "b", "--ref", "release"], ["--config", "b", "--ref", "main"],
])
def test_invalid_cli_arguments_are_rejected(argv):
    with pytest.raises(SystemExit):
        runtime().parse_args(argv)


def test_ref_defaults_to_main_only_for_generated_repositories():
    assert runtime().parse_args(["--repo", "a"]).ref == "main"
    assert runtime().parse_args(["--repo", "a", "--ref", "release"]).ref == "release"
    assert runtime().parse_args(["--config", "b"]).ref is None


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "::1", "localhost"])
def test_cli_host_and_disabled_refresh(host):
    args = runtime().parse_args(["--repo", ".", "--host", host, "--refresh-interval", "0"])
    assert args.host == host
    assert args.refresh_interval == 0


def test_wrapper_propagates_lifespan_and_cancels_periodic_refresh(monkeypatch):
    from fastapi import FastAPI
    events = []

    @asynccontextmanager
    async def lifespan(app):
        events.append("started")
        yield
        events.append("stopped")

    viewer = SimpleNamespace(app=FastAPI(lifespan=lifespan), config=SimpleNamespace(repositories=[]))
    cancelled = []

    async def refresh(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(runtime(), "refresh_repositories", refresh)
    with TestClient(runtime().create_app(refresh_interval=1, viewer=viewer)) as client:
        assert events == ["started"]
        assert client.get("/", follow_redirects=False).headers["location"] == "/view/"
    assert events == ["started", "stopped"]
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_refresh_failure_does_not_stop_other_repositories():
    called = []

    class Manager:
        def __init__(self, repo_id):
            self.repo_id = repo_id

        async def fetch(self, source):
            called.append(self.repo_id)
            if self.repo_id == "broken":
                raise RuntimeError("unavailable")

    viewer = SimpleNamespace(config=SimpleNamespace(repositories=[SimpleNamespace(id=x) for x in ("broken", "good")]),
                             get_repo_manager=Manager)
    task = asyncio.create_task(runtime().refresh_repositories(viewer, 0.001))
    for _ in range(100):
        if "good" in called:
            break
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert called[:2] == ["broken", "good"]


def test_real_git_clone_render_meeting_and_html_export(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(source)], check=True, capture_output=True)
    (source / "README.md").write_text("# Local document\n\n```mermaid\ngraph TD\n A-->B\n```\n\n```python\nprint(1)\n```\n")
    (source / "meeting.md").write_text('---\nsynctx:\n  type: meeting\n  meeting:\n    id: local\n    title: Local meeting\n    starts_at: "2026-10-24T10:00:00+09:00"\n    channel: "local-team"\n---\n# Local meeting\n\n[Join](https://meet.example/local)\n\n```mermaid\ngraph TD\n A-->B\n```\n')
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture"], check=True, capture_output=True)
    args = runtime().parse_args(["--repo", str(source), "--cache-dir", str(tmp_path / "cache"), "--refresh-interval", "0"])
    path = runtime().prepare_config(args)
    monkeypatch.setenv("SYNCTX_CONFIG_PATH", str(path))
    monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer")
    wrapper = runtime().create_app(refresh_interval=0)
    with TestClient(wrapper) as client:
        # Startup synchronization is asynchronous; wait for readiness before rendering.
        import time
        for _ in range(200):
            if client.get("/view/readyz").status_code == 200:
                break
            time.sleep(0.01)
        repo_id = yaml.safe_load(path.read_text())["repositories"][0]["id"]
        response = client.get(f"/view/{repo_id}/README.md")
        assert response.status_code == 200
        assert "Local document" in response.text
        for asset in ("mermaid.min.js", "highlight.min.js", "hljs/github.min.css"):
            url = "/view/static/vendor/" + asset
            assert url in response.text
            asset_response = client.get(url)
            assert asset_response.status_code == 200
            assert asset_response.content == (Path(__file__).parents[1] / "src/static/vendor" / asset).read_bytes()
        assert "/cdn/" not in response.text
        assert client.get("/view/healthz").status_code == 200
        meeting = client.get(f"/view/{repo_id}/meeting.md?mode=meeting")
        assert meeting.status_code == 200
        assert "https://meet.example/local" in meeting.text
        assert 'id="meeting-config"' in meeting.text
        exported = client.get(f"/view/api/repos/{repo_id}/download", params={"path": "README.md", "format": "html"})
        assert exported.status_code == 200
        assert "Local document" in exported.text
        meeting_export = client.get(f"/view/api/repos/{repo_id}/meeting-export", params={"path": "meeting.md"})
        assert meeting_export.status_code == 200
        assert "mermaid.initialize" in meeting_export.text
        assert "https://meet.example/local" in meeting_export.text
        assert client.get("/marp/healthz").status_code == 404


def test_repository_ids_are_stable_and_do_not_shadow_routes(tmp_path):
    args = runtime().parse_args(["--repo", "https://git.example/team/search.git", "--repo", "https://git.example/team/docs.git", "--cache-dir", str(tmp_path)])
    first = yaml.safe_load(runtime().prepare_config(args).read_text())["repositories"]
    second = yaml.safe_load(runtime().prepare_config(args).read_text())["repositories"]
    assert first == second
    assert all(repo["id"] not in {"search", "docs", "api", "raw", "static"} for repo in first)


def test_colliding_repository_ids_remain_stable_when_order_changes(tmp_path):
    urls = ["https://git.example/a/docs.git", "https://git.example/b/docs.git"]
    def ids(order):
        args = runtime().parse_args([part for url in order for part in ("--repo", url)] + ["--cache-dir", str(tmp_path)])
        return {repo["url"]: repo["id"] for repo in yaml.safe_load(runtime().prepare_config(args).read_text())["repositories"]}
    assert ids(urls) == ids(list(reversed(urls)))


def test_explicit_config_is_not_overwritten_even_at_generated_path(tmp_path):
    original = tmp_path / "synctx.lite.yaml"
    original.write_text('repositories:\n- id: input\n  name: Input\n  url: https://git.example/input.git\n')
    before = original.read_bytes()
    args = runtime().parse_args(["--config", str(original), "--cache-dir", str(tmp_path)])
    result = runtime().prepare_config(args)
    assert result != original
    assert original.read_bytes() == before


@pytest.mark.parametrize("url", ["https://git.example/repo.git?token=secret", "https://git.example/repo.git#secret"])
def test_url_query_and_fragment_credentials_are_not_persisted(url):
    with pytest.raises(SystemExit):
        runtime().parse_args(["--repo", url])


def test_local_repository_ids_use_canonical_origin_across_callers(tmp_path, monkeypatch):
    cache = tmp_path / "shared-cache"
    ids = []
    for caller in (tmp_path / "a", tmp_path / "b"):
        source = caller / "source"
        source.mkdir(parents=True)
        monkeypatch.chdir(caller)
        args = runtime().parse_args(["--repo", "source", "--cache-dir", str(cache)])
        first = yaml.safe_load(runtime().prepare_config(args).read_text())["repositories"][0]
        second = yaml.safe_load(runtime().prepare_config(args).read_text())["repositories"][0]
        assert first["id"] == second["id"]
        assert first["url"] == str(source)
        ids.append(first["id"])
    assert ids[0] != ids[1]


def test_explicit_public_base_url_is_preserved(tmp_path):
    config = tmp_path / "input.yaml"
    config.write_text('runtime:\n  public_base_url: https://proxy.example/synctx\nrepositories:\n- id: input\n  name: Input\n  url: https://git.example/input.git\n')
    args = runtime().parse_args(["--config", str(config), "--cache-dir", str(tmp_path / "cache")])
    generated = yaml.safe_load(runtime().prepare_config(args).read_text())
    assert generated["runtime"]["public_base_url"] == "https://proxy.example/synctx"


def test_cli_public_base_url_overrides_config_and_bind_address(tmp_path):
    config = tmp_path / "input.yaml"
    config.write_text('runtime:\n  public_base_url: https://proxy.example/old\nrepositories:\n- id: input\n  name: Input\n  url: https://git.example/input.git\n')
    args = runtime().parse_args(["--config", str(config), "--host", "0.0.0.0", "--port", "8000", "--public-base-url", "http://127.0.0.1:18000", "--cache-dir", str(tmp_path / "cache")])
    generated = yaml.safe_load(runtime().prepare_config(args).read_text())
    assert generated["runtime"]["public_base_url"] == "http://127.0.0.1:18000"


@pytest.mark.parametrize("host,expected", [("0.0.0.0", "http://127.0.0.1:18000"), ("::", "http://[::1]:18000")])
def test_wildcard_bind_derives_loopback_public_url(tmp_path, host, expected):
    args = runtime().parse_args(["--repo", ".", "--host", host, "--cache-dir", str(tmp_path)])
    assert yaml.safe_load(runtime().prepare_config(args).read_text())["runtime"]["public_base_url"] == expected


@pytest.mark.parametrize("url", ["ftp://example.com", "https://user:secret@example.com", "https://example.com?token=secret", "https://example.com#secret", "https:///missing", "https://example.com:65536"])
def test_public_base_url_rejects_invalid_or_credential_urls(url, capsys):
    with pytest.raises(SystemExit):
        runtime().parse_args(["--repo", ".", "--public-base-url", url])
    assert "public base URL must be HTTP(S)" in capsys.readouterr().err


def test_environment_public_url_fallback_does_not_override_config_or_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNCTX_VIEWER_PUBLIC_BASE_URL", "http://127.0.0.1:28000")
    args = runtime().parse_args(["--repo", ".", "--cache-dir", str(tmp_path)])
    assert yaml.safe_load(runtime().prepare_config(args).read_text())["runtime"]["public_base_url"] == "http://127.0.0.1:28000"
    config = tmp_path / "input.yaml"
    config.write_text('repositories:\n- id: input\n  name: Input\n  url: https://git.example/input.git\n')
    args = runtime().parse_args(["--config", str(config), "--cache-dir", str(tmp_path)])
    assert yaml.safe_load(runtime().prepare_config(args).read_text())["runtime"]["public_base_url"] == "http://127.0.0.1:28000"
    config.write_text('runtime:\n  public_base_url: https://proxy.example/synctx\nrepositories:\n- id: input\n  name: Input\n  url: https://git.example/input.git\n')
    args = runtime().parse_args(["--config", str(config), "--cache-dir", str(tmp_path)])
    assert yaml.safe_load(runtime().prepare_config(args).read_text())["runtime"]["public_base_url"] == "https://proxy.example/synctx"
    args = runtime().parse_args(["--repo", ".", "--public-base-url", "https://cli.example", "--cache-dir", str(tmp_path)])
    assert yaml.safe_load(runtime().prepare_config(args).read_text())["runtime"]["public_base_url"] == "https://cli.example"


def test_invalid_environment_public_url_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNCTX_VIEWER_PUBLIC_BASE_URL", "https://token@example.com")
    args = runtime().parse_args(["--repo", ".", "--cache-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        runtime().prepare_config(args)


def test_generated_configs_are_content_isolated_stable_and_private(tmp_path):
    import stat

    cache = tmp_path / "shared-cache"
    first_args = runtime().parse_args(["--repo", str(tmp_path / "repo-a"), "--port", "18001", "--cache-dir", str(cache)])
    second_args = runtime().parse_args(["--repo", str(tmp_path / "repo-b"), "--port", "18002", "--cache-dir", str(cache)])
    first = runtime().prepare_config(first_args)
    original = first.read_bytes()
    second = runtime().prepare_config(second_args)
    assert first != second
    assert first.read_bytes() == original
    assert yaml.safe_load(first.read_text())["runtime"]["public_base_url"] == "http://127.0.0.1:18001"
    assert yaml.safe_load(second.read_text())["runtime"]["public_base_url"] == "http://127.0.0.1:18002"
    assert runtime().prepare_config(first_args) == first
    assert first.read_bytes() == original
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(second.stat().st_mode) == 0o600


@pytest.mark.parametrize("url", ["https://user:secret@example.com/a.git", "https://example.com/a.git?token=secret", "ftp://example.com/a.git"])
def test_config_with_invalid_repository_url_exits_without_leaking_secret(tmp_path, url):
    config = tmp_path / "input.yaml"
    config.write_text(yaml.safe_dump({"repositories": [{"id": "bad", "name": "Bad", "url": url}]}))
    args = runtime().parse_args(["--config", str(config), "--cache-dir", str(tmp_path / "cache")])
    with pytest.raises(SystemExit) as exc:
        runtime().prepare_config(args)
    assert "secret" not in str(exc.value.code)


@pytest.mark.parametrize("ttl", [None, 7200])
@pytest.mark.parametrize("embed", [None, {"enabled": True}, {"enabled": False, "render_files": True}, {"enabled": True, "render_files": True}])
def test_render_config_preserves_ttl_and_explicit_trust_only(tmp_path, monkeypatch, ttl, embed):
    monkeypatch.setenv("SYNCTX_RENDER_SECRET", "current-never-in-config" * 2)
    monkeypatch.setenv("SYNCTX_RENDER_SECRET_PREVIOUS", "previous-never-in-config" * 2)
    source = {"repositories": [{"id": "docs", "name": "Docs", "url": "https://git.example/docs.git"}]}
    if ttl is not None:
        source["viewer"] = {"render_token_ttl_seconds": ttl}
    if embed is not None:
        source["repositories"][0]["html_embed"] = embed
    config = tmp_path / "input.yaml"
    config.write_text(yaml.safe_dump(source))
    before = config.read_bytes()
    args = runtime().parse_args(["--config", str(config), "--cache-dir", str(tmp_path / "cache")])
    generated_path = runtime().prepare_config(args)
    generated = yaml.safe_load(generated_path.read_text())
    assert generated["viewer"]["render_token_ttl_seconds"] == (14400 if ttl is None else ttl)
    assert generated["repositories"][0].get("html_embed") == embed
    assert "never-in-config" not in generated_path.read_text()
    assert config.read_bytes() == before


def test_generated_repo_defaults_ttl_without_trust_or_key_environment(tmp_path, monkeypatch):
    import os
    for name in ("SYNCTX_RENDER_SECRET", "SYNCTX_RENDER_SECRET_PREVIOUS", "SYNCTX_RENDER_SECRET_FILE", "SYNCTX_RENDER_SECRET_PREVIOUS_FILE"):
        monkeypatch.delenv(name, raising=False)
    args = runtime().parse_args(["--repo", "https://git.example/docs.git", "--cache-dir", str(tmp_path)])
    generated = yaml.safe_load(runtime().prepare_config(args).read_text())
    assert generated["viewer"]["render_token_ttl_seconds"] == 14400
    assert "html_embed" not in generated["repositories"][0]
    assert not any(name.startswith("SYNCTX_RENDER_SECRET") for name in os.environ)


def test_main_keeps_synctx_log_masking_by_not_letting_uvicorn_replace_handlers(tmp_path, monkeypatch):
    import os

    import uvicorn

    captured = {}
    module = runtime()
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(module, "create_app", lambda *args, **kwargs: object())
    monkeypatch.setattr(module, "prepare_config", lambda args: tmp_path / "synctx.yaml")
    module.main(["--repo", "https://git.example/a/docs.git", "--cache-dir", str(tmp_path)])
    assert "log_config" in captured and captured["log_config"] is None
