import time
import pytest
import evidence as ev
import renderer
from synctx_common.frontmatter import parse_frontmatter

def _body(doc):
    return parse_frontmatter(doc)[1]

def _best(fn, arg, runs=5):
    """여러 번 재어 가장 짧은 시간을 돌려준다(한 번 잰 값의 잡음을 줄인다)."""
    best = float("inf")
    for _ in range(runs):
        t0 = time.monotonic(); fn(arg); best = min(best, time.monotonic() - t0)
    return best

DOC = """---
title: 전자금융감독규정
---
# 전자금융감독규정 (발췌)

머리말 한 줄

## 제1장 총칙

<a id="efsr-8-3"></a>
**제8조의3(정보처리시스템)** 본문 가

① 항 하나

**주의** 일반 굵은 문장은 경계가 아니다

**제9조(다음 조)** 다음

<a id="a10"></a>**제10조(같은 줄 앵커)** 같은 줄 본문

**제11조(그다음)** 끝

## 같은 제목

첫째

## 같은 제목

둘째

## 표 절

| 구분 | 내용 |
|---|---|
| <a id="c3-1-4"></a>3-1-4 | 행 내용 |
| 3-1-5 | 다른 행 |

## 해설 시험

<div class="commentary" markdown="1">
해설블록문구 <div>중첩안쪽</div> 끝
</div>
원문 계속 [링크](x.md) ![그림](a.png)
"""

def test_doc_name_prefers_front_matter_title():
    assert ev.doc_name(DOC, "a/규정.md") == "전자금융감독규정"
    assert ev.doc_name("# 제목 *강조*\n본문", "a/b.md") == "제목 강조"
    assert ev.doc_name("본문만", "a/파일 이름.md") == "파일 이름"

def test_extract_explicit_anchor_article_until_next_article():
    label, md = ev.extract(_body(DOC), "efsr-8-3")
    assert label == "제8조의3(정보처리시스템)"
    assert "① 항 하나" in md and "일반 굵은 문장" in md and "제9조" not in md and "efsr-8-3" not in md

def test_extract_same_line_anchor_is_article():
    label, md = ev.extract(_body(DOC), "a10")
    assert label == "제10조(같은 줄 앵커)" and "같은 줄 본문" in md and "제11조" not in md and "a10" not in md

def test_extract_heading_slug_with_duplicate_suffix():
    assert ev.extract(_body(DOC), "같은-제목-1")[1].strip().splitlines()[-1] == "둘째"
    assert "둘째" not in ev.extract(_body(DOC), "같은-제목")[1]

def test_extract_table_row_keeps_header():
    label, md = ev.extract(_body(DOC), "c3-1-4")
    assert label == "표: 3-1-4"
    assert md.splitlines() == ["| 구분 | 내용 |", "|---|---|", "| 3-1-4 | 행 내용 |"]

def test_extract_without_anchor_is_doc_start_14_lines_max():
    label, md = ev.extract(_body(DOC), "")
    assert label == ev.DOC_START and "머리말 한 줄" in md and len(md.splitlines()) <= ev.START_LINES

def test_extract_missing_anchor_falls_back_with_notice():
    label, _ = ev.extract(_body(DOC), "없는-앵커")
    assert label == "문서 첫 부분 (‘#없는-앵커’를 찾지 못해 문서 첫 부분을 보입니다)"

def test_extract_percent_encoded_korean_anchor():
    assert ev.extract(_body(DOC), "%EA%B0%99%EC%9D%80-%EC%A0%9C%EB%AA%A9")[0] == "같은 제목"

def test_commentary_removed_and_links_images_cleaned():
    label, md = ev.extract(_body(DOC), "해설-시험")
    assert label == "해설 시험"
    assert "해설블록문구" not in md and "중첩안쪽" not in md
    assert "원문 계속 링크 [그림: 그림]" in md

@pytest.mark.parametrize("body", [
    "## A & B\n\n```\n## 가짜\n```\n\n<h2>Same</h2>\n\n## Same\n\n## `코드` *강조*\n",
    "## [범위][r]\n\n[r]: http://x\n\nSame\n====\n\n## Same\n\n    ## 들여쓴 코드\n",
])
def test_heading_ids_match_renderer(body):
    import re
    rendered = renderer.render_markdown(body)["html"]
    assert [i for _, i in ev.heading_ids(body)] == re.findall(r'<h\d id="([^"]*)"', rendered)

def test_extract_finds_reference_link_and_setext_headings():
    body = "## [범위][r]\n\n범위 본문\n\n[r]: http://x\n\nSame\n====\n\nsetext 본문\n\n## Same\n\n뒤 본문\n"
    assert "범위 본문" in ev.extract(body, "범위")[1]
    assert "뒤 본문" in ev.extract(body, "same-1")[1] and "setext 본문" not in ev.extract(body, "same-1")[1]

