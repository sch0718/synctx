"""synctx-html 블록: 저장소의 HTML 묶음을 한 파일로 합치고 sandbox iframe으로 감싼다."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from html import escape as _html_escape
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

import yaml

REASONS = (
    "missing_file",
    "outside_repo",
    "disallowed_host",
    "too_large",
    "invalid_block",
    "unsupported",
)

_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
_ASPECT_RE = re.compile(r"^\d+(?:\.\d+)?/\d+(?:\.\d+)?$")
_BLOCK_KEYS = {"src", "aspect", "height", "title"}


class BundleError(Exception):
    def __init__(self, reason: str, message: str, files=(), missing=()):
        super().__init__(message)
        self.reason = reason
        self.files = tuple(sorted(set(files)))
        self.missing = tuple(sorted(set(missing)))


@dataclass(frozen=True)
class BlockMatch:
    start: int
    end: int
    body: str


@dataclass(frozen=True)
class HtmlBlock:
    src: str
    aspect: str = "16/9"
    height: Optional[int] = None
    title: Optional[str] = None


@dataclass(frozen=True)
class BundleResult:
    html: str
    sha256: str
    size: int
    files: tuple
    title: Optional[str]


def find_blocks(md_text: str) -> list:
    """CommonMark 펜스 규칙으로 최상위 ```synctx-html 블록만 찾는다. 다른 펜스 안의 예시는 무시한다."""
    blocks = []
    fence = None
    pos = 0
    for line in md_text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        match = _FENCE_RE.match(content)
        if fence is None:
            if match:
                marker, info = match.group(2), match.group(3).strip()
                if not (marker[0] == "`" and "`" in info):
                    fence = (marker[0], len(marker), info == "synctx-html", pos, pos + len(line))
        else:
            char, length, is_block, start, body_start = fence
            if match and match.group(2)[0] == char and len(match.group(2)) >= length and not match.group(3).strip():
                if is_block:
                    blocks.append(BlockMatch(start, pos + len(content), md_text[body_start:pos]))
                fence = None
        pos += len(line)
    return blocks


def replace_blocks(md_text: str, render: Callable[[int, BlockMatch], str]) -> str:
    out = []
    last = 0
    for index, block in enumerate(find_blocks(md_text)):
        out.append(md_text[last:block.start])
        out.append(render(index, block))
        last = block.end
    out.append(md_text[last:])
    return "".join(out)


def escape_html(value: str) -> str:
    """속성값·텍스트용 이스케이프. XWiki 매크로 경계를 지키려고 중괄호도 엔티티로 바꾼다."""
    return _html_escape(value, quote=True).replace("{", "&#123;").replace("}", "&#125;")


def classify_url(url: str) -> str:
    value = url.strip()
    lowered = value.lower()
    if not value or value.startswith("#") or lowered.startswith(("data:", "about:")):
        return "keep"
    if value.startswith("//") or value.startswith("\\\\"):
        return "protocol_relative"
    if lowered.startswith(("https:", "http:")):
        return "absolute"
    if _SCHEME_RE.match(value):
        return "other_scheme"
    return "relative"


def resolve_path(base_file: str, reference: str) -> str:
    try:
        path = unquote(urlsplit(reference.strip()).path)
    except ValueError as exc:
        raise BundleError("unsupported", f"해석할 수 없는 경로입니다: {reference}") from exc
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base_file), path))
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        raise BundleError("outside_repo", f"저장소 밖을 가리키는 경로입니다: {reference}")
    return joined


def parse_block(body: str, md_path: str) -> HtmlBlock:
    try:
        data = yaml.safe_load(body) if body.strip() else None
    except yaml.YAMLError as exc:
        raise BundleError("invalid_block", f"블록 YAML을 읽을 수 없습니다: {exc}") from exc
    if not isinstance(data, dict):
        raise BundleError("invalid_block", "블록 내용은 src를 담은 YAML 객체여야 합니다")
    unknown = sorted(str(key) for key in data if key not in _BLOCK_KEYS)
    if unknown:
        raise BundleError("invalid_block", f"알 수 없는 키입니다: {', '.join(unknown)}")
    src = data.get("src")
    if not isinstance(src, str) or not src.strip():
        raise BundleError("invalid_block", "src가 없습니다")
    if classify_url(src) != "relative":
        raise BundleError("invalid_block", f"src는 Markdown 파일 기준 상대 경로여야 합니다: {src}")
    aspect = data.get("aspect", "16/9")
    if not isinstance(aspect, str) or not _ASPECT_RE.match(aspect):
        raise BundleError("invalid_block", f"aspect는 W/H 형식이어야 합니다: {aspect}")
    height = data.get("height")
    if height is not None and (isinstance(height, bool) or not isinstance(height, int) or not 1 <= height <= 10000):
        raise BundleError("invalid_block", f"height는 1~10000 사이 정수여야 합니다: {height}")
    title = data.get("title")
    if title is not None and not isinstance(title, str):
        raise BundleError("invalid_block", "title은 문자열이어야 합니다")
    return HtmlBlock(src=resolve_path(md_path, src), aspect=aspect, height=height, title=title)


def to_iframe(result: BundleResult, aspect: str = "16/9", height: Optional[int] = None, title: Optional[str] = None) -> str:
    label = title or result.title or "인터랙티브 콘텐츠"
    style = f"width:100%;height:{height}px;border:0" if height else f"width:100%;aspect-ratio:{aspect};border:0"
    return (
        f'<iframe class="synctx-html" sandbox="allow-scripts" title="{escape_html(label)}" '
        f'style="{escape_html(style)}" srcdoc="{escape_html(result.html)}"></iframe>'
    )


def render_notice(message: str, src: Optional[str], href: Optional[str] = None) -> str:
    parts = ['<div class="synctx-html-notice" role="note">', f"<p>{escape_html(message)}</p>"]
    if src:
        label = escape_html(src)
        parts.append(f'<p><a href="{escape_html(href)}">{label}</a></p>' if href else f"<p><code>{label}</code></p>")
    parts.append("</div>")
    return "".join(parts)


def xwiki_html_macro(inner: str) -> str:
    if "{" in inner or "}" in inner:
        raise ValueError("html 매크로 본문에 중괄호가 남아 있습니다")
    return '{{html clean="false"}}' + inner + "{{/html}}"
