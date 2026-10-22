"""회의 문서 근거: 본문의 근거 링크를 찾아 근거 문서에서 조문·절 발췌를 뽑고 정화한다."""

from __future__ import annotations

import bisect
import functools
import html
import re
import secrets
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Callable
from urllib.parse import unquote, urlsplit

import yaml

from synctx_common.frontmatter import parse_frontmatter
from synctx_common.html_bundle import find_blocks

from renderer import _heading_attrs, _is_external_link, _path_part, longest_prefix, map_href, render_markdown

MAX_LINKS = 200
MAX_DOCS = 50
EXCERPT_MAX_LINES = 80
EXCERPT_MAX_BYTES = 16 * 1024
START_LINES = 14
TRUNCATED_NOTE = "…(발췌가 길어 줄였습니다)"
DOC_START = "문서 첫 부분"
HTML_EMBED = "[인터랙티브 HTML: {}]"

_ANCHOR = r"""<a\s+id\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+)\s*>\s*</a>"""
_ANCHOR_RE = re.compile(_ANCHOR, re.IGNORECASE)
_LEADING_ANCHOR_RE = re.compile(rf"[ \t]*(?:{_ANCHOR}[ \t]*)+", re.IGNORECASE)
_FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})")
_SETEXT_RE = re.compile(r"[=-]+[ ]*$")
_LIST_ITEM_RE = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")
_RAW_HEADING_RE = re.compile(r" {0,3}<h([1-6])(?=[\s>/])", re.IGNORECASE)
_HR_RE = re.compile(r" {0,3}-{3,}[ \t]*$")
_REF_DEF_RE = re.compile(r" {0,3}\[[^\[\]\n]+\]:")
_ARTICLE_RE = re.compile(r"\*\*제\d+조(?:의\d+)?")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_IMAGE_RE = re.compile(r"!\[([^\[\]\n]*)\]\([^()\n]*\)")
_LINK_RE = re.compile(r"\[([^\[\]\n]*)\]\([^()\n]*\)")
_REF_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\[[^\[\]\n]*\]")
_TAG_RE = re.compile(r"</?[A-Za-z][^<>]*>")
_EMPHASIS_RE = re.compile(r"\*+|`+|~~|__")
_RENDERED_ID_RE = re.compile(r'<h[1-6] id="([^"]*)"')
_DIV_RE = re.compile(r"<(/?)div(?=[\s/>])", re.IGNORECASE)
_CLOSE_END_RE = re.compile(r"\s*>")
_TAG_START_RE = re.compile(r"<[A-Za-z][A-Za-z0-9-]*(?=[\s/>])")
_TAG_TEXT_RE = re.compile(r"[^>\"']*")
_BACKTICKS_RE = re.compile(r"`+")
_MD_PUNCT_RE = re.compile(r"([\\`*_{}\[\]()!])")


def _split(md: str) -> list[str]:
    return md.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _strip_leading_anchors(line: str) -> str:
    m = _LEADING_ANCHOR_RE.match(line)
    return line[m.end():] if m else line


def _is_anchor_line(line: str) -> bool:
    return _LEADING_ANCHOR_RE.match(line) is not None


def _is_lone_anchor(line: str) -> bool:
    return _is_anchor_line(line) and not _strip_leading_anchors(line).strip()


def _indent(line: str) -> int:
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())


def _atx_level(line: str) -> int:
    return min(len(line) - len(line.lstrip("#")), 6)


def _inline(text: str) -> str:
    text = _ANCHOR_RE.sub("", text)
    text = _IMAGE_RE.sub(lambda m: f"[그림: {m.group(1)}]", text)
    text = _LINK_RE.sub(r"\1", text)
    return _REF_LINK_RE.sub(r"\1", text)


def _plain(text: str) -> str:
    text = _EMPHASIS_RE.sub("", _TAG_RE.sub("", _inline(text)))
    return " ".join(text.split())


