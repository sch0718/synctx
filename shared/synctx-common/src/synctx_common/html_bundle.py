"""synctx-html 블록: 저장소의 HTML 묶음을 한 파일로 합치고 sandbox iframe으로 감싼다."""

from __future__ import annotations

import base64
import hashlib
import posixpath
import re
import subprocess
from dataclasses import dataclass
from html import escape as _html_escape
from html import unescape as _html_unescape
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

import yaml

from synctx_common.config import HtmlEmbedConfig

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


Reader = Callable[[str], bytes]

_CSS_URL_RE = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s]*))\s*\)""", re.IGNORECASE)
_CSS_IMPORT_RE = re.compile(r"@import\b", re.IGNORECASE)
_JS_TYPES = {"", "text/javascript", "application/javascript", "module"}
_DROPPED_SCRIPT_ATTRS = {"src", "defer", "async", "integrity", "crossorigin", "charset", "referrerpolicy"}
_JS_GAP = r"(?:\s|/\*.*?\*/|//[^\n]*(?:\n|$))*"
_JS_STRING = r"""(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`)"""
_STATIC_IMPORT_RE = re.compile(rf"(?<![\w$.])(?:from|import){_JS_GAP}{_JS_STRING}", re.S)
_DYNAMIC_IMPORT_RE = re.compile(rf"(?<![\w$.])import{_JS_GAP}\({_JS_GAP}(?:{_JS_STRING}{_JS_GAP}(?=[),]))?", re.S)


def _literal(match) -> Optional[str]:
    return next((group for group in match.groups() if group is not None), None)
_MEDIA_TAGS = {"video", "audio", "source", "track", "iframe", "frame", "embed", "object"}
_SVG_REF_TAGS = {"image", "use", "feimage"}


def csp_meta(origins) -> str:
    hosts = "".join(f" {origin}" for origin in origins)
    policy = (
        f"default-src 'none'; script-src 'unsafe-inline'{hosts}; style-src 'unsafe-inline'{hosts}; "
        f"img-src data:{hosts}; font-src data:{hosts}; connect-src {hosts.strip() or chr(39) + 'none' + chr(39)}; "
        "frame-src 'none'; worker-src 'none'; form-action 'none'; base-uri 'none'"
    )
    return f'<meta http-equiv="Content-Security-Policy" content="{policy}">'
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}


def url_origin(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = -1
    if port is None or port == {"https": 443, "http": 80}.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _escape_raw(text: str, tag: str) -> str:
    """인라인으로 넣은 코드가 요소를 일찍 닫거나 double-escaped 상태로 들어가지 않게 한다."""
    text = re.sub(rf"</({tag})", r"<\\/\1", text, flags=re.IGNORECASE)
    return text.replace("<!--", "<\\!--") if tag == "script" else text


def _render_tag(tag: str, attrs, self_closing: bool = False) -> str:
    parts = [f"<{tag}"]
    for name, value in attrs:
        parts.append(f" {name}" if value is None else f' {name}="{_html_escape(value, quote=True)}"')
    parts.append(" />" if self_closing else ">")
    return "".join(parts)


class _Bundler(HTMLParser):
    def __init__(self, read: Reader, entry: str, policy: HtmlEmbedConfig):
        super().__init__(convert_charrefs=False)
        self.read = read
        self.entry = entry
        self.policy = policy
        self.allowed = set(policy.allowed_origins)
        self.files: set = set()
        self.missing: set = set()
        self.out: list = []
        self.meta_at = 0
        self.title_parts: list = []
        self.in_title = False
        self.raw_tag: Optional[str] = None
        self.skip_raw = False
        self.script_text: Optional[list] = None
        self.script_is_module = False
        self.deferred: list = []
        self.deferred_flushed = False
        self.has_module = False
        self.has_external_defer = False

    def fail(self, reason: str, message: str):
        raise BundleError(reason, message, self.files, self.missing)

    def load(self, path: str) -> Optional[bytes]:
        try:
            data = self.read(path)
        except FileNotFoundError:
            self.missing.add(path)
            return None
        self.files.add(path)
        return data

    def load_text(self, path: str) -> Optional[str]:
        data = self.load(path)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            self.fail("unsupported", f"UTF-8로 읽을 수 없는 파일입니다: {path}")

    def path_of(self, url: str, base_file: str) -> str:
        try:
            return resolve_path(base_file, url)
        except BundleError as exc:
            self.fail(exc.reason, str(exc))

    def check_external(self, url: str, kind: str) -> None:
        if kind == "protocol_relative":
            self.fail("disallowed_host", f"스킴이 없는 외부 주소는 쓸 수 없습니다: {url}")
        if kind == "other_scheme":
            self.fail("unsupported", f"지원하지 않는 주소입니다: {url}")
        if kind == "absolute":
            try:
                origin = url_origin(url)
            except ValueError:
                self.fail("disallowed_host", f"해석할 수 없는 외부 주소입니다: {url}")
            if origin not in self.allowed:
                self.fail("disallowed_host", f"허용되지 않은 외부 주소입니다: {url}")

    def resource_url(self, url: str, base_file: str) -> str:
        kind = classify_url(url)
        if kind == "keep":
            return url
        if kind != "relative":
            self.check_external(url, kind)
            return url
        path = self.path_of(url, base_file)
        mime = _MIME_TYPES.get(posixpath.splitext(path)[1].lower())
        if mime is None:
            self.fail("unsupported", f"data URI로 바꿀 수 없는 파일 형식입니다: {path}")
        data = self.load(path)
        if data is None:
            return url
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def rewrite_css(self, css: str, base_file: str) -> str:
        if _CSS_IMPORT_RE.search(css):
            self.fail("unsupported", f"CSS @import는 지원하지 않습니다: {base_file}")

        def replace(match):
            url = next((group for group in match.groups() if group is not None), "")
            return 'url("' + self.resource_url(url, base_file).replace('"', "%22") + '")'

        return _CSS_URL_RE.sub(replace, css)

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")
        before = self.out[:-1]
        if decl.lower().startswith("doctype") and all(not part.strip() or part.startswith("<!--") for part in before):
            # 앞에 공백·주석만 있는 doctype일 때만 그 뒤로 옮긴다. 스크립트 뒤에 온 doctype을 따라가면 CSP보다 코드가 먼저 실행된다.
            self.meta_at = len(self.out)

    def handle_starttag(self, tag, attrs):
        self.start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self.start(tag, attrs, self_closing=True)

    def start(self, tag, attrs, self_closing):
        if tag == "title":
            self.in_title = True
        if tag == "script":
            self.start_script(attrs)
            return
        if tag == "link":
            self.start_link(attrs, self_closing)
            return
        if tag == "style":
            self.raw_tag = "style"
            self.skip_raw = False
        self.check_tag(tag, attrs)
        self.out.append(_render_tag(tag, self.rewrite_attrs(tag, attrs), self_closing))

    def check_tag(self, tag, attrs):
        if tag == "base":
            self.fail("unsupported", "<base> 요소는 지원하지 않습니다")
        for name, value in attrs:
            if not value:
                continue
            if tag in _MEDIA_TAGS and name in ("src", "srcset", "srcdoc", "data", "poster"):
                self.fail("unsupported", f"<{tag}> 요소의 외부 리소스는 지원하지 않습니다: {value[:80]}")
            if tag in _SVG_REF_TAGS and name in ("href", "xlink:href") and not value.strip().startswith(("#", "data:")):
                self.fail("unsupported", f"SVG <{tag}>의 외부 참조는 지원하지 않습니다: {value}")
    def rewrite_attrs(self, tag, attrs):
        rewritten = []
        for name, value in attrs:
            if value is not None and name == "style":
                value = self.rewrite_css(value, self.entry)
            elif value is not None and tag == "img" and name == "src":
                value = self.resource_url(value, self.entry)
            elif value and tag == "img" and name == "srcset":
                self.fail("unsupported", "img srcset은 지원하지 않습니다")
            rewritten.append((name, value))
        return rewritten

    def start_link(self, attrs, self_closing):
        values = dict(attrs)
        href = values.get("href")
        if href is None:
            self.out.append(_render_tag("link", attrs, self_closing))
            return
        rel = {token.lower() for token in (values.get("rel") or "").split()}
        kind = classify_url(href)
        if "stylesheet" in rel and kind == "relative":
            path = self.path_of(href, self.entry)
            css = self.load_text(path)
            media = values.get("media")
            self.out.append(_render_tag("style", [("media", media)] if media else []))
            self.out.append(_escape_raw(self.rewrite_css(css, path), "style") if css is not None else "")
            self.out.append("</style>")
            return
        if "icon" in rel:
            attrs = [(name, self.resource_url(value, self.entry) if name == "href" else value) for name, value in attrs]
        elif kind == "relative":
            self.fail("unsupported", f"지원하지 않는 link 요소입니다: rel={values.get('rel')} href={href}")
        elif kind != "keep":
            self.check_external(href, kind)
        self.out.append(_render_tag("link", attrs, self_closing))

    def start_script(self, attrs):
        values = {name.lower(): value for name, value in attrs}
        src = values.get("src")
        script_type = (values.get("type") or "").strip().lower()
        is_module = script_type == "module"
        self.raw_tag = "script"
        self.skip_raw = False
        self.script_text = None
        self.script_is_module = is_module
        if is_module:
            self.has_module = True
        if src is None:
            if script_type in _JS_TYPES:
                self.script_text = []
            self.out.append(_render_tag("script", attrs))
            return
        if "async" in values:
            self.fail("unsupported", f"async 스크립트는 지원하지 않습니다: {src}")
        kind = classify_url(src)
        if kind != "relative":
            if kind != "keep":
                self.check_external(src, kind)
            if "defer" in values and not is_module:
                self.has_external_defer = True
            self.out.append(_render_tag("script", attrs))
            return
        if script_type not in _JS_TYPES:
            self.fail("unsupported", f"JavaScript가 아닌 외부 스크립트는 지원하지 않습니다: {src}")
        path = self.path_of(src, self.entry)
        code = self.load_text(path)
        if code is not None:
            self.check_imports(code, is_module, path)
        kept = [(name, value) for name, value in attrs if name.lower() not in _DROPPED_SCRIPT_ATTRS]
        element = _render_tag("script", kept) + (_escape_raw(code, "script") if code is not None else "") + "</script>"
        self.skip_raw = True
        if "defer" in values and not is_module:
            self.deferred.append(element)
        else:
            self.out.append(element)

    def check_imports(self, code: str, is_module: bool, where: str) -> None:
        """합본 안에서 실제로 불러올 수 있는 import는 허용 출처의 https 주소뿐이다. 판단할 수 없으면 거부한다."""
        specifiers = [_literal(match) for match in _STATIC_IMPORT_RE.finditer(code)] if is_module else []
        for match in _DYNAMIC_IMPORT_RE.finditer(code):
            specifier = _literal(match)
            if specifier is None:
                # 첫 인자 전체가 문자열 리터럴 하나일 때만(바로 뒤가 `)`나 `,`) 선택 그룹이 맞는다.
                self.fail("unsupported", f"첫 인자가 문자열 리터럴 하나가 아닌 동적 import는 지원하지 않습니다: {where}")
            specifiers.append(specifier)
        for specifier in specifiers:
            allowed = False
            if "${" not in specifier and classify_url(specifier) == "absolute":
                try:
                    allowed = url_origin(specifier) in self.allowed
                except ValueError:
                    allowed = False
            if not allowed:
                self.fail("unsupported", f"합본 안에서 불러올 수 없는 import입니다: {specifier} ({where})")
    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.raw_tag and self.skip_raw:
            return
        if self.raw_tag == "style":
            data = self.rewrite_css(data, self.entry)
        if self.script_text is not None:
            self.script_text.append(data)
        self.out.append(data)
    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in ("script", "style") and self.raw_tag == tag:
            if self.script_text is not None:
                self.check_imports("".join(self.script_text), self.script_is_module, f"{self.entry}의 인라인 스크립트")
            self.raw_tag = None
            self.script_text = None
            if self.skip_raw:
                self.skip_raw = False
                return
        if tag in ("body", "html") and not self.deferred_flushed:
            self.flush_deferred()
        self.out.append(f"</{tag}>")

    def flush_deferred(self):
        self.out.extend(self.deferred)
        self.deferred_flushed = True
    def handle_entityref(self, name):
        text = f"&{name};"
        if self.in_title:
            self.title_parts.append(text)
        self.out.append(text)

    def handle_charref(self, name):
        text = f"&#{name};"
        if self.in_title:
            self.title_parts.append(text)
        self.out.append(text)

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")

    def handle_pi(self, data):
        self.out.append(f"<?{data}>")

    def unknown_decl(self, data):
        self.out.append(f"<![{data}]>")

    def title(self) -> Optional[str]:
        text = _html_unescape("".join(self.title_parts)).strip()
        return text or None

    def finish(self) -> str:
        self.close()
        if not self.deferred_flushed:
            self.flush_deferred()
        if self.deferred and (self.has_module or self.has_external_defer):
            self.fail("unsupported", "defer 스크립트는 module이나 외부 defer 스크립트와 함께 쓸 수 없습니다")
        if self.missing:
            self.fail("missing_file", "찾을 수 없는 파일: " + ", ".join(sorted(self.missing)))
        self.out.insert(self.meta_at, csp_meta(self.policy.allowed_origins))
        return "".join(self.out)


def bundle(read: Reader, entry: str, policy: HtmlEmbedConfig) -> BundleResult:
    parser = _Bundler(read, entry, policy)
    source = parser.load_text(entry)
    if source is None:
        parser.fail("missing_file", f"찾을 수 없는 파일: {entry}")
    parser.feed(source)
    html_text = parser.finish()
    encoded = html_text.encode("utf-8")
    if len(encoded) > policy.max_bundle_bytes:
        parser.fail("too_large", f"합본 크기 {len(encoded)}바이트가 상한 {policy.max_bundle_bytes}바이트를 넘습니다")
    return BundleResult(
        html=html_text,
        sha256=hashlib.sha256(encoded).hexdigest(),
        size=len(encoded),
        files=tuple(sorted(parser.files)),
        title=parser.title(),
    )


def repo_reader(manager, ref: Optional[str] = None) -> Reader:
    def read(path: str) -> bytes:
        try:
            return manager.read_file(path, ref=ref)
        except subprocess.CalledProcessError as exc:
            raise FileNotFoundError(path) from exc

    return read
