"""실제 Viewer 앱을 ASGI로 순회해 페이지·트리·그림을 모은다. 시험 데이터는 임시 Git 저장소의 가상 문서뿐이다."""

import importlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201a5f645400000000049454e44ae426082")
SECRET = "SECRET-UNIQUE-777"


@pytest.fixture(autouse=True)
def _isolate_viewer_environment(monkeypatch):
    for name in ("SYNCTX_CONFIG_PATH", "SYNCTX_ENABLED_SERVICES"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.delitem(sys.modules, "synctx_standalone_viewer_app", raising=False)
    yield
    sys.modules.pop("synctx_standalone_viewer_app", None)


def _crawl():
    return importlib.import_module("static_export.crawl")


def _config():
    return importlib.import_module("static_export.config")


def _runtime():
    return importlib.import_module("static_export.runtime")


def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   cwd=cwd, check=True, capture_output=True)


def make_source(tmp_path: Path, files: dict[str, bytes | str], name="source") -> Path:
    source = tmp_path / name
    source.mkdir()
    _git("init", "-b", "main", ".", cwd=source)
    for rel, content in files.items():
        target = source / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    _git("add", "-A", cwd=source)
    _git("commit", "-m", "fixture", cwd=source)
    return source


BASE_FILES: dict[str, bytes | str] = {
    "README.md": "# Top\n\n![a](docs/img/a.png)\n\n[한글](docs/한글 문서.md)\n\n[비공개](internal/secret.md)\n",
    "docs/한글 문서.md": "# 한글\n\n![x](img/a.png)\n",
    "docs/a#b.md": "# Hash\n",
    "docs/what?.md": "# Question\n",
    "docs/mermaid.md": "# M\n\n```mermaid\ngraph TD\n A-->B\n```\n",
    "docs/img/a.png": PNG,
    "docs/img/b.png": PNG,
    "docs/img/unused.png": PNG,
    "bin.dat": b"\x00\x01\x02",
    f"internal/secret.md": f"# Internal\n\n{SECRET}\n",
    "internal/s.png": PNG,
    ".hidden.md": f"# Hidden\n\n{SECRET}\n",
}


def make_site(tmp_path, source, **repo_extra):
    repo = {"id": "repo-a", "name": "Repo A", "url": str(source), **repo_extra}
    return _config().SiteConfig(title="사이트", repositories=[repo])


async def run_crawl(tmp_path, source, **repo_extra):
    site = make_site(tmp_path, source, **repo_extra)
    viewer = _runtime().load_viewer(_runtime().write_runtime_config(site, tmp_path / "cache"))
    return await _crawl().crawl(viewer, site)


def everything(result) -> str:
    parts = [*result.pages, *result.json_files, *result.raw_files, *result.pages.values(), *result.json_files.values(),
             *(f"{a} {b}" for a, b in result.skipped)]
    parts += [value.decode("latin-1") for value in result.raw_files.values()]
    return "\n".join(parts)


def test_allowlist_applies_include_and_exclude_prefixes():
    repo = _config().SiteRepo(id="r", name="R", url="/x", include=["docs/", "README.md"], exclude=["docs/private"])
    paths = ["README.md", "docs/a.md", "docs/private/b.md", "other/c.md", ".hidden"]
    assert _crawl().build_allowlist(paths, repo) == frozenset({"README.md", "docs/a.md"})
    open_repo = _config().SiteRepo(id="r", name="R", url="/x")
    assert _crawl().build_allowlist(paths, open_repo) == frozenset(p for p in paths if p != ".hidden")


HIDDEN_PATHS = [".claude/notes.md", ".github/workflows/ci.md", ".hidden.md", "docs/.draft/a.md", "docs/.secret.md",
                "docs/ok.md", "README.md", "docs/dot.name.md"]


def test_allowlist_excludes_paths_with_dot_segment_by_default():
    repo = _config().SiteRepo(id="r", name="R", url="/x")
    assert _crawl().build_allowlist(HIDDEN_PATHS, repo) == frozenset({"docs/ok.md", "README.md", "docs/dot.name.md"})