class _Lines:
    """줄마다 코드 여부·제목 레벨·setext 밑줄 여부를 한 번 훑어 정한다(렌더러의 제목 판정에 맞춘다).

    verbatim은 해설 표시·발췌 정리가 원문 그대로 둘 코드 줄이다. 코드 펜스와, 들여쓴 코드 중 지금 목록
    항목 내용 들여쓰기(목록 밖이면 0)보다 4칸 이상 더 들여쓴 줄이다. 목록 깊이는 Python-Markdown처럼
    4칸 단위로 센다. 확실하지 않으면 코드로 보지 않는다(해설이 새는 것보다 예시가 바뀌는 편이 낫다).
    """

    def __init__(self, lines: list[str]):
        n = len(lines)
        self.lines = lines
        self.code = [False] * n
        self.verbatim = [False] * n
        self.level = [0] * n
        self.underline = [False] * n
        self.headings: list[int] = []
        fence = None
        block_start = True
        # 열린 목록 항목들의 내용 들여쓰기(바깥 → 안쪽)
        items: list[int] = []
        after_blank = False
        for i, line in enumerate(lines):
            if self.underline[i]:
                block_start = True
                after_blank = False
                continue
            if fence:
                self.code[i] = self.verbatim[i] = True
                m = _FENCE_RE.match(line)
                if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line[m.end():].strip():
                    fence = None
                    block_start = True
                continue
            if not line.strip():
                block_start = True
                after_blank = True
                continue
            indent = _indent(line)
            item = _LIST_ITEM_RE.match(line) is not None
            if item:
                content = (indent // 4 + 1) * 4
                while items and items[-1] >= content:
                    items.pop()
                items.append(content)
            elif after_blank:
                # 빈 줄 뒤에는 들여쓰기가 모자란 목록 항목을 닫는다(빈 줄 없이 이어지면 느슨한 이어쓰기로 남는다)
                while items and indent < items[-1]:
                    items.pop()
            after_blank = False
            m = _FENCE_RE.match(line)
            if m:
                fence = m.group(1)
                self.code[i] = self.verbatim[i] = True
                continue
            if line.startswith(("    ", "\t")) and block_start:
                self.code[i] = True
                self.verbatim[i] = not item and indent >= (items[-1] if items else 0) + 4
                continue
            level = 0
            if line.startswith("#"):
                level = _atx_level(line)
            elif (raw := _RAW_HEADING_RE.match(line)):
                level = int(raw.group(1))
            elif block_start and i + 1 < n and (under := _SETEXT_RE.match(lines[i + 1])):
                level = 1 if under.group(0)[0] == "=" else 2
                self.underline[i + 1] = True
            if level:
                self.level[i] = level
                self.headings.append(i)
                block_start = True
                continue
            block_start = bool(_HR_RE.match(line) or _REF_DEF_RE.match(line) or line.startswith("<"))

    def is_hr(self, i: int) -> bool:
        return not self.code[i] and not self.underline[i] and bool(_HR_RE.match(self.lines[i]))

    def heading_text(self, i: int) -> str:
        line = self.lines[i]
        if line.startswith("#"):
            line = line[_atx_level(line):].rstrip().rstrip("#")
        return _plain(line)


def heading_ids(md_body: str) -> list[tuple[int, str]]:
    scan = _Lines(_split(md_body))
    rendered = render_markdown(md_body)["html"]
    ids = [html.unescape(i) for i in _RENDERED_ID_RE.findall(rendered)]
    if len(ids) != len(scan.headings):
        return []
    return list(zip(scan.headings, ids))


def doc_name(md_text: str, path: str) -> str:
    front_matter, body = parse_frontmatter(md_text)
    title = front_matter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    scan = _Lines(_split(body))
    for i in scan.headings:
        if scan.level[i] == 1:
            name = scan.heading_text(i)
            if name:
                return name
    return PurePosixPath(path).stem


def _commentary_spans(md_body: str):
    """class에 commentary 낱말이 든 div가 짝이 맞는 닫는 태그까지 차지하는 (시작, 끝)을 낸다. 짝이 없으면 끝까지."""
    start = -1
    depth = 0
    gt = -1
    for m in _DIV_RE.finditer(md_body):
        # 다른 여는 태그의 속성 자리에 든 것은 태그가 아니다
        if m.start() < gt:
            continue
        if m.group(1):
            end = _CLOSE_END_RE.match(md_body, m.end())
            if not end or depth == 0:
                continue
            depth -= 1
            if depth == 0:
                yield start, end.end()
            continue
        gt = md_body.find(">", m.end())
        if gt == -1:
            break
        if depth:
            depth += 1
            continue
        for name, value, _ in _heading_attrs(md_body[m.end():gt]):
            if name == "class" and value and "commentary" in html.unescape(value.strip("\"'")).lower().split():
                start = m.start()
                depth = 1
                break
    if depth:
        yield start, len(md_body)


def strip_commentary(md_body: str) -> str:
    """class에 commentary 낱말이 든 div를 짝이 맞는 닫는 태그까지 지운다. 짝이 없으면 끝까지 지운다."""
    out = []
    pos = 0
    for start, end in _commentary_spans(md_body):
        out.append(md_body[pos:start])
        pos = end
    out.append(md_body[pos:])
    return "".join(out)


def _finish(lines: list[str], code_lines: list[bool] | None = None) -> str:
    # 코드 블록(펜스·들여쓰기) 줄과 인라인 코드는 자리표시로 바꿔 해설 지우기·링크 풀기에서 빼고, 끝에 원문 그대로 되돌린다
    token = f"\x00{secrets.token_hex(8)}:"
    code: list[str] = []

    def hold(text: str) -> str:
        code.append(text)
        return f"{token}{len(code) - 1}\x00"

    masked = [
        hold(line) if code_lines and code_lines[k] else "".join(
            hold(part) if n % 2 else part for n, part in enumerate(_outside_code(line)))
        for k, line in enumerate(lines)
    ]
    held = re.compile(re.escape(token) + r"(\d+)\x00")
    kept = []
    for line in strip_commentary("\n".join(masked)).split("\n"):
        if _REF_DEF_RE.match(line):
            continue
        cleaned = _inline(line)
        if line.strip() and not cleaned.strip():
            continue
        kept.append(held.sub(lambda m: code[int(m.group(1))], cleaned))
    first = next((i for i, line in enumerate(kept) if line.strip()), len(kept))
    while len(kept) > first and not kept[-1].strip():
        kept.pop()
    kept = kept[first:]
    result = "\n".join(kept)
    if len(kept) <= EXCERPT_MAX_LINES and len(result.encode("utf-8")) <= EXCERPT_MAX_BYTES:
        return result
    budget = EXCERPT_MAX_BYTES - len(TRUNCATED_NOTE.encode("utf-8"))
    out = []
    for line in kept[:EXCERPT_MAX_LINES - 1]:
        size = len(line.encode("utf-8")) + 1
        if size > budget:
            out.append(line.encode("utf-8")[:max(budget - 1, 0)].decode("utf-8", "ignore"))
            break
        out.append(line)
        budget -= size
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out + [TRUNCATED_NOTE])


