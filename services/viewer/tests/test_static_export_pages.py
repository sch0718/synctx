"""정적 내보내기 페이지 후처리: 출력 경로 변환, 정적 모드 주입, 숨김 선택자 계약."""

import re
from html.parser import HTMLParser
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient

from static_export import pages

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def walk(self):
        for child in self.children:
            yield child
            yield from child.walk()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [], None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent


def parse_dom(html):
    builder = _Builder()
    builder.feed(html)
    return builder.root


def _split_compound(text):
    return re.findall(r"(?:[\w-]+|[.#][\w-]+|\[[^\]]+\]|:has\((?:[^()]|\([^()]*\))*\))", text.strip())


def _attr_match(node, body):
    m = re.fullmatch(r'([\w-]+)(?:([*^$]?=)"([^"]*)")?', body)
    name, op, value = m.groups()
    actual = node.attrs.get(name)
    if actual is None:
        return False
    if op is None:
        return True
    return {"=": actual == value, "*=": value in actual, "^=": actual.startswith(value), "$=": actual.endswith(value)}[op]


def _compound_match(node, text):
    for part in _split_compound(text):
        if part.startswith(".") and part[1:] not in (node.attrs.get("class") or "").split():
            return False
        if part.startswith("#") and node.attrs.get("id") != part[1:]:
            return False
        if part.startswith("[") and not _attr_match(node, part[1:-1]):
            return False
        if part.startswith(":has("):
            inner = part[5:-1]
            if not any(_compound_match(n, inner) for n in node.walk()):
                return False
        if re.fullmatch(r"[\w-]+", part) and node.tag != part:
            return False
    return True


def select_exists(root, selector):
    """HIDE_SELECTORS가 쓰는 부분 집합: 복합 선택자, 자손 결합자, :has()."""
    chain = re.split(r"\s+(?![^()]*\))", selector.strip())
    candidates = list(root.walk())
    for node in candidates:
        if not _compound_match(node, chain[-1]):
            continue
        cur, ok = node, True
        for part in reversed(chain[:-1]):
            cur = cur.parent
            while cur is not None and cur.tag != "#root" and not _compound_match(cur, part):
                cur = cur.parent
            if cur is None or cur.tag == "#root":
                ok = False
                break
        if ok:
            return True
    return False


def test_select_exists_helper_semantics():
    dom = parse_dom('<div class="a"><label class="f"><select data-action="branch"></select></label><p class="x"></p></div>')
    assert select_exists(dom, ".a .x")
    assert select_exists(dom, '.f:has(select[data-action="branch"])')
    assert not select_exists(dom, '.f:has(select[data-action="theme"])')
    assert not select_exists(dom, ".b .x")


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    from synctx_common.config import SynctxConfig, ViewerConfig, AuthzConfig, RepoProfile, RepoAuthConfig, CollabRepoConfig
    cfg = SynctxConfig(
        authz=AuthzConfig(enabled=False),
        viewer=ViewerConfig(max_render_size_bytes=100_000, cdn_base_url="/cdn"),
        repositories=[RepoProfile(id="r", name="T", url="https://git.test/r.git", default_ref="main",
                                  auth=RepoAuthConfig(type="none"), collab=CollabRepoConfig(enabled=True))],
    )
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "config", cfg)
    manager = MagicMock()
    manager.profile = cfg.repositories[0]
    manager.file_size.return_value = 100
    monkeypatch.setattr(viewer_app, "repo_managers", {"r": manager})


@pytest.fixture
def manager():
    import app as viewer_app
    return viewer_app.repo_managers["r"]


@pytest.fixture
def client():
    import app as viewer_app
    return TestClient(viewer_app.app, raise_server_exceptions=True)


def meeting_doc():
    fm = {"synctx": {"type": "meeting", "meeting": {
        "id": "m-1", "title": "가상 회의", "starts_at": "2026-10-08T14:00+09:00", "channel": "team"}}}
    return "---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n# 가상 회의\n\n본문\n"


def doc_html(client, manager, text="# 제목\n\n본문 문단\n", path="a.md"):
    manager.read_file_text.return_value = text
    resp = client.get(f"/r/{path}")
    assert resp.status_code == 200
    return resp.text


class TestOutputPath:
    def test_output_path_encodes_korean_space_hash(self):
        assert pages.page_output_path("/view/r/docs/한글.md") == "view/r/docs/한글.md/index.html"
        assert pages.page_output_path("/view/r/docs/%ED%95%9C%EA%B8%80%20a%23b%3F.md") == "view/r/docs/한글 a#b?.md/index.html"
        assert pages.page_output_path("/view/") == "view/index.html"
        assert pages.page_output_path("/view/r") == "view/r/index.html"

    @pytest.mark.parametrize("bad", [
        "/view/r/../../etc/passwd", "/view/r/%2e%2e/%2e%2e/etc", "/view/r/a%2F..%2Fb", "view/r/a.md",
        "//etc/passwd", "/view/r/a%00b.md", "/view/r/a\x00b.md", "/view//r", "",
    ])
    def test_output_path_rejects_traversal(self, bad):
        with pytest.raises(ValueError):
            pages.page_output_path(bad)

    def test_output_path_stays_inside_dist(self, tmp_path):
        out = (tmp_path / pages.page_output_path("/view/r/a/b.md")).resolve()
        assert tmp_path.resolve() in out.parents


