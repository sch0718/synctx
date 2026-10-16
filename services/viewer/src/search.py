"""Viewer 본문 검색: 여러 저장소를 기한 안에서 찾고 결과 줄을 화면용으로 다듬는다."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

QUERY_MIN = 2
QUERY_MAX = 100
RESULT_LIMIT = 50
TIMEOUT_SECONDS = 3.0
SNIPPET_WIDTH = 160
MAX_CONCURRENT = 4

_ELLIPSIS = "…"
# 줄바꿈은 -F에서 여러 패턴이 되고, NUL은 subprocess 인자로 넘기면 예외가 난다
_FORBIDDEN_QUERY_CHARS = ("\n", "\r", "\x00")
_PARTIAL_CLONE_MODES = {"blobless", "treeless"}

# 저장소 동기화가 쓰는 기본 실행기를 검색이 점유하지 않게 따로 둔다
_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="viewer-search")


def normalize_query(raw: str | None) -> str | None:
    if raw is None:
        return None
    query = raw.strip()
    if not QUERY_MIN <= len(query) <= QUERY_MAX or any(char in query for char in _FORBIDDEN_QUERY_CHARS):
        return None
    return query


def viewable_pathspecs(
    markdown_extensions: Iterable[str],
    code_extensions: Iterable[str],
    code_filenames: Iterable[str],
) -> list[str]:
    specs = {f":(glob,icase)**/*{ext}" for ext in (*markdown_extensions, *code_extensions)}
    specs.update(f":(glob,icase)**/{name}" for name in code_filenames)
    return sorted(specs)


def split_snippet(line: str, query: str, width: int = SNIPPET_WIDTH) -> tuple[str, str, str]:
    found = re.search(re.escape(query), line, re.IGNORECASE)
    if found is None:
        if len(line) <= width:
            return line, "", ""
        return line[: width - 1] + _ELLIPSIS, "", ""

    start, end = found.span()
    if len(line) <= width:
        return line[:start], line[start:end], line[end:]

    match = line[start:end][:width]
    room = width - len(match)
    before_budget = min(start, room // 2)
    after_budget = min(len(line) - end, room - before_budget)
    before_budget = min(start, room - after_budget)

    before = line[start - before_budget : start] if before_budget else ""
    after = line[end : end + after_budget]
    if before_budget < start and before_budget:
        before = _ELLIPSIS + before[1:]
    if end + after_budget < len(line) and after_budget:
        after = after[:-1] + _ELLIPSIS
    return before, match, after


def _result(repo_id: str, repo_name: str, status: str, truncated: bool = False,
            matches: list[dict] | None = None) -> dict:
    return {"id": repo_id, "name": repo_name, "status": status,
            "truncated": truncated, "matches": matches or []}


def _grep_repository(repo_id: str, repo_name: str, manager: Any, query: str, ref: str | None,
                     pathspecs: Sequence[str], path_filter: Callable[[str], bool],
                     started: float) -> dict:
    # 차례를 기다린 시간도 기한에 들어간다. 기한이 지나 늦게 실행되는 작업은 git을 부르지 않는다
    remaining = TIMEOUT_SECONDS - (time.monotonic() - started)
    if remaining <= 0:
        return _result(repo_id, repo_name, "timeout")
    try:
        grep = manager.grep(query, ref, pathspecs=pathspecs, path_filter=path_filter,
                            limit=RESULT_LIMIT, timeout=remaining)
        matches = []
        for item in grep.matches:
            before, match, after = split_snippet(item.text, query)
            matches.append({"path": item.path, "line": item.line,
                            "before": before, "match": match, "after": after})
        return _result(repo_id, repo_name, grep.status, grep.truncated, matches)
    except Exception as exc:
        # git 인자(인증 헤더 포함)가 예외 문구에 실릴 수 있어 종류만 남긴다
        logger.warning("search_failed repo_id=%s error=%s", repo_id, type(exc).__name__)
        return _result(repo_id, repo_name, "failed")


async def search_repositories(
    managers: Sequence[tuple[str, str, Any]],
    query: str,
    ref: str | None,
    pathspecs: Sequence[str],
    path_filter: Callable[[str], bool],
) -> list[dict]:
    results: list[dict | None] = []
    targets: list[tuple[int, str, str, Any]] = []
    for index, (repo_id, repo_name, manager) in enumerate(managers):
        # 실행기를 기다리지 않는 판정이라 작업 풀이 꽉 차도 상태가 유지된다
        if manager.status != "ready":
            results.append(_result(repo_id, repo_name, "unavailable"))
        elif manager.profile.storage_mode in _PARTIAL_CLONE_MODES:
            results.append(_result(repo_id, repo_name, "unsupported"))
        else:
            results.append(None)
            targets.append((index, repo_id, repo_name, manager))
    if not targets:
        return results  # type: ignore[return-value]

    started = time.monotonic()
    futures = {
        asyncio.wrap_future(_EXECUTOR.submit(
            _grep_repository, repo_id, repo_name, manager, query, ref, pathspecs, path_filter, started
        )): (index, repo_id, repo_name)
        for index, repo_id, repo_name, manager in targets
    }
    done, pending = await asyncio.wait(
        futures, timeout=max(TIMEOUT_SECONDS - (time.monotonic() - started), 0))
    for future in pending:
        future.cancel()
        index, repo_id, repo_name = futures[future]
        results[index] = _result(repo_id, repo_name, "timeout")
    for future in done:
        results[futures[future][0]] = future.result()
    return results  # type: ignore[return-value]
