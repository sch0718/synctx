import base64
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from synctx_common.git_manager import GitRepoManager, GrepMatch, GrepResult
from synctx_common.config import RepoAuthConfig, RepoProfile

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


@pytest.mark.asyncio
async def test_last_commit_for_repo_and_branch(tmp_path, remote):
    bare, work, _ = remote
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    head = _git(bare, "rev-parse", "main")
    assert manager.last_commit("").sha == head
    assert manager.last_commit("docs/a.md").sha == _git(work, "log", "-1", "--format=%H", "--", "docs/a.md")

    # _commit 헬퍼는 main으로 push하므로 feature 커밋은 직접 만든다
    _git(work, "checkout", "-q", "-b", "feature")
    (work / "docs/a.md").write_text("feature\n")
    _git(work, "commit", "-qam", "feature edit")
    _git(work, "push", "-q", "origin", "feature")
    feature_sha = _git(work, "rev-parse", "HEAD")
    assert manager.last_commit("docs/a.md", ref="feature").sha == feature_sha
    assert manager.last_commit("", ref="feature").sha == feature_sha
    assert manager.last_commit("docs/a.md").sha != feature_sha


@pytest.mark.asyncio
async def test_grep_finds_case_insensitive_matches_with_line_numbers(tmp_path, remote):
    bare, work, _ = remote
    _commit(work, "docs/guide.md", "intro\nHello Search\nsearch again\n", "guide")
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    result = manager.grep("SEARCH")
    assert result.status == "ok" and result.truncated is False
    assert [(m.path, m.line, m.text) for m in result.matches] == [
        ("docs/guide.md", 2, "Hello Search"), ("docs/guide.md", 3, "search again")]
    assert isinstance(result.matches[0], GrepMatch)


@pytest.mark.asyncio
async def test_grep_is_literal_and_option_safe(tmp_path, remote):
    bare, work, _ = remote
    _commit(work, "docs/x.md", "a.b\naxb\n--help line\n[x]\n", "x")
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    assert [m.line for m in manager.grep("a.b").matches] == [1]
    assert [m.line for m in manager.grep("--help").matches] == [3]
    assert [m.line for m in manager.grep("[x]").matches] == [4]


@pytest.mark.asyncio
async def test_grep_parses_paths_with_colon_space_and_newline(tmp_path, remote):
    bare, work, _ = remote
    _commit(work, "회의 자료/a:b.md", "needle\n", "colon")
    _commit(work, "docs/a\nb.md", "needle\n", "newline")
    _commit(work, "docs/z.md", "needle\n", "after")
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    assert sorted(m.path for m in manager.grep("needle").matches) == ["docs/a\nb.md", "docs/z.md", "회의 자료/a:b.md"]


@pytest.mark.asyncio
async def test_grep_skips_binary_and_honours_pathspecs(tmp_path, remote):
    bare, work, _ = remote
    (work / "bin.dat").write_bytes(b"\x00\x01needle")
    _git(work, "add", "bin.dat")
    _commit(work, "code/x.py", "needle = 1\n", "py")
    _commit(work, "notes.txt", "needle\n", "txt")
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    assert {m.path for m in manager.grep("needle").matches} == {"code/x.py", "notes.txt"}
    assert [m.path for m in manager.grep("needle", pathspecs=[":(glob,icase)**/*.py"]).matches] == ["code/x.py"]


@pytest.mark.asyncio
async def test_grep_applies_path_filter_before_limit(tmp_path, remote):
    bare, work, _ = remote
    for name in (".env", "a.env", "b.md"):
        _commit(work, name, "hit\n", name)
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    result = manager.grep("hit", path_filter=lambda p: not p.split("/")[-1].startswith("."), limit=2)
    assert sorted(m.path for m in result.matches) == ["a.env", "b.md"] and result.truncated is False


@pytest.mark.asyncio
async def test_grep_limits_and_reports_truncation(tmp_path, remote):
    bare, work, _ = remote
    for i in range(4):
        _commit(work, f"docs/n{i}.md", "hit\nhit\n", f"n{i}")
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    result = manager.grep("hit", limit=5)
    assert len(result.matches) == 5 and result.truncated is True
    small = manager.grep("hit", max_output_bytes=40)
    assert small.truncated is True and len(small.matches) < 8


