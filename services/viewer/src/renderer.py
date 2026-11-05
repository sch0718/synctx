"""Markdown 렌더링 엔진.

convert_md_to_html.py에서 핵심 로직을 추출하여 웹 서비스용으로 재구성.
"""

import hashlib
import html as html_lib
import re
import secrets
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote

import markdown
from markdown.extensions.toc import TocExtension
from markdown.treeprocessors import Treeprocessor

from linear_inline import LinearInlineExtension


def _strip_tags(text):
    """re.sub(r"<[^>]+>", "", text)와 같은 결과를 앞으로만 훑어 만든다(닫히지 않은 "<"마다 끝까지 다시 훑지 않는다)."""
    out = []
    pos = 0
    gt = -1
    lt = text.find("<")
    while lt != -1:
        if gt < lt:
            gt = text.find(">", lt + 1)
            if gt == -1:
                break
        if gt > lt + 1:
            out.append(text[pos:lt])
            pos = gt + 1
            lt = text.find("<", pos)
        else:
            lt = text.find("<", lt + 1)
    out.append(text[pos:])
    return "".join(out)


def github_slugify(text, sep="-"):
    text = _strip_tags(text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\s\-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s]+", sep, text)
    return text


def preprocess_md(text):
    """테이블 헤더 행 앞에 빈 줄을 넣어 markdown tables 인식을 돕는다."""
    lines = text.split("\n")
    result = []
    for line in lines:
        if (
            line.lstrip().startswith("|")
            and result
            and not result[-1].lstrip().startswith("|")
            and result[-1].strip()
        ):
            result.append("")
        result.append(line)
    return "\n".join(result)


HEADING_MARK = "data-synctx-md"
_HEADING_TAGS = {f"h{n}" for n in range(1, 7)}


class _HeadingMarker(Treeprocessor):
    """Markdown이 만든 제목에 표시를 붙여 원시 HTML 제목과 구분한다.

    작성자가 같은 속성을 직접 써서 흉내 내지 못하도록 표시 값은 렌더링마다 새로 만든 값을 쓴다.
    """

    def __init__(self, md, mark):
        super().__init__(md)
        self.mark = mark

    def run(self, root):
        for el in root.iter():
            if el.tag in _HEADING_TAGS:
                el.set(HEADING_MARK, self.mark)


class _HeadingMarkExtension(markdown.Extension):
    def __init__(self, mark):
        super().__init__()
        self.mark = mark

    def extendMarkdown(self, md):
        md.treeprocessors.register(_HeadingMarker(md, self.mark), "synctx-heading-mark", 4)


def _is_marked(parsed, mark):
    return mark is not None and any(
        name == HEADING_MARK and value and value.strip("\"'") == mark for name, value, _ in parsed
    )


def fix_heading_ids(html, id_map=None, mark=None):
    """heading id를 GitHub 방식으로 맞춘다.

    Markdown이 만든 제목은 HEADING_MARK 속성값이 mark인 제목이다.
    id_map이 주어지면 Markdown이 만든 제목의 원래 id를 새 id로 기록한다.
    여는 태그마다 그 뒤 첫 같은 이름의 닫는 태그(대소문자 무관)를 짝으로 삼되, 짝이 Markdown이 만든
    제목을 넘어가면 닫히지 않은 원시 HTML 태그로 보고 건드리지 않는다(뒤 제목을 삼키지 않도록).
    닫는 태그가 없는 여는 태그마다 문서 끝까지 다시 훑지 않도록 위치를 미리 모아 앞으로만 나아간다.
    """
    used = {}

    def rewrite(tag, attrs, inner):
        plain = _strip_tags(inner).strip()
        slug = github_slugify(plain)
        if slug in used:
            used[slug] += 1
            slug = f"{slug}-{used[slug]}"
        else:
            used[slug] = 0
        # 속성값 안의 "id=" 같은 글을 속성으로 읽지 않도록 속성을 하나씩 나눠 읽는다
        parsed = _heading_attrs(attrs)
        if id_map is not None and _is_marked(parsed, mark):
            old_id = next((value for name, value, _ in parsed if name == "id" and value), None)
            if old_id:
                id_map[html_lib.unescape(old_id.strip("\"'"))] = slug
        rest = "".join(" " + raw for name, _, raw in parsed if name not in ("id", HEADING_MARK))
        return f'<{tag} id="{slug}"{rest}>{inner}</{tag}>'

    closers = {}
    for m in _HEADING_CLOSE.finditer(html):
        closers.setdefault(m.group(1).lower(), []).append(m.start())
    openers = []
    marked = []
    gt = -1
    for m in _HEADING_OPEN.finditer(html):
        # 다른 여는 태그의 속성 자리에 든 것은 Markdown 제목일 수 없으니 속성을 다시 읽지 않는다
        inside_tag = m.start() < gt
        if gt < m.end():
            gt = html.find(">", m.end())
            if gt == -1:
                break
        if not inside_tag and _is_marked(_heading_attrs(html[m.end():gt]), mark):
            marked.append(m.start())
        openers.append((m, gt))

    next_closer = dict.fromkeys(closers, 0)
    next_marked = 0
    out = []
    pos = 0
    for m, gt in openers:
        if m.start() < pos:
            continue
        tag = m.group(1).lower()
        ends = closers.get(tag, ())
        i = next_closer.get(tag, 0)
        while i < len(ends) and ends[i] <= gt:
            i += 1
        next_closer[tag] = i
        if i == len(ends):
            continue
        end = ends[i]
        while next_marked < len(marked) and marked[next_marked] <= m.start():
            next_marked += 1
        if next_marked < len(marked) and marked[next_marked] < end:
            continue
        out.append(html[pos:m.start()])
        out.append(rewrite(tag, html[m.end():gt], html[gt + 1:end]))
        pos = end + len(tag) + 3
    out.append(html[pos:])
    return "".join(out)


_HEADING_OPEN = re.compile(r"<(h[1-6])", re.IGNORECASE)
_HEADING_CLOSE = re.compile(r"</(h[1-6])>", re.IGNORECASE)