def _commentary_mask(scan: _Lines) -> list[bool]:
    """줄마다 해설 블록 안에서 시작하는지. 코드 블록(펜스·들여쓰기, 목록에 이어지는 문단 제외)·인라인 코드는 해설을 열거나 닫지 않는다."""
    lines = ["" if scan.verbatim[i] else _drop_code_spans(line) for i, line in enumerate(scan.lines)]
    spans = _commentary_spans("\n".join(lines))
    span = next(spans, None)
    mask = []
    pos = 0
    for line in lines:
        while span is not None and span[1] <= pos:
            span = next(spans, None)
        mask.append(span is not None and span[0] <= pos)
        pos += len(line) + 1
    return mask


def _finish_at(doc: Prepared, picked: list[int]) -> str:
    """synctx-html 블록은 여는 줄이 들면 블록 전체를 안내 한 줄로 바꾼다(원문 그대로 둘 줄로 넘겨 정리에서 뺀다)."""
    scan = doc.scan
    lines: list[str] = []
    code: list[bool] = []
    skip_to = -1
    for i in picked:
        if i <= skip_to:
            continue
        embed = doc.embeds.get(i)
        if embed:
            skip_to, text = embed
            lines.append(text)
            code.append(True)
        else:
            lines.append(scan.lines[i])
            code.append(scan.verbatim[i])
    return _finish(lines, code)


