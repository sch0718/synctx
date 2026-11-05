"""정적 내보내기 설정(static-export.yaml) 읽기와 검증."""

from pathlib import Path

import pytest
import yaml


def _config():
    import importlib
    return importlib.import_module("static_export.config")


def _write(tmp_path: Path, data) -> Path:
    path = tmp_path / "static-export.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _repo(repo_id="repo-a", **extra):
    return {"id": repo_id, "name": repo_id.upper(), "url": f"https://git.example/{repo_id}.git", **extra}


def _site(*repos, **extra):
    return {"title": "문서 사이트", "repositories": list(repos) or [_repo()], **extra}


def test_loads_repos_and_defaults(tmp_path):
    site = _config().load_site_config(_write(tmp_path, _site(_repo("repo-a"), _repo("repo-b", default_ref="release"))))
    assert site.title == "문서 사이트"
    assert site.theme == "corporate"
    assert site.default_repo is None
    assert [r.id for r in site.repositories] == ["repo-a", "repo-b"]
    first, second = site.repositories
    assert (first.default_ref, first.auth, first.include, first.exclude) == ("main", {}, [], [])
    assert second.default_ref == "release"


def test_selected_subset_keeps_order_and_rejects_unknown_id(tmp_path):
    path = _write(tmp_path, _site(_repo("repo-a"), _repo("repo-b"), _repo("repo-c")))
    site = _config().load_site_config(path, ["repo-c", "repo-a"])
    assert [r.id for r in site.repositories] == ["repo-a", "repo-c"]
    with pytest.raises(ValueError, match="repo-x"):
        _config().load_site_config(path, ["repo-a", "repo-x"])


@pytest.mark.parametrize("reserved", ["api", "static", "search", "meetings"])
def test_rejects_reserved_repo_id(tmp_path, reserved):
    import app
    assert reserved in app.RESERVED_PATHS
    with pytest.raises(ValueError, match=reserved):
        _config().load_site_config(_write(tmp_path, _site(_repo(reserved))))


def test_reserved_ids_come_from_viewer_app(tmp_path):
    import app
    for reserved in sorted(app.RESERVED_PATHS):
        if not reserved.replace("-", "").replace("_", "").isalnum():
            continue
        with pytest.raises(ValueError):
            _config().load_site_config(_write(tmp_path, _site(_repo(reserved))))


def test_rejects_duplicate_id(tmp_path):
    with pytest.raises(ValueError, match="repo-a"):
        _config().load_site_config(_write(tmp_path, _site(_repo("repo-a"), _repo("repo-a"))))


@pytest.mark.parametrize("data", [{"title": "t"}, {"title": "t", "repositories": []}])
def test_rejects_empty_repositories(tmp_path, data):
    with pytest.raises(ValueError):
        _config().load_site_config(_write(tmp_path, data))


def test_rejects_empty_file(tmp_path):
    path = tmp_path / "static-export.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        _config().load_site_config(path)


def test_rejects_unknown_theme(tmp_path):
    with pytest.raises(ValueError, match="theme"):
        _config().load_site_config(_write(tmp_path, _site(theme="neon")))


def test_accepts_every_viewer_theme(tmp_path):
    import renderer
    for theme in renderer.THEME_CSS:
        assert _config().load_site_config(_write(tmp_path, _site(theme=theme))).theme == theme


@pytest.mark.parametrize("url", ["https://user:pw@git.example/a.git", "https://token@git.example/a.git"])
def test_rejects_url_with_credentials(tmp_path, url):
    repo = _repo()
    repo["url"] = url
    with pytest.raises(ValueError) as error:
        _config().load_site_config(_write(tmp_path, _site(repo)))
    assert "pw" not in str(error.value)


def test_allows_ssh_user_and_local_path(tmp_path):
    site = _config().load_site_config(_write(tmp_path, _site(
        {**_repo("a"), "url": "ssh://git@git.example/a.git"}, {**_repo("b"), "url": str(tmp_path)})))
    assert len(site.repositories) == 2


def test_secret_values_are_never_loaded(tmp_path):
    ok = _config().load_site_config(_write(tmp_path, _site(_repo(auth={"credential_env": "GIT_READ_TOKEN"}))))
    assert ok.repositories[0].auth == {"credential_env": "GIT_READ_TOKEN"}
    for auth in ({"token": "ghp_secret"}, {"credential_env": "GIT_READ_TOKEN", "token": "ghp_secret"},
                 {"credential_file": "/tmp/token"}):
        with pytest.raises(ValueError) as error:
            _config().load_site_config(_write(tmp_path, _site(_repo(auth=auth))))
        assert "ghp_secret" not in str(error.value)


def test_default_repo_must_exist_and_survives_selection(tmp_path):
    path = _write(tmp_path, _site(_repo("repo-a"), _repo("repo-b"), default_repo="repo-b"))
    assert _config().load_site_config(path).default_repo == "repo-b"
    assert _config().load_site_config(path, ["repo-a"]).default_repo is None
    with pytest.raises(ValueError, match="repo-z"):
        _config().load_site_config(_write(tmp_path, _site(default_repo="repo-z")))


def test_rejects_unsafe_repo_id_and_unknown_keys(tmp_path):
    for bad in ("a/b", "..", "a b", ""):
        with pytest.raises(ValueError):
            _config().load_site_config(_write(tmp_path, _site(_repo(bad))))
    with pytest.raises(ValueError):
        _config().load_site_config(_write(tmp_path, _site(thme="corporate")))


def test_missing_file_is_value_error(tmp_path):
    with pytest.raises(ValueError):
        _config().load_site_config(tmp_path / "none.yaml")


def test_sample_file_is_valid():
    sample = Path(__file__).resolve().parents[3] / "static-export.sample.yaml"
    site = _config().load_site_config(sample)
    assert site.theme == "corporate"
    assert site.repositories