@pytest.mark.parametrize("include, expected", [
    ([".claude/"], {".claude/notes.md"}),
    ([".claude"], {".claude/notes.md"}),
    ([".github/workflows/"], {".github/workflows/ci.md"}),
    (["docs/.draft/"], {"docs/.draft/a.md"}),
    (["docs/.secret.md"], {"docs/.secret.md"}),
    ([".hidden.md", "README.md"], {".hidden.md", "README.md"}),
    (["docs/"], {"docs/ok.md", "docs/dot.name.md"}),
    ([".github/"], {".github/workflows/ci.md"}),
])
def test_allowlist_include_must_name_the_hidden_path_to_enable_it(include, expected):
    repo = _config().SiteRepo(id="r", name="R", url="/x", include=include)
    assert _crawl().build_allowlist(HIDDEN_PATHS, repo) == frozenset(expected)


def test_allowlist_include_of_parent_does_not_enable_hidden_children():
    repo = _config().SiteRepo(id="r", name="R", url="/x", include=["docs/", ".claude/"])
    allowed = _crawl().build_allowlist(HIDDEN_PATHS, repo)
    assert "docs/.draft/a.md" not in allowed and "docs/.secret.md" not in allowed
    assert ".claude/notes.md" in allowed


def test_allowlist_exclude_still_wins_over_hidden_include():
    repo = _config().SiteRepo(id="r", name="R", url="/x", include=[".claude/"], exclude=[".claude/private"])
    assert _crawl().build_allowlist([".claude/notes.md", ".claude/private/x.md"], repo) == frozenset({".claude/notes.md"})


HIDDEN_MARK = "HIDDEN-MARK-555"

HIDDEN_FILES = {**BASE_FILES, ".claude/notes.md": f"# Notes\n\n{HIDDEN_MARK}\n", ".github/ISSUE.md": f"# Issue\n\n{HIDDEN_MARK}\n",
                "docs/.draft/wip.md": f"# Wip\n\n{HIDDEN_MARK}\n", "docs/.note.md": f"# Note\n\n{HIDDEN_MARK}\n",
                ".claude/pic.png": PNG}


@pytest.mark.asyncio
async def test_hidden_paths_are_absent_from_everything_by_default(tmp_path):
    result = await run_crawl(tmp_path, make_source(tmp_path, HIDDEN_FILES))
    body = everything(result)
    assert HIDDEN_MARK not in body
    for needle in (".claude", ".github", ".draft", ".note.md"):
        assert needle not in body, needle
    assert "/view/repo-a/README.md" in result.pages


@pytest.mark.asyncio
async def test_hidden_path_exported_only_when_include_names_it(tmp_path):
    result = await run_crawl(tmp_path, make_source(tmp_path, HIDDEN_FILES), include=["README.md", ".claude/notes.md"])
    assert "/view/repo-a/.claude/notes.md" in result.pages
    assert "/view/repo-a/.github/ISSUE.md" not in result.pages
    files = json.loads(result.json_files["/view/api/repos/repo-a/files.json"])["files"]
    assert ".claude/notes.md" in files and ".github/ISSUE.md" not in files
    assert "/view/api/repos/repo-a/tree/.claude.json" in result.json_files
    assert ".github" not in "\n".join([*result.pages.values(), *result.json_files.values()])


@pytest.mark.asyncio
async def test_pages_for_index_repo_and_every_viewable_file(tmp_path):
    result = await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES))
    pages = set(result.pages)
    assert {"/view/", "/view/repo-a", "/view/repo-a/README.md", "/view/repo-a/docs/mermaid.md"} <= pages
    assert "/view/repo-a/docs/%ED%95%9C%EA%B8%80%20%EB%AC%B8%EC%84%9C.md" in pages
    assert "/view/repo-a/docs/a%23b.md" in pages
    assert "/view/repo-a/docs/what%3F.md" in pages
    assert "Top" in result.pages["/view/repo-a/README.md"]
    assert "mermaid" in result.pages["/view/repo-a/docs/mermaid.md"]
    assert not any(key.endswith((".png", ".dat")) for key in pages)


@pytest.mark.asyncio
async def test_tree_json_for_every_directory(tmp_path):
    result = await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES))
    keys = {key for key in result.json_files if "/tree" in key}
    assert keys == {"/view/api/repos/repo-a/tree.json", "/view/api/repos/repo-a/tree/docs.json",
                    "/view/api/repos/repo-a/tree/docs/img.json", "/view/api/repos/repo-a/tree/internal.json"}
    docs = json.loads(result.json_files["/view/api/repos/repo-a/tree/docs.json"])
    assert docs["path"] == "docs"
    assert "docs/a#b.md" in {entry["path"] for entry in docs["entries"]}