def _embed_name(body: str) -> str | None:
    """블록의 title, 없으면 src 파일 이름. src가 없거나 YAML 객체가 아니면 None(블록 원문을 그대로 둔다)."""
    try:
        data = yaml.safe_load(body)
    except (yaml.YAMLError, ValueError):
        # 스칼라 변환 오류(예: 없는 날짜 2026-02-30)는 YAMLError가 아니라 ValueError다
        return None
    if not isinstance(data, dict):
        return None
    src = data.get("src")
    if not isinstance(src, str) or not src.strip():
        return None
    title = data.get("title")
    if isinstance(title, str) and title.split():
        return " ".join(title.split())
    try:
        name = PurePosixPath(unquote(urlsplit(src.strip()).path)).name
    except ValueError:
        return None
    return name or None


def _embed_lines(lines: list[str]) -> dict[int, tuple[int, str]]:
    """Viewer가 iframe으로 바꾸는 synctx-html 블록(html_bundle.find_blocks)의 여는 줄 → (닫는 줄, 안내 한 줄 md)."""
    text = "\n".join(lines)
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    embeds = {}
    for block in find_blocks(text):
        first = bisect.bisect_right(starts, block.start) - 1
        last = bisect.bisect_right(starts, block.end) - 1
        # find_blocks와 줄 나누기가 어긋나 블록이 줄 중간에서 시작·끝나면 바꾸지 않는다
        if starts[first] + len(block.indent) != block.start or starts[last] + len(lines[last]) != block.end:
            continue
        name = _embed_name(block.body)
        if name is not None:
            label = _MD_PUNCT_RE.sub(r"\\\1", html.escape(name, quote=False))
            embeds[first] = (last, block.indent + HTML_EMBED.format(label))
    return embeds


def _body_start(scan: _Lines, heading: int) -> int:
    i = heading + 1
    return i + 1 if i < len(scan.lines) and scan.underline[i] else i


def _doc_start(doc: Prepared) -> str:
    scan = doc.scan
    first = next((i for i in scan.headings if scan.level[i] == 1), None)
    picked = []
    in_section = False
    for i in range(0 if first is None else _body_start(scan, first), len(scan.lines)):
        line = scan.lines[i]
        if not picked and not line.strip():
            continue
        if not scan.code[i] and not doc.commentary[i]:
            level = scan.level[i]
            if level == 1 or (in_section and (level == 2 or scan.is_hr(i))):
                break
            if level == 2:
                in_section = True
        picked.append(i)
        if len(picked) >= START_LINES:
            break
    return _finish_at(doc, picked)


def _code_spans(line: str) -> list[tuple[int, int]]:
    """줄 안 인라인 코드(같은 길이의 백틱 묶음 사이, 백틱 포함)의 (시작, 끝). 묶음 길이별 다음 위치를 앞으로만 옮겨 선형이다."""
    runs = [(m.start(), m.end()) for m in _BACKTICKS_RE.finditer(line)]
    spans: list[tuple[int, int]] = []
    if len(runs) < 2:
        return spans
    by_len: dict[int, list[int]] = {}
    for k, (start, end) in enumerate(runs):
        by_len.setdefault(end - start, []).append(k)
    next_at = dict.fromkeys(by_len, 0)
    k = 0
    while k < len(runs):
        size = runs[k][1] - runs[k][0]
        same = by_len[size]
        while next_at[size] < len(same) and same[next_at[size]] <= k:
            next_at[size] += 1
        if next_at[size] == len(same):
            k += 1
            continue
        close = same[next_at[size]]
        spans.append((runs[k][0], runs[close][1]))
        k = close + 1
    return spans