# 공백 묶음·속성·그 밖의 한 글자 중 하나를 매번 소비해 앞으로만 나아간다(같은 자리를 다시 훑지 않는다)
_HEADING_ATTR_TOKEN = re.compile(r"""(\s+)|([^\s"'>/=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+))?|(.)""", re.DOTALL)


def _heading_attrs(attrs):
    """여는 태그의 속성을 (소문자 이름, 값, 원문) 목록으로 나눈다. 이름이 아닌 글자는 이름 None으로 둔다."""
    parsed = []
    for m in _HEADING_ATTR_TOKEN.finditer(attrs):
        if m.group(1):
            continue
        if m.group(2):
            parsed.append((m.group(2).lower(), m.group(3), m.group(0)))
        else:
            parsed.append((None, None, m.group(0)))
    return parsed


def _remap_toc_links(html, toc_html, id_map):
    """본문의 [TOC] 블록 링크도 바뀐 제목 id를 가리키게 한다.

    목차 확장이 만든 목차 HTML(md.toc)이 본문에 그대로 들어가므로 그 문자열만 바꿔 끼운다.
    """
    if not toc_html or not id_map or toc_html not in html:
        return html

    def remap_href(match):
        old = html_lib.unescape(match.group(1))
        return f'href="#{html_lib.escape(id_map.get(old, old))}"'

    return html.replace(toc_html, re.sub(r'href="#([^"]*)"', remap_href, toc_html))


def _remap_toc_ids(tokens, id_map):
    for token in tokens:
        token["id"] = id_map.get(token["id"], token["id"])
        _remap_toc_ids(token.get("children", []), id_map)


def transform_mermaid_blocks(html):
    """mermaid 코드 블록을 mermaid.js가 인식하는 형태로 변환."""
    return re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        r'<pre class="mermaid">\1</pre>',
        html,
        flags=re.DOTALL,
    )


def _is_external_link(url: str) -> bool:
    lowered = (url or "").strip().lower()
    return (
        not lowered
        or lowered.startswith("#")
        or lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("//")
        or lowered.startswith("data:")
        or lowered.startswith("mailto:")
        or lowered.startswith("javascript:")
    )


def _resolve_relative_path(base_path: str, target: str) -> str:
    base_dir = str(PurePosixPath(base_path).parent) if base_path else ""
    joined = str(PurePosixPath(base_dir) / target)
    parts: list[str] = []
    for part in PurePosixPath(joined).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _rewrite_tag_attr(
    html: str,
    *,
    tag: str,
    attr: str,
    url_rewriter,
) -> str:
    tag_re = re.compile(rf"<{tag}\b[^>]*>", flags=re.IGNORECASE)
    attr_re = re.compile(rf'\b{attr}=(["\'])([^"\']+)\1', flags=re.IGNORECASE)

    def replace_tag(tag_match):
        tag_html = tag_match.group(0)

        def replace_attr(attr_match):
            quote = attr_match.group(1)
            raw = attr_match.group(2)
            rewritten = url_rewriter(raw)
            if rewritten is None:
                return attr_match.group(0)
            return f"{attr}={quote}{rewritten}{quote}"

        return attr_re.sub(replace_attr, tag_html, count=1)

    return tag_re.sub(replace_tag, html)


