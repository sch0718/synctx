"""scripts/local/run_viewer.py 테스트: 로컬 설정 생성과 /view·/marp·/cdn 묶음 앱."""

import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "local"))

import run_viewer  # noqa: E402

VALUES = """
synctxConfig:
  create: true
  data: |
    runtime:
      environment: development
      public_base_url: "https://synctx.synctx.local"
    authz:
      enabled: false
    repositories:
      - id: docs
        name: "docs"
        url: "https://git.example/docs.git"
        auth:
          type: token
          credential_env: GIT_READ_TOKEN
    viewer:
      cdn_base_url: "https://synctx.synctx.local/cdn"
    marp:
      publish_repo: "https://git.example/slides.git"
"""


def test_build_local_config_keeps_repositories_and_rewrites_local_values(tmp_path):
    values = tmp_path / "values-local.yaml"
    values.write_text(VALUES)
    config = run_viewer.build_local_config(values, port=18000, cache_dir=tmp_path / "cache")
    assert config["repositories"][0]["url"] == "https://git.example/docs.git"
    assert config["authz"] == {"enabled": False}
    assert config["runtime"]["public_base_url"] == "http://127.0.0.1:18000"
    assert config["viewer"]["cdn_base_url"] == "/cdn"
    assert config["viewer"]["marp_service_url"] == "http://127.0.0.1:18000/marp"
    assert config["viewer"]["repo_cache_path"] == str(tmp_path / "cache" / "repos")
    assert config["marp"]["worktree_path"] == str(tmp_path / "cache" / "marp-publish")
    # Marp 출판은 첫 저장소 인증을 그대로 쓰므로 읽기 토큰에 쓰기 권한이 있으면 공유 저장소에 push된다
    assert config["marp"]["publish_repo"] == ""


def test_build_local_config_without_chart_config_fails(tmp_path):
    values = tmp_path / "values.yaml"
    values.write_text("synctxConfig:\n  create: false\n  data: ''\n")
    with pytest.raises(SystemExit):
        run_viewer.build_local_config(values, port=18000, cache_dir=tmp_path)


def test_read_token_prefers_environment(tmp_path, monkeypatch):
    secret = tmp_path / "values-local.secret.yaml"
    secret.write_text("git:\n  secrets:\n    readToken: from-file\n")
    monkeypatch.setenv("GIT_READ_TOKEN", "from-env")
    assert run_viewer.read_token(secret) == "from-env"
    monkeypatch.delenv("GIT_READ_TOKEN")
    assert run_viewer.read_token(secret) == "from-file"
    assert run_viewer.read_token(tmp_path / "missing.yaml") == ""


def test_enabled_services_drop_marp_without_tools():
    assert run_viewer.enabled_services(marp_ready=True) == "viewer,cdn,marp"
    assert run_viewer.enabled_services(marp_ready=False) == "viewer,cdn"


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    config = {
        "authz": {"enabled": False},
        "repositories": [],
        "viewer": {"cdn_base_url": "/cdn", "repo_cache_path": str(tmp_path / "repos")},
    }
    config_path = tmp_path / "synctx.local.yaml"
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv("SYNCTX_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("SYNCTX_ENABLED_SERVICES", "viewer,cdn,marp")
    return run_viewer.create_app(ROOT)


def test_composite_app_serves_viewer_marp_and_cdn(local_app):
    with TestClient(local_app) as client:
        assert client.get("/view/healthz").json() == {"status": "ok"}
        assert client.get("/view/readyz").status_code == 200
        assert client.get("/marp/healthz").json() == {"status": "ok"}
        assert client.get("/marp/api/marp/themes").status_code == 200
        cdn = client.get("/cdn/mermaid/10.9.1/mermaid.min.js")
        assert cdn.status_code == 200
        assert len(cdn.content) > 1000
        assert client.get("/view/").status_code == 200
        root = client.get("/", follow_redirects=False)
        assert root.status_code in (302, 307)
        assert root.headers["location"] == "/view/"
        assert client.get("/drawio/").status_code == 404


@pytest.mark.parametrize("ttl", [None, 7200])
@pytest.mark.parametrize("embed", [None, {"enabled": True}, {"enabled": False, "render_files": True}, {"enabled": True, "render_files": True}])
def test_local_render_config_preserves_ttl_and_explicit_trust_only(tmp_path, monkeypatch, ttl, embed):
    monkeypatch.setenv("SYNCTX_RENDER_SECRET", "current-key-never-in-yaml" * 2)
    monkeypatch.setenv("SYNCTX_RENDER_SECRET_PREVIOUS", "previous-key-never-in-yaml" * 2)
    data = yaml.safe_load(yaml.safe_load(VALUES)["synctxConfig"]["data"])
    if ttl is not None:
        data["viewer"]["render_token_ttl_seconds"] = ttl
    if embed is not None:
        data["repositories"][0]["html_embed"] = embed
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump({"synctxConfig": {"data": yaml.safe_dump(data)}}))
    generated = run_viewer.build_local_config(values, 18000, tmp_path)
    assert generated["viewer"]["render_token_ttl_seconds"] == (14400 if ttl is None else ttl)
    assert generated["repositories"][0].get("html_embed") == embed
    assert "never-in-yaml" not in yaml.safe_dump(generated)


def test_local_render_config_does_not_create_key_environment(tmp_path, monkeypatch):
    for name in ("SYNCTX_RENDER_SECRET", "SYNCTX_RENDER_SECRET_PREVIOUS", "SYNCTX_RENDER_SECRET_FILE", "SYNCTX_RENDER_SECRET_PREVIOUS_FILE"):
        monkeypatch.delenv(name, raising=False)
    values = tmp_path / "values.yaml"
    values.write_text(VALUES)
    run_viewer.build_local_config(values, 18000, tmp_path)
    import os
    assert not any(name.startswith("SYNCTX_RENDER_SECRET") for name in os.environ)


def test_main_keeps_synctx_log_masking_by_not_letting_uvicorn_replace_handlers(tmp_path, monkeypatch):
    import os

    import uvicorn

    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(run_viewer, "create_app", lambda *args, **kwargs: object())
    monkeypatch.setattr(run_viewer, "find_chrome", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_viewer.py", "--cache-dir", str(tmp_path), "--values", str(tmp_path / "v.yaml")])
    monkeypatch.setattr(run_viewer, "build_local_config", lambda *args: {})
    run_viewer.main()
    assert "log_config" in captured and captured["log_config"] is None
