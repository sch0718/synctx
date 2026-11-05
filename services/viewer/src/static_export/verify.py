import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Sequence
from urllib.parse import unquote, urljoin, urlsplit

MAX_FILES = 20_000
MAX_FILE_BYTES = 25 * 1024 * 1024
REQUIRED_FILES = ("_headers", "_redirects", "robots.txt")

SERVER_ONLY_HREF_PATTERNS = (
    re.compile(r"^/view/api/repos/[^/?#]+/download(?:[?#]|$)"),
)

_SECRET_PATTERNS = (
    ("ghp_ 토큰 패턴", re.compile(rb"ghp_[A-Za-z0-9]{8,}")),
    ("github_pat_ 토큰 패턴", re.compile(rb"github_pat_[A-Za-z0-9_]{8,}")),
    ("Authorization 헤더 패턴", re.compile(rb"(?i)authorization\s*[:=]\s*(?:bearer|basic|token)\s+\S+")),
)
_REPOSITORY_PAGE = re.compile(r"view/[^/]+/index\.html")
_EXTERNAL_SCHEMES = ("http", "https", "mailto", "tel", "data", "javascript", "blob")
_LINK_ATTRS = {"href", "src"}


_SRCSET_CANDIDATE = re.compile(r"[\s,]*([^\s]+)")


def _srcset_urls(value: str) -> list[str]:
    urls = []
    pos = 0
    while True:
        match = _SRCSET_CANDIDATE.match(value, pos)
        if not match:
            return urls
        url = match.group(1)
        pos = match.end()
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            comma = value.find(",", pos)
            pos = len(value) if comma == -1 else comma + 1
        if url:
            urls.append(url)


class _RefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if not value:
                continue
            if name in _LINK_ATTRS:
                self.refs.append(value.strip())
            elif name == "srcset":
                self.refs.extend(_srcset_urls(value))


def _page_url(rel: str) -> str:
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel


def _resolves(out_dir: Path, root_resolved: Path, url_path: str, has_root_redirect: bool) -> bool:
    if url_path == "/":
        return has_root_redirect or (out_dir / "index.html").is_file()
    target = (out_dir / unquote(url_path).lstrip("/")).resolve()
    if root_resolved != target and root_resolved not in target.parents:
        return False
    return target.is_file() or (target / "index.html").is_file()


def _broken_refs(out_dir: Path, root_resolved: Path, rel: str, data: bytes, has_root_redirect: bool) -> list[str]:
    collector = _RefCollector()
    collector.feed(data.decode("utf-8", errors="replace"))
    base = _page_url(rel)
    broken = []
    for ref in collector.refs:
        if not ref or ref.startswith("#"):
            continue
        parts = urlsplit(ref)
        if parts.scheme in _EXTERNAL_SCHEMES or ref.startswith("//"):
            continue
        if any(pattern.match(ref) for pattern in SERVER_ONLY_HREF_PATTERNS):
            continue
        url_path = urljoin(base, parts.path) if parts.path else base
        if not _resolves(out_dir, root_resolved, url_path, has_root_redirect):
            broken.append(ref)
    return broken


def _redact(message: str, secrets: Sequence[tuple[bytes, str]]) -> str:
    for _, value in secrets:
        message = message.replace(value, "***")
    encoded = message.encode("utf-8")
    for _, pattern in _SECRET_PATTERNS[:2]:
        encoded = pattern.sub(b"***", encoded)
    return encoded.decode("utf-8")


def verify_dist(out_dir: Path, *, secret_values: Sequence[str], exclude_markers: Sequence[str] = ()) -> list[str]:
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return ["산출물 디렉터리가 없다"]

    secrets = [(s.encode("utf-8"), s) for s in secret_values if s and s.strip()]
    markers = [(i, m, m.encode("utf-8")) for i, m in enumerate(exclude_markers, 1) if m and m.strip()]
    violations: list[str] = []

    for name in REQUIRED_FILES:
        if not (out_dir / name).is_file():
            violations.append(f"필수 파일이 없다: {name}")

    root_resolved = out_dir.resolve()
    redirects = out_dir / "_redirects"
    has_root_redirect = redirects.is_file() and any(
        line.split()[:1] == ["/"] for line in redirects.read_text(encoding="utf-8", errors="replace").splitlines()
    )

    files: list[tuple[str, Path]] = []
    for current, dirs, names in os.walk(out_dir, followlinks=False):
        current_path = Path(current)
        for d in list(dirs):
            if d == ".git":
                violations.append(f".git 디렉터리가 있다: {(current_path / d).relative_to(out_dir).as_posix()}")
                dirs.remove(d)
            elif (current_path / d).is_symlink():
                violations.append(f"심볼릭 링크가 있다: {(current_path / d).relative_to(out_dir).as_posix()}")
                dirs.remove(d)
        for n in names:
            path = current_path / n
            files.append((path.relative_to(out_dir).as_posix(), path))

    if len(files) > MAX_FILES:
        violations.append(f"파일 수 한도 초과: {len(files)}개 (한도 {MAX_FILES}개)")

    for rel, path in sorted(files):
        if path.is_symlink():
            violations.append(f"심볼릭 링크가 있다: {rel}")
            continue
        rel_bytes = rel.encode("utf-8")
        path_has_secret = any(raw in rel_bytes for raw, _ in secrets)
        if path_has_secret:
            violations.append("비밀 값이 파일 경로에 있다(경로는 출력하지 않음)")
        for i, _, raw in markers:
            if raw in rel_bytes:
                violations.append(f"제외 대상 표지 #{i}가 경로에 있다: {'(경로 생략)' if path_has_secret else rel}")

        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            violations.append(f"파일 크기 한도 초과: {rel} ({size}바이트, 한도 {MAX_FILE_BYTES}바이트)")
            continue
        label = "(경로 생략)" if path_has_secret else rel
        data = path.read_bytes()
        if any(raw in data for raw, _ in secrets):
            violations.append(f"비밀 값이 본문에 있다: {label}")
        for pattern_name, pattern in _SECRET_PATTERNS:
            if pattern.search(data):
                violations.append(f"{pattern_name}이 있다: {label}")
        for i, _, raw in markers:
            if raw in data:
                violations.append(f"제외 대상 표지 #{i}가 본문에 있다: {label}")
        if _REPOSITORY_PAGE.fullmatch(rel) and b"data-expand-path" in data:
            violations.append(f"저장소 페이지에 data-expand-path가 있다(트리는 주소의 path 쿼리로 펼친다): {label}")
        if rel.endswith((".html", ".htm")):
            for ref in _broken_refs(out_dir, root_resolved, rel, data, has_root_redirect):
                violations.append(f"깨진 내부 링크·자산 참조: {label} → {ref}")

    return [_redact(v, secrets) for v in violations]
