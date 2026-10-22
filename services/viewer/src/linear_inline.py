"""Python-Markdown 인라인 처리가 입력 길이의 제곱 이상으로 느려지는 곳을 바꿔 끼운다.

원래 처리기는 매치 하나를 placeholder로 바꿀 때마다 문자열을 새로 만들고 같은 패턴을 처음부터 다시 훑는다.
- 백틱: 원래 정규식은 긴 백틱 묶음에서 여는 길이마다 끝까지 되돌아간다(세제곱). 다시 훑어 얻는 결과까지 같게
  문자열을 한 번만 훑어 만든다.
- 링크·자동 링크 등(_FORWARD_PATTERNS): 매치 끝에서 이어 훑고 문자열은 한 번만 조립한다. 앞에서 실패한 "["·"<"가
  뒤 매치의 placeholder를 넘어 다시 매치되던 경우만 결과가 달라지며, 원래 결과는 href에 placeholder가 새는 등
  이미 깨진 출력이다.
- 원시 HTML: 속성값에 "<"가 든 태그(<span title="<b>">)는 안쪽이 먼저 매치된 뒤 다시 매치되어야 하므로 원래 동작을
  지키되, 다시 매치될 수 있는 가장 이른 자리부터 이어 훑는다.
- 강조(*·_)는 다시 매치되는 동작에 기대므로("**a*b*" → <em><em>a</em>b</em>) 원래대로 둔다.
- 닫히지 않은 "["마다 끝까지 훑던 대괄호 짝 찾기는 같은 문자열 안에서 한 번만 계산한다.
"""

import bisect
import re
import xml.etree.ElementTree as etree

import markdown
from markdown import inlinepatterns, util
from markdown.treeprocessors import InlineProcessor

_BACKTICK_RUN = re.compile(r"`+")
_BRACKET = re.compile(r"[\[\]]")
_ANGLE = re.compile(r"[<>]")
# inlinepatterns.HTML_RE 태그 갈래의 이름 부분
_TAG_NAME = re.compile(r"</?[a-zA-Z][^<>@ ]*")
_HTML_OPENERS = ("<!--", "<?", "<![CDATA[")
_FORWARD_PATTERNS = (
    "escape", "reference", "link", "image_link", "image_reference", "short_reference", "short_image_ref",
    "autolink", "automail", "linebreak", "entity",
)
_BRACKET_PATTERNS = ("reference", "link", "image_link", "image_reference", "short_reference", "short_image_ref")
# 짝을 이만큼 훑어도 못 찾으면 문자열 전체의 짝을 한 번에 계산해 둔다
_SCAN_BUDGET = 64


def _closer(lengths, nearest, limit):
    at = bisect.bisect_right(lengths, limit)
    if at == 0:
        return None
    size = lengths[at - 1]
    return size, nearest[size]


def _code_span_closers(runs):
    """백틱 묶음마다 여는 길이가 묶음 길이 L일 때와 L-1일 때 닫는 (길이, 묶음 번호)를 구한다.

    원래 정규식은 여는 길이를 L부터 줄여 가며, 뒤에 그 길이와 정확히 같은 묶음이 처음 나오는 곳에서 닫는다.
    """
    lengths = []
    nearest = {}
    closers = [None] * len(runs)
    for i in range(len(runs) - 1, -1, -1):
        start, end = runs[i]
        size = end - start
        closers[i] = (_closer(lengths, nearest, size), _closer(lengths, nearest, size - 1))
        if size not in nearest:
            bisect.insort(lengths, size)
        nearest[size] = i
    return closers


def _scan_brackets(data, index, budget=None):
    depth = 1
    for count, m in enumerate(_BRACKET.finditer(data, index)):
        if count == budget:
            return None
        depth += 1 if m.group() == "[" else -1
        if depth == 0:
            return data[index:m.start()], m.end(), True
    return data[index:], len(data), False


def _bracket_pairs(data):
    closes = {}
    opens = []
    for m in _BRACKET.finditer(data):
        if m.group() == "[":
            opens.append(m.start())
        elif opens:
            closes[opens.pop()] = m.start()
    return closes


def _tag_may_close(data, at):
    """at의 "<"에서 시작한 태그 시도가, 뒤의 "<"가 매치되어 사라지면 이어서 매치될 수 있는지.

    태그가 아니거나 이름 부분에 "@"가 끼면 그 자리에서 끝난다.
    """
    m = _TAG_NAME.match(data, at)
    return m is not None and not data.startswith("@", m.end())


