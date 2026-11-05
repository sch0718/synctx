"""정적 내보내기 페이지 후처리: 출력 경로 변환과 정적 모드 주입."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import unquote

from renderer import THEME_CSS
from static_export import themes
from viewer_ui import _THEME_BOOT_ATTR, _THEME_BOOT_QUERY

STATIC_MODE_URL = "/view/static/static-mode.js"

HIDE_SELECTORS: tuple[str, ...] = (
    ".finder",
    ".shell-header .shell-link",
    '.shell-field:has(select[data-action="branch"])',
    ".doc-actions details.menu",
    '.doc-actions a[href*="mode=meeting"]',
    '.doc-actions a[href*="mode=present"]',
    '.doc-actions [data-action^="marp-"]',
    ".meeting-info-dashboard",
)

_STATIC_HIDE_MARK = 'id="static-hide"'
_CLASS_ATTR = re.compile(r"""(\sclass\s*=\s*)(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""", re.IGNORECASE)


def page_output_path(url_path: str) -> str:
    if not url_path.startswith("/") or "\x00" in url_path:
        raise ValueError("잘못된 페이지 경로")
    decoded = unquote(url_path)
    if "\x00" in decoded:
        raise ValueError("잘못된 페이지 경로")
    segments = decoded[1:].split("/")
    if segments and segments[-1] == "":
        segments.pop()
    if not segments or any(s in ("", ".", "..") or "\\" in s for s in segments):
        raise ValueError("잘못된 페이지 경로")
    return "/".join([*segments, "index.html"])


_HLJS_HREF = re.compile(r"/(?:hljs|styles)/[\w-]+\.min\.css$")
_MERMAID_THEME = re.compile(r"theme:\s*'[^']*'")
_SERVER_BOOTS = frozenset(boot.removeprefix("<script>").removesuffix("</script>") for boot in (_THEME_BOOT_QUERY, _THEME_BOOT_ATTR))
_THEME_STYLES = frozenset(css.strip() for css in THEME_CSS.values())


class _Locator(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_starts = [0]
        for m in re.finditer("\n", source):
            self._line_starts.append(m.end())
        self.head_end: int | None = None
        self.body_start: tuple[int, str] | None = None
        self.body_end: int | None = None
        self.has_static_hide = False
        self.edits: list[tuple[int, int, str]] = []
        self._open: tuple[str, int, dict, list[str]] | None = None
        self.theme_link_replaced = False
        self.feed(source)
        self.close()

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "body" and self.body_start is None:
            self.body_start = (self._offset(), self.get_starttag_text())
        elif tag in ("script", "style"):
            self._open = (tag, self._offset(), attrs, [])
            if tag == "style" and attrs.get("id") == "static-hide":
                self.has_static_hide = True
        elif tag == "link" and "stylesheet" in (attrs.get("rel") or "").split() and _HLJS_HREF.search(attrs.get("href") or ""):
            start = self._offset()
            self.edits.append((start, start + len(self.get_starttag_text()), "hljs"))

    def handle_data(self, data):
        if self._open is not None:
            self._open[3].append(data)

    def handle_endtag(self, tag):
        if self._open is not None and tag == self._open[0]:
            kind, start, attrs, chunks = self._open
            self._open = None
            end = self._source.index(">", self._offset()) + 1
            self._classify(kind, start, end, attrs, "".join(chunks))
        elif tag == "head" and self.head_end is None:
            self.head_end = self._offset()
        elif tag == "body":
            self.body_end = self._offset()

    def _classify(self, kind, start, end, attrs, text):
        if kind == "style" and text.strip() in _THEME_STYLES:
            self.edits.append((start, end, "theme"))
        elif kind == "script" and "src" not in attrs and (attrs.get("type") or "text/javascript") == "text/javascript":
            if text in _SERVER_BOOTS:
                self.edits.append((start, end, "boot"))
            elif "mermaid.initialize(" in text:
                self.edits.append((start, end, "mermaid"))


def _hide_style() -> str:
    selectors = ",\n".join(HIDE_SELECTORS)
    return f'<style {_STATIC_HIDE_MARK}>\n{selectors}{{display:none !important}}\n</style>'


def _theme_link(default_theme: str) -> str:
    return f'<link id="theme-css" rel="stylesheet" href="{themes.css_url(default_theme)}">'


def _hljs_link(default_theme: str) -> str:
    return f'<link id="hljs-css" rel="stylesheet" href="{themes.hljs_url(default_theme)}">'


def _boot_script(default_theme: str) -> str:
    config = json.dumps({"defaultTheme": default_theme, "table": themes.browser_table()}, ensure_ascii=True).replace("<", "\\u003c")
    return (f"<script>(function(){{var C={config};var s=null;try{{s=localStorage.getItem('synctx-theme');}}catch(e){{}}"
            "var n=(typeof s==='string'&&Object.prototype.hasOwnProperty.call(C.table,s))?s:C.defaultTheme;var t=C.table[n];"
            "document.documentElement.setAttribute('data-theme',n);"
            "var c=document.getElementById('theme-css');if(c)c.setAttribute('href',t.css);"
            "var h=document.getElementById('hljs-css');if(h)h.setAttribute('href',t.hljs);"
            "window.SYNCTX_STATIC={theme:n,defaultTheme:C.defaultTheme,css:t.css,hljs:t.hljs,mermaid:t.mermaid,table:C.table};})();</script>")


def _with_static_class(tag_text: str) -> str:
    match = _CLASS_ATTR.search(tag_text)
    if match is None:
        return tag_text[:5] + ' class="page-static"' + tag_text[5:]
    value = next(g for g in match.groups()[1:] if g is not None)
    if "page-static" in value.split():
        return tag_text
    merged = f'{match.group(1)}"{(value + " page-static").strip()}"'
    return tag_text[: match.start()] + merged + tag_text[match.end():]


def _replace_mermaid_theme(script: str) -> str:
    return _MERMAID_THEME.sub("theme: SYNCTX_STATIC.mermaid", script, count=1)


def finalize_page(html: str, *, default_theme: str) -> str:
    if default_theme not in THEME_CSS:
        raise ValueError(f"알 수 없는 테마: {default_theme}")
    loc = _Locator(html)
    if loc.head_end is None or loc.body_start is None or loc.body_end is None:
        raise ValueError("head·body가 있는 HTML 문서가 아니다")
    if loc.has_static_hide:
        return html
    body_pos, body_tag = loc.body_start
    kinds = {kind for _, _, kind in loc.edits}
    head_parts = [_hide_style()]
    if "theme" not in kinds:
        head_parts.append(_theme_link(default_theme))
    head_parts.append(_boot_script(default_theme))
    edits = [(loc.head_end, loc.head_end, "\n".join(head_parts) + "\n"),
             (body_pos, body_pos + len(body_tag), _with_static_class(body_tag)),
             (loc.body_end, loc.body_end, f'<script src="{STATIC_MODE_URL}"></script>\n')]
    for start, end, kind in loc.edits:
        if kind == "boot":
            edits.append((start, end, ""))
        elif kind == "theme":
            edits.append((start, end, _theme_link(default_theme)))
        elif kind == "hljs":
            edits.append((start, end, _hljs_link(default_theme)))
        else:
            edits.append((start, end, _replace_mermaid_theme(html[start:end])))
    out = html
    for start, end, text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        out = out[:start] + text + out[end:]
    return out