def test_heading_ids_count_mismatch_falls_back():
    body = "<div>\n## 원시 블록 안\n</div>\n\n## 진짜\n"
    assert ev.heading_ids(body) == []  # 원문 제목 줄 2개, 렌더된 제목 1개 → 짝짓지 않는다
    assert ev.extract(body, "진짜")[0].startswith("문서 첫 부분")

def test_extract_heading_skips_boundaries_before_first_content():
    body = '## 규정\n\n<a id="a1"></a>\n**제1조(목적)** 본문 가\n\n<a id="a2"></a>\n**제2조(정의)** 본문 나\n\n## 다음\n'
    label, md = ev.extract(body, "규정")
    assert label == "규정" and "제1조(목적)" in md and "본문 가" in md
    assert "제2조" not in md and "a1" not in md
    assert ev.extract("## 절\n\n---\n\n내용\n\n---\n\n뒤\n", "절")[1] == "---\n\n내용"
    assert ev.extract("## 절\n\n<a id=\"x\"></a>\n## 다음\n본문\n", "절")[1] == ""  # 같은 레벨 제목은 곧바로 끝

def test_extract_heading_stops_at_anchor_line_after_content():
    body = '## 절\n\n첫 문단\n\n<a id="a1"></a>\n**제1조(목적)** 본문\n'
    assert ev.extract(body, "절") == ("절", "첫 문단")

def test_extract_explicit_anchor_ignores_ids_outside_html_tags():
    body = ("# 문서\n\n## 설명\n\n앵커는 `id=\"a1\"` 처럼 씁니다. id=\"a1\" 글로도 씁니다.\n\n"
            "예: `<a id=\"a1\"></a>`\n\n## 조문\n\n<a id=\"a1\"></a>\n**제1조(목적)** 진짜 조문\n")
    label, md = ev.extract(body, "a1")
    assert label == "제1조(목적)" and "진짜 조문" in md
    assert ev.extract("`` `<a id='a2'></a>` `` 코드\n\n<h3 class=x id='a2'>원시 제목</h3>\n본문\n", "a2")[0] == "원시 제목"

@pytest.mark.parametrize("make", [
    lambda n: "".join("`" * (k % 50 + 1) + "x" for k in range(n)) + " <a id='z'></a>",
    lambda n: "<a " * n,
    lambda n: '<a title="x" ' * n,
    lambda n: "<a id='x'>" * n,
    lambda n: "<!-- <a id='x'> --> " * n,
    lambda n: "<!--\n" + "<a id='x'> -- > <!--\n" * n,
    lambda n: "".join(" " * (4 * (k % 100)) + "- x\n\n" + " " * (4 * (k % 100) + 4) + "y\n" for k in range(n)),
])
def test_explicit_id_index_is_linear(make):
    a, b = _best(ev.Prepared, make(5000)), _best(ev.Prepared, make(20000))
    assert b < 1.0 and b < max(a, 0.002) * 12

def test_extract_explicit_anchor_ignores_html_comments():
    body = ('# 문서\n\n<!-- 예: <a id="a1"></a> -->\n주석 뒤 무관한 문단\n\n'
            '<!--\n여러 줄 주석 <a id="a1"></a>\n-->\n\n## 조문\n\n<a id="a1"></a>\n**제1조(목적)** 진짜\n')
    label, md = ev.extract(body, "a1")
    assert label == "제1조(목적)" and "진짜" in md
    code = '`<!--` 는 주석 시작\n\n    <!--\n\n<a id="b"></a>\n**제2조(코드 뒤)** 보임\n'
    assert ev.extract(code, "b")[0] == "제2조(코드 뒤)"  # 코드 안의 주석 표시는 주석을 열지 않는다
    unclosed = '<!-- 닫지 않은 주석\n\n<a id="c"></a>\n**제3조(숨음)** 본문\n'
    assert ev.extract(unclosed, "c")[0].startswith("문서 첫 부분")

def test_extract_indented_code_commentary_example_is_not_commentary():
    body = '## 예시\n\n해설은 이렇게 씁니다:\n\n    <div class="commentary">\n\n정상 본문\n\n## 다음\n'
    assert ev.extract(body, "예시")[1] == '해설은 이렇게 씁니다:\n\n    <div class="commentary">\n\n정상 본문'
    article = '<a id="a1"></a>\n**제1조(목적)** 본문\n\n    <div class="commentary">\n\n**제2조(다음)** 끝\n'
    assert "제2조" not in ev.extract(article, "a1")[1]  # 들여쓴 예시 뒤 조문 경계도 그대로