@pytest.mark.asyncio
async def test_files_json_saved(tmp_path):
    result = await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES))
    files = json.loads(result.json_files["/view/api/repos/repo-a/files.json"])["files"]
    assert "docs/한글 문서.md" in files and "README.md" in files
    assert "bin.dat" not in files and "docs/img/a.png" not in files


@pytest.mark.asyncio
async def test_referenced_raw_images_collected(tmp_path):
    files = {**BASE_FILES, "docs/pic.md": '# P\n\n<img src="img/a.png" srcset="img/a.png 1x, img/b.png 2x">\n'}
    result = await run_crawl(tmp_path, make_source(tmp_path, files))
    assert result.raw_files["/raw/repo-a/docs/img/a.png"] == PNG
    assert result.raw_files["/raw/repo-a/docs/img/b.png"] == PNG
    assert "/raw/repo-a/docs/img/unused.png" not in result.raw_files


@pytest.mark.asyncio
async def test_raw_image_over_limit_fails_build(tmp_path, monkeypatch):
    monkeypatch.setattr(_crawl(), "MAX_FILE_BYTES", len(PNG) - 1)
    with pytest.raises(_crawl().CrawlError, match="a.png"):
        await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES))


@pytest.mark.asyncio
async def test_missing_raw_image_raises(tmp_path):
    files = {"README.md": "# T\n\n![gone](missing.png)\n"}
    with pytest.raises(_crawl().CrawlError, match="missing.png"):
        await run_crawl(tmp_path, make_source(tmp_path, files))


@pytest.mark.asyncio
async def test_unsupported_and_oversize_are_skipped_with_reason(tmp_path):
    files = {**BASE_FILES, "big.md": "# Big\n\n" + "x" * (5 * 1024 * 1024 + 10)}
    result = await run_crawl(tmp_path, make_source(tmp_path, files), exclude=["internal/", ".hidden"])
    reasons = dict(result.skipped)
    assert "확장자" in reasons["bin.dat"]
    assert "크기" in reasons["big.md"]
    assert "/view/repo-a/big.md" not in result.pages
    assert "docs/img/unused.png" not in reasons


@pytest.mark.asyncio
async def test_failed_page_raises_not_silently_skipped(tmp_path, monkeypatch):
    original = importlib.import_module("synctx_common.git_manager").GitRepoManager.read_file

    def failing(self, path, ref=None):
        if path == "docs/mermaid.md":
            raise RuntimeError("boom")
        return original(self, path, ref)

    monkeypatch.setattr("synctx_common.git_manager.GitRepoManager.read_file", failing)
    with pytest.raises(_crawl().CrawlError, match="mermaid.md"):
        await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES), exclude=["internal/", ".hidden"])


@pytest.mark.asyncio
async def test_waits_for_startup_sync_before_crawl(tmp_path, monkeypatch):
    manager_cls = importlib.import_module("synctx_common.git_manager").GitRepoManager
    original = manager_cls._clone
    events = []

    def slow_clone(self, *args, **kwargs):
        events.append("clone-start")
        time.sleep(0.5)
        original(self, *args, **kwargs)
        events.append("clone-end")

    monkeypatch.setattr(manager_cls, "_clone", slow_clone)
    result = await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES))
    assert events == ["clone-start", "clone-end"]
    assert "/view/repo-a/README.md" in result.pages


@pytest.mark.asyncio
async def test_clone_failure_aborts_build(tmp_path):
    with pytest.raises(_crawl().CrawlError, match="repo-a"):
        await run_crawl(tmp_path, tmp_path / "does-not-exist")


@pytest.mark.asyncio
async def test_fetch_failure_with_existing_cache_aborts_build(tmp_path):
    source = make_source(tmp_path, BASE_FILES)
    await run_crawl(tmp_path, source)
    source.rename(tmp_path / "moved")
    with pytest.raises(_crawl().CrawlError, match="repo-a"):
        await run_crawl(tmp_path, source)