@pytest.mark.asyncio
async def test_grep_takes_at_most_five_lines_per_file(tmp_path, remote):
    bare, work, _ = remote
    _commit(work, "docs/many.md", "hit\n" * 7, "many")
    _commit(work, "docs/one.md", "hit\n", "one")
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    paths = [m.path for m in manager.grep("hit").matches]
    assert paths.count("docs/many.md") == 5 and paths.count("docs/one.md") == 1


@pytest.mark.asyncio
async def test_grep_no_match_is_ok_and_empty(tmp_path, remote):
    bare, _, _ = remote
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    assert manager.grep("zzz-not-there") == GrepResult(status="ok", matches=[], truncated=False)


@pytest.mark.asyncio
async def test_grep_times_out_without_results(tmp_path, remote):
    bare, _, _ = remote
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    assert manager.grep("v1", timeout=0) == GrepResult(status="timeout", matches=[], truncated=False)


def _process_gone(pid: int, wait: float = 3.0) -> bool:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_fetch_timeout_also_stops_transport_processes(tmp_path, remote, monkeypatch):
    bare, _, _ = remote
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    _git(manager.repo_dir, "remote", "set-url", "origin", "ssh://example.invalid/repo.git")
    pid_file = tmp_path / "transport.pid"
    # git이 뒤에 붙이는 인자는 안쪽 sh의 위치 인자로 버려진다
    monkeypatch.setenv("GIT_SSH_COMMAND", f"sh -c 'echo $$ > {pid_file}; exec sleep 30' --")
    assert manager.grep("v1", ref="feature", timeout=0.5).status == "timeout"
    pid = int(pid_file.read_text().strip())
    try:
        assert _process_gone(pid), "fetch timeout left the transport process running"
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


@pytest.mark.asyncio
async def test_grep_budget_covers_ref_fetch(tmp_path, remote, monkeypatch):
    bare, _, _ = remote
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    seen = {}

    def fetch_timeout(*args, **kwargs):
        if args and args[0] == "fetch":
            seen["timeout"] = kwargs.get("timeout")
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        raise AssertionError("grep must not run after the fetch timed out")

    monkeypatch.setattr(manager, "_run_git", fetch_timeout)
    assert manager.grep("v1", ref="feature", timeout=0.5).status == "timeout"
    assert 0 < seen["timeout"] <= 0.5


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_mode", ["blobless", "treeless"])
async def test_grep_skips_partial_clone(tmp_path, remote, monkeypatch, storage_mode):
    bare, _, _ = remote
    manager = _manager(tmp_path, bare, storage_mode=storage_mode)
    await manager.ensure()
    calls = []
    monkeypatch.setattr(manager, "_run_git", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a))
    assert manager.grep("v1").status == "unsupported" and calls == []


@pytest.mark.asyncio
async def test_grep_kills_git_when_deadline_passes_mid_read(tmp_path, remote, monkeypatch):
    import time

    bare, _, _ = remote
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    killed = threading.Event()

    class SilentStdout:
        def read1(self, size=-1):
            # kill()이 불릴 때까지 아무것도 주지 않는다. 상한 1초라 kill 처리가 빠진 구현도 무한 대기하지 않는다
            killed.wait(1.0)
            return b""

        def close(self):
            pass

    class FakeProc:
        stdout = SilentStdout()
        returncode = None

        def kill(self):
            self.returncode = -9
            killed.set()

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProc())
    started = time.monotonic()
    result = manager.grep("v1", timeout=0.05)
    elapsed = time.monotonic() - started
    assert result == GrepResult(status="timeout", matches=[], truncated=False)
    assert killed.is_set()
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_grep_runs_git_with_a_single_thread(tmp_path, remote, monkeypatch):
    bare, _, _ = remote
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    captured: list[list[str]] = []
    real_popen = subprocess.Popen

    def spy(args, *a, **k):
        captured.append(list(args))
        return real_popen(args, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", spy)
    manager.grep("v1")
    assert len(captured) == 1
    assert "--threads=1" in captured[0]
    assert captured[0].index("--threads=1") < captured[0].index("-e")


@pytest.mark.asyncio
async def test_grep_searches_non_default_branch(tmp_path, remote):
    bare, work, _ = remote
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    _git(work, "checkout", "-q", "-b", "feature")
    (work / "docs/a.md").write_text("only on feature\n")
    _git(work, "commit", "-qam", "feature edit")
    _git(work, "push", "-q", "origin", "feature")
    on_feature = manager.grep("only on feature", ref="feature")
    assert [(m.path, m.line, m.text) for m in on_feature.matches] == [("docs/a.md", 1, "only on feature")]
    assert manager.grep("only on feature").matches == []


@pytest.mark.asyncio
async def test_grep_ignores_user_grep_column_config(tmp_path, remote, monkeypatch):
    bare, work, _ = remote
    _commit(work, "docs/a.md", "ab Hit\n", "col")
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "grep.column")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "true")
    result = manager.grep("hit")
    assert [(m.path, m.line, m.text) for m in result.matches] == [("docs/a.md", 1, "ab Hit")]