def test_extract_strips_commentary_in_list_continuation():
    body = '## 목록\n\n1. 항목\n\n    <div class="commentary">\n    사내 해설 비밀\n    </div>\n\n    계속'
    md = ev.extract(body, "목록")[1]
    assert "사내 해설 비밀" not in md and "계속" in md
    # 목록 안 코드 블록은 Python-Markdown처럼 8칸 들여쓰기다
    nested = '## 목록\n\n- 항목\n\n        <div class="commentary">\n\n    계속 [링크](x.md)\n'
    assert ev.extract(nested, "목록")[1] == '- 항목\n\n        <div class="commentary">\n\n    계속 링크'

_SECRET = '<div class="commentary">\n{0}사내 해설 비밀\n{0}</div>\n\n{0}뒤 본문\n'

@pytest.mark.parametrize("head, pad", [
    ("- a\nlazy\n\n", "    "),                 # 느슨한 이어쓰기 뒤 문단
    ("- a\n    - b\n\n", "        "),          # 빈 줄 없는 중첩 항목의 문단
    ("- a\n\n    - b\n\n", "        "),      # 빈 줄 뒤 중첩 항목의 문단
    ("* a\n\n", "    "),
    ("+ a\n\n", "    "),
    ("1. a\n\n", "    "),
    ("1. a\nlazy\n\n", "    "),
    ("1. a\n    - b\n\n", "        "),
    ("- a\n    1. b\n        - c\n\n", "            "),
    ("1) a\n\n", "    "),                       # 렌더러는 목록으로 보지 않지만 확실하지 않으면 해설로 뺀다
])
def test_extract_strips_commentary_in_lazy_and_nested_lists(head, pad):
    body = "## 목록\n\n" + head + pad + _SECRET.format(pad)
    md = ev.extract(body, "목록")[1]
    assert "사내 해설 비밀" not in md and "뒤 본문" in md

def test_extract_keeps_real_code_block_in_nested_list():
    body = '## 목록\n\n- a\n    - b\n\n            <div class="commentary">\n\n        뒤 [링크](x.md)\n'
    assert renderer.render_markdown(body)["html"].count("&lt;div") == 1  # 렌더러도 코드 블록으로 본다
    assert ev.extract(body, "목록")[1] == (
        '- a\n    - b\n\n            <div class="commentary">\n\n        뒤 링크')

def test_extract_explicit_anchor_decodes_html_entities():
    body = '# 문서\n\n## 서문\n\n앞\n\n<a id="scope&amp;limits"></a>\n**제1조(범위)** 진짜\n'
    label, md = ev.extract(body, "scope%26limits")
    assert label == "제1조(범위)" and "진짜" in md and "scope" not in md
    same_line = '<a id="a&#49;"></a>**제2조(같은 줄)** 본문\n'
    assert ev.extract(same_line, "a1")[0] == "제2조(같은 줄)"

def test_extract_cleanup_keeps_code_verbatim():
    body = ("## 예시\n\n링크 문법은 `[글](x.md)` 입니다.\n\n```\n[글](x.md) ![alt](a.png)\n"
            "<div class=\"commentary\">예제</div>\n```\n\n## 다음\n")
    assert ev.extract(body, "예시")[1] == (
        "링크 문법은 `[글](x.md)` 입니다.\n\n```\n[글](x.md) ![alt](a.png)\n<div class=\"commentary\">예제</div>\n```")
    crossing = '## 절\n\n<div class="commentary">\n해설\n~~~\n</div>\n~~~\n</div>\n본문 [링크](y.md)\n'
    assert ev.extract(crossing, "절")[1] == "본문 링크"  # 코드 안의 </div>로 해설이 끝나지 않는다

HTML_WRAPPER = '# 규제 조문\n\n```synctx-html\nsrc: ./규제_조문.html\nheight: 1100\ntitle: "금융권 규제 — 근거 조문"\n```\n'

def test_extract_html_embed_block_becomes_one_line():
    assert ev.extract(HTML_WRAPPER, "") == (ev.DOC_START, "[인터랙티브 HTML: 금융권 규제 — 근거 조문]")
    label, md = ev.extract(HTML_WRAPPER, "efsr-8-3")
    assert label.startswith(ev.DOC_START) and md == "[인터랙티브 HTML: 금융권 규제 — 근거 조문]"
    assert ev.extract(HTML_WRAPPER, "규제-조문")[1] == "[인터랙티브 HTML: 금융권 규제 — 근거 조문]"

def test_extract_html_embed_block_without_title_uses_src_file_name():
    body = "## 절\n\n앞 문단\n\n  ~~~~ synctx-html \n  src: ../자료/규제_조문.html?v=1#top\n  ~~~~\n\n뒤 문단\n"
    md = ev.extract(body, "절")[1]
    assert md == "앞 문단\n\n  [인터랙티브 HTML: 규제\\_조문.html]\n\n뒤 문단"
    out = ev.sanitize_excerpt(renderer.render_markdown(md)["html"])
    assert out == "<p>앞 문단</p>\n<p>[인터랙티브 HTML: 규제_조문.html]</p>\n<p>뒤 문단</p>"

