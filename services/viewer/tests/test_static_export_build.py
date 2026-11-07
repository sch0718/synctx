"""정적 사이트 빌드 조립 시험. 시험 데이터는 임시 Git 저장소의 가상 문서뿐이다."""

import hashlib
import importlib
import json
import os
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from test_static_export_crawl import BASE_FILES, PNG, SECRET, _isolate_viewer_environment, make_source  # noqa: F401

TOKEN = "ghp_buildtoken1234567890"


def _build():
    return importlib.import_module("static_export.build")


def _config():
    return importlib.import_module("static_export.config")


def _runtime():
    return importlib.import_module("static_export.runtime")


def make_site(source, **repo_extra):
    repo = {"id": "repo-a", "name": "Repo A", "url": str(source), **repo_extra}
    return _config().SiteConfig(title="사이트", repositories=[repo])


def run_build(tmp_path, source, **repo_extra):
    out = tmp_path / "dist"
    report = _build().build_site(make_site(source, **repo_extra), out, tmp_path / "cache")
    return out, report


def tree_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def parse_headers(text: str) -> dict[str, dict[str, str]]:
    blocks: dict[str, dict[str, str]] = {}
    current = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            current = blocks.setdefault(line.strip(), {})
        else:
            name, _, value = line.strip().partition(":")
            current[name.strip()] = value.strip()
    return blocks


def directives(csp: str) -> dict[str, str]:
    return {part.strip().split(" ", 1)[0]: part.strip() for part in csp.split(";") if part.strip()}


def test_build_writes_all_expected_files(tmp_path):
    out, report = run_build(tmp_path, make_source(tmp_path, BASE_FILES))
    expected = [
        "view/index.html", "view/repo-a/index.html", "view/repo-a/README.md/index.html",
        "view/repo-a/docs/한글 문서.md/index.html", "view/repo-a/docs/a#b.md/index.html",
        "view/repo-a/docs/what?.md/index.html", "view/repo-a/docs/mermaid.md/index.html",
        "view/api/repos/repo-a/files.json", "view/api/repos/repo-a/tree.json", "view/api/repos/repo-a/tree/docs.json",
        "view/static/static-mode.js", "view/static/viewer.js", "view/static/vendor/mermaid.min.js",
        "view/static/vendor/hljs/github.min.css", "view/static/themes/corporate.css", "view/static/themes/github-dark.css",
        "raw/repo-a/docs/img/a.png", "_headers", "_redirects", "robots.txt",
    ]
    for rel in expected:
        assert (out / rel).is_file(), rel
    assert (out / ".static-export-out").is_file()
    assert report.page_count == len([p for p in out.glob("view/**/index.html") if "static" not in p.parts])
    assert report.file_count == len([p for p in out.rglob("*") if p.is_file() and p.name != ".static-export-out"])
    assert report.total_bytes == sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and p.name != ".static-export-out")
    assert {"crawl", "write"} <= set(report.stage_seconds)
    assert (out / "_redirects").read_text(encoding="utf-8").split() == ["/", "/view/", "302"]
    assert (out / "robots.txt").read_text(encoding="utf-8").split() == ["User-agent:", "*", "Disallow:", "/"]


def test_root_redirects_to_index_even_with_default_repo(tmp_path):
    """로그인 모드에서는 볼 수 있는 저장소가 사람마다 달라 `/`는 첫 화면으로만 보낸다."""
    source = make_source(tmp_path, BASE_FILES)
    site = make_site(source).model_copy(update={"default_repo": "repo-a"})
    out = tmp_path / "dist"
    _build().build_site(site, out, tmp_path / "cache")
    assert (out / "_redirects").read_text(encoding="utf-8").split() == ["/", "/view/", "302"]
    assert json.loads((out / "_synctx/repos.json").read_text(encoding="utf-8")) == [{"id": "repo-a", "name": site.repositories[0].name}]
    assert (out / "view/repo-a/index.html").is_file()


def test_pages_are_finalized_for_static_mode(tmp_path):
    out, _ = run_build(tmp_path, make_source(tmp_path, BASE_FILES))
    html = (out / "view/repo-a/README.md/index.html").read_text(encoding="utf-8")
    assert 'id="static-hide"' in html and "page-static" in html and "/view/static/static-mode.js" in html
    assert 'id="theme-css"' in html