def _srcset_candidates(value: str) -> list[tuple[str, str]]:
    """srcset을 (주소, 설명자) 후보로 나눈다. HTML 표준처럼 주소는 공백까지라서 data URL 등 주소 안의 쉼표는 그대로 둔다."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(value)
    while i < n:
        while i < n and (value[i].isspace() or value[i] == ","):
            i += 1
        start = i
        while i < n and not value[i].isspace():
            i += 1
        url = value[start:i]
        if not url:
            break
        if url.endswith(","):
            out.append((url.rstrip(","), ""))
            continue
        start, depth = i, 0
        while i < n and not (value[i] == "," and depth == 0):
            depth += {"(": 1, ")": -1}.get(value[i], 0)
            i += 1
        out.append((url, value[start:i].strip()))
    return out


def _path_part(href: str) -> str:
    return href.split("?", 1)[0].split("#", 1)[0]


def longest_prefix(href: str, prefixes) -> str | None:
    best = None
    for prefix in prefixes:
        if href.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return best


def map_href(href: str, references: dict[str, str]) -> tuple[str, str] | None:
    """href 원문이 매핑 키로 시작하면 가장 긴 키로 (저장소 id, 경로 접두어 + 나머지)를 돌려준다."""
    key = longest_prefix(href, references)
    if key is None:
        return None
    repo, _, prefix = references[key].partition(":")
    return repo, prefix + href[len(key):]


def rewrite_links(
    html: str,
    repo_id: str,
    base_path: str,
    ref: str | None = None,
    link_map: dict[str, str] | None = None,
    keep_prefixes: tuple[str, ...] = (),
) -> str:
    """상대 이미지/링크를 repo permalink 경로로 재작성한다. ref가 있으면 이미지·문서·폴더 주소에 붙여 같은 브랜치를 유지한다.

    link_map(접두어 매핑)에 걸린 링크는 매핑한 저장소 경로로 바꾸고, keep_prefixes에 걸린 링크는 그대로 둔다.
    """

    ref_query = f"ref={quote(ref, safe='')}" if ref else ""
    link_map = link_map or {}
    prefixes = (*link_map, *keep_prefixes)

    def to_attr(prefix: str, relative: str, base: str = base_path, with_ref: bool = True) -> str:
        """경로·쿼리에 ref를 붙이고 #앵커는 그 뒤에 둔 뒤 속성값으로 이스케이프한다."""
        path_and_query, hash_sign, fragment = relative.partition("#")
        url = f"{prefix}/{_resolve_relative_path(base, path_and_query)}"
        if ref_query and with_ref:
            url += ("&" if "?" in url else "?") + ref_query
        return html_lib.escape(url + hash_sign + fragment)

    def rewrite_src(raw: str) -> str | None:
        if _is_external_link(raw):
            return None
        return to_attr(f"/raw/{repo_id}", html_lib.unescape(raw))

    def rewrite_srcset(raw: str) -> str | None:
        """`url [설명자], …` 후보마다 상대 주소만 바꾼다(<picture>의 다크 모드 그림 등). 바꿀 것이 없으면 그대로 둔다."""
        candidates = _srcset_candidates(raw)
        rewritten = [(rewrite_src(url), descriptor) for url, descriptor in candidates]
        if all(new is None for new, _ in rewritten):
            return None
        return ", ".join((new or url) + (f" {descriptor}" if descriptor else "")
                         for (url, descriptor), (new, _) in zip(candidates, rewritten))

    def view_href(
        target_repo: str, unescaped: str, base: str, with_ref: bool, any_file: bool, as_folder: bool = False
    ) -> str | None:
        target = _path_part(unescaped)
        if as_folder or target.endswith("/"):
            folder = unquote(_resolve_relative_path(base, target))
            ref_param = f"&amp;{ref_query}" if ref_query and with_ref else ""
            return f"/view/{target_repo}?path={quote(folder, safe='/')}{ref_param}"
        pure = PurePosixPath(target)
        if not any_file and pure.suffix.lower() not in {".md", ".markdown"}:
            return None
        return to_attr(f"/view/{target_repo}", unescaped, base, with_ref)

    def rewrite_href(raw: str) -> str | None:
        if _is_external_link(raw):
            return None
        # 속성값은 HTML 이스케이프된 상태(`a&amp;b/`)로 들어오므로 경로로 쓰기 전에 되돌린다
        unescaped = html_lib.unescape(raw)
        key = longest_prefix(unescaped, prefixes) if prefixes else None
        if key is None:
            return view_href(repo_id, unescaped, base_path, True, any_file=False)
        # 없는 저장소를 가리킨 매핑이 이기면 원문 그대로 둔다
        if key not in link_map:
            return None
        target_repo, mapped = map_href(unescaped, link_map)
        mapped = mapped.lstrip("/")
        # 저장소 루트로 매핑됐거나 원래 폴더를 가리킨 링크는 폴더 주소로 보낸다
        as_folder = not _path_part(mapped) or _path_part(unescaped).endswith("/")
        return view_href(target_repo, mapped, "", target_repo == repo_id, any_file=True, as_folder=as_folder)

    preserved: list[str] = []
    marker_prefix = "__MD_PREVIEW_PRESERVED_BLOCK_"
    marker_suffix = "__"
    marker_nonce = hashlib.sha1(html.encode("utf-8")).hexdigest()[:8]

    def preserve_block(match):
        preserved.append(match.group(0))
        return f"{marker_prefix}{marker_nonce}_{len(preserved) - 1}{marker_suffix}"

    masked = re.sub(r"<script\b.*?</script>", preserve_block, html, flags=re.IGNORECASE | re.DOTALL)
    masked = re.sub(r"<style\b.*?</style>", preserve_block, masked, flags=re.IGNORECASE | re.DOTALL)

    rewritten = _rewrite_tag_attr(masked, tag="img", attr="src", url_rewriter=rewrite_src)
    rewritten = _rewrite_tag_attr(rewritten, tag="img", attr="srcset", url_rewriter=rewrite_srcset)
    rewritten = _rewrite_tag_attr(rewritten, tag="source", attr="srcset", url_rewriter=rewrite_srcset)
    rewritten = _rewrite_tag_attr(rewritten, tag="a", attr="href", url_rewriter=rewrite_href)

    marker_re = re.compile(
        rf"{re.escape(marker_prefix)}{re.escape(marker_nonce)}_(\d+){re.escape(marker_suffix)}"
    )

    def restore_block(match):
        idx = int(match.group(1))
        if 0 <= idx < len(preserved):
            return preserved[idx]
        return match.group(0)

    return marker_re.sub(restore_block, rewritten)


NO_TITLE_FALLBACK = "Markdown Preview"


def extract_title(md_text):
    """마크다운 첫 번째 h1 제목을 추출한다."""
    for line in md_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return NO_TITLE_FALLBACK


def render_markdown(md_text):
    """마크다운 텍스트를 HTML로 렌더링한다.

    Returns:
        dict: {"html": str, "toc": list, "title": str}
    """
    preprocessed = preprocess_md(md_text)
    title = extract_title(md_text)
    mark = secrets.token_hex(8)

    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            TocExtension(slugify=github_slugify, toc_depth="1-4"),
            "sane_lists",
            "md_in_html",
            _HeadingMarkExtension(mark),
            LinearInlineExtension(),
        ],
        output_format="html5",
    )

    html_body = md.convert(preprocessed)
    id_map = {}
    html_body = fix_heading_ids(html_body, id_map, mark)
    html_body = _remap_toc_links(html_body, getattr(md, "toc", ""), id_map)
    html_body = transform_mermaid_blocks(html_body)

    toc_tokens = getattr(md, "toc_tokens", [])
    _remap_toc_ids(toc_tokens, id_map)

    return {
        "html": html_body,
        "toc": toc_tokens,
        "title": title,
    }


DOWNLOAD_TEMPLATE = """<!DOCTYPE html>
<html lang="ko" data-theme="{theme}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body, .markdown-body, .markdown-body * {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR",
        "Apple SD Gothic Neo", "Malgun Gothic", "맑은 고딕", "Noto Sans CJK KR",
        "Noto Sans", Helvetica, Arial, sans-serif;
    }}
  </style>
  <style>{hljs_css}</style>
  <style>{css}</style>
</head>
<body>
  <article class="markdown-body">{body}</article>
  <script>{hljs_js}</script>
  <script>hljs.highlightAll();</script>
{mermaid_block}
</body>
</html>"""


_MERMAID_BLOCK_TEMPLATE = """  <script>{mermaid_js}</script>
  <script>
    mermaid.initialize({{ startOnLoad: false, theme: '{mermaid_theme}' }});
    mermaid.run();
  </script>"""

_VENDOR_DIR = Path(__file__).parent / "static" / "vendor"
_HLJS_DIR = _VENDOR_DIR / "hljs"


def _load_vendor(path):
    return path.read_text(encoding="utf-8")


_HLJS_JS = None
_MERMAID_JS = None
_HLJS_CSS_CACHE = {}


