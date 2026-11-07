"""실제 Viewer 앱을 ASGI로 순회해 페이지·트리 JSON·참조 그림을 수집한다.

수집 키는 산출물의 공개 경로(Viewer URL 경로)다. 한글·공백·`#`·`?`가 든 경로는 `quote(path, safe="/")`로
인코딩한 형태이고, 파일 시스템 경로로 바꿀 때는 디코딩해서 쓴다.
- pages: `/view/`, `/view/{repo}`, `/view/{repo}/{경로}`
- json_files: `/view/api/repos/{repo}/tree/{디렉터리}.json`(루트는 `tree.json`), `/view/api/repos/{repo}/files.json`
- raw_files: `/raw/{repo}/{경로}`
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import posixpath
from collections.abc import Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import quote, unquote

import httpx

from static_export.config import SiteConfig, SiteRepo

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 25 * 1024 * 1024
CONCURRENCY = 8
REASON_UNSUPPORTED = "지원하지 않는 확장자"
REASON_TOO_LARGE = "렌더 크기 한도 초과"
REASON_EXCLUDED_LINK = "내보내기 대상에서 제외된 파일을 가리키는 링크"
REASON_EXCLUDED_IMAGE = "내보내기 대상에서 제외된 그림 참조"
REASON_BAD_IMAGE = "지원하지 않거나 올바르지 않은 그림 참조"


class CrawlError(Exception):
    """순회를 계속할 수 없는 오류. 메시지에는 URL·토큰·예외 본문을 넣지 않는다."""


@dataclass
class CrawlResult:
    pages: dict[str, str] = field(default_factory=dict)
    json_files: dict[str, str] = field(default_factory=dict)
    raw_files: dict[str, bytes] = field(default_factory=dict)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _hidden_prefix_length(path: str) -> int:
    """마지막으로 `.`으로 시작하는 경로 구간의 끝까지의 길이. 숨김 구간이 없으면 0."""
    end = offset = 0
    for segment in path.split("/"):
        offset += len(segment)
        if segment.startswith("."):
            end = offset
        offset += 1
    return end


def build_allowlist(paths: Iterable[str], repo: SiteRepo) -> frozenset[str]:
    """`include`·`exclude`를 적용한 내보내기 허용 목록. 비교는 sync_engine `_should_process`의 경로 접두 비교와 같다.

    경로의 어느 구간이든 `.`으로 시작하면(`.claude/`·`.github/`·`.hidden.md`) 기본 제외하고, `include`가 그 숨김 구간까지
    적은 경우에만 허용한다(`include: ["docs/"]`는 `docs/.draft/`를 켜지 않는다). `exclude`는 항상 우선한다.
    """
    includes = tuple(item.rstrip("*").rstrip("/") for item in repo.include)
    excludes = tuple(repo.exclude)

    def hidden_ok(path: str) -> bool:
        needed = _hidden_prefix_length(path)
        return needed == 0 or any(path.startswith(item) and len(item) >= needed for item in includes)

    return frozenset(
        path for path in paths
        if (not includes or path.startswith(includes)) and not (excludes and path.startswith(excludes)) and hidden_ok(path)
    )


@asynccontextmanager
async def _restrict_evidence_reads(viewer, repos: dict[str, SiteRepo]):
    """HTML에 발췌가 들어가기 전에 대상 저장소의 제한을 검사한다. 일반 Viewer의 읽기는 유지한다.

    페이지와 다른 저장소의 발췌는 읽지 않는다. 로그인 모드에서 열람자는 저장소마다 다르고, 발췌는 페이지 HTML과
    JSON 두 곳에 들어가 edge에서 지울 수 없기 때문이다.
    """
    original = viewer._evidence_reader

    def restricted_reader(page_repo_id: str, *args, **kwargs):
        read = original(page_repo_id, *args, **kwargs)

        def restricted_read(repo_id: str, path: str) -> str:
            if repo_id != page_repo_id:
                raise ValueError("다른 저장소의 근거는 내보내지 않습니다")
            repo = repos.get(repo_id)
            if repo is None or not build_allowlist([path], repo):
                raise ValueError("내보내기 대상에서 제외된 근거 파일입니다")
            return read(repo_id, path)

        return restricted_read

    viewer._evidence_reader = restricted_reader
    try:
        yield
    finally:
        viewer._evidence_reader = original


class _RefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in ("img", "source"):
            if values.get("src"):
                self.images.append(values["src"])
            if values.get("srcset"):
                srcset_candidates = importlib.import_module("renderer")._srcset_candidates
                self.images.extend(url for url, _ in srcset_candidates(values["srcset"]))
        elif tag == "a" and values.get("href"):
            self.links.append(values["href"])


def _encode(path: str) -> str:
    return quote(path, safe="/")


async def _get(client: httpx.AsyncClient, url: str, what: str, **kwargs) -> httpx.Response:
    try:
        response = await client.get(url, **kwargs)
    except Exception as exc:
        logger.warning("crawl_request_failed what=%s error=%s", what, type(exc).__name__)
        raise CrawlError(f"{what} 요청 중 오류가 났습니다({type(exc).__name__})") from None
    if response.status_code != 200:
        raise CrawlError(f"{what} 요청이 실패했습니다(HTTP {response.status_code})")
    return response


async def _wait_for_sync(viewer, site: SiteConfig) -> None:
    """lifespan은 clone을 백그라운드로 띄우고 바로 돌려주므로 끝날 때까지 기다린 뒤 모든 저장소가 ready인지 본다."""
    task = getattr(viewer, "_startup_sync", None)
    if task is not None:
        try:
            await asyncio.shield(task)
        except Exception as exc:
            raise CrawlError(f"저장소 동기화 작업이 실패했습니다({type(exc).__name__})") from None
    for repo in site.repositories:
        manager = viewer.get_repo_manager(repo.id)
        if manager.status != "ready":
            raise CrawlError(f"저장소 {repo.id}를 동기화하지 못했습니다({manager.last_error or manager.status})")


def _parent_dirs(paths: Iterable[str]) -> set[str]:
    dirs = {""}
    for path in paths:
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            dirs.add(str(parent))
            parent = parent.parent
    return dirs


def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


async def _crawl_repo(viewer, client, repo: SiteRepo, result: CrawlResult, semaphore: asyncio.Semaphore) -> dict[str, str]:
    manager = viewer.get_repo_manager(repo.id)
    limit = viewer.config.viewer.max_render_size_bytes
    image_types = viewer.RAW_IMAGE_TYPES

    all_paths = await asyncio.to_thread(manager.file_paths)
    allowed = build_allowlist(all_paths, repo)
    files = (await _get(client, f"/view/api/repos/{repo.id}/files", f"{repo.id} 파일 목록")).json()["files"]
    viewable = {path for path in files if path in allowed}
    for path in sorted(allowed - viewable):
        if PurePosixPath(path).suffix.lower() not in image_types:
            result.skipped.append((path, REASON_UNSUPPORTED))

    renderable = []
    for path in sorted(viewable):
        if await asyncio.to_thread(manager.file_size, path) > limit:
            result.skipped.append((path, REASON_TOO_LARGE))
        else:
            renderable.append(path)
    if not renderable:
        raise CrawlError(f"저장소 {repo.id}에 내보낼 문서가 없습니다")

    result.json_files[f"/view/api/repos/{repo.id}/files.json"] = _dump({"files": renderable})

    dirs = _parent_dirs(allowed)
    original_list_tree = manager.list_tree

    def list_allowed_tree(*args, **kwargs):
        return [e for e in original_list_tree(*args, **kwargs) if (e.path in allowed if e.type == "file" else e.path in dirs)]

    manager.list_tree = list_allowed_tree
    try:
        return await _crawl_allowed_pages(client, repo, result, semaphore, dirs, renderable)
    finally:
        manager.list_tree = original_list_tree


async def _crawl_allowed_pages(client, repo: SiteRepo, result: CrawlResult, semaphore: asyncio.Semaphore,
                               dirs: set[str], renderable: list[str]) -> dict[str, str]:
    result.pages[f"/view/{repo.id}"] = (await _get(client, f"/view/{repo.id}", f"{repo.id} 홈 페이지")).text
    for directory in sorted(dirs):
        data = (await _get(client, f"/view/api/repos/{repo.id}/tree", f"{repo.id} 트리", params={"path": directory})).json()
        tree_key = f"/view/api/repos/{repo.id}/tree" + (f"/{_encode(directory)}" if directory else "")
        result.json_files[f"{tree_key}.json"] = _dump(data)

    async def fetch(path: str) -> tuple[str, str]:
        async with semaphore:
            url = f"/view/{repo.id}/{_encode(path)}"
            return url, (await _get(client, url, f"{repo.id}/{path} 페이지")).text

    for url, html in await asyncio.gather(*(fetch(path) for path in renderable)):
        result.pages[url] = html
    return {path: result.pages[f"/view/{repo.id}/{_encode(path)}"] for path in renderable}


async def _collect_raw_images(client, repos: dict[str, SiteRepo], viewer, result: CrawlResult, html_pages: Iterable[str]) -> None:
    image_types = viewer.RAW_IMAGE_TYPES
    seen: set[tuple[str, str]] = set()
    for html in html_pages:
        collector = _RefCollector()
        collector.feed(html)
        for src in collector.images:
            if not src.startswith("/raw/"):
                continue
            repo_id, _, encoded = src[len("/raw/"):].partition("/")
            encoded = encoded.split("#", 1)[0].split("?", 1)[0]
            path = posixpath.normpath(unquote(encoded)) if encoded else ""
            if (repo_id, path) in seen:
                continue
            seen.add((repo_id, path))
            repo = repos.get(repo_id)
            if repo is None or path in ("", ".") or path.startswith("..") or path.startswith("/") \
                    or PurePosixPath(path).suffix.lower() not in image_types:
                result.skipped.append((path or src, REASON_BAD_IMAGE))
                continue
            if not build_allowlist([path], repo):
                result.skipped.append((path, REASON_EXCLUDED_IMAGE))
                continue
            manager = viewer.get_repo_manager(repo_id)
            try:
                size = await asyncio.to_thread(manager.file_size, path)
            except Exception as exc:
                logger.warning("crawl_image_size_failed repo_id=%s error=%s", repo_id, type(exc).__name__)
                raise CrawlError(f"참조된 그림을 찾지 못했습니다: {repo_id}/{path}") from None
            if size > MAX_FILE_BYTES:
                raise CrawlError(f"그림 크기가 한도({MAX_FILE_BYTES}바이트)를 넘습니다: {repo_id}/{path}")
            key = f"/raw/{repo_id}/{_encode(path)}"
            result.raw_files[key] = (await _get(client, f"/view{key}", f"그림 {repo_id}/{path}")).content


def _record_excluded_links(repos: dict[str, SiteRepo], result: CrawlResult, html_pages: Iterable[str]) -> None:
    for html in html_pages:
        collector = _RefCollector()
        collector.feed(html)
        for href in collector.links:
            if not href.startswith("/view/"):
                continue
            repo_id, _, encoded = href[len("/view/"):].partition("/")
            repo = repos.get(repo_id)
            encoded = encoded.split("#", 1)[0].split("?", 1)[0]
            if repo is None or not encoded:
                continue
            path = unquote(encoded)
            if not build_allowlist([path], repo):
                entry = (path, REASON_EXCLUDED_LINK)
                if entry not in result.skipped:
                    result.skipped.append(entry)


async def crawl(viewer, site: SiteConfig) -> CrawlResult:
    standalone = importlib.import_module("standalone")
    app = standalone.create_app(0, viewer=viewer)
    result = CrawlResult()
    repos = {repo.id: repo for repo in site.repositories}
    semaphore = asyncio.Semaphore(CONCURRENCY)
    async with _restrict_evidence_reads(viewer, repos), app.router.lifespan_context(app):
        await _wait_for_sync(viewer, site)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://static.local") as client:
            result.pages["/view/"] = (await _get(client, "/view/", "저장소 목록 페이지")).text
            documents: list[str] = []
            for repo in site.repositories:
                documents.extend((await _crawl_repo(viewer, client, repo, result, semaphore)).values())
            await _collect_raw_images(client, repos, viewer, result, documents)
    _record_excluded_links(repos, result, documents)
    result.skipped = sorted(set(result.skipped))
    return result