class TestFinalize:
    def test_finalize_injects_once(self, client, manager):
        html = doc_html(client, manager)
        once = pages.finalize_page(html, default_theme="corporate")
        twice = pages.finalize_page(once, default_theme="corporate")
        assert once == twice
        assert once.count('id="static-hide"') == 1
        assert once.count('id="theme-css"') == 1
        assert once.count("static-mode.js") == 1
        assert once.count("page-static") == 1

    def test_finalize_places_parts(self, client, manager):
        out = pages.finalize_page(doc_html(client, manager), default_theme="corporate")
        head, body = out.split("</head>", 1)
        assert 'id="static-hide"' in head and 'id="theme-css"' in head
        assert '/view/static/themes/corporate.css' in head
        assert re.search(r'<body class="[^"]*\bpage-doc\b[^"]*\bpage-static\b', body)
        assert body.rstrip().endswith('<script src="/view/static/static-mode.js"></script>\n</body>\n</html>') or \
            re.search(r'static-mode\.js"></script>\s*</body>', body)

    def test_finalize_adds_class_when_body_has_none(self):
        out = pages.finalize_page("<html><head></head><body><p>x</p></body></html>", default_theme="corporate")
        assert '<body class="page-static">' in out

    def test_finalize_keeps_body_content_byte_identical(self, client, manager):
        html = doc_html(client, manager, text="# 한글 제목\n\n```python\nprint('</body>')\n```\n\n`</head>` 표 | a\n")
        out = pages.finalize_page(html, default_theme="corporate")
        pattern = re.compile(r'<article class="markdown-body">.*?</article>', re.DOTALL)
        assert pattern.search(html).group(0) == pattern.search(out).group(0)
        assert out.count("<article") == 1

    def test_finalize_ignores_end_tags_inside_script_and_comments(self):
        html = ('<html><head><title>t</title></head><body><script>var s="</body></head>";</script>'
                '<!-- </body> --><p>x</p></body></html>')
        out = pages.finalize_page(html, default_theme="corporate")
        assert out.count("static-mode.js") == 1
        assert out.index("static-mode.js") > out.index("<p>x</p>")
        assert out.index('id="static-hide"') < out.index("<title>") + 200

    def test_finalize_rejects_html_without_head_or_body(self):
        with pytest.raises(ValueError):
            pages.finalize_page("<p>fragment</p>", default_theme="corporate")


class TestHideSelectorsContract:
    def test_hide_selectors_is_tuple_of_strings(self):
        assert isinstance(pages.HIDE_SELECTORS, tuple) and pages.HIDE_SELECTORS
        assert all(isinstance(s, str) and s for s in pages.HIDE_SELECTORS)
        assert len(set(pages.HIDE_SELECTORS)) == len(pages.HIDE_SELECTORS)

    def test_hide_selectors_exist_in_rendered_viewer_html(self, client, manager, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app.config.marp, "publish_repo", "https://git.test/slides.git")
        repo_page = client.get("/r").text
        plain = doc_html(client, manager)
        meeting = doc_html(client, manager, text=meeting_doc(), path="m.md")
        marp = doc_html(client, manager, text="---\nmarp: true\n---\n# 슬라이드\n", path="s.md")
        doms = [parse_dom(h) for h in (repo_page, plain, meeting, marp)]
        missing = [s for s in pages.HIDE_SELECTORS if not any(select_exists(d, s) for d in doms)]
        assert missing == [], f"Viewer 마크업이 바뀌어 숨김 선택자가 맞지 않는다: {missing}"

    def test_hide_selectors_do_not_hide_theme_or_repo_controls(self, client, manager):
        dom = parse_dom(doc_html(client, manager))
        for kept in ('select[data-action="theme"]', 'select[data-action="repo"]', "#file-tree", ".markdown-body"):
            assert select_exists(dom, kept)
        for selector in pages.HIDE_SELECTORS:
            assert 'data-action="theme"' not in selector.replace(':has(select[data-action="branch"])', "")
            assert 'data-action="repo"' not in selector

    def test_static_hide_style_lists_every_selector(self, client, manager):
        out = pages.finalize_page(doc_html(client, manager), default_theme="corporate")
        style = re.search(r'<style id="static-hide">(.*?)</style>', out, re.DOTALL).group(1)
        for selector in pages.HIDE_SELECTORS:
            assert selector in style
        assert "display:none" in style.replace(" ", "")