def _escape_script_close_tags(script_text: str) -> str:
    """Inline script 본문에서 </script>를 안전하게 이스케이프한다."""
    return re.sub(r"</script", r"<\\/script", script_text, flags=re.IGNORECASE)


def _get_hljs_js():
    global _HLJS_JS
    if _HLJS_JS is None:
        _HLJS_JS = _load_vendor(_VENDOR_DIR / "highlight.min.js")
    return _HLJS_JS


def _get_mermaid_js():
    global _MERMAID_JS
    if _MERMAID_JS is None:
        _MERMAID_JS = _load_vendor(_VENDOR_DIR / "mermaid.min.js")
    return _MERMAID_JS


def _get_hljs_css(theme_name):
    if theme_name not in _HLJS_CSS_CACHE:
        _HLJS_CSS_CACHE[theme_name] = _load_vendor(
            _HLJS_DIR / f"{theme_name}.min.css"
        )
    return _HLJS_CSS_CACHE[theme_name]


GITHUB_LIGHT_CSS = """
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
  font-size: 16px; line-height: 1.5; color: #1f2328; background: #fff; margin: 0; padding: 0;
}
.markdown-body {
  max-width: 980px; margin: 0 auto; padding: 45px; box-sizing: border-box;
}
@media (max-width: 767px) { .markdown-body { padding: 15px; } }
.markdown-body h1,.markdown-body h2,.markdown-body h3,.markdown-body h4,.markdown-body h5,.markdown-body h6 {
  margin-top: 24px; margin-bottom: 16px; font-weight: 600; line-height: 1.25;
}
.markdown-body h1 { font-size: 2em; padding-bottom: .3em; border-bottom: 1px solid #d1d9e0b3; }
.markdown-body h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid #d1d9e0b3; }
.markdown-body h3 { font-size: 1.25em; } .markdown-body h4 { font-size: 1em; }
.markdown-body p { margin-top: 0; margin-bottom: 16px; }
.markdown-body a { color: #0969da; text-decoration: none; }
.markdown-body a:hover { text-decoration: underline; }
.markdown-body strong { font-weight: 600; }
.markdown-body blockquote { margin: 0 0 16px; padding: 0 1em; color: #656d76; border-left: .25em solid #d0d7de; }
.markdown-body blockquote>:first-child { margin-top: 0; }
.markdown-body blockquote>:last-child { margin-bottom: 0; }
.markdown-body ul,.markdown-body ol { margin-top: 0; margin-bottom: 16px; padding-left: 2em; }
.markdown-body ul ul,.markdown-body ul ol,.markdown-body ol ol,.markdown-body ol ul { margin-top: 0; margin-bottom: 0; }
.markdown-body li { margin-top: .25em; }
.markdown-body li > p { margin-top: 16px; }
.markdown-body li + li { margin-top: .25em; }
.markdown-body code {
  padding: .2em .4em; font-size: 85%; background: rgba(175,184,193,.2); border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
.markdown-body pre {
  margin-top: 0; margin-bottom: 16px; padding: 16px; overflow: auto;
  font-size: 85%; line-height: 1.45; background: #f6f8fa; border-radius: 6px;
}
.markdown-body pre code {
  display: inline; padding: 0; background: transparent; border: 0; font-size: 100%; white-space: pre;
}
.markdown-body table {
  border-spacing: 0; border-collapse: collapse; margin-bottom: 16px;
  display: block; width: max-content; max-width: 100%; overflow: auto;
}
.markdown-body table th { font-weight: 600; padding: 6px 13px; border: 1px solid #d0d7de; background: #f6f8fa; }
.markdown-body table td { padding: 6px 13px; border: 1px solid #d0d7de; }
.markdown-body table tr { background: #fff; border-top: 1px solid #d8dee4; }
.markdown-body table tr:nth-child(2n) { background: #f6f8fa; }
.markdown-body hr { height: .25em; margin: 24px 0; background: #d0d7de; border: 0; }
.markdown-body img { max-width: 100%; }
.markdown-body>*:first-child { margin-top: 0!important; }
.markdown-body>*:last-child { margin-bottom: 0!important; }
"""

GITHUB_DARK_CSS = """
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
  font-size: 16px; line-height: 1.5; color: #e6edf3; background: #0d1117; margin: 0; padding: 0;
}
.markdown-body {
  max-width: 980px; margin: 0 auto; padding: 45px; box-sizing: border-box;
}
@media (max-width: 767px) { .markdown-body { padding: 15px; } }
.markdown-body h1,.markdown-body h2,.markdown-body h3,.markdown-body h4,.markdown-body h5,.markdown-body h6 {
  margin-top: 24px; margin-bottom: 16px; font-weight: 600; line-height: 1.25; color: #e6edf3;
}
.markdown-body h1 { font-size: 2em; padding-bottom: .3em; border-bottom: 1px solid #21262d; }
.markdown-body h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid #21262d; }
.markdown-body h3 { font-size: 1.25em; } .markdown-body h4 { font-size: 1em; }
.markdown-body p { margin-top: 0; margin-bottom: 16px; }
.markdown-body a { color: #58a6ff; text-decoration: none; }
.markdown-body a:hover { text-decoration: underline; }
.markdown-body strong { font-weight: 600; }
.markdown-body blockquote { margin: 0 0 16px; padding: 0 1em; color: #8b949e; border-left: .25em solid #30363d; }
.markdown-body blockquote>:first-child { margin-top: 0; }
.markdown-body blockquote>:last-child { margin-bottom: 0; }
.markdown-body ul,.markdown-body ol { margin-top: 0; margin-bottom: 16px; padding-left: 2em; }
.markdown-body ul ul,.markdown-body ul ol,.markdown-body ol ol,.markdown-body ol ul { margin-top: 0; margin-bottom: 0; }
.markdown-body li { margin-top: .25em; }
.markdown-body li > p { margin-top: 16px; }
.markdown-body li + li { margin-top: .25em; }
.markdown-body code {
  padding: .2em .4em; font-size: 85%; background: rgba(110,118,129,.4); border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
.markdown-body pre {
  margin-top: 0; margin-bottom: 16px; padding: 16px; overflow: auto;
  font-size: 85%; line-height: 1.45; background: #161b22; border-radius: 6px;
}
.markdown-body pre code {
  display: inline; padding: 0; background: transparent; border: 0; font-size: 100%; white-space: pre;
}
.markdown-body table {
  border-spacing: 0; border-collapse: collapse; margin-bottom: 16px;
  display: block; width: max-content; max-width: 100%; overflow: auto;
}
.markdown-body table th { font-weight: 600; padding: 6px 13px; border: 1px solid #30363d; background: #161b22; }
.markdown-body table td { padding: 6px 13px; border: 1px solid #30363d; }
.markdown-body table tr { background: #0d1117; border-top: 1px solid #21262d; }
.markdown-body table tr:nth-child(2n) { background: #161b22; }
.markdown-body hr { height: .25em; margin: 24px 0; background: #30363d; border: 0; }
.markdown-body img { max-width: 100%; }
.markdown-body>*:first-child { margin-top: 0!important; }
.markdown-body>*:last-child { margin-bottom: 0!important; }
"""

