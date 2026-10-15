import os
import subprocess
from pathlib import Path

import pytest
from synctx_common.git_manager import GitRepoManager
from synctx_common.config import RepoProfile

def test_validate_path():
    profile = RepoProfile(id="test", name="test", url="dummy")
    manager = GitRepoManager(profile, "/tmp")
    
    assert manager.validate_path("docs/test.md") == "docs/test.md"
    assert manager.validate_path("/docs/test.md") == "docs/test.md"
    
    with pytest.raises(ValueError):
        manager.validate_path("../docs/test.md")
        
    with pytest.raises(ValueError):
        manager.validate_path("docs/../../etc/passwd")


_COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, **_COMMIT_ENV}
    )
    return result.stdout.decode("utf-8").strip()


def _commit(work: Path, path: str, content: str, message: str) -> str:
    target = work / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git(work, "add", path)
    _git(work, "commit", "-qm", message)
    _git(work, "push", "-q", "origin", "main")
    return _git(work, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def _no_git_identity(monkeypatch):
    """운영 컨테이너처럼 git 사용자 설정이 없고 이메일 추측도 하지 않는 환경에서 실행한다."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")


@pytest.fixture
def remote(tmp_path):
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    _git(bare, "config", "uploadpack.allowFilter", "true")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(bare), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    first = _commit(work, "docs/a.md", "v1\n", "first")
    _commit(work, "other/b.md", "b\n", "other")
    return bare, work, first


def _manager(tmp_path: Path, bare: Path, **profile_kwargs) -> GitRepoManager:
    profile = RepoProfile(id="r", name="r", url=bare.as_uri(), **profile_kwargs)
    return GitRepoManager(profile, str(tmp_path / "cache"))


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_mode", ["full", "shallow", "blobless", "treeless"])
@pytest.mark.parametrize("sparse_paths", [[], ["docs"]])
@pytest.mark.parametrize("method", ["ensure", "fetch"])
async def test_refetch_sees_new_remote_commit(tmp_path, remote, storage_mode, sparse_paths, method):
    bare, work, _ = remote
    manager = _manager(tmp_path, bare, storage_mode=storage_mode, sparse_paths=sparse_paths)
    await manager.ensure()
    assert manager.status == "ready"
    before = manager.head_sha()

    new_sha = _commit(work, "docs/a.md", "v2\n", "second")
    if method == "ensure":
        await manager.ensure()
    else:
        await manager.fetch()

    assert manager.status == "ready"
    assert manager.head_sha() == new_sha
    assert manager.head_sha_value == new_sha
    assert manager.read_file_text("docs/a.md") == "v2\n"
    assert manager.diff_since(before) == [("M", "docs/a.md")]
    assert manager.last_commit("docs/a.md").sha == new_sha


@pytest.mark.asyncio
async def test_commit_and_push_on_stale_cache_keeps_remote_commits(tmp_path, remote):
    bare, work, _ = remote
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    remote_sha = _commit(work, "docs/a.md", "v2\n", "second")
    await manager.ensure()

    pushed = manager.commit_and_push("slides/deck.html", b"<html/>", "docs(slides): publish deck")

    assert pushed == _git(bare, "rev-parse", "main")
    assert _git(bare, "rev-parse", f"{pushed}~1") == remote_sha
    assert manager.head_sha() == pushed
    assert manager.read_file_text("docs/a.md") == "v2\n"
    assert manager.read_file("slides/deck.html") == b"<html/>"


@pytest.mark.asyncio
async def test_read_resolved_reads_branch_after_single_resolve(tmp_path, remote):
    bare, work, _ = remote
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    _git(work, "checkout", "-q", "-b", "feature")
    target = work / "docs/a.md"
    target.write_text("feature\n")
    _git(work, "commit", "-qam", "feature")
    _git(work, "push", "-q", "origin", "feature")

    resolved = manager.resolve_ref("feature")

    assert resolved == "origin/feature"
    assert manager.read_resolved("docs/a.md", resolved) == b"feature\n"
    assert manager.read_resolved("other/b.md", resolved) == b"b\n"
    assert manager.read_file("docs/a.md", ref="feature") == b"feature\n"
    with pytest.raises(ValueError):
        manager.read_resolved("../x", resolved)


@pytest.mark.asyncio
async def test_file_paths_lists_every_blob_including_hidden_and_special_names(tmp_path, remote):
    bare, work, _ = remote
    for name in ["docs/.hidden.md", "docs/한글.md", 'docs/a "b".md', "docs/tab\tname.md", ".github/x.md"]:
        _commit(work, name, "x\n", f"add {name}")
    manager = _manager(tmp_path, bare)
    await manager.ensure()

    paths = manager.file_paths()

    assert {"docs/a.md", "other/b.md", "docs/.hidden.md", "docs/한글.md", 'docs/a "b".md', "docs/tab\tname.md", ".github/x.md"} <= paths
    assert not any(path.endswith("/") or path in {"docs", "other"} for path in paths)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["clone", "ensure_existing", "fetch"])
async def test_git_work_does_not_block_event_loop(tmp_path, remote, monkeypatch, method):
    import asyncio
    import time

    bare, _, _ = remote
    manager = _manager(tmp_path, bare)
    if method != "clone":
        await manager.ensure()
    original = manager._run_git

    def slow_git(*args, **kwargs):
        time.sleep(0.3)
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "_run_git", slow_git)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    if method == "fetch":
        await manager.fetch()
    else:
        await manager.ensure()
    task.cancel()
    assert manager.status == "ready"
    assert ticks >= 5


@pytest.mark.asyncio
async def test_concurrent_ensure_on_same_repo_is_serialized(tmp_path, remote, monkeypatch):
    import asyncio
    import time

    bare, _, first = remote
    manager = _manager(tmp_path, bare)
    original = manager._run_git

    def slow_git(*args, **kwargs):
        time.sleep(0.2)
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "_run_git", slow_git)
    await asyncio.gather(manager.ensure(), manager.ensure(), manager.fetch())
    assert manager.status == "ready"
    assert manager.head_sha() == _git(bare, "rev-parse", "main")


@pytest.mark.asyncio
async def test_concurrent_ensure_from_separate_managers_of_same_repo_is_serialized(tmp_path, remote, monkeypatch):
    import asyncio
    import time

    bare, _, _ = remote
    managers = [_manager(tmp_path, bare), _manager(tmp_path, bare)]
    for manager in managers:
        original = manager._run_git

        def slow_git(*args, _original=original, **kwargs):
            time.sleep(0.2)
            return _original(*args, **kwargs)

        monkeypatch.setattr(manager, "_run_git", slow_git)
    await asyncio.gather(*(m.ensure() for m in managers))
    assert [m.status for m in managers] == ["ready", "ready"]
    assert {m.head_sha() for m in managers} == {_git(bare, "rev-parse", "main")}
