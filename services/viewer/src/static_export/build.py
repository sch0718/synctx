"""정적 사이트 빌드 조립: 순회 결과를 후처리해 `dist/`로 쓰고 Cloudflare Pages용 설정 파일을 만든다."""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from static_export import crawl, pages, runtime, themes
from static_export.config import SiteConfig

OUT_MARKER = ".static-export-out"
STATIC_SOURCE = Path(__file__).resolve().parents[1] / "static"
STATIC_MODE_SOURCE = Path(__file__).resolve().parent / "assets" / "static-mode.js"
REPO_ROOT = Path(__file__).resolve().parents[4]

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'")


class BuildConfigError(ValueError):
    """출력 위치처럼 설정이 잘못돼 빌드를 시작할 수 없는 오류."""


@dataclass
class BuildReport:
    page_count: int = 0
    file_count: int = 0
    total_bytes: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    stage_seconds: dict[str, float] = field(default_factory=dict)


def output_file_path(url_path: str) -> str:
    """Viewer URL 경로를 `dist` 안의 파일 경로로 바꾼다(디코딩, `..`·절대 경로·NUL은 ValueError)."""
    return pages.page_output_path(url_path).removesuffix("/index.html")


def _related(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _contains_or_equals(outer: Path, inner: Path) -> bool:
    return outer == inner or outer in inner.parents


def _prepare_out_dir(out_dir: Path, cache_dir: Path, config_path: Path | None,
                     local_sources: Sequence[Path] = ()) -> Path:
    """거부 조건을 모두 통과한 경우에만 비우고 표지를 남긴다. 거부하면 아무것도 지우지 않는다."""
    out_dir = Path(out_dir).expanduser()
    if out_dir.is_symlink():
        raise BuildConfigError("출력 디렉터리가 심볼릭 링크입니다")
    out = out_dir.resolve()
    if out.parent == out:
        raise BuildConfigError("파일 시스템 루트는 출력 디렉터리로 쓸 수 없습니다")
    if _contains_or_equals(out, REPO_ROOT.resolve()):
        raise BuildConfigError("출력 디렉터리가 저장소 루트이거나 그 상위입니다")
    if _contains_or_equals(out, Path.home().resolve()):
        raise BuildConfigError("출력 디렉터리가 홈 디렉터리이거나 그 상위입니다")
    if _contains_or_equals(out, Path.cwd().resolve()):
        raise BuildConfigError("출력 디렉터리가 현재 작업 디렉터리이거나 그 상위입니다")
    if _related(out, Path(cache_dir).expanduser().resolve()):
        raise BuildConfigError("출력 디렉터리와 캐시 디렉터리가 같거나 서로 포함합니다")
    if config_path is not None and _contains_or_equals(out, Path(config_path).expanduser().resolve()):
        raise BuildConfigError("출력 디렉터리가 설정 파일 위치를 포함합니다")
    if any(_contains_or_equals(out, source) for source in local_sources):
        raise BuildConfigError("출력 디렉터리가 로컬 경로로 지정한 원본 저장소를 포함합니다")
    if out.exists():
        if not out.is_dir():
            raise BuildConfigError("출력 경로가 디렉터리가 아닙니다")
        if any(out.iterdir()) and not (out / OUT_MARKER).is_file():
            raise BuildConfigError("비어 있지 않고 이전 빌드 표지가 없는 디렉터리는 출력 디렉터리로 쓸 수 없습니다")
        for child in out.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    out.mkdir(parents=True, exist_ok=True)
    (out / OUT_MARKER).write_bytes(b"")
    return out


class _Writer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.written: set[str] = set()

    def write(self, rel: str, data: bytes | str) -> None:
        target = (self.root / rel).resolve()
        if self.root not in target.parents:
            raise ValueError("출력 경로가 출력 디렉터리 밖입니다")
        if rel in self.written or target.exists():
            raise BuildConfigError(f"같은 출력 경로로 겹치는 파일이 있습니다: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        self.written.add(rel)


def _headers_file() -> str:
    raw_svg_csp = importlib.import_module("app").RAW_SVG_CSP
    return (
        "/*\n"
        f"  Content-Security-Policy: {CSP}\n"
        "  X-Frame-Options: DENY\n"
        "  X-Content-Type-Options: nosniff\n"
        "  X-Robots-Tag: noindex\n"
        "  Cache-Control: no-store\n"
        "/raw/*.svg\n"
        f"  Content-Security-Policy: {raw_svg_csp}\n"
        "  X-Content-Type-Options: nosniff\n"
    )


def _copy_static(writer: _Writer) -> None:
    for source in sorted(STATIC_SOURCE.rglob("*")):
        if source.is_file() and not source.is_symlink() and "__pycache__" not in source.parts:
            writer.write(f"view/static/{source.relative_to(STATIC_SOURCE).as_posix()}", source.read_bytes())


def build_site(site: SiteConfig, out_dir: Path, cache_dir: Path, *, config_path: Path | None = None) -> BuildReport:
    local_sources = [Path(repo.url).expanduser().resolve() for repo in site.repositories if "://" not in repo.url]
    out = _prepare_out_dir(Path(out_dir), Path(cache_dir), config_path, local_sources)
    report = BuildReport()

    started = time.perf_counter()
    viewer = runtime.load_viewer(runtime.write_runtime_config(site, cache_dir))
    result = asyncio.run(crawl.crawl(viewer, site))
    report.stage_seconds["crawl"] = round(time.perf_counter() - started, 3)

    started = time.perf_counter()
    writer = _Writer(out)
    _copy_static(writer)
    writer.write("view/static/static-mode.js", STATIC_MODE_SOURCE.read_bytes())
    writer.written.update(themes.export_theme_assets(out).values())
    for url in sorted(result.pages):
        writer.write(pages.page_output_path(url), pages.finalize_page(result.pages[url], default_theme=site.theme))
    for url in sorted(result.json_files):
        writer.write(output_file_path(url), result.json_files[url])
    for url in sorted(result.raw_files):
        writer.write(output_file_path(url), result.raw_files[url])
    writer.write("_headers", _headers_file())
    writer.write("_redirects", "/ /view/ 302\n")
    writer.write("_synctx/repos.json", json.dumps(
        [{"id": repo.id, "name": repo.name} for repo in site.repositories], ensure_ascii=False))
    writer.write("robots.txt", "User-agent: *\nDisallow: /\n")
    report.stage_seconds["write"] = round(time.perf_counter() - started, 3)

    report.page_count = len(result.pages)
    report.skipped = list(result.skipped)
    sizes = [(out / rel).stat().st_size for rel in writer.written]
    report.file_count = len(sizes)
    report.total_bytes = sum(sizes)
    return report