CAYMAN_CSS = """
body {
  font-family: "Open Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 16px; line-height: 1.5; color: #606c71; background: #fff; margin: 0; padding: 0;
}
.markdown-body {
  max-width: 980px; margin: 0 auto; padding: 45px; box-sizing: border-box;
}
@media (max-width: 767px) { .markdown-body { padding: 15px; } }
.markdown-body h1,.markdown-body h2,.markdown-body h3,.markdown-body h4,.markdown-body h5,.markdown-body h6 {
  margin-top: 2rem; margin-bottom: 1rem; font-weight: normal; line-height: 1.1; color: #159957;
}
.markdown-body h1 { font-size: 2em; }
.markdown-body h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid #eff0f1; }
.markdown-body h3 { font-size: 1.25em; } .markdown-body h4 { font-size: 1.1em; }
.markdown-body p { margin-top: 0; margin-bottom: 16px; }
.markdown-body a { color: #1e6bb8; text-decoration: none; }
.markdown-body a:hover { text-decoration: underline; }
.markdown-body strong { font-weight: 700; }
.markdown-body blockquote { margin: 0 0 16px; padding: 0 1em; color: #819198; border-left: .25em solid #dce6f0; }
.markdown-body blockquote>:first-child { margin-top: 0; }
.markdown-body blockquote>:last-child { margin-bottom: 0; }
.markdown-body ul,.markdown-body ol { margin-top: 0; margin-bottom: 16px; padding-left: 2em; }
.markdown-body ul ul,.markdown-body ul ol,.markdown-body ol ol,.markdown-body ol ul { margin-top: 0; margin-bottom: 0; }
.markdown-body li { margin-top: .25em; }
.markdown-body li > p { margin-top: 16px; }
.markdown-body li + li { margin-top: .25em; }
.markdown-body code {
  padding: .2em .4em; font-size: 85%; background: rgba(21,89,153,.06); border-radius: 3px;
  font-family: Consolas, "Liberation Mono", Menlo, Courier, monospace; color: #567482;
}
.markdown-body pre {
  margin-top: 0; margin-bottom: 16px; padding: 16px; overflow: auto;
  font-size: 85%; line-height: 1.45; background: #f3f6fa; border-radius: 6px; border: 1px solid #dce6f0;
}
.markdown-body pre code {
  display: inline; padding: 0; background: transparent; border: 0; font-size: 100%; white-space: pre; color: inherit;
}
.markdown-body table {
  border-spacing: 0; border-collapse: collapse; margin-bottom: 16px;
  display: block; width: max-content; max-width: 100%; overflow: auto;
}
.markdown-body table th { font-weight: 600; padding: 6px 13px; border: 1px solid #e9ebec; background: #f3f6fa; }
.markdown-body table td { padding: 6px 13px; border: 1px solid #e9ebec; }
.markdown-body table tr { background: #fff; border-top: 1px solid #dce6f0; }
.markdown-body table tr:nth-child(2n) { background: #f3f6fa; }
.markdown-body hr { height: 2px; margin: 24px 0; background: linear-gradient(to right, #155799, #159957); border: 0; }
.markdown-body img { max-width: 100%; }
.markdown-body>*:first-child { margin-top: 0!important; }
.markdown-body>*:last-child { margin-bottom: 0!important; }
"""

MINIMAL_CSS = """
body {
  font-family: "Noto Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 14px; line-height: 1.6; color: #333333; background: #fff; margin: 0; padding: 0;
}
.markdown-body {
  max-width: 980px; margin: 0 auto; padding: 45px; box-sizing: border-box;
}
@media (max-width: 767px) { .markdown-body { padding: 15px; } }
.markdown-body h1,.markdown-body h2,.markdown-body h3,.markdown-body h4,.markdown-body h5,.markdown-body h6 {
  margin-top: 24px; margin-bottom: 16px; font-weight: 700; line-height: 1.3;
}
.markdown-body h1 { font-size: 28px; padding-bottom: .3em; border-bottom: 1px solid #ccc; color: #222; }
.markdown-body h2 { font-size: 24px; padding-bottom: .3em; border-bottom: 1px solid #ccc; color: #393939; }
.markdown-body h3 { font-size: 18px; color: #494949; } .markdown-body h4 { font-size: 16px; color: #595959; }
.markdown-body p { margin-top: 0; margin-bottom: 16px; }
.markdown-body a { color: #4183c4; text-decoration: none; }
.markdown-body a:hover { text-decoration: underline; }
.markdown-body strong { font-weight: 700; }
.markdown-body blockquote { margin: 0 0 16px; padding: 0 1em; color: #777; border-left: .25em solid #e0e0e0; }
.markdown-body blockquote>:first-child { margin-top: 0; }
.markdown-body blockquote>:last-child { margin-bottom: 0; }
.markdown-body ul,.markdown-body ol { margin-top: 0; margin-bottom: 16px; padding-left: 2em; }
.markdown-body ul ul,.markdown-body ul ol,.markdown-body ol ol,.markdown-body ol ul { margin-top: 0; margin-bottom: 0; }
.markdown-body li { margin-top: .25em; }
.markdown-body li > p { margin-top: 16px; }
.markdown-body li + li { margin-top: .25em; }
.markdown-body code {
  padding: .2em .4em; font-size: 85%; background: #f8f8f8; border: 1px solid #dedede; border-radius: 3px;
  font-family: Monaco, "Bitstream Vera Sans Mono", "Lucida Console", Terminal, monospace; color: #333;
}
.markdown-body pre {
  margin-top: 0; margin-bottom: 16px; padding: 8px 15px; overflow: auto;
  font-size: 85%; line-height: 1.45; background: #f8f8f8; border: 1px solid #dedede; border-radius: 3px;
}
.markdown-body pre code {
  display: inline; padding: 0; background: transparent; border: 0; font-size: 100%; white-space: pre;
}
.markdown-body table {
  border-spacing: 0; border-collapse: collapse; margin-bottom: 16px;
  display: block; width: max-content; max-width: 100%; overflow: auto;
}
.markdown-body table th { font-weight: 700; padding: 6px 13px; border: 1px solid #dedede; background: #f8f8f8; }
.markdown-body table td { padding: 6px 13px; border: 1px solid #dedede; }
.markdown-body table tr { background: #fff; border-top: 1px solid #dedede; }
.markdown-body table tr:nth-child(2n) { background: #f8f8f8; }
.markdown-body hr { height: 1px; margin: 24px 0; background: #e0e0e0; border: 0; }
.markdown-body img { max-width: 100%; }
.markdown-body>*:first-child { margin-top: 0!important; }
.markdown-body>*:last-child { margin-bottom: 0!important; }
"""