@pytest.mark.asyncio
async def test_grep_strips_carriage_return_from_crlf_lines(tmp_path, remote):
    bare, work, _ = remote
    (work / "docs").mkdir(exist_ok=True)
    (work / "docs/crlf.md").write_bytes(b"Hello Search\r\nnext\r\n")
    _git(work, "add", "docs/crlf.md")
    _git(work, "commit", "-qm", "crlf")
    _git(work, "push", "-q", "origin", "main")
    manager = _manager(tmp_path, bare)
    await manager.ensure()
    assert [(m.line, m.text) for m in manager.grep("hello").matches] == [(1, "Hello Search")]


FAKE_TOKEN = "FAKE-TOKEN-1234"


def _token_manager(tmp_path: Path, monkeypatch, url: str = "https://example.invalid/r.git") -> GitRepoManager:
    monkeypatch.setenv("SYNCTX_TEST_GIT_TOKEN", FAKE_TOKEN)
    profile = RepoProfile(id="r", name="r", url=url, auth=RepoAuthConfig(type="token", credential_env="SYNCTX_TEST_GIT_TOKEN"))
    return GitRepoManager(profile, str(tmp_path / "cache"))


@pytest.mark.parametrize("timeout", [None, 5.0])
def test_failed_git_error_does_not_carry_auth_header(tmp_path, monkeypatch, timeout):
    manager = _token_manager(tmp_path, monkeypatch)
    with pytest.raises(subprocess.CalledProcessError) as info:
        manager._run_git("rev-parse", "HEAD", cwd=tmp_path, timeout=timeout)
    text = str(info.value) + repr(info.value.cmd)
    assert FAKE_TOKEN not in text
    assert base64.b64encode(f"x-access-token:{FAKE_TOKEN}".encode()).decode() not in text
    assert "extraHeader" not in text
    assert info.value.cmd == ["git", "rev-parse", "HEAD"]


@pytest.mark.parametrize("timeout", [None, 5.0])
def test_failed_git_error_masks_token_in_url_args(tmp_path, monkeypatch, caplog, timeout):
    manager = _token_manager(tmp_path, monkeypatch)
    with caplog.at_level("WARNING"), pytest.raises(subprocess.CalledProcessError) as info:
        manager._run_git("ls-remote", f"https://{FAKE_TOKEN}@127.0.0.1:9/r.git", cwd=tmp_path, timeout=timeout)
    assert FAKE_TOKEN not in str(info.value)
    assert FAKE_TOKEN not in caplog.text
    assert "https://***@127.0.0.1:9/r.git" in str(info.value)


def test_sanitize_git_text_masks_url_userinfo(tmp_path):
    manager = GitRepoManager(RepoProfile(id="r", name="r", url="https://example.invalid/r.git"), str(tmp_path / "cache"))
    assert manager._sanitize_git_text("fatal: https://ghp_abc123@github.com/o/r.git") == "fatal: https://***@github.com/o/r.git"
    assert manager._sanitize_git_text("https://user:pw@host/r.git") == "https://user:***@host/r.git"
    assert manager._sanitize_git_text("see https://host/r.git and a@b.com") == "see https://host/r.git and a@b.com"
