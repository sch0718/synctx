import asyncio, threading, time
from types import SimpleNamespace
import pytest
import search
from synctx_common.git_manager import GrepMatch, GrepResult

@pytest.mark.parametrize("raw, expected", [
    ("  회의 ", "회의"), ("a", None), (" ", None), (None, None),
    ("x" * 100, "x" * 100), ("x" * 101, None), ("a\nb", None), ("a\rb", None), ("a\x00b", None)])
def test_normalize_query(raw, expected):
    assert search.normalize_query(raw) == expected

def test_viewable_pathspecs():
    assert search.viewable_pathspecs([".md"], [".py", ".md"], ["Dockerfile"]) == [
        ":(glob,icase)**/*.md", ":(glob,icase)**/*.py", ":(glob,icase)**/Dockerfile"]

@pytest.mark.parametrize("line, query, expected", [
    ("Hello Search", "search", ("Hello ", "Search", "")),
    ("😀 Hello Search", "search", ("😀 Hello ", "Search", "")),
    ("ß Hello Search", "search", ("ß Hello ", "Search", "")),
    ("nothing", "zz", ("nothing", "", "")),
])
def test_split_snippet_short_lines(line, query, expected):
    assert search.split_snippet(line, query) == expected

def test_split_snippet_without_match_on_long_line():
    before, match, after = search.split_snippet("x" * 300, "zz")
    assert (match, after) == ("", "") and len(before) == 160 and before.endswith("…")

def test_split_snippet_keeps_long_query_whole():
    q = "Q" * 100
    before, match, after = search.split_snippet("a" * 300 + q + "b" * 300, q.lower())
    assert match == q and before.startswith("…") and after.endswith("…")
    assert len(before) + len(match) + len(after) <= 160

def _manager(status="ready", result=None, exc=None, delay=0.0, storage_mode="full"):
    def grep(query, ref=None, **kwargs):
        time.sleep(delay)
        if exc:
            raise exc
        return result
    return SimpleNamespace(status=status, grep=grep, profile=SimpleNamespace(storage_mode=storage_mode))

def test_search_repositories_statuses_and_order():
    ok = GrepResult(status="ok", matches=[GrepMatch("a.md", 3, "Hello Search")], truncated=True)
    repos = [("r1", "One", _manager(result=ok)),
             ("r2", "Two", _manager(status="cloning")),
             ("r3", "Three", _manager(exc=RuntimeError("Authorization: Basic SECRET"))),
             ("r4", "Four", _manager(storage_mode="blobless"))]
    out = asyncio.run(search.search_repositories(repos, "search", None, [], lambda p: True))
    assert [(r["id"], r["status"]) for r in out] == [("r1", "ok"), ("r2", "unavailable"), ("r3", "failed"), ("r4", "unsupported")]
    assert out[0]["truncated"] is True
    assert out[0]["matches"] == [{"path": "a.md", "line": 3, "before": "Hello ", "match": "Search", "after": ""}]
    assert "SECRET" not in repr(out)

def test_search_runs_at_most_max_concurrent_greps(monkeypatch):
    active, peak, lock = [0], [0], threading.Lock()
    def grep(query, ref=None, **kwargs):
        with lock:
            active[0] += 1; peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return GrepResult("ok", [], False)
    repos = [(f"r{i}", f"R{i}", SimpleNamespace(status="ready", grep=grep, profile=SimpleNamespace(storage_mode="full"))) for i in range(10)]
    asyncio.run(search.search_repositories(repos, "ab", None, [], lambda p: True))
    assert peak[0] <= search.MAX_CONCURRENT

def _slow_grep(seconds, seen):
    # 실제 grep처럼 받은 기한 안에서만 기다리고, 기한을 넘기면 결과 없이 timeout을 돌려준다
    def grep(query, ref=None, **kwargs):
        seen.append(kwargs["timeout"])
        time.sleep(min(seconds, kwargs["timeout"]))
        return GrepResult("ok", [], False) if seconds <= kwargs["timeout"] else GrepResult("timeout", [], False)
    return grep