HACKER_CSS = """
body {
  font-family: Monaco, "Bitstream Vera Sans Mono", "Lucida Console", Terminal, monospace;
  font-size: 14px; line-height: 1.5; color: #b0b0b0; background: #151515; margin: 0; padding: 0;
}
.markdown-body {
  max-width: 980px; margin: 0 auto; padding: 45px; box-sizing: border-box;
}
@media (max-width: 767px) { .markdown-body { padding: 15px; } }
.markdown-body h1,.markdown-body h2,.markdown-body h3,.markdown-body h4,.markdown-body h5,.markdown-body h6 {
  margin-top: 24px; margin-bottom: 16px; font-weight: normal; line-height: 1.3; color: #b5e853;
}
.markdown-body h1 { font-size: 1.8em; padding-bottom: .3em; border-bottom: 1px solid #333; }
.markdown-body h2 { font-size: 1.4em; padding-bottom: .3em; border-bottom: 1px solid #333; }
.markdown-body h3 { font-size: 1.2em; } .markdown-body h4 { font-size: 1em; }
.markdown-body p { margin-top: 0; margin-bottom: 16px; }
.markdown-body a { color: #63c0f5; text-decoration: none; }
.markdown-body a:hover { color: #b5e853; text-decoration: underline; }
.markdown-body strong { font-weight: 700; color: #e0e0e0; }
.markdown-body blockquote { margin: 0 0 16px; padding: 0 1em; color: #777; border-left: .25em solid #444; }
.markdown-body blockquote>:first-child { margin-top: 0; }
.markdown-body blockquote>:last-child { margin-bottom: 0; }
.markdown-body ul,.markdown-body ol { margin-top: 0; margin-bottom: 16px; padding-left: 2em; }
.markdown-body ul ul,.markdown-body ul ol,.markdown-body ol ol,.markdown-body ol ul { margin-top: 0; margin-bottom: 0; }
.markdown-body li { margin-top: .25em; }
.markdown-body li > p { margin-top: 16px; }
.markdown-body li + li { margin-top: .25em; }
.markdown-body code {
  padding: .2em .4em; font-size: 100%; background: rgba(0,0,0,.6); border: 1px solid #333; border-radius: 2px;
  font-family: Monaco, "Bitstream Vera Sans Mono", "Lucida Console", Terminal, monospace; color: #b5e853;
}
.markdown-body pre {
  margin-top: 0; margin-bottom: 16px; padding: 16px; overflow: auto;
  font-size: 85%; line-height: 1.45; background: rgba(0,0,0,.9); border: 1px solid #333; border-radius: 2px;
}
.markdown-body pre code {
  display: inline; padding: 0; background: transparent; border: 0; font-size: 100%; white-space: pre; color: #b0b0b0;
}
.markdown-body table {
  border-spacing: 0; border-collapse: collapse; margin-bottom: 16px;
  display: block; width: max-content; max-width: 100%; overflow: auto;
}
.markdown-body table th { font-weight: 700; padding: 6px 13px; border: 1px solid #333; background: #1a1a1a; color: #b5e853; }
.markdown-body table td { padding: 6px 13px; border: 1px solid #333; }
.markdown-body table tr { background: #151515; border-top: 1px solid #333; }
.markdown-body table tr:nth-child(2n) { background: #1a1a1a; }
.markdown-body hr { height: 1px; margin: 24px 0; background: #333; border: 0; }
.markdown-body img { max-width: 100%; }
.markdown-body>*:first-child { margin-top: 0!important; }
.markdown-body>*:last-child { margin-bottom: 0!important; }
"""