def _outside_code(line: str) -> list[str]:
    """인라인 코드를 경계로 나눈 조각. 짝수 번째는 코드 밖, 홀수 번째는 코드다."""
    parts = []
    pos = 0
    for start, end in _code_spans(line):
        parts += [line[pos:start], line[start:end]]
        pos = end
    parts.append(line[pos:])
    return parts


def _drop_code_spans(line: str) -> str:
    return " ".join(_outside_code(line)[::2])


def _outside_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """HTML 주석(<!-- … -->, 여러 줄 포함) 밖의 글과 줄 끝에서 주석 안인지를 돌려준다."""
    visible = []
    pos = 0
    while True:
        if in_comment:
            end = line.find("-->", pos)
            if end == -1:
                break
            pos = end + 3
            in_comment = False
        else:
            start = line.find("<!--", pos)
            if start == -1:
                visible.append(line[pos:])
                break
            visible.append(line[pos:start])
            pos = start + 4
            in_comment = True
    return " ".join(visible), in_comment


def _tag_ids(line: str):
    """줄 안 HTML 여는 태그의 따옴표 친 id 값을 HTML 엔티티를 풀어 차례로 낸다."""
    pos = 0
    while (m := _TAG_START_RE.search(line, pos)):
        end = m.end()
        while True:
            end = _TAG_TEXT_RE.match(line, end).end()
            if end == len(line):
                return
            if line[end] == ">":
                break
            end = line.find(line[end], end + 1)
            if end == -1:
                return
            end += 1
        for name, value, _ in _heading_attrs(line[m.end():end]):
            if name == "id" and value and value[0] in "\"'":
                yield html.unescape(value[1:-1])
        pos = end + 1


class Prepared:
    """근거 문서 하나를 한 번만 줄로 나누고 명시 앵커 id 위치를 모아 둔다. 같은 문서의 발췌들이 함께 쓴다."""

    def __init__(self, md_body: str):
        self.scan = _Lines(_split(md_body))
        self.ids = functools.cache(lambda: heading_ids(md_body))
        # HTML 여는 태그의 명시 id 값 → 그 값이 처음 나온 줄(코드 블록·인라인 코드·HTML 주석 밖)
        self.anchors: dict[str, int] = {}
        in_comment = False
        for i, line in enumerate(self.scan.lines):
            if self.scan.code[i] or (not in_comment and "<" not in line):
                continue
            visible, in_comment = _outside_comments(_drop_code_spans(line), in_comment)
            for value in _tag_ids(visible):
                self.anchors.setdefault(value, i)
        # 해설 블록 안 줄은 발췌 경계로 보지 않는다(해설은 정리 때 지워진다)
        self.commentary = _commentary_mask(self.scan)
        self.embeds = _embed_lines(self.scan.lines)


def _find_start(doc: Prepared, anchor: str, ids: Callable[[], list[tuple[int, str]]]) -> int | None:
    scan = doc.scan
    found = doc.anchors.get(anchor)
    if found is None:
        return next((i for i, hid in ids() if hid == anchor), None)
    if _ANCHOR_RE.sub("", scan.lines[found]).strip():
        return found
    return next((j for j in range(found + 1, len(scan.lines)) if scan.lines[j].strip()), None)


