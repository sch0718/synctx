import re
import time

import pytest

import renderer


def _ids(r, level):
    return re.findall(rf'<h{level} id="([^"]*)"', r["html"])


def test_toc_ids_match_body_heading_ids():
    r = renderer.render_markdown("## A & B\n\n## Same\n\n## Same\n\n## 안건 1\n")
    assert [t["id"] for t in r["toc"]] == _ids(r, 2) == ["a-amp-b", "same", "same-1", "안건-1"]


def test_nested_toc_ids_match_body():
    r = renderer.render_markdown("# T\n\n## A & B\n\n### Same\n\n## Same\n")
    top = r["toc"][0]
    assert top["id"] == _ids(r, 1)[0]
    assert [c["id"] for c in top["children"]] == _ids(r, 2)
    assert top["children"][0]["children"][0]["id"] == _ids(r, 3)[0]


def test_raw_html_heading_does_not_shift_toc_ids():
    r = renderer.render_markdown("<h2>Same</h2>\n\n## Same\n\n## Same\n")
    assert _ids(r, 2) == ["same", "same-1", "same-2"]
    assert [t["id"] for t in r["toc"]] == ["same-1", "same-2"]


@pytest.mark.parametrize("text,toc,body", [
    ("## Same\n\n<h2 id=\"same\">Other</h2>\n", ["same"], ["same", "other"]),
    ("<h2 id=\"same\">Other</h2>\n\n## Same\n", ["same"], ["other", "same"]),
])
def test_raw_html_heading_with_same_id_does_not_steal_toc_link(text, toc, body):
    r = renderer.render_markdown(text)
    assert [t["id"] for t in r["toc"]] == toc and _ids(r, 2) == body
    assert "data-synctx-md" not in r["html"]


def test_toc_marker_links_use_body_ids():
    r = renderer.render_markdown("[TOC]\n\n## A & B\n\n## Same\n\n## Same\n")
    toc_div = re.search(r'<div class="toc">(.*?)</div>', r["html"], re.S).group(1)
    assert re.findall(r'href="#([^"]*)"', toc_div) == _ids(r, 2) == ["a-amp-b", "same", "same-1"]


def test_heading_keeps_author_attributes():
    r = renderer.render_markdown('<h2 hidden class="note">Secret</h2>\n\n## Open\n')
    assert '<h2 id="secret" hidden class="note">Secret</h2>' in r["html"]
    assert '<h2 id="open">Open</h2>' in r["html"]
    assert "data-synctx-md" not in r["html"]


def test_heading_replaces_author_id_with_slug():
    r = renderer.render_markdown("<h2 id='custom' class=\"x\">Title</h2>\n")
    assert '<h2 id="title" class="x">Title</h2>' in r["html"]


def test_toc_covers_md_in_html_and_skips_h5():
    r = renderer.render_markdown('<div markdown="1">\n\n## In Div\n\n</div>\n\n##### Deep\n\n## After\n')
    assert [t["id"] for t in r["toc"]] == ["in-div", "after"]
    assert _ids(r, 2) == ["in-div", "after"]


@pytest.mark.parametrize("attrs,kept", [
    ('title="Set id=example"', 'title="Set id=example"'),
    ("title='x data-synctx-md=1' hidden", "title='x data-synctx-md=1' hidden"),
    ('data-id="k" id=raw class=c', 'data-id="k" class=c'),
])
def test_heading_attribute_values_are_not_mistaken_for_attributes(attrs, kept):
    r = renderer.render_markdown(f"<h2 {attrs}>Title</h2>\n\n## Next\n")
    assert f'<h2 id="title" {kept}>Title</h2>' in r["html"]
    assert '<h2 id="next">Next</h2>' in r["html"]


@pytest.mark.parametrize("text", [
    '<h2 class="a"' + " " * 50000 + ">T</h2>\n",
    '<h2 class="a"' + " " * 50000 + '"x">T</h2>\n',
    '<div class="toc"></DIV>\n' * 10000,
])
def test_heading_and_toc_rewrites_stay_linear(text):
    started = time.monotonic()
    renderer.render_markdown(text)
    assert time.monotonic() - started < 1.0