def test_extract_keeps_code_and_broken_html_embed_blocks():
    nested = "## 예시\n\n````\n```synctx-html\nsrc: a.html\n```\n````\n"
    assert ev.extract(nested, "예시")[1] == "````\n```synctx-html\nsrc: a.html\n```\n````"
    unclosed = "## 절\n\n```synctx-html\nsrc: a.html\n"
    assert ev.extract(unclosed, "절")[1] == "```synctx-html\nsrc: a.html"
    no_src = "## 절\n\n```synctx-html\nheight: 10\n```\n"
    assert ev.extract(no_src, "절")[1] == "```synctx-html\nheight: 10\n```"
    bad_date = "## 절\n\n본문\n\n```synctx-html\nsrc: a.html\ntitle: 2026-02-30\n```\n"
    assert ev.extract(bad_date, "절")[1] == "본문\n\n```synctx-html\nsrc: a.html\ntitle: 2026-02-30\n```"
    hidden = '## 절\n\n<div class="commentary">\n\n```synctx-html\nsrc: a.html\n```\n\n</div>\n본문\n'
    assert ev.extract(hidden, "절")[1] == "본문"

def test_extract_html_embed_title_is_plain_text():
    body = '```synctx-html\nsrc: a.html\ntitle: "<b>굵게</b> *별* _밑_ [x](y) `c` \\\\ &amp;\n  둘째 줄"\n```\n'
    md = ev.extract(body, "")[1]
    assert "\n" not in md
    out = ev.sanitize_excerpt(renderer.render_markdown(md)["html"])
    assert out == "<p>[인터랙티브 HTML: &lt;b&gt;굵게&lt;/b&gt; *별* _밑_ [x](y) `c` \\ &amp;amp; 둘째 줄]</p>"

def test_extract_html_embed_cut_by_doc_start_limit_is_one_line():
    body = "# 제목\n\n" + "줄\n" * 10 + "\n```synctx-html\nsrc: a.html\ntitle: 끝\nheight: 5\n```\n"
    md = ev.extract(body, "")[1]
    assert md.endswith("줄\n\n[인터랙티브 HTML: 끝]") and "src" not in md

def test_extract_ignores_boundaries_inside_commentary():
    body = ('<a id="a1"></a>\n**제1조(목적)** 본문\n\n<div class="commentary">\n해설: 예를 들어\n\n'
            '**제2조(예시)** 인용\n</div>\n\n② 실제 적용 조건\n\n**제3조(다음)** 끝\n')
    label, md = ev.extract(body, "a1")
    assert label == "제1조(목적)" and "② 실제 적용 조건" in md
    assert "해설" not in md and "제2조" not in md and "제3조" not in md
    heading = ('## 진짜\n\n앞 문단\n\n<div class="commentary" markdown="1">\n## 가짜\n\n---\n<a id="x"></a>\n</div>\n\n'
               '뒤 문단\n\n## 다음\n')
    assert ev.extract(heading, "진짜") == ("진짜", "앞 문단\n\n\n\n뒤 문단")
    other = '<a id="p"></a>\n문단 시작\n<div class="commentary">\n\n\n\n</div>\n이어짐\n\n\n그 뒤\n'
    assert ev.extract(other, "p")[1] == "문단 시작\n\n이어짐"
    mention = '## 규약\n\n해설은 `<div class="commentary">`로 쓴다.\n\n## 다음\n\n뒤\n'
    assert ev.extract(mention, "규약")[1] == '해설은 `<div class="commentary">`로 쓴다.'  # 인라인 코드는 해설을 열지 않는다

def test_extract_paragraph_start_until_two_blank_lines_or_boundary():
    body = '<a id="p1"></a>\n일반 문단 시작\n이어지는 줄\n\n\n그 뒤 문단\n\n<a id="p2"></a>\n두 번째\n둘째 줄\n\n## 다음 제목\n'
    label, md = ev.extract(body, "p1")
    assert label == "일반 문단 시작" and "이어지는 줄" in md and "그 뒤 문단" not in md
    assert "다음 제목" not in ev.extract(body, "p2")[1] and "둘째 줄" in ev.extract(body, "p2")[1]

def test_extract_bold_table_caption_until_table_end():
    body = '<a id="t1"></a>\n**<표 1> 점검 항목**\n\n| 가 | 나 |\n|---|---|\n| 1 | 2 |\n\n표 뒤 문단\n'
    label, md = ev.extract(body, "t1")
    assert label == "<표 1> 점검 항목" and "| 1 | 2 |" in md and "표 뒤 문단" not in md

