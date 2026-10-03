"""Markdown 렌더링 엔진.

convert_md_to_html.py에서 핵심 로직을 추출하여 웹 서비스용으로 재구성.
"""

import hashlib
import html as html_lib
import re
from pathlib import Path, PurePosixPath

import markdown
from markdown.extensions.toc import TocExtension


def github_slugify(text, sep="-"):
    text = re.sub(r"<[^>]+>", "", text)
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


def fix_heading_ids(html):
    """heading id를 GitHub 방식으로 맞춘다."""
    used = {}

    def replacer(match):
        tag = match.group(1)
        inner = match.group(2)
        plain = re.sub(r"<[^>]+>", "", inner).strip()
        slug = github_slugify(plain)
        if slug in used:
            used[slug] += 1
            slug = f"{slug}-{used[slug]}"
        else:
            used[slug] = 0
        return f'<{tag} id="{slug}">{inner}</{tag}>'

    return re.sub(r"<(h[1-6])[^>]*>(.*?)</\1>", replacer, html, flags=re.DOTALL)


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


def rewrite_links(html: str, repo_id: str, base_path: str) -> str:
    """상대 이미지/링크를 repo permalink 경로로 재작성한다."""

    def rewrite_src(raw: str) -> str | None:
        if _is_external_link(raw):
            return None
        resolved = _resolve_relative_path(base_path, raw)
        return f"/raw/{repo_id}/{resolved}"

    def rewrite_href(raw: str) -> str | None:
        if _is_external_link(raw):
            return None
        pure = PurePosixPath(raw.split("?", 1)[0].split("#", 1)[0])
        if pure.suffix.lower() not in {".md", ".markdown"}:
            return None
        resolved = _resolve_relative_path(base_path, raw)
        return f"/view/{repo_id}/{resolved}"

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


def extract_title(md_text):
    """마크다운 첫 번째 h1 제목을 추출한다."""
    for line in md_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return "Markdown Preview"


def render_markdown(md_text):
    """마크다운 텍스트를 HTML로 렌더링한다.

    Returns:
        dict: {"html": str, "toc": list, "title": str}
    """
    preprocessed = preprocess_md(md_text)
    title = extract_title(md_text)

    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            TocExtension(slugify=github_slugify, toc_depth="1-4"),
            "sane_lists",
            "md_in_html",
        ],
        output_format="html5",
    )

    html_body = md.convert(preprocessed)
    html_body = fix_heading_ids(html_body)
    html_body = transform_mermaid_blocks(html_body)

    toc_tokens = getattr(md, "toc_tokens", [])

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
  font-family: "Public Sans", "Nanum Gothic", "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 14px; line-height: 1.57; color: #1C252E; background: #fff; margin: 0; padding: 0;
}
.markdown-body {
  max-width: 980px; margin: 0 auto; padding: 45px; box-sizing: border-box;
}
@media (max-width: 767px) { .markdown-body { padding: 15px; } }
.markdown-body h1,.markdown-body h2,.markdown-body h3,.markdown-body h4,.markdown-body h5,.markdown-body h6 {
  margin-top: 40px; margin-bottom: 8px; font-weight: 800; line-height: 1.25; color: #000;
}
.markdown-body h1 { font-size: 2em; padding-bottom: .3em; border-bottom: 3px solid #E01E1F; }
.markdown-body h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid #DFE3E8; }
.markdown-body h3 { font-size: 1.25em; font-weight: 700; }
.markdown-body h4 { font-size: 1.1em; font-weight: 700; }
.markdown-body h5 { font-size: 1em; font-weight: 700; }
.markdown-body h6 { font-size: 0.95em; font-weight: 600; }
.markdown-body p { margin-top: 0; margin-bottom: 1.25rem; }
.markdown-body a { color: #E01E1F; text-decoration: none; }
.markdown-body a:hover { text-decoration: underline; }
.markdown-body strong { font-weight: 700; }
.markdown-body blockquote {
  margin: 0 0 16px; padding: 6px 16px; color: #454F5B;
  border-left: none; background: #F9FAFB; border-radius: 0 8px 8px 0; position: relative;
}
.markdown-body blockquote::before {
  content: ""; position: absolute; left: 0; top: 1px; bottom: 1px;
  width: 4px; background: #E01E1F; border-radius: 4px;
}
.markdown-body blockquote>:first-child { margin-top: 0; }
.markdown-body blockquote>:last-child { margin-bottom: 0; }
.markdown-body ul,.markdown-body ol { margin-top: 0; margin-bottom: 16px; padding-left: 16px; line-height: 2; }
.markdown-body ul ul,.markdown-body ul ol,.markdown-body ol ol,.markdown-body ol ul { margin-top: 0; margin-bottom: 0; }
.markdown-body li { margin-top: .25em; }
.markdown-body li > p { margin-top: 16px; }
.markdown-body li + li { margin-top: .25em; }
.markdown-body code {
  padding: 2px 4px; font-size: 85%; background: rgba(145,158,171,.2); border-radius: 4px;
  font-family: "D2Coding", ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; color: #1C252E;
}
.markdown-body pre {
  margin-top: 0; margin-bottom: 16px; padding: 24px; overflow: auto;
  font-size: 14px; line-height: 1.57; background: #F9FAFB;
  border: 1px solid rgba(145,158,171,.16); border-radius: 8px;
}
.markdown-body pre code {
  display: inline; padding: 0; background: transparent; border: 0; font-size: 100%; white-space: pre;
}
.markdown-body table {
  border-spacing: 0; border-collapse: collapse; margin-bottom: 16px;
  display: block; width: max-content; max-width: 100%; overflow: auto; font-size: 14px;
}
.markdown-body table th {
  font-weight: 600; padding: 8px; border: 1px solid rgba(145,158,171,.2); background: #F4F6F8; color: #454F5B;
}
.markdown-body table td { padding: 8px; border: 1px solid rgba(145,158,171,.2); }
.markdown-body table tr { background: #fff; }
.markdown-body table tr:nth-child(odd) { background: #F9FAFB; }
.markdown-body hr { height: 1px; margin: 24px 0; background: #DFE3E8; border: 0; }
.markdown-body img { max-width: 100%; height: auto; border-radius: 16px; margin-bottom: 1.25rem; }
.markdown-body>*:first-child { margin-top: 0!important; }
.markdown-body>*:last-child { margin-bottom: 0!important; }
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
