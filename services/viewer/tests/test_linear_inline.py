import random

import markdown
import pytest
from markdown.extensions.toc import TocExtension

import renderer
from linear_inline import LinearInlineExtension


def _convert(text, *extra):
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            TocExtension(slugify=renderer.github_slugify, toc_depth="1-4"),
            "sane_lists",
            "md_in_html",
            *extra,
        ],
        output_format="html5",
    )
    return md.convert(text), md.toc_tokens


def _assert_same_as_python_markdown(text):
    assert _convert(text, LinearInlineExtension()) == _convert(text), repr(text)


@pytest.mark.parametrize("text", [
    "``a`",
    "x ``` `a` ``",
    "``x`` ``` y``",
    "\\\\`a`",
    "\\``a`",
    "\\\\\\`a`",
    "`a\\```b```",
    "` a ``x`` \\```b```",
    "**a*b*",
    "[a [b](c) d](e)",
    '[]("(',
    'p [a]("x [b](c)',
    "[r] and [r][] and ![i][r]\n\n[r]: /u",
    "<b>x</b> &amp; &#35; <!-- c -->",
    "<a <b>>",
    "List<List<Integer>> g",
    '<span title="<b>">x</span> <i>y</i>',
    "x<y <b>z</b> <!-- a <!-- b --> c --> <? p <!-- q ?> -->",
    "\\[x\\] \\` a\\b",
    "<http://e.com> <a@b.c>",
    "a  \nb",
    "[" * 100 + "x](y)" + "]" * 50,
    "![" * 80 + "i](s)",
    "## `a` [x](y)\n\n- `b` <i>c</i>\n",
], ids=lambda text: repr(text)[:40])
def test_matches_python_markdown(text):
    _assert_same_as_python_markdown(text)


@pytest.mark.parametrize("tokens", [
    list("`\\a [](") + ["``", "```", "\\\\", "\n", "\n\n"],
    list("<>a \"/") + ["<a ", "<b>", "</b>", "x<y", "<!--", "-->", "<?", "?>", "<![CDATA[", "]]>", "`", "\n"],
], ids=["backticks-brackets", "raw-html"])
def test_matches_python_markdown_on_random_input(tokens):
    rnd = random.Random(0)
    for _ in range(2000):
        _assert_same_as_python_markdown("".join(rnd.choice(tokens) for _ in range(rnd.randint(1, 60))))


@pytest.mark.parametrize("text,expected", [
    ("[a [x](b]c)](d)", '<p>[a <a href="b]c">x</a>](d)</p>'),
])
def test_failed_opener_is_not_revived_through_later_placeholder(text, expected):
    assert _convert(text, LinearInlineExtension())[0] == expected