def test_search_waiting_time_counts_toward_deadline(monkeypatch):
    monkeypatch.setattr(search, "TIMEOUT_SECONDS", 0.1)
    seen = []
    repos = [(f"r{i}", f"R{i}", SimpleNamespace(status="ready", grep=_slow_grep(0.15, seen), profile=SimpleNamespace(storage_mode="full")))
             for i in range(search.MAX_CONCURRENT + 2)]
    started = time.monotonic()
    out = asyncio.run(search.search_repositories(repos, "ab", None, [], lambda p: True))
    assert time.monotonic() - started < 0.2
    assert all(0 < t <= 0.1 for t in seen)
    assert {r["status"] for r in out} == {"timeout"} and all(r["matches"] == [] for r in out)

def test_search_answers_by_deadline_when_executor_is_busy(monkeypatch):
    monkeypatch.setattr(search, "TIMEOUT_SECONDS", 0.1)
    seen, search_futures = [], []
    release = threading.Event()
    started = threading.Barrier(search.MAX_CONCURRENT + 1)
    def block():
        started.wait(timeout=1); release.wait(timeout=2)
    blockers = [search._EXECUTOR.submit(block) for _ in range(search.MAX_CONCURRENT)]
    started.wait(timeout=1)  # 작업자 4개가 모두 막힌 뒤에 검색을 시작한다
    real_submit = search._EXECUTOR.submit
    def submit(fn, *args, **kwargs):
        future = real_submit(fn, *args, **kwargs); search_futures.append(future); return future
    monkeypatch.setattr(search._EXECUTOR, "submit", submit)
    try:
        repos = [("r1", "R1", SimpleNamespace(status="ready", grep=_slow_grep(0.0, seen), profile=SimpleNamespace(storage_mode="full"))),
                 ("r2", "R2", SimpleNamespace(status="cloning", grep=_slow_grep(0.0, seen), profile=SimpleNamespace(storage_mode="full"))),
                 ("r3", "R3", SimpleNamespace(status="ready", grep=_slow_grep(0.0, seen), profile=SimpleNamespace(storage_mode="treeless")))]
        begin = time.monotonic()
        # 기한 처리가 빠진 구현도 무한 대기하지 않고 실패하도록 바깥 제한을 둔다
        out = asyncio.run(asyncio.wait_for(search.search_repositories(repos, "ab", None, [], lambda p: True), 1.0))
        assert time.monotonic() - begin < 0.2
        assert [r["status"] for r in out] == ["timeout", "unavailable", "unsupported"]
    finally:
        release.set()
        for f in blockers:
            f.result(timeout=2)
        # 기한을 넘긴 대기 작업을 취소하는 구현도 맞다(취소된 작업은 git을 부르지 않는다)
        for f in search_futures:
            if not f.cancelled():
                f.result(timeout=2)
    assert len(search_futures) == 1 and seen == []

def test_search_skips_executor_when_every_repo_is_excluded(monkeypatch):
    submitted = []
    monkeypatch.setattr(search._EXECUTOR, "submit", lambda *a, **k: submitted.append(a))
    repos = [("r1", "R1", _manager(status="cloning")), ("r2", "R2", _manager(storage_mode="blobless"))]
    out = asyncio.run(search.search_repositories(repos, "ab", None, [], lambda p: True))
    assert [r["status"] for r in out] == ["unavailable", "unsupported"] and submitted == []

def test_search_with_no_repositories_returns_empty():
    assert asyncio.run(search.search_repositories([], "ab", None, [], lambda p: True)) == []


def test_failed_repository_logs_only_exception_type(caplog):
    repos = [("r1", "One", _manager(exc=RuntimeError("Authorization: Basic SECRET")))]
    with caplog.at_level("DEBUG"):
        out = asyncio.run(search.search_repositories(repos, "ab", None, [], lambda p: True))
    assert out[0]["status"] == "failed"
    assert "SECRET" not in caplog.text
    assert "RuntimeError" in caplog.text and "r1" in caplog.text


def test_result_building_error_fails_only_that_repository():
    ok = GrepResult(status="ok", matches=[GrepMatch("a.md", 1, "ab")], truncated=False)
    broken = _manager(result=SimpleNamespace(status="ok", truncated=False))
    repos = [("r1", "One", _manager(result=ok)), ("r2", "Two", broken), ("r3", "Three", _manager(result=ok))]
    out = asyncio.run(search.search_repositories(repos, "ab", None, [], lambda p: True))
    assert [(r["id"], r["status"]) for r in out] == [("r1", "ok"), ("r2", "failed"), ("r3", "ok")]