def extract(
    md_body: str,
    anchor: str,
    *,
    ids: Callable[[], list[tuple[int, str]]] | None = None,
    prepared: Prepared | None = None,
) -> tuple[str, str]:
    """prepared가 있으면 md_body 대신 그 준비 결과를 쓴다(같은 문서에서 여러 번 뽑을 때)."""
    anchor = unquote(anchor or "")
    doc = prepared or Prepared(md_body)
    scan = doc.scan
    if not anchor:
        return DOC_START, _doc_start(doc)
    start = _find_start(doc, anchor, ids or doc.ids)
    if start is None:
        return f"{DOC_START} (‘#{anchor}’를 찾지 못해 문서 첫 부분을 보입니다)", _doc_start(doc)
    lines = scan.lines
    masked = doc.commentary
    line = _strip_leading_anchors(lines[start])

    if scan.level[start]:
        level = scan.level[start]
        body = []
        has_content = False
        for i in range(_body_start(scan, start), len(lines)):
            if masked[i]:
                body.append(i)
                continue
            if not scan.code[i]:
                if scan.level[i] and scan.level[i] <= level:
                    break
                # `---`·앵커 줄은 본문 줄이 하나라도 나온 뒤에만 경계다(제목 바로 뒤 앵커 줄로 발췌가 비지 않게)
                if scan.is_hr(i) or _is_lone_anchor(lines[i]):
                    if has_content:
                        break
                    body.append(i)
                    continue
            has_content = has_content or bool(lines[i].strip())
            body.append(i)
        return scan.heading_text(start), _finish_at(doc, body)

    if line.lstrip().startswith("|"):
        top = start
        while top > 0 and not scan.code[top - 1] and lines[top - 1].lstrip().startswith("|"):
            top -= 1
        rows = lines[top:top + 2] + ([lines[start]] if start >= top + 2 else [])
        first_cell = line.strip().strip("|").split("|")[0]
        return f"표: {_plain(first_cell)}", _finish(rows)

    bold = _BOLD_RE.match(line)
    table = bold is not None and bold.group(1).startswith(("<표", "&lt;표"))
    body = [start]
    seen_table = False
    blanks = 0
    for i in range(start + 1, len(lines)):
        current = lines[i]
        if masked[i]:
            body.append(i)
            continue
        if not scan.code[i]:
            if scan.level[i] or scan.is_hr(i) or _is_anchor_line(current):
                break
            if not table and _ARTICLE_RE.match(_strip_leading_anchors(current)):
                break
        if table:
            if current.lstrip().startswith("|"):
                seen_table = True
            elif seen_table and current.strip():
                break
        elif bold is None:
            blanks = blanks + 1 if not current.strip() else 0
            if blanks == 2:
                break
        body.append(i)
    label = _plain(bold.group(1)) if bold else _plain(line)[:40]
    return label, _finish_at(doc, body)


_ALLOWED_TAGS = frozenset(
    "p br strong em b i u s del code pre kbd sub sup mark blockquote ul ol li dl dt dd "
    "table thead tbody tfoot tr th td caption h1 h2 h3 h4 h5 h6 hr span div".split()
)
_DROP_CONTENT_TAGS = frozenset("script style iframe object embed template noscript".split())
_VOID_TAGS = frozenset({"br", "hr", "img", "embed"})
_SPAN_ATTRS = ("colspan", "rowspan")
_SPAN_VALUE_RE = re.compile(r"[0-9]{1,4}")