def test_excerpt_is_truncated_by_bytes():
    body = "## 긴 줄\n\n" + "가" * 10000 + "\n"
    md = ev.extract(body, "긴-줄")[1]
    assert md.splitlines()[-1] == ev.TRUNCATED_NOTE and len(md.encode("utf-8")) <= ev.EXCERPT_MAX_BYTES

def test_excerpt_is_truncated():
    body = "## 긴 절\n\n" + "\n".join(f"줄 {i}" for i in range(200))
    md = ev.extract(body, "긴-절")[1]
    assert md.splitlines()[-1] == ev.TRUNCATED_NOTE and len(md.splitlines()) <= ev.EXCERPT_MAX_LINES

@pytest.mark.parametrize("make", [
    lambda n: '<div class="commentary">' + "<div>" * n + "x",
    lambda n: ('<div class="commentary">a</div>\n' * n),
    lambda n: "<div class=commentary>\n" + "</div>" * n,
])
def test_strip_commentary_is_linear(make):
    small, big = make(5000), make(20000)
    t0 = time.monotonic(); ev.strip_commentary(small); a = time.monotonic() - t0
    t0 = time.monotonic(); ev.strip_commentary(big); b = time.monotonic() - t0
    assert b < 1.0 and b < max(a, 0.002) * 12

def test_sanitize_excerpt_drops_scripts_links_images_and_attrs():
    raw = ('<p onclick="x()" style="color:red">가 <a href="javascript:alert(1)">링크</a> '
           '<img src="http://ext/x.png" alt="그림"><style>p{}</style><script>alert(1)</script>'
           '<table><tr><td colspan="2" class="x">칸</td></tr></table><unknown>나</unknown></p>')
    out = ev.sanitize_excerpt(raw)
    assert out == ('<p>가 링크 [그림: 그림]<table><tr><td colspan="2">칸</td></tr></table>나</p>')

def test_sanitize_excerpt_balances_cut_raw_html_table():
    body = "# 문서\n\n<table>\n" + "".join(f"<tr><td>{i}</td></tr>\n" for i in range(30)) + "</table>\n\n## 다음\n"
    out = ev.sanitize_excerpt(renderer.render_markdown(ev.extract(body, "")[1])["html"])
    for tag in ("table", "tr", "td"):
        assert out.count(f"<{tag}>") == out.count(f"</{tag}>") > 0
    assert ev.sanitize_excerpt("<div><p>가") == "<div><p>가</p></div>"

def test_sanitize_excerpt_drops_stray_and_closes_skipped_end_tags():
    assert ev.sanitize_excerpt("가</div>나</p>") == "가나"
    assert ev.sanitize_excerpt("<div><p>가</div>나") == "<div><p>가</p></div>나"
    assert ev.sanitize_excerpt("<ul><li>가<li>나</ul>") == "<ul><li>가<li>나</li></li></ul>"

@pytest.mark.parametrize("make", [
    lambda n: "<div>" * n + "</span>" * n,
    lambda n: "<div><p>" * n + "</div>" * n,
])
def test_sanitize_excerpt_balancing_is_linear(make):
    a, b = _best(ev.sanitize_excerpt, make(5000)), _best(ev.sanitize_excerpt, make(20000))
    assert b < 1.0 and b < max(a, 0.002) * 12

def test_extract_unwraps_reference_links_defined_outside_range():
    body = "## 가\n\n[근거 링크][r] 와 [짧게][]\n\n## 나\n\n[r]: http://x\n[짧게]: http://y\n"
    md = ev.extract(body, "가")[1]
    assert "근거 링크 와 짧게" in md and "][" not in md and "http" not in md

def test_sanitize_excerpt_handles_reference_links():
    html = renderer.render_markdown("[r]: javascript:alert(2)\n\n[참조][r]\n")["html"]
    assert "javascript" not in ev.sanitize_excerpt(html) and "참조" in ev.sanitize_excerpt(html)

def test_extract_uses_given_heading_ids_callable(monkeypatch):
    calls = []
    def fail(*_):
        raise AssertionError("heading_ids/render_markdown를 다시 부르면 안 된다")
    monkeypatch.setattr(ev, "heading_ids", fail)
    monkeypatch.setattr(ev, "render_markdown", fail)
    body = _body(DOC)
    lines = body.split("\n")
    second = lines.index("## 같은 제목", lines.index("## 같은 제목") + 1)
    def given():
        calls.append(1)
        return [(second, "같은-제목-1")]
    assert ev.extract(body, "efsr-8-3", ids=given)[0] == "제8조의3(정보처리시스템)" and calls == []
    assert ev.extract(body, "같은-제목-1", ids=given)[1].strip().splitlines()[-1] == "둘째" and calls == [1]