MODERNIST_CSS = """
body {
  font-family: "Lato", "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 15px; font-weight: 300; line-height: 1.6; color: #474747; background: #f1f1f1; margin: 0; padding: 0;
}
.markdown-body {
  max-width: 980px; margin: 0 auto; padding: 45px; box-sizing: border-box; background: #fff;
}
@media (max-width: 767px) { .markdown-body { padding: 15px; } }
.markdown-body h1,.markdown-body h2,.markdown-body h3,.markdown-body h4,.markdown-body h5,.markdown-body h6 {
  margin-top: 24px; margin-bottom: 16px; font-weight: 700; line-height: 1.3; color: #474747;
}
.markdown-body h1 { font-size: 2em; padding-bottom: .3em; border-bottom: 3px solid #cdddeb; }
.markdown-body h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 2px solid #cdddeb; }
.markdown-body h3 { font-size: 1.25em; } .markdown-body h4 { font-size: 1.1em; }
.markdown-body p { margin-top: 0; margin-bottom: 16px; }
.markdown-body a { color: #4276b6; text-decoration: none; }
.markdown-body a:hover { text-decoration: underline; }
.markdown-body strong { font-weight: 700; }
.markdown-body blockquote { margin: 0 0 16px; padding: 0 1em; color: #7a8a96; border-left: .25em solid #cdddeb; }
.markdown-body blockquote>:first-child { margin-top: 0; }
.markdown-body blockquote>:last-child { margin-bottom: 0; }
.markdown-body ul,.markdown-body ol { margin-top: 0; margin-bottom: 16px; padding-left: 2em; }
.markdown-body ul ul,.markdown-body ul ol,.markdown-body ol ol,.markdown-body ol ul { margin-top: 0; margin-bottom: 0; }
.markdown-body li { margin-top: .25em; }
.markdown-body li > p { margin-top: 16px; }
.markdown-body li + li { margin-top: .25em; }
.markdown-body code {
  padding: .2em .4em; font-size: 85%; background: #e6ecf2; border: 1px solid #c5d1db; border-radius: 3px;
  font-family: Monaco, "Bitstream Vera Sans Mono", Consolas, monospace; color: #3a3c42;
}
.markdown-body pre {
  margin-top: 0; margin-bottom: 16px; padding: 16px; overflow: auto;
  font-size: 85%; line-height: 1.45; background: #3a3c42; border-radius: 6px; color: #d0d0d0;
}
.markdown-body pre code {
  display: inline; padding: 0; background: transparent; border: 0; font-size: 100%; white-space: pre; color: #d0d0d0;
}
.markdown-body table {
  border-spacing: 0; border-collapse: collapse; margin-bottom: 16px;
  display: block; width: max-content; max-width: 100%; overflow: auto;
}
.markdown-body table th { font-weight: 700; padding: 6px 13px; border: 1px solid #c5d1db; background: #e8f0fa; }
.markdown-body table td { padding: 6px 13px; border: 1px solid #c5d1db; }
.markdown-body table tr { background: #fff; border-top: 1px solid #c5d1db; }
.markdown-body table tr:nth-child(2n) { background: #f3f7fb; }
.markdown-body hr { height: 2px; margin: 24px 0; background: #cdddeb; border: 0; }
.markdown-body img { max-width: 100%; }
.markdown-body>*:first-child { margin-top: 0!important; }
.markdown-body>*:last-child { margin-bottom: 0!important; }
"""

CORPORATE_CSS = """
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", Helvetica, Arial, sans-serif;
  font-size: 14px; line-height: 1.57; color: #1A1A1A; background: #fff; margin: 0; padding: 0;
}
body::before { content: ""; display: block; height: 6px; background: #EF3B24; }
.markdown-body {
  max-width: 980px; margin: 0 auto; padding: 45px; box-sizing: border-box;
}
@media (max-width: 767px) { .markdown-body { padding: 15px; } }
.markdown-body h1,.markdown-body h2,.markdown-body h3,.markdown-body h4,.markdown-body h5,.markdown-body h6 {
  margin-top: 40px; margin-bottom: 8px; font-weight: 800; line-height: 1.25; color: #1A1A1A;
}
.markdown-body h1 { font-size: 2em; padding-bottom: .3em; border-bottom: 1px solid #E5E5E5; }
.markdown-body h1::before { content: ""; display: block; width: 46px; height: 4px; margin-bottom: 14px; background: #EF3B24; }
.markdown-body h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid #E5E5E5; }
.markdown-body h3 { font-size: 1.25em; font-weight: 700; }
.markdown-body h4 { font-size: 1.1em; font-weight: 700; }
.markdown-body h5 { font-size: 1em; font-weight: 700; }
.markdown-body h6 { font-size: 0.95em; font-weight: 600; }
.markdown-body p { margin-top: 0; margin-bottom: 1.25rem; }
.markdown-body a { color: #B02A14; text-decoration: none; }
.markdown-body a:hover { text-decoration: underline; text-decoration-color: #EF3B24; }
.markdown-body strong { font-weight: 700; }
.markdown-body blockquote {
  margin: 0 0 16px; padding: 6px 16px; color: #6D6E71;
  border-left: none; background: #F7F7F7; border-radius: 0 8px 8px 0; position: relative;
}
.markdown-body blockquote::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0;
  width: 4px; background: #EF3B24;
}
.markdown-body blockquote>:first-child { margin-top: 0; }
.markdown-body blockquote>:last-child { margin-bottom: 0; }
.markdown-body ul,.markdown-body ol { margin-top: 0; margin-bottom: 16px; padding-left: 16px; line-height: 2; }
.markdown-body ul ul,.markdown-body ul ol,.markdown-body ol ol,.markdown-body ol ul { margin-top: 0; margin-bottom: 0; }
.markdown-body li { margin-top: .25em; }
.markdown-body li > p { margin-top: 16px; }
.markdown-body li + li { margin-top: .25em; }
.markdown-body code {
  padding: 2px 4px; font-size: 85%; background: #F7F7F7; border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; color: #B02A14;
}
.markdown-body pre {
  margin-top: 0; margin-bottom: 16px; padding: 24px; overflow: auto;
  font-size: 14px; line-height: 1.57; background: #F7F7F7;
  border: 1px solid #E5E5E5; border-radius: 8px;
}
.markdown-body pre code {
  display: inline; padding: 0; background: transparent; border: 0; font-size: 100%; white-space: pre; color: #1A1A1A;
}
.markdown-body table {
  border-spacing: 0; border-collapse: collapse; margin-bottom: 16px;
  display: block; width: max-content; max-width: 100%; overflow: auto; font-size: 14px;
}
.markdown-body table th {
  font-weight: 600; padding: 8px; border: 1px solid #E5E5E5; background: #F7F7F7; color: #6D6E71;
}
.markdown-body table td { padding: 8px; border: 1px solid #E5E5E5; }
.markdown-body table tr { background: #fff; }
.markdown-body table tr:nth-child(odd) { background: #F7F7F7; }
.markdown-body hr { height: 1px; margin: 24px 0; background: #E5E5E5; border: 0; }
.markdown-body img { max-width: 100%; height: auto; border-radius: 16px; margin-bottom: 1.25rem; }
.markdown-body>*:first-child { margin-top: 0!important; }
.markdown-body>*:last-child { margin-bottom: 0!important; }
"""

