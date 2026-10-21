import re

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