class _ExcerptSanitizer(HTMLParser):
    """허용 태그만 남기고, 잘린 발췌도 짝이 맞는 조각이 되게 열린 태그를 쌓아 닫는다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.open: list[str] = []
        self.open_count: dict[str, int] = {}

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_CONTENT_TAGS:
            if tag not in _VOID_TAGS:
                self.skip += 1
            return
        if self.skip:
            return
        if tag == "img":
            alt = next((value for name, value in attrs if name == "alt"), None) or ""
            self.out.append(html.escape(f"[그림: {alt}]"))
            return
        if tag not in _ALLOWED_TAGS:
            return
        kept = ""
        if tag in ("td", "th"):
            kept = "".join(
                f' {name}="{html.escape(value)}"'
                for name, value in attrs
                if name in _SPAN_ATTRS and value and _SPAN_VALUE_RE.fullmatch(value)
            )
        self.out.append(f"<{tag}{kept}>")
        if tag not in _VOID_TAGS:
            self.open.append(tag)
            self.open_count[tag] = self.open_count.get(tag, 0) + 1

    def handle_endtag(self, tag):
        if tag in _DROP_CONTENT_TAGS:
            if tag not in _VOID_TAGS and self.skip:
                self.skip -= 1
            return
        if self.skip or tag in _VOID_TAGS or tag not in _ALLOWED_TAGS or not self.open_count.get(tag):
            return
        # 짝이 되는 여는 태그까지 사이에 열린 태그를 먼저 닫는다
        while True:
            closed = self._close()
            if closed == tag:
                return

    def _close(self) -> str:
        tag = self.open.pop()
        self.open_count[tag] -= 1
        self.out.append(f"</{tag}>")
        return tag

    def close(self):
        super().close()
        while self.open:
            self._close()

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data))


def sanitize_excerpt(html_text: str) -> str:
    parser = _ExcerptSanitizer()
    parser.feed(html_text)
    parser.close()
    return "".join(parser.out)


def split_references(references: dict[str, str], known_repos: set[str]) -> tuple[dict[str, str], tuple[str, ...]]:
    """설정에 있는 저장소를 가리키는 매핑과, 없는 저장소 id 목록(중복 없이 키 순서)으로 나눈다."""
    valid = {}
    missing: dict[str, None] = {}
    for key, value in references.items():
        repo = value.partition(":")[0]
        if repo in known_repos:
            valid[key] = value
        else:
            missing[repo] = None
    return valid, tuple(missing)


@dataclass(frozen=True)
class Entry:
    id: str
    repo_id: str
    path: str
    anchor: str
    doc: str
    loc: str
    html: str
    cites: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Evidence:
    html: str
    entries: tuple[Entry, ...]
    ids: dict[str, str]


# 브라우저가 안의 태그를 요소로 만들지 않는 요소(원시 텍스트·RCDATA, 스크립트가 켜진 noscript)
_RAW_TEXT_TAGS = frozenset("script style xmp iframe noembed noframes noscript textarea title plaintext".split())
_HEADING_TAGS = frozenset(f"h{n}" for n in range(1, 7))
_CITE_TAGS = frozenset({"h2", "h3"})
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")
_UNSAFE_SEGMENT_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\\\u2028\u2029]")
_MD_SUFFIXES = (".md", ".markdown")


class _LinkScanner(HTMLParser):
    """본문 HTML을 한 번 훑어 a[href]의 속성 넣을 자리·앞 제목과 본문 id를 모은다."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.line_starts = [0, *(m.end() for m in re.finditer("\n", source))]
        self.links: list[tuple[int, str, tuple[str, list[str]] | None]] = []
        self.ids: set[str] = set()
        self.raw: str | None = None
        self.heading: tuple[str, list[str]] | None = None
        self.text: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if self.raw:
            return
        values = dict(reversed(attrs))
        if values.get("id"):
            self.ids.add(values["id"])
        if tag in _RAW_TEXT_TAGS:
            self.raw = tag
        elif tag in _HEADING_TAGS:
            self.text = None
            if tag in _CITE_TAGS and values.get("id"):
                self.heading = (values["id"], [])
                self.text = self.heading[1]
        elif tag == "a" and values.get("href") is not None:
            self.links.append((self._insert_at(), values["href"], self.heading))

    def handle_startendtag(self, tag, attrs):
        # 브라우저는 <textarea/>·<style/>도 여는 태그로 본다
        self.handle_starttag(tag, attrs)
        if tag not in _RAW_TEXT_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.raw:
            if tag == self.raw:
                self.raw = None
        elif tag in _HEADING_TAGS:
            self.text = None

    def handle_data(self, data):
        if self.text is not None and not self.raw:
            self.text.append(data)

    def _insert_at(self) -> int:
        line, column = self.getpos()
        tag = self.get_starttag_text()
        end = self.line_starts[line - 1] + column + len(tag)
        # 따옴표 없는 값 뒤의 "/"는 값의 일부라 ">" 앞에 넣는다
        if tag.endswith("/>") and tag[-3] in "\"' \t\n\r\f":
            return end - 2
        return end - 1


def _safe_path(base_dir: str, target: str) -> str | None:
    """target을 base_dir 기준으로 풀어 저장소 경로를 만든다. 루트를 넘거나 위험한 경로 조각이면 None."""
    parts = [part for part in base_dir.split("/") if part not in ("", ".")]
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part.startswith(".") or _UNSAFE_SEGMENT_RE.search(part):
            return None
        else:
            parts.append(part)
    return "/".join(parts)