THEME_VARS_CSS = """
[data-theme="github-light"] {
    --primary: #0969da; --primary-light: #4493f8; --primary-lighter: #ddf4ff; --primary-dark: #0550ae;
    --bg-default: #ffffff; --bg-neutral: #f6f8fa; --bg-card: #ffffff;
    --text-primary: #1f2328; --text-secondary: #57606a; --text-disabled: #8b949e;
    --on-primary: #ffffff;
    --border-color: rgba(208,215,222,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="github-dark"] {
    --primary: #58a6ff; --primary-light: #79c0ff; --primary-lighter: #0d1117; --primary-dark: #388bfd;
    --bg-default: #0d1117; --bg-neutral: #161b22; --bg-card: #161b22;
    --text-primary: #e6edf3; --text-secondary: #8b949e; --text-disabled: #484f58;
    --on-primary: #0d1117;
    --border-color: rgba(48,54,61,0.6);
    --shadow-card: 0 0 2px 0 rgba(0,0,0,0.4), 0 12px 24px -4px rgba(0,0,0,0.3);
    --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.3);
}
[data-theme="cayman"] {
    --primary: #128049; --primary-light: #1e9b52; --primary-lighter: #e6f9ef; --primary-dark: #0e7a42;
    --bg-default: #ffffff; --bg-neutral: #f3f6fa; --bg-card: #ffffff;
    --text-primary: #606c71; --text-secondary: #627178; --text-disabled: #9aa6ac;
    --on-primary: #ffffff;
    --border-color: rgba(220,230,240,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="minimal"] {
    --primary: #3774b0; --primary-light: #6ba4d9; --primary-lighter: #e8f0fa; --primary-dark: #2e6da4;
    --bg-default: #ffffff; --bg-neutral: #f8f8f8; --bg-card: #ffffff;
    --text-primary: #333333; --text-secondary: #6f6f6f; --text-disabled: #999999;
    --on-primary: #ffffff;
    --border-color: rgba(222,222,222,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="hacker"] {
    --primary: #b5e853; --primary-light: #c4ee7e; --primary-lighter: #1a2a1a; --primary-dark: #8fc43e;
    --bg-default: #151515; --bg-neutral: #1a1a1a; --bg-card: #1a1a1a;
    --text-primary: #b0b0b0; --text-secondary: #888888; --text-disabled: #666666;
    --on-primary: #151515;
    --border-color: rgba(51,51,51,0.6);
    --shadow-card: 0 0 2px 0 rgba(0,0,0,0.4), 0 12px 24px -4px rgba(0,0,0,0.3);
    --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.3);
}
[data-theme="modernist"] {
    --primary: #3d6ca7; --primary-light: #6694c8; --primary-lighter: #e8f0fa; --primary-dark: #325fa0;
    --bg-default: #f1f1f1; --bg-neutral: #e8f0fa; --bg-card: #ffffff;
    --text-primary: #474747; --text-secondary: #5e6c77; --text-disabled: #9aa6ac;
    --on-primary: #ffffff;
    --border-color: rgba(197,209,219,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="corporate"] {
    --primary: #B02A14; --primary-light: #EF3B24; --primary-lighter: #FDECEA; --primary-dark: #8F2210;
    --bg-default: #ffffff; --bg-neutral: #F7F7F7; --bg-card: #ffffff;
    --text-primary: #1A1A1A; --text-secondary: #6D6E71; --text-disabled: #919EAB;
    --on-primary: #ffffff;
    --border-color: rgba(145,158,171,0.2);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
"""

THEME_CSS = {
    "github-light": GITHUB_LIGHT_CSS,
    "github-dark": GITHUB_DARK_CSS,
    "cayman": CAYMAN_CSS,
    "minimal": MINIMAL_CSS,
    "hacker": HACKER_CSS,
    "modernist": MODERNIST_CSS,
    "corporate": CORPORATE_CSS,
}

THEME_HLJS = {
    "github-light": "github",
    "github-dark": "github-dark",
    "cayman": "github",
    "minimal": "github",
    "hacker": "monokai",
    "modernist": "atom-one-light",
    "corporate": "github",
}

THEME_MERMAID = {
    "github-light": "default",
    "github-dark": "dark",
    "cayman": "default",
    "minimal": "default",
    "hacker": "dark",
    "modernist": "default",
    "corporate": "default",
}


def render_download_html(md_text, theme="github-light", html_filter=None):
    """다운로드용 자체 완결형 HTML을 생성한다. html_filter는 렌더링한 본문 HTML에만 적용한다."""
    result = render_markdown(md_text)
    body_html = html_filter(result["html"]) if html_filter else result["html"]
    if theme not in THEME_CSS:
        theme = "github-light"
    css = THEME_CSS[theme]
    hljs_theme = THEME_HLJS.get(theme, "github")
    mermaid_theme = THEME_MERMAID.get(theme, "default")

    # mermaid 블록이 없으면 대용량 mermaid.min.js 임베드를 생략해
    # 다운로드 HTML 크기와 Chromium 스크립트 평가 시간을 줄인다.
    has_mermaid = 'class="mermaid"' in body_html
    if has_mermaid:
        mermaid_block = _MERMAID_BLOCK_TEMPLATE.format(
            mermaid_js=_escape_script_close_tags(_get_mermaid_js()),
            mermaid_theme=mermaid_theme,
        )
    else:
        mermaid_block = ""

    return DOWNLOAD_TEMPLATE.format(
        theme=theme,
        title=html_lib.escape(result["title"]),
        css=css,
        hljs_css=_get_hljs_css(hljs_theme),
        hljs_js=_escape_script_close_tags(_get_hljs_js()),
        mermaid_block=mermaid_block,
        body=body_html,
    )