def _read(files):
    def read(repo, path):
        if (repo, path) not in files:
            raise FileNotFoundError(path)
        return files[(repo, path)]
    return read

def _build(html, files, references=None):
    return ev.build(html, repo_id="r", path="회의/m.md", references=references or {},
                    read=_read(files), render=lambda md: f"<p>{md}</p>")

def test_split_references_keeps_known_only():
    valid, missing = ev.split_references({"../a/": "regs:x/", "../b/": "ghost:", "../c/": "ghost:y"}, {"r", "regs"})
    assert valid == {"../a/": "regs:x/"} and missing == ("ghost",)

def test_map_href_longest_prefix():
    refs = {"../": "a:", "../사내규정/": "regs:규정/"}
    assert ev.map_href("../사내규정/x.md#a1", refs) == ("regs", "규정/x.md#a1")
    assert ev.map_href("../y.md", refs) == ("a", "y.md")
    assert ev.map_href("./z.md", refs) is None

def test_build_marks_links_and_merges_same_target():
    html = ('<h2 id="s1">1. 점검</h2><p><a href="../근거/a.md#c1">가</a> <a href="../근거/a.md#c1">나</a></p>'
            '<h3 id="s2">세부</h3><p><a href="../근거/a.md">다</a> <a href="#local">안</a> <a href="https://x">밖</a></p>')
    files = {("r", "근거/a.md"): "# 규정 A\n\n<a id=\"c1\"></a>\n**제1조(목적)** 내용\n"}
    out = _build(html, files)
    assert out.html.count('data-evidence="evidence-1"') == 2 and out.html.count('data-evidence="evidence-2"') == 1
    assert '<a href="#local">' in out.html and '<a href="https://x">' in out.html
    e1, e2 = out.entries
    assert (e1.doc, e1.loc, e1.anchor, e1.cites) == ("규정 A", "제1조(목적)", "c1", (("s1", "1. 점검"),))
    assert (e2.loc, e2.anchor, e2.cites) == ("문서 첫 부분", "", (("s2", "세부"),))

def test_build_skips_missing_outside_dot_and_non_md():
    calls = []
    def read(repo, path):
        calls.append(path)
        raise FileNotFoundError(path)
    html = ('<a href="없음.md">a</a><a href="../../../밖.md">b</a><a href=".hidden/x.md">c</a>'
            '<a href="img.png">d</a><a href="m.md">self</a><a href="폴더/">e</a>')
    out = ev.build(html, repo_id="r", path="회의/m.md", references={}, read=read, render=str)
    assert out.entries == () and "data-evidence" not in out.html
    assert calls == ["회의/없음.md"]  # 판정에서 걸러진 대상은 읽지 않는다

def test_build_skip_prefix_is_not_resolved_in_same_repo():
    calls = []
    def read(repo, path):
        calls.append((repo, path))
        return "# 같은 이름 파일"
    html = '<a href="../유령/y.md">g</a><a href="../유령/깊이/z.md">h</a>'
    out = ev.build(html, repo_id="r", path="docs/m.md", references={"../유령/깊이/": "r:근거/"},
                   skip_prefixes=("../유령/",), read=read, render=str)
    assert calls == [("r", "근거/z.md")]  # 더 긴 유효 키는 이기고, 짧은 무효 키의 링크는 읽지 않는다
    assert [e.path for e in out.entries] == ["근거/z.md"]

def test_build_entry_id_avoids_body_ids():
    html = ('<h2 id="evidence-1">evidence 1</h2><h2 id="evidence-data">Evidence data</h2>'
            '<h2 id="evidence-list">Evidence list</h2><p><a href="x.md">x</a></p>')
    out = _build(html, {("r", "회의/x.md"): "# X"})
    assert out.entries[0].id == "evidence-1-2" and 'data-evidence="evidence-1-2"' in out.html
    assert out.ids == {"list": "evidence-list-2", "title": "evidence-title", "data": "evidence-data-2"}

def test_build_limits_links(monkeypatch):
    monkeypatch.setattr(ev, "MAX_LINKS", 2)
    html = "".join(f'<a href="d{i}.md">{i}</a>' for i in range(3))
    files = {("r", f"회의/d{i}.md"): f"# D{i}" for i in range(3)}
    out = _build(html, files)
    assert len(out.entries) == 2 and out.html.count("data-evidence") == 2

def test_build_merges_encoded_and_plain_anchor():
    html = '<h2 id="s">S</h2><p><a href="a.md#범위">가</a> <a href="a.md#%EB%B2%94%EC%9C%84">나</a></p>'
    out = _build(html, {("r", "회의/a.md"): "# A\n\n## 범위\n본문"})
    assert len(out.entries) == 1 and out.html.count('data-evidence="evidence-1"') == 2
    assert out.entries[0].anchor == "범위" and out.entries[0].cites == (("s", "S"),)