def _target(
    href: str, repo_id: str, path: str, references: dict[str, str], skip_prefixes: tuple[str, ...]
) -> tuple[str, str, str] | None:
    """근거 링크면 (저장소 id, 경로, 퍼센트 디코딩 전 앵커), 아니면 None."""
    if _is_external_link(href):
        return None
    key = longest_prefix(href, (*references, *skip_prefixes))
    if key is not None:
        if key not in references:
            return None
        repo, mapped = map_href(href, references)
        base = ""
    elif href.startswith("/") or _SCHEME_RE.match(href):
        return None
    else:
        repo, mapped, base = repo_id, href, str(PurePosixPath(path).parent)
    target, _, anchor = mapped.partition("#")
    target = unquote(target.partition("?")[0])
    # 원래 폴더를 가리킨 링크는 매핑 값이 파일처럼 보여도 근거가 아니다(rewrite_links의 폴더 판정과 같다)
    if target.endswith("/") or _path_part(href).endswith("/"):
        return None
    resolved = _safe_path(base, target)
    if not resolved or not resolved.lower().endswith(_MD_SUFFIXES) or (repo, resolved) == (repo_id, path):
        return None
    return repo, resolved, anchor


def _unique_id(base: str, taken: set[str]) -> str:
    candidate = base
    n = 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def build(
    html_text: str,
    *,
    repo_id: str,
    path: str,
    references: dict[str, str],
    skip_prefixes: tuple[str, ...] = (),
    read: Callable[[str, str], str],
    render: Callable[[str], str],
) -> Evidence:
    """본문 HTML의 근거 링크를 모아 근거 항목을 만들고, 근거 링크에 data-evidence를 더한 본문을 돌려준다."""
    scanner = _LinkScanner(html_text)
    scanner.feed(html_text)
    scanner.close()

    docs: dict[tuple[str, str], tuple[str, str, Prepared] | None] = {}
    loaded = 0
    judged = 0
    found: dict[tuple[str, str, str], dict] = {}
    marks: list[tuple[int, dict]] = []
    for offset, href, heading in scanner.links:
        target = _target(href, repo_id, path, references, skip_prefixes)
        if target is None:
            continue
        if judged >= MAX_LINKS:
            break
        judged += 1
        repo, doc_path, anchor = target
        key = (repo, doc_path, unquote(anchor))
        item = found.get(key)
        if item is None:
            if (repo, doc_path) not in docs:
                if loaded >= MAX_DOCS:
                    continue
                try:
                    text = read(repo, doc_path)
                    body = parse_frontmatter(text)[1]
                    docs[(repo, doc_path)] = (doc_name(text, doc_path), body, Prepared(body))
                    loaded += 1
                except Exception:
                    docs[(repo, doc_path)] = None
            doc = docs[(repo, doc_path)]
            if doc is None:
                continue
            name, body, prepared = doc
            loc, excerpt = extract(body, anchor, prepared=prepared)
            item = found[key] = {
                "repo": repo, "path": doc_path, "anchor": anchor, "doc": name, "loc": loc,
                "html": render(excerpt), "cites": {},
            }
        if heading is not None:
            item["cites"].setdefault(*heading)
        marks.append((offset, item))

    taken = set(scanner.ids)
    entries = []
    for n, item in enumerate(found.values(), 1):
        item["id"] = _unique_id(f"evidence-{n}", taken)
        taken.add(item["id"])
        cites = tuple((hid, " ".join("".join(parts).split())) for hid, parts in item["cites"].items())
        entries.append(Entry(
            id=item["id"], repo_id=item["repo"], path=item["path"], anchor=item["anchor"],
            doc=item["doc"], loc=item["loc"], html=item["html"], cites=cites,
        ))
    ids = {name: _unique_id(f"evidence-{name}", taken) for name in ("list", "title", "data")}

    out = []
    pos = 0
    for offset, item in marks:
        out.append(html_text[pos:offset])
        out.append(f' data-evidence="{item["id"]}"')
        pos = offset
    out.append(html_text[pos:])
    return Evidence(html="".join(out), entries=tuple(entries), ids=ids)
