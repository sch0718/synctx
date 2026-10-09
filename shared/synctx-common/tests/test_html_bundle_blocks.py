import pytest

from synctx_common.html_bundle import (
    BundleError,
    BundleResult,
    escape_html,
    find_blocks,
    parse_block,
    render_notice,
    replace_blocks,
    resolve_path,
    to_iframe,
    xwiki_html_macro,
)

MD = "# 제목\n\n```synctx-html\nsrc: ./motion/index.html\n```\n\n끝\n"


def _result(html="<p>x</p>", title=None):
    return BundleResult(html=html, sha256="s", size=len(html), files=("a",), title=title)


def test_find_blocks_returns_body_and_span():
    [block] = find_blocks(MD)
    assert block.body == "src: ./motion/index.html\n"
    assert MD[block.start:block.end] == "```synctx-html\nsrc: ./motion/index.html\n```"


def test_find_blocks_crlf():
    text = MD.replace("\n", "\r\n")
    [block] = find_blocks(text)
    assert parse_block(block.body, "docs/a.md").src == "docs/motion/index.html"
    assert text[block.end:].startswith("\r\n")


def test_find_blocks_ignores_nested_example():
    text = "````markdown\n```synctx-html\nsrc: ./x.html\n```\n````\n"
    assert find_blocks(text) == []


def test_find_blocks_ignores_other_languages_and_unclosed():
    assert find_blocks("```mermaid\ngraph TD\n```\n") == []
    assert find_blocks("```synctx-html\nsrc: a.html\n") == []


def test_replace_blocks_renders_each_in_order():
    text = MD + "\n```synctx-html\nsrc: b.html\n```\n"
    out = replace_blocks(text, lambda i, b: f"[{i}]")
    assert out == "# 제목\n\n[0]\n\n끝\n\n[1]\n"


def test_parse_block_defaults_and_resolution():
    block = parse_block("src: ./motion/index.html\n", "docs/a.md")
    assert block.src == "docs/motion/index.html"
    assert block.aspect == "16/9"
    assert block.height is None
    assert block.title is None


def test_parse_block_options():
    block = parse_block("src: m/i.html\naspect: 16/10\nheight: 600\ntitle: 모션\n", "a.md")
    assert (block.src, block.aspect, block.height, block.title) == ("m/i.html", "16/10", 600, "모션")


@pytest.mark.parametrize(
    "body",
    [
        "",
        "- not a mapping\n",
        "src: [unclosed\n",
        "aspect: 16/9\n",
        "src: a.html\nunknown: 1\n",
        "src: a.html\naspect: wide\n",
        "src: a.html\nheight: 0\n",
        "src: a.html\nheight: true\n",
        "src: https://cdn.synctx.local/a.html\n",
    ],
)
def test_parse_block_invalid(body):
    with pytest.raises(BundleError) as exc:
        parse_block(body, "docs/a.md")
    assert exc.value.reason == "invalid_block"


def test_parse_block_outside_repo():
    with pytest.raises(BundleError) as exc:
        parse_block("src: ../../etc/x.html\n", "docs/a.md")
    assert exc.value.reason == "outside_repo"


def test_resolve_path_strips_query_and_decodes():
    assert resolve_path("m/index.html", "./css/a.css?v=2#x") == "m/css/a.css"
    assert resolve_path("m/index.html", "%EC%9D%B4.png") == "m/이.png"


def test_escape_html_removes_braces():
    assert escape_html("{{/html}}\"<&'") == "&#123;&#123;/html&#125;&#125;&quot;&lt;&amp;&#x27;"


def test_to_iframe_shape_and_escaping():
    out = to_iframe(_result('<p title="a">{{/html}}{{velocity}}x{{/velocity}}</p>'), "16/10", None, 'T"{{/html}}')
    assert out.startswith('<iframe class="synctx-html" sandbox="allow-scripts" title="T&quot;&#123;&#123;/html&#125;&#125;"')
    assert 'style="width:100%;aspect-ratio:16/10;border:0"' in out
    assert "{" not in out and "}" not in out
    assert 'srcdoc="&lt;p title=&quot;a&quot;&gt;' in out
    assert out.count("sandbox=") == 1
    for banned in ("allow-same-origin", "allow-top-navigation", "allow-forms", "allow-popups"):
        assert banned not in out


def test_to_iframe_height_and_title_fallbacks():
    assert 'style="width:100%;height:600px;border:0"' in to_iframe(_result(), height=600)
    assert 'title="모션"' in to_iframe(_result(title="모션"))
    assert 'title="인터랙티브 콘텐츠"' in to_iframe(_result())


def test_render_notice_escapes_everything():
    out = render_notice("오류 {{/html}}", "docs/{{x}}/i.html", "/view/r/docs/%7B%7Bx%7D%7D/i.html")
    assert "{" not in out and "}" not in out
    assert out.startswith('<div class="synctx-html-notice" role="note">')
    assert 'href="/view/r/docs/%7B%7Bx%7D%7D/i.html"' in out


def test_xwiki_html_macro():
    assert xwiki_html_macro("<p>a</p>") == '{{html clean="false"}}<p>a</p>{{/html}}'
    with pytest.raises(ValueError):
        xwiki_html_macro("<p>{{/html}}</p>")


def test_escape_html_encodes_line_breaks():
    assert escape_html("a\nb\r\nc") == "a&#10;b&#13;&#10;c"


def test_to_iframe_and_notice_are_single_line():
    multiline = "<p>1</p>\n\n<script>\nwindow.a = 1;\n</script>\r\n"
    assert "\n" not in to_iframe(_result(multiline), title="제목\n둘째 줄")
    assert "\r" not in to_iframe(_result(multiline))
    assert "\n" not in render_notice("YAML 오류:\n  line 2", "docs/a\nb.html")
