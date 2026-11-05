"""정적 내보내기용 런타임 설정과 Viewer 로더."""

import importlib
import os
import sys
from pathlib import Path

import pytest
import yaml
from synctx_common.config import SynctxConfig
from synctx_common.git_manager import GitRepoManager


@pytest.fixture(autouse=True)
def _isolate_viewer_environment(monkeypatch):
    for name in ("SYNCTX_CONFIG_PATH", "SYNCTX_ENABLED_SERVICES"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.delitem(sys.modules, "synctx_standalone_viewer_app", raising=False)
    yield
    sys.modules.pop("synctx_standalone_viewer_app", None)


def _runtime():
    return importlib.import_module("static_export.runtime")


def _config():
    return importlib.import_module("static_export.config")


def _site(**repo_extra):
    repo = {"id": "repo-a", "name": "Repo A", "url": "https://git.example/a.git", **repo_extra}
    return _config().SiteConfig(title="사이트", repositories=[repo, {"id": "repo-b", "name": "B", "url": "/tmp/b"}])


def _write(tmp_path, site=None):
    path = _runtime().write_runtime_config(site or _site(), tmp_path / "cache")
    return path, yaml.safe_load(path.read_text(encoding="utf-8"))


def test_runtime_config_validates_with_synctx_config(tmp_path):
    path, data = _write(tmp_path)
    assert path.is_file()
    config = SynctxConfig.model_validate(data)
    assert [r.id for r in config.repositories] == ["repo-a", "repo-b"]
    assert config.repositories[0].default_ref == "main"
    assert config.viewer.asset_source == "bundled"
    assert config.viewer.repo_cache_path == str(tmp_path / "cache" / "repos")


def test_runtime_config_has_no_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_READ_TOKEN", "ghp_secret")
    path, _ = _write(tmp_path, _site(auth={"credential_env": "GIT_READ_TOKEN"}))
    text = path.read_text(encoding="utf-8")
    assert "ghp_secret" not in text
    assert "GIT_READ_TOKEN" in text


def test_credential_env_sets_token_auth_type(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_READ_TOKEN", "ghp_secret")
    _, data = _write(tmp_path, _site(auth={"credential_env": "GIT_READ_TOKEN"}))
    with_auth, without_auth = SynctxConfig.model_validate(data).repositories
    assert with_auth.auth.type == "token"
    assert with_auth.auth.credential_env == "GIT_READ_TOKEN"
    assert without_auth.auth.type == "none"
    args = GitRepoManager(with_auth, str(tmp_path / "repos"))._git_config_args()
    assert any(arg.startswith("http.extraHeader=Authorization:") for arg in args)
    assert not any("http.extraHeader" in arg for arg in GitRepoManager(without_auth, str(tmp_path))._git_config_args())


def test_include_exclude_are_not_passed_to_runtime_config(tmp_path):
    _, data = _write(tmp_path, _site(include=["docs/"], exclude=["internal/"]))
    for repo in data["repositories"]:
        assert set(repo) <= {"id", "name", "url", "default_ref", "auth"}
        assert repo.get("watch_paths", []) == [] and repo.get("ignore_paths", []) == []
    assert "internal/" not in yaml.safe_dump(data)


def test_authz_disabled_and_jitsi_disabled(tmp_path):
    _, data = _write(tmp_path)
    config = SynctxConfig.model_validate(data)
    assert config.authz.enabled is False
    assert config.collab.jitsi.enabled is False
    assert config.runtime.public_base_url.startswith("http://127.0.0.1")


def test_load_viewer_sets_environment_and_returns_app_module(tmp_path, monkeypatch):
    monkeypatch.delenv("SYNCTX_CONFIG_PATH", raising=False)
    monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
    path, _ = _write(tmp_path)
    viewer = _runtime().load_viewer(path)
    import os
    assert os.environ["SYNCTX_CONFIG_PATH"] == str(path)
    assert os.environ["SYNCTX_ENABLED_SERVICES"] == "viewer"
    assert [r.id for r in viewer.config.repositories] == ["repo-a", "repo-b"]
    assert hasattr(viewer, "app") and hasattr(viewer, "get_repo_manager")
    assert Path(viewer.config.viewer.repo_cache_path) == tmp_path / "cache" / "repos"