def test_index_lists_only_selected_repos(tmp_path):
    source = make_source(tmp_path, BASE_FILES)
    other = make_source(tmp_path, {"README.md": "# Other\n"}, name="other")
    repos = [{"id": "repo-a", "name": "Repo A", "url": str(source)}, {"id": "repo-b", "name": "Repo B", "url": str(other)}]
    site = _config().SiteConfig(title="사이트", repositories=repos)
    selected = site.model_copy(update={"repositories": [site.repositories[1]]})
    out = tmp_path / "dist"
    _build().build_site(selected, out, tmp_path / "cache")
    index = (out / "view/index.html").read_text(encoding="utf-8")
    assert "repo-b" in index and "repo-a" not in index
    assert not (out / "view/repo-a").exists()


def test_headers_csp_matches_app_middleware_directives(tmp_path):
    source = make_source(tmp_path, BASE_FILES)
    out, _ = run_build(tmp_path, source)
    blocks = parse_headers((out / "_headers").read_text(encoding="utf-8"))
    star = blocks["/*"]

    viewer = _runtime().load_viewer(_runtime().write_runtime_config(make_site(source), tmp_path / "cache2"))
    response = TestClient(viewer.app).get("/")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    assert directives(star["Content-Security-Policy"]) == directives(response.headers["content-security-policy"])
    assert star["X-Frame-Options"] == response.headers["x-frame-options"] == "DENY"
    assert star["X-Content-Type-Options"] == response.headers["x-content-type-options"] == "nosniff"
    assert star["X-Robots-Tag"] == "noindex"
    assert star["Cache-Control"] == "no-store"