@pytest.mark.asyncio
async def test_does_not_log_exception_text(tmp_path, monkeypatch, caplog):
    def leaking(self, *args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "clone", "https://user:ghp_secret@host/x.git"])

    monkeypatch.setattr("synctx_common.git_manager.GitRepoManager._clone", leaking)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(_crawl().CrawlError) as error:
            await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES))
    assert "ghp_secret" not in caplog.text and "ghp_secret" not in str(error.value)


@pytest.mark.asyncio
async def test_excluded_file_absent_from_pages_trees_files_and_assets(tmp_path):
    files = {**BASE_FILES, "meeting.md": '---\nsynctx:\n  type: meeting\n  meeting:\n    id: m1\n    title: M\n    starts_at: "2026-10-24T10:00:00+09:00"\n    channel: c\n    references:\n      sec: internal/secret.md\n---\n# Meeting\n\n[근거](internal/secret.md)\n'}
    result = await run_crawl(tmp_path, make_source(tmp_path, files), exclude=["internal/", ".hidden"])
    text = everything(result)
    assert SECRET not in text
    for forbidden in ("internal/secret", "internal%2Fsecret", ".hidden", "internal/s.png", "/view/api/repos/repo-a/tree/internal.json"):
        assert forbidden not in "\n".join([*result.pages, *result.json_files, *result.raw_files]), forbidden
    assert not any("internal" in key or ".hidden" in key for key in [*result.pages, *result.json_files, *result.raw_files])
    for body in result.json_files.values():
        assert "internal" not in body and ".hidden" not in body
    assert "/view/repo-a/meeting.md" in result.pages
    assert ("internal/secret.md", "내보내기 대상에서 제외된 파일을 가리키는 링크") in result.skipped


@pytest.mark.asyncio
async def test_excluded_image_reference_is_skipped_with_reason(tmp_path):
    files = {"README.md": "# T\n\n![s](internal/s.png)\n", "internal/s.png": PNG, "internal/x.md": "# x\n"}
    result = await run_crawl(tmp_path, make_source(tmp_path, files), exclude=["internal/"])
    assert result.raw_files == {}
    assert ("internal/s.png", "내보내기 대상에서 제외된 그림 참조") in result.skipped


@pytest.mark.asyncio
async def test_include_limits_everything_not_only_pages(tmp_path):
    result = await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES), include=["docs/"])
    page_keys = [key for key in result.pages if key not in ("/view/", "/view/repo-a")]
    assert page_keys and all(key.startswith("/view/repo-a/docs/") for key in page_keys)
    tree_keys = {key for key in result.json_files if "/tree" in key}
    assert tree_keys == {"/view/api/repos/repo-a/tree.json", "/view/api/repos/repo-a/tree/docs.json",
                         "/view/api/repos/repo-a/tree/docs/img.json"}
    root = json.loads(result.json_files["/view/api/repos/repo-a/tree.json"])
    assert [entry["path"] for entry in root["entries"]] == ["docs"]
    files = json.loads(result.json_files["/view/api/repos/repo-a/files.json"])["files"]
    assert files and all(path.startswith("docs/") for path in files)
    assert "README.md" not in files
    assert SECRET not in everything(result)


@pytest.mark.asyncio
async def test_repo_without_documents_raises(tmp_path):
    with pytest.raises(_crawl().CrawlError, match="repo-a"):
        await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES), include=["nothing/"])


@pytest.mark.asyncio
async def test_excluded_names_absent_from_server_rendered_navigation(tmp_path):
    result = await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES), exclude=["internal/", ".hidden"])
    for url in ("/view/repo-a", "/view/repo-a/docs/mermaid.md"):
        html = result.pages[url]
        assert "secret.md" not in html and "internal/" not in html and ".hidden" not in html, url
    assert "docs/" in result.pages["/view/repo-a"]


@pytest.mark.asyncio
async def test_include_hides_unexported_files_from_homepage_and_tree(tmp_path):
    result = await run_crawl(tmp_path, make_source(tmp_path, BASE_FILES), include=["docs/"])
    for url in ("/view/repo-a", "/view/repo-a/docs/mermaid.md"):
        html = result.pages[url]
        assert "README" not in html and "bin.dat" not in html and "internal" not in html, url