class _BracketText:
    """LinkInlineProcessor.getText와 같은 값을 돌려주되, 같은 문자열에서는 대괄호 짝을 한 번만 계산한다."""

    def __init__(self):
        self._data = None
        self._closes = {}

    def __call__(self, data, index):
        if index == 0 or data[index - 1] != "[":
            return _scan_brackets(data, index)
        if data is not self._data:
            found = _scan_brackets(data, index, _SCAN_BUDGET)
            if found is not None:
                return found
            self._data = data
            self._closes = _bracket_pairs(data)
        close = self._closes.get(index - 1)
        if close is None:
            return data[index:], len(data), False
        return data[index:close], close + 1, True


class _LinearInlineProcessor(InlineProcessor):
    def __init__(self, md, forward, html):
        super().__init__(md)
        self.forward = forward
        self.html = html

    def _InlineProcessor__handleInline(self, data, patternIndex=0):
        if patternIndex == 0 and not isinstance(data, util.AtomicString) and self._is_backtick(self.inlinePatterns[0]):
            data = self._code_spans(self.inlinePatterns[0], data)
            patternIndex = 1
        return super()._InlineProcessor__handleInline(data, patternIndex)

    def _InlineProcessor__applyPattern(self, pattern, data, patternIndex, startIndex=0):
        if pattern is not self.html and pattern not in self.forward:
            return super()._InlineProcessor__applyPattern(pattern, data, patternIndex, startIndex)
        # 한 번 불릴 때 끝까지 처리하므로 이어 불리면(startIndex > 0) 더 찾을 것이 없다
        if startIndex or self._excluded(pattern):
            return data, False, 0
        if pattern is self.html:
            return self._apply_html(pattern, data)
        return self._apply_forward(pattern, data, patternIndex)

    def _apply_forward(self, pattern, data, patternIndex):
        regex = pattern.getCompiledRegExp()
        out = []
        pos = 0
        m = regex.search(data)
        while m:
            node, start, end = pattern.handleMatch(m, data)
            if start is None or end is None:
                m = regex.search(data, m.end())
                continue
            if end < 0:
                # getLink은 닫히지 않은 따옴표에서 -1을 돌려주고, 원래 처리기는 그대로 data[end:]를 이어 붙인다
                end += len(data)
            m = regex.search(data, max(end, m.end()))
            if node is None:
                continue
            self._handle_children(node, patternIndex)
            out += [data[pos:start], self._InlineProcessor__stashNode(node, pattern.type())]
            pos = end
        if not out:
            return data, False, 0
        out.append(data[pos:])
        data = "".join(out)
        return data, True, len(data) + 1

    def _apply_html(self, pattern, data):
        """원래처럼 매치마다 처음부터 다시 훑은 결과를 만들되, 다시 매치될 수 있는 가장 이른 자리부터만 훑는다.

        앞에서 실패한 자리가 다시 매치되려면 그 시도가 이번 매치 자리까지 읽었어야 한다. 태그 시도는 다음 "<"·">"에서
        멈추므로 후보는 마지막 "<" 하나이고, 주석·PI·CDATA 시도는 다음 여는 표시에서 멈추므로 각 종류의 마지막 여는
        자리다(태그 안에는 "<"가 없어 태그 매치는 이들을 멈추게 하지 못한다). 후보가 없으면 매치 끝에서 이어 훑고,
        있을 때만 지금까지의 문자열을 합쳐 그 자리부터 다시 찾는다.
        """
        regex = pattern.getCompiledRegExp()
        # 현재 문자열 = "".join(out) + data[pos:]. marks는 훑은 구간의 "<"·">"의 현재 문자열 위치와, 그 "<"가
        # 태그로 다시 매치될 수 있는지다. 뒤 매치가 덮으면 꺼낸다
        out = []
        out_len = 0
        pos = 0
        marks = []
        scanned = 0
        start = 0
        matched = False
        while m := regex.search(data, start):
            s = m.start()
            here = out_len + s - pos
            if s >= scanned:
                for t in _ANGLE.finditer(data, scanned, s):
                    may_close = t.group() == "<" and _tag_may_close(data, t.start())
                    marks.append((out_len + t.start() - pos, may_close))
            else:
                while marks and marks[-1][0] >= here:
                    marks.pop()
            node, _, end = pattern.handleMatch(m, data)
            placeholder = self._InlineProcessor__stashNode(node, pattern.type())
            out += [data[pos:s], placeholder]
            out_len = here + len(placeholder)
            matched = True
            pos = scanned = start = end
            again = [marks[-1][0]] if marks and marks[-1][1] else []
            if m.group().startswith(("<!", "<?")):
                head = "".join(out)
                again += [at for at in (head.rfind(opener, 0, here) for opener in _HTML_OPENERS) if at != -1]
            if again:
                data = "".join(out) + data[pos:]
                out, pos, scanned, start = [], 0, out_len, min(again)
                out_len = 0
        if not matched:
            return data, False, 0
        out.append(data[pos:])
        data = "".join(out)
        return data, True, len(data) + 1

    def _handle_children(self, node, patternIndex):
        """원래 __applyPattern처럼 매치가 만든 요소의 글과 꼬리에 나머지 패턴을 적용한다."""
        if isinstance(node, str) or isinstance(node.text, util.AtomicString):
            return
        for child in [node, *node]:
            if child.text:
                self.ancestors.append(child.tag.lower())
                child.text = self._InlineProcessor__handleInline(child.text, patternIndex + 1)
                self.ancestors.pop()
            if child.tail:
                child.tail = self._InlineProcessor__handleInline(child.tail, patternIndex)

    def _excluded(self, pattern):
        return any(tag.lower() in self.ancestors for tag in pattern.ANCESTOR_EXCLUDES)

    def _is_backtick(self, pattern):
        return (
            type(pattern) is inlinepatterns.BacktickInlineProcessor
            and pattern.compiled_re.pattern == inlinepatterns.BACKTICK_RE
            and not self._excluded(pattern)
        )

    def _code_spans(self, pattern, data):
        """원래 패턴이 매치마다 처음부터 다시 훑어 얻는 결과를 앞으로만 훑어 만든다.

        다시 훑어도 결과가 달라지는 곳은 하나다. 이스케이프된 묶음은 둘째 백틱부터 여므로, 매치되면 첫 백틱이
        길이 1인 묶음으로 남아 앞에서 닫히지 못한 가장 이른 여는 자리를 닫는다.
        """
        runs = [m.span() for m in _BACKTICK_RUN.finditer(data)]
        if not runs:
            return data
        closers = _code_span_closers(runs)

        def code(text):
            el = etree.Element(pattern.tag)
            el.text = util.AtomicString(util.code_escape(text.strip()))
            return self._InlineProcessor__stashNode(el, pattern.type())

        out = []
        pos = 0
        pending = None
        i = 0
        while i < len(runs):
            start, end = runs[i]
            slash = start
            while slash > pos and data[slash - 1] == "\\":
                slash -= 1
            escaped = (start - slash) % 2
            if start > slash and not escaped:
                # 짝수 개 백슬래시는 그 자체로 매치되고, 뒤 백틱 묶음은 이스케이프되지 않는다
                node = data[slash:start].replace("\\\\", pattern.ESCAPED_BSLASH)
                out += [data[pos:slash], self._InlineProcessor__stashNode(node, pattern.type())]
                pos = start
            opener = start + escaped
            closer = closers[i][escaped]
            if closer is None:
                if pending is None and end > opener:
                    out.append(data[pos:opener])
                    pos = opener
                    pending = len(out)
                i += 1
                continue
            size, j = closer
            out += [data[pos:opener], code(data[opener + size:runs[j][0]])]
            pos = runs[j][1]
            i = j + 1
            if escaped and pending is not None:
                wrapped = "".join(out[pending:-1])
                out[pending:-1] = [code(wrapped[1:-1])]
                pending = None
        out.append(data[pos:])
        return "".join(out)


class LinearInlineExtension(markdown.Extension):
    def extendMarkdown(self, md):
        forward = {md.inlinePatterns[name] for name in _FORWARD_PATTERNS if name in md.inlinePatterns}
        html = md.inlinePatterns["html"] if "html" in md.inlinePatterns else None
        if not (type(html) is inlinepatterns.HtmlInlineProcessor and html.compiled_re.pattern == inlinepatterns.HTML_RE):
            html = None
        md.treeprocessors.register(_LinearInlineProcessor(md, forward, html), "inline", 20)
        get_text = _BracketText()
        for name in _BRACKET_PATTERNS:
            if name in md.inlinePatterns:
                md.inlinePatterns[name].getText = get_text
