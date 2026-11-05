"""정적 내보내기 CLI 시험. 시험 데이터는 임시 Git 저장소의 가상 문서뿐이다."""

import importlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from test_static_export_crawl import BASE_FILES, _isolate_viewer_environment, make_source  # noqa: F401

TOKEN = "ghp_clitoken1234567890"


def _cli():
    return importlib.import_module("static_export.__main__")


def write_config(tmp_path, repos, name="static-export.yaml", **extra):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"title": "사이트", "repositories": repos, **extra}, allow_unicode=True), encoding="utf-8")
    return path


def two_repos(tmp_path):
    a = make_source(tmp_path, BASE_FILES, name="src-a")
    b = make_source(tmp_path, {"README.md": "# B\n"}, name="src-b")
    return [{"id": "repo-a", "name": "A", "url": str(a)}, {"id": "repo-b", "name": "B", "url": str(b)}]


def run(argv, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STATIC_EXPORT_REPOS", raising=False)
    return _cli().main(argv)


def test_build_succeeds_and_verifies(tmp_path, monkeypatch, capsys):
    config = write_config(tmp_path, two_repos(tmp_path))
    out = tmp_path / "dist"
    code = run(["build", "--config", str(config), "--out", str(out), "--cache-dir", str(tmp_path / "cache")], monkeypatch, tmp_path)
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    assert (out / "view/repo-a/README.md/index.html").is_file()
    assert "페이지" in captured.out and "검사 통과" in captured.out


def test_repos_option_keeps_config_order_and_limits_output(tmp_path, monkeypatch):
    config = write_config(tmp_path, two_repos(tmp_path))
    out = tmp_path / "dist"
    code = run(["build", "--config", str(config), "--out", str(out), "--cache-dir", str(tmp_path / "cache"),
                "--repos", "repo-b,repo-a"], monkeypatch, tmp_path)
    assert code == 0
    index = (out / "view/index.html").read_text(encoding="utf-8")
    assert index.index("repo-a") < index.index("repo-b")
    code = run(["build", "--config", str(config), "--out", str(out), "--cache-dir", str(tmp_path / "cache"),
                "--repos", "repo-b"], monkeypatch, tmp_path)
    assert code == 0
    assert not (out / "view/repo-a").exists()


def test_argument_wins_over_environment(tmp_path, monkeypatch):
    cli = _cli()
    assert cli.select_repos("repo-a", "repo-b") == ["repo-a"]
    assert cli.select_repos(None, "repo-b, repo-a") == ["repo-b", "repo-a"]
    assert cli.select_repos(None, "") is None
    assert cli.select_repos(None, None) is None
    assert cli.select_repos("", "repo-b") is None


def test_environment_selects_repos_when_no_argument(tmp_path, monkeypatch):
    config = write_config(tmp_path, two_repos(tmp_path))
    out = tmp_path / "dist"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATIC_EXPORT_REPOS", "repo-b")
    assert _cli().main(["build", "--config", str(config), "--out", str(out), "--cache-dir", str(tmp_path / "cache")]) == 0
    assert not (out / "view/repo-a").exists()
    monkeypatch.setenv("STATIC_EXPORT_REPOS", "repo-b")
    assert _cli().main(["build", "--config", str(config), "--out", str(out), "--cache-dir", str(tmp_path / "cache"),
                        "--repos", "repo-a"]) == 0
    assert (out / "view/repo-a").exists() and not (out / "view/repo-b").exists()


class TestExitCodes:
    def test_missing_config_is_exit_2_in_korean(self, tmp_path, monkeypatch, capsys):
        code = run(["build", "--config", str(tmp_path / "none.yaml"), "--out", str(tmp_path / "dist")], monkeypatch, tmp_path)
        assert code == 2
        assert re.search(r"[가-힣]", capsys.readouterr().err)

    def test_unknown_repo_id_is_exit_2(self, tmp_path, monkeypatch, capsys):
        config = write_config(tmp_path, two_repos(tmp_path))
        code = run(["build", "--config", str(config), "--out", str(tmp_path / "dist"), "--repos", "nope"], monkeypatch, tmp_path)
        assert code == 2
        assert "nope" in capsys.readouterr().err

    def test_invalid_config_is_exit_2(self, tmp_path, monkeypatch):
        config = write_config(tmp_path, [{"id": "api", "name": "x", "url": "/x"}])
        assert run(["build", "--config", str(config), "--out", str(tmp_path / "dist")], monkeypatch, tmp_path) == 2

    def test_unsafe_out_dir_is_exit_2(self, tmp_path, monkeypatch):
        config = write_config(tmp_path, two_repos(tmp_path))
        occupied = tmp_path / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("x", encoding="utf-8")
        code = run(["build", "--config", str(config), "--out", str(occupied), "--cache-dir", str(tmp_path / "cache")], monkeypatch, tmp_path)
        assert code == 2
        assert (occupied / "keep.txt").is_file()

    def test_verify_failure_is_exit_1_and_keeps_dist(self, tmp_path, monkeypatch, capsys):
        config = write_config(tmp_path, two_repos(tmp_path))
        out = tmp_path / "dist"
        args = ["--config", str(config), "--out", str(out), "--cache-dir", str(tmp_path / "cache")]
        assert run(["build", *args], monkeypatch, tmp_path) == 0
        (out / "leak.txt").write_text("token=ghp_abcdefghijklmnop", encoding="utf-8")
        capsys.readouterr()
        assert run(["verify", "--out", str(out)], monkeypatch, tmp_path) == 1
        captured = capsys.readouterr()
        assert "leak.txt" in captured.out + captured.err
        assert out.is_dir()

    def test_verify_passes_clean_dist(self, tmp_path, monkeypatch):
        config = write_config(tmp_path, two_repos(tmp_path))
        out = tmp_path / "dist"
        assert run(["build", "--config", str(config), "--out", str(out), "--cache-dir", str(tmp_path / "cache")], monkeypatch, tmp_path) == 0
        assert run(["verify", "--out", str(out)], monkeypatch, tmp_path) == 0

    def test_build_aborted_by_verify_leaves_dist_and_returns_1(self, tmp_path, monkeypatch, capsys):
        repos = two_repos(tmp_path)
        repos[0]["exclude"] = ["internal/"]
        config = write_config(tmp_path, repos)
        out = tmp_path / "dist"
        code = run(["build", "--config", str(config), "--out", str(out), "--cache-dir", str(tmp_path / "cache")], monkeypatch, tmp_path)
        captured = capsys.readouterr()
        assert code == 1, captured.out + captured.err
        assert out.is_dir() and (out / "_headers").is_file()
        assert "internal/secret.md" in captured.out + captured.err

    def test_sync_failure_is_exit_1(self, tmp_path, monkeypatch):
        config = write_config(tmp_path, [{"id": "repo-a", "name": "A", "url": str(tmp_path / "missing-repo")}])
        assert run(["build", "--config", str(config), "--out", str(tmp_path / "dist"), "--cache-dir", str(tmp_path / "cache")],
                   monkeypatch, tmp_path) == 1


def test_messages_never_contain_token_value(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GIT_READ_TOKEN", TOKEN)
    config = write_config(tmp_path, [{"id": "repo-a", "name": "A", "url": str(tmp_path / "missing-repo"),
                                      "auth": {"credential_env": "GIT_READ_TOKEN"}}])
    code = run(["build", "--config", str(config), "--out", str(tmp_path / "dist"), "--cache-dir", str(tmp_path / "cache")],
               monkeypatch, tmp_path)
    captured = capsys.readouterr()
    assert code == 1
    assert TOKEN not in captured.out + captured.err
    assert re.search(r"[가-힣]", captured.err)


def test_token_in_dist_is_caught_by_verify(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GIT_READ_TOKEN", TOKEN)
    repos = two_repos(tmp_path)
    repos[1]["auth"] = {"credential_env": "GIT_READ_TOKEN"}
    config = write_config(tmp_path, repos)
    out = tmp_path / "dist"
    assert run(["build", "--config", str(config), "--out", str(out), "--cache-dir", str(tmp_path / "cache")], monkeypatch, tmp_path) == 0
    (out / "oops.txt").write_text(f"x{TOKEN}y", encoding="utf-8")
    capsys.readouterr()
    assert run(["verify", "--out", str(out), "--config", str(config)], monkeypatch, tmp_path) == 1
    captured = capsys.readouterr()
    assert "oops.txt" in captured.out + captured.err
    assert TOKEN not in captured.out + captured.err


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv가 없다")
def test_build_script_forwards_arguments_and_exit_code(tmp_path):
    script = Path(__file__).resolve().parents[3] / "scripts" / "static-export" / "build.sh"
    assert os.access(script, os.X_OK)
    result = subprocess.run([str(script), "build", "--config", str(tmp_path / "none.yaml"), "--out", str(tmp_path / "dist")],
                            cwd=tmp_path, capture_output=True, text=True, timeout=180)
    assert result.returncode == 2
    assert "설정 파일을 읽을 수 없습니다" in result.stderr
    assert not (tmp_path / "dist").exists()


def test_exclude_markers_catch_excluded_file_paths_in_dist(tmp_path, monkeypatch, capsys):
    repos = two_repos(tmp_path)
    repos[1]["exclude"] = ["internal/"]
    config = write_config(tmp_path, repos)
    out = tmp_path / "dist"
    assert run(["build", "--config", str(config), "--out", str(out), "--cache-dir", str(tmp_path / "cache")], monkeypatch, tmp_path) == 0
    leaked = out / "raw/repo-b/internal/s.png"
    leaked.parent.mkdir(parents=True)
    leaked.write_bytes(b"png")
    capsys.readouterr()
    assert run(["verify", "--out", str(out), "--config", str(config)], monkeypatch, tmp_path) == 1
    assert "raw/repo-b/internal" in capsys.readouterr().err


def test_tilde_in_out_is_expanded_for_build_and_verify(tmp_path, monkeypatch):
    config = write_config(tmp_path, two_repos(tmp_path))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert run(["build", "--config", str(config), "--out", "~/site", "--cache-dir", str(tmp_path / "cache")], monkeypatch, tmp_path) == 0
    assert (home / "site/view/index.html").is_file()
    assert run(["verify", "--out", "~/site"], monkeypatch, tmp_path) == 0


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv가 없다")
def test_build_script_keeps_callers_working_directory(tmp_path):
    script = Path(__file__).resolve().parents[3] / "scripts" / "static-export" / "build.sh"
    config = write_config(tmp_path, [{"id": "repo-a", "name": "A", "url": str(make_source(tmp_path, BASE_FILES, name="src-s"))}])
    result = subprocess.run([str(script), "build", "--config", config.name, "--out", "site", "--cache-dir", "cache"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "site/view/index.html").is_file()