def test_build_cross_repo_mapping():
    html = '<a href="../사내규정/x.md#a">가</a>'
    out = _build(html, {("regs", "규정/x.md"): "# X\n\n## A\n본문"}, {"../사내규정/": "regs:규정/"})
    assert [(e.repo_id, e.path, e.anchor) for e in out.entries] == [("regs", "규정/x.md", "a")]

def test_build_limits_documents_and_reads_each_file_once(monkeypatch):
    monkeypatch.setattr(ev, "MAX_DOCS", 2)
    calls = []
    files = {("r", f"회의/d{i}.md"): f"# D{i}\n\n## A\n가\n\n## B\n나\n" for i in range(4)}
    def read(repo, path):
        calls.append(path)
        if path == "회의/bad.md":
            raise FileNotFoundError(path)
        return files[(repo, path)]
    html = ('<a href="bad.md">x</a><a href="bad.md#a">y</a>'
            + "".join(f'<a href="d{i}.md#a">{i}</a><a href="d{i}.md#b">{i}</a>' for i in range(4)))
    out = ev.build(html, repo_id="r", path="회의/m.md", references={}, read=read, render=str)
    assert calls == ["회의/bad.md", "회의/d0.md", "회의/d1.md"]  # 파일마다 한 번, 성공한 파일 2개에서 멈춘다
    assert [(e.path, e.anchor) for e in out.entries] == [
        ("회의/d0.md", "a"), ("회의/d0.md", "b"), ("회의/d1.md", "a"), ("회의/d1.md", "b")]

def test_build_ignores_fake_link_inside_attribute_value():
    html = '<p title=\'<a href="x.md">\'>본문</p><textarea><a href="x.md">t</a></textarea>'
    out = _build(html, {("r", "회의/x.md"): "# X"})
    assert out.entries == () and out.html == html

def test_build_ignores_links_inside_style_and_comments():
    html = '<style>/* <a href="x.md">가짜</a> */</style><!-- <a href="x.md">주석</a> --><p><a href="x.md">진짜</a></p>'
    out = _build(html, {("r", "회의/x.md"): "# X"})
    assert out.html.count("data-evidence") == 1
    assert out.html.startswith('<style>/* <a href="x.md">가짜</a> */</style><!-- <a href="x.md">주석</a> -->')

def test_build_masking_is_linear():
    small = "<style>" * 5000 + '<a href="x.md">x</a>'
    big = "<style>" * 20000 + '<a href="x.md">x</a>'
    t0 = time.monotonic(); _build(small, {}); a = time.monotonic() - t0
    t0 = time.monotonic(); _build(big, {}); b = time.monotonic() - t0
    assert b < 1.0 and b < max(a, 0.002) * 12

def test_build_many_anchors_in_large_document_is_linear():
    def run(n):
        filler = "".join(f"채움 줄 {k} " * 4 + "\n" for k in range(60))
        doc = "# 큰 규정\n\n" + "".join(f'<a id="a{i}"></a>\n**제{i}조(조문)** 본문\n{filler}\n' for i in range(n))
        html = "".join(f'<a href="big.md#a{i}">{i}</a>' for i in range(n))
        out = _build(html, {("r", "회의/big.md"): doc})
        assert len(out.entries) == n and out.entries[-1].loc == f"제{n - 1}조(조문)"
        return _best(lambda h: _build(h, {("r", "회의/big.md"): doc}), html, runs=3)
    a, b = run(50), run(200)  # 문서 크기와 앵커 수가 함께 4배
    assert b < 1.0 and b < max(a, 0.002) * 8

def test_extract_with_prepared_document_matches_plain_extract():
    body = _body(DOC) + "\n<p id='q\"x'>따옴표 앵커</p>\n\n```\n<a id=\"코드\"></a>\n```\n"
    prepared = ev.Prepared(body)
    for anchor in ["", "efsr-8-3", "a10", "같은-제목-1", "c3-1-4", "해설-시험", "없는-앵커", 'q"x', "코드"]:
        assert ev.extract(body, anchor, prepared=prepared) == ev.extract(body, anchor)
    assert ev.extract(body, 'q"x')[0] == "따옴표 앵커"

@pytest.mark.parametrize("make", [
    lambda n: "## A\n\n" + "`" * (n * 50) + "\n",
    lambda n: "## A\n\n" + '`a` [x](y) <div class="commentary">c</div>\n' * n,
])
def test_extract_without_rendering_is_linear(make):
    # 제목 id 렌더(Python-Markdown)는 빼고 근거 코드(줄 판정·인라인 코드·해설 표시·정리)만 잰다
    def run(body):
        assert ev.extract(body, "a", ids=lambda: [(0, "a")])[0] == "A"
    a, b = _best(run, make(4000)), _best(run, make(16000))
    assert b < 1.0 and b < max(a, 0.002) * 12