def test_no_dot_git_and_no_token_in_dist(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_READ_TOKEN", TOKEN)
    out, _ = run_build(tmp_path, make_source(tmp_path, BASE_FILES), auth={"credential_env": "GIT_READ_TOKEN"})
    assert not [p for p in out.rglob("*") if p.name == ".git"]
    for path in out.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes() and TOKEN not in path.as_posix()


def test_report_lists_skipped_files(tmp_path):
    _, report = run_build(tmp_path, make_source(tmp_path, BASE_FILES))
    assert ("bin.dat", "지원하지 않는 확장자") in report.skipped


def test_raw_images_written_and_svg_header_preserved(tmp_path):
    out, _ = run_build(tmp_path, make_source(tmp_path, BASE_FILES))
    assert (out / "raw/repo-a/docs/img/a.png").read_bytes() == PNG
    assert not (out / "raw/repo-a/docs/img/unused.png").exists()
    viewer = importlib.import_module("app")
    blocks = parse_headers((out / "_headers").read_text(encoding="utf-8"))
    svg = blocks["/raw/*.svg"]
    assert svg["Content-Security-Policy"] == viewer.RAW_SVG_CSP
    assert svg["X-Content-Type-Options"] == "nosniff"


def test_build_is_deterministic(tmp_path):
    source = make_source(tmp_path, BASE_FILES)
    first, _ = run_build(tmp_path, source)
    first_hashes = tree_hashes(first)
    second_out = tmp_path / "dist2"
    _build().build_site(make_site(source), second_out, tmp_path / "cache")
    assert first_hashes == tree_hashes(second_out)


def test_excluded_content_is_not_in_dist(tmp_path):
    out, _ = run_build(tmp_path, make_source(tmp_path, BASE_FILES), exclude=["internal/", ".hidden.md"])
    for path in out.rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes()
    assert not (out / "raw/repo-a/internal").exists()


def test_rebuild_replaces_previous_output(tmp_path):
    source = make_source(tmp_path, BASE_FILES)
    out, _ = run_build(tmp_path, source)
    (out / "stale.txt").write_text("old", encoding="utf-8")
    _build().build_site(make_site(source), out, tmp_path / "cache")
    assert not (out / "stale.txt").exists()
    assert (out / "view/index.html").is_file()


def test_unsafe_url_path_raises(tmp_path):
    with pytest.raises(ValueError):
        _build().output_file_path("/view/r/../../etc/passwd")
    with pytest.raises(ValueError):
        _build().output_file_path("/raw/r/a\x00.png")
    assert _build().output_file_path("/raw/repo-a/docs/%ED%95%9C.png") == "raw/repo-a/docs/한.png"


def _site(tmp_path):
    name = f"source{len(list(tmp_path.glob('source*')))}"
    return make_site(make_source(tmp_path, {"README.md": "# T\n"}, name=name))


class TestUnsafeOutDir:
    def _reject(self, tmp_path, out_dir, cache_dir=None, config_path=None):
        site = _site(tmp_path)
        with pytest.raises(_build().BuildConfigError):
            _build().build_site(site, out_dir, cache_dir or tmp_path / "cache", config_path=config_path)

    def test_repository_root_and_ancestors(self, tmp_path):
        root = Path(_build().__file__).resolve().parents[4]
        self._reject(tmp_path, root)
        self._reject(tmp_path, root.parent)

    def test_home_and_filesystem_root(self, tmp_path):
        self._reject(tmp_path, Path.home())
        self._reject(tmp_path, Path("/"))

    def test_current_directory_and_ancestors(self, tmp_path, monkeypatch):
        work = tmp_path / "work" / "sub"
        work.mkdir(parents=True)
        monkeypatch.chdir(work)
        self._reject(tmp_path, work)
        self._reject(tmp_path, tmp_path / "work")

    def test_cache_dir_relations(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        self._reject(tmp_path, cache, cache)
        self._reject(tmp_path, tmp_path, cache)
        self._reject(tmp_path, cache / "inner", cache)

    def test_tilde_cache_dir_is_expanded_before_comparison(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        out = tmp_path / "build"
        out.mkdir()
        (out / _build().OUT_MARKER).write_bytes(b"")
        self._reject(tmp_path, out, "~/build/cache")

    def test_contains_local_source_repository(self, tmp_path):
        out = tmp_path / "work"
        out.mkdir()
        (out / _build().OUT_MARKER).write_bytes(b"")
        source = make_source(tmp_path / "work", {"README.md": "# T\n"}, name="docs-repo")
        with pytest.raises(_build().BuildConfigError):
            _build().build_site(make_site(source), out, tmp_path / "cache")
        assert (Path(source) / "README.md").is_file()

    def test_config_file_location(self, tmp_path):
        conf_dir = tmp_path / "conf"
        conf_dir.mkdir()
        config = conf_dir / "static-export.yaml"
        config.write_text("title: x\n", encoding="utf-8")
        self._reject(tmp_path, conf_dir, config_path=config)
        self._reject(tmp_path, tmp_path, config_path=config)

    def test_non_empty_directory_without_marker(self, tmp_path):
        target = tmp_path / "mine"
        target.mkdir()
        keep = target / "keep.txt"
        keep.write_text("important", encoding="utf-8")
        self._reject(tmp_path, target)
        assert keep.read_text(encoding="utf-8") == "important"

    def test_symlink_out_dir(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)
        self._reject(tmp_path, link)
        assert list(real.iterdir()) == []

    def test_nothing_is_deleted_on_rejection(self, tmp_path):
        target = tmp_path / "dist"
        target.mkdir()
        (target / ".static-export-out").write_text("", encoding="utf-8")
        (target / "a.txt").write_text("a", encoding="utf-8")
        cache = target / "cache"
        self._reject(tmp_path, target, cache)
        assert (target / "a.txt").is_file()

    def test_existing_empty_directory_is_accepted(self, tmp_path):
        target = tmp_path / "empty"
        target.mkdir()
        report = _build().build_site(_site(tmp_path), target, tmp_path / "cache")
        assert report.page_count >= 2
        assert os.path.exists(target / ".static-export-out")


class TestWriterOverlap:
    def test_rejects_path_already_on_disk(self, tmp_path):
        root = tmp_path.resolve()
        (root / "view").mkdir()
        (root / "view/index.html").write_text("old", encoding="utf-8")
        with pytest.raises(_build().BuildConfigError):
            _build()._Writer(root).write("view/index.html", "new")
        assert (root / "view/index.html").read_text(encoding="utf-8") == "old"

    def test_rejects_case_only_difference_on_case_insensitive_fs(self, tmp_path):
        root = tmp_path.resolve()
        (root / "Probe").write_text("", encoding="utf-8")
        if not (root / "probe").exists():
            pytest.skip("대소문자를 구분하는 파일 시스템")
        (root / "Probe").unlink()
        writer = _build()._Writer(root)
        writer.write("view/r/docs/Readme.md/index.html", "A")
        with pytest.raises(_build().BuildConfigError):
            writer.write("view/r/docs/readme.md/index.html", "B")
        assert (root / "view/r/docs/Readme.md/index.html").read_text(encoding="utf-8") == "A"