@pytest.mark.parametrize("make", [
    lambda n: "`" * (n * 50) + " <a id='z'></a>\n",
    lambda n: "`<a id='x'></a>` <a id='y'></a> ``` ` ``\n" * n,
])
def test_explicit_id_index_with_backticks_is_linear(make):
    a, b = _best(ev.Prepared, make(4000)), _best(ev.Prepared, make(16000))
    assert b < 1.0 and b < max(a, 0.002) * 12

def test_build_keeps_text_identical():
    import re
    html = '<h2 id="a">A</h2><p>앞 <a href="x.md">근거 <mark>링크</mark></a> 뒤</p>'
    out = _build(html, {("r", "회의/x.md"): "# X"})
    strip = lambda h: re.sub(r"<[^>]+>", "", h)
    assert strip(out.html) == strip(html)

def test_build_renders_each_document_headings_once(monkeypatch):
    calls = []
    real = ev.heading_ids
    def counting(body):
        calls.append(1)
        return real(body)
    monkeypatch.setattr(ev, "heading_ids", counting)
    doc = '# A\n\n## 가\n일\n\n## 나\n이\n\n<a id="c1"></a>\n**제1조(목적)** 셋\n'
    html = '<a href="a.md#가">1</a><a href="a.md#나">2</a><a href="a.md#c1">3</a><a href="a.md">4</a>'
    out = _build(html, {("r", "회의/a.md"): doc})
    assert [e.loc for e in out.entries] == ["가", "나", "제1조(목적)", "문서 첫 부분"] and calls == [1]
    calls.clear()
    out = _build('<a href="a.md#c1">1</a><a href="a.md">2</a>', {("r", "회의/a.md"): doc})
    assert len(out.entries) == 2 and calls == []

def test_build_rejects_escaping_and_unsafe_paths():
    calls = []
    def read(repo, path):
        calls.append((repo, path))
        return "# X"
    refs = {"../사내규정/": "regs:규정/"}
    html = ('<a href="../사내규정/../../x.md">a</a><a href="../사내규정/%2e%2e/%2E%2E/x.md">b</a>'
            '<a href="%2e%2e/%2e%2e/x.md">c</a><a href="../사내규정/a%5Cb.md">d</a><a href="x%00.md">e</a>'
            '<a href="../사내규정/%2Ehidden/x.md">f</a><a href="/x.md">g</a><a href="tel:x.md">h</a>'
            '<a href="x.md/">i</a><a href="x%E2%80%A8.md">j</a>')
    out = ev.build(html, repo_id="r", path="회의/m.md", references=refs, read=read, render=str)
    assert out.entries == () and out.html == html and calls == []

def test_build_mapped_path_resolves_inside_target_repo():
    out = _build('<a href="../사내규정/a/../x.md">가</a>', {("regs", "규정/x.md"): "# X"}, {"../사내규정/": "regs:규정/"})
    assert [(e.repo_id, e.path) for e in out.entries] == [("regs", "규정/x.md")]

def test_build_ignores_links_mapped_to_repo_root_or_folder():
    calls = []
    def read(repo, path):
        calls.append((repo, path))
        return "# X"
    html = '<a href="../규정/">a</a><a href="../규정/#x">b</a><a href="../파일처럼/">c</a>'
    refs = {"../규정/": "regs:", "../파일처럼/": "regs:x.md"}
    out = ev.build(html, repo_id="r", path="회의/m.md", references=refs, read=read, render=str)
    assert out.entries == () and out.html == html and calls == []

def test_build_ignores_links_inside_self_closed_raw_text_elements():
    html = ('<textarea/><a href="x.md">t</a></textarea><style/><a href="x.md">s</a></style>'
            '<noscript><a href="x.md">n</a></noscript><p><a href="x.md">진짜</a></p>')
    out = _build(html, {("r", "회의/x.md"): "# X"})
    assert out.html.count("data-evidence") == 1 and '<a href="x.md" data-evidence="evidence-1">진짜' in out.html

def test_build_inserts_before_self_closing_slash():
    html = '<a href="x.md"/><a href=x.md/>'
    out = _build(html, {("r", "회의/x.md"): "# X"})
    assert out.html == '<a href="x.md" data-evidence="evidence-1"/><a href=x.md/>'

def test_build_cites_heading_containing_link_and_dedupes():
    html = '<h2 id="h">제목 <a href="x.md">링크</a> 끝</h2><p><a href="x.md">또</a></p>'
    out = _build(html, {("r", "회의/x.md"): "# X"})
    assert out.entries[0].cites == (("h", "제목 링크 끝"),)
