"""Viewer API 단위 테스트."""

import json
from unittest.mock import MagicMock, patch
from pathlib import Path, PurePosixPath

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    """Viewer config 모킹."""
    from synctx_common.config import SynctxConfig, ViewerConfig, AuthzConfig, RuntimeConfig, RepoProfile, RepoAuthConfig
    mock_config = SynctxConfig(
        authz=AuthzConfig(enabled=False),
        viewer=ViewerConfig(max_render_size_bytes=1024, cdn_base_url="/cdn"),
        repositories=[
            RepoProfile(
                id="test-repo",
                name="Test Repo",
                url="https://git.test/repo.git",
                auth=RepoAuthConfig(type="none"),
                watch_paths=["docs/"],
            )
        ],
    )
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "config", mock_config)


@pytest.fixture
def mock_repo_manager():
    manager = MagicMock()
    manager.profile = MagicMock()
    manager.profile.default_ref = "main"
    manager.head_sha_value = "abc123"
    manager.status = "ready"
    manager.file_size.return_value = 100
    return manager


@pytest.fixture
def client(mock_repo_manager, monkeypatch):
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "repo_managers", {"test-repo": mock_repo_manager})
    return TestClient(viewer_app.app, raise_server_exceptions=False)


class TestHealthEndpoints:
    def test_healthz(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readyz(self, client):
        resp = client.get("/readyz")
        assert resp.status_code == 200

    def test_version(self, client):
        resp = client.get("/version")
        assert resp.status_code == 200
        assert "version" in resp.json()

    def test_metrics(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "synctx_http_requests_total" in resp.text


class TestStartupSync:
    @staticmethod
    def _manager(release, fail=False):
        import asyncio

        class SlowManager:
            status = "cloning"

            async def ensure(self):
                while not release.is_set():
                    await asyncio.sleep(0.01)
                if fail:
                    self.status = "error"
                    raise RuntimeError("clone failed")
                self.status = "ready"

        return SlowManager()

    @staticmethod
    def _wait_ready(client):
        import time
        deadline = time.time() + 5
        while client.get("/readyz").status_code != 200 and time.time() < deadline:
            time.sleep(0.05)
        return client.get("/readyz")

    @pytest.mark.parametrize("fail", [False, True])
    def test_server_answers_while_repos_sync_and_ready_after(self, monkeypatch, fail):
        import threading
        import app as viewer_app
        release = threading.Event()
        monkeypatch.setattr(viewer_app, "repo_managers", {"test-repo": self._manager(release, fail)})
        timer = threading.Timer(1.5, release.set)
        timer.start()
        try:
            with TestClient(viewer_app.app) as c:
                assert c.get("/healthz").status_code == 200
                not_ready = c.get("/readyz")
                assert not_ready.status_code == 503
                assert not_ready.json() == {"status": "not_ready"}
                release.set()
                ready = self._wait_ready(c)
                assert ready.status_code == 200
                assert ready.json() == {"status": "ready"}
        finally:
            timer.cancel()
            release.set()


class TestCSPHeaders:
    def test_html_response_has_csp(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/readme.md")
        assert resp.status_code == 200
        assert "Content-Security-Policy" in resp.headers
        assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_json_response_no_csp(self, client):
        resp = client.get("/api/repos")
        assert "Content-Security-Policy" not in resp.headers


class TestMaxRenderSize:
    def test_file_within_limit_renders(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Small file"
        resp = client.get("/test-repo/docs/small.md")
        assert resp.status_code == 200

    def test_file_exceeding_limit_returns_413(self, client, mock_repo_manager):
        mock_repo_manager.file_size.return_value = 2000
        resp = client.get("/test-repo/docs/large.md")
        assert resp.status_code == 413
        assert "text/html" in resp.headers["content-type"]
        assert "화면에 보이기에 너무 큰 파일입니다" in resp.text


class TestHTMLSanitization:
    def test_script_tags_removed(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = '# Title\n\n<script>alert("xss")</script>\n\nSafe content'
        resp = client.get("/test-repo/docs/xss.md")
        assert resp.status_code == 200
        assert '<script>alert("xss")</script>' not in resp.text
        assert "Safe content" in resp.text

    def test_sanitize_function_removes_iframe(self):
        from app import _sanitize_html
        result = _sanitize_html('<div>safe</div><iframe src="evil.com"></iframe>')
        assert "<iframe" not in result
        assert "safe" in result

    def test_sanitize_function_removes_event_handlers(self):
        from app import _sanitize_html
        result = _sanitize_html('<div onload="evil()">content</div>')
        assert "onload" not in result
        assert "content" in result

    def test_sanitize_function_removes_script(self):
        from app import _sanitize_html
        result = _sanitize_html('<p>ok</p><script>bad()</script><p>also ok</p>')
        assert "<script" not in result
        assert "ok" in result


_REPO_ROOT = Path(__file__).resolve().parents[3]
_HLJS_CDN_DIR = _REPO_ROOT / "cdn-data" / "highlight.js" / "11.9.0"


class TestHighlightJsCdnAssets:
    @pytest.mark.parametrize("theme", ["github-light", "github-dark", "hacker", "modernist"])
    def test_theme_css_links_styles_dir(self, client, mock_repo_manager, theme):
        from renderer import THEME_HLJS
        mock_repo_manager.read_file_text.return_value = "# Title\n\n```python\nprint(1)\n```\n"
        resp = client.get(f"/test-repo/docs/code.md?theme={theme}")
        assert resp.status_code == 200
        assert f'href="/cdn/highlight.js/11.9.0/styles/{THEME_HLJS[theme]}.min.css"' in resp.text
        assert '<script src="/cdn/highlight.js/11.9.0/highlight.min.js"></script>' in resp.text

    @pytest.mark.skipif(not _HLJS_CDN_DIR.is_dir(), reason="cdn-data 미포함 환경")
    def test_cdn_highlight_js_is_browser_bundle(self):
        script = (_HLJS_CDN_DIR / "highlight.min.js").read_text(encoding="utf-8")
        assert "require(" not in script
        assert "var hljs=" in script

    @pytest.mark.skipif(not _HLJS_CDN_DIR.is_dir(), reason="cdn-data 미포함 환경")
    def test_cdn_has_every_theme_css(self):
        from renderer import THEME_HLJS
        missing = [name for name in set(THEME_HLJS.values()) if not (_HLJS_CDN_DIR / "styles" / f"{name}.min.css").is_file()]
        assert missing == []


class TestRawMarkdownScript:
    def test_script_breakout_is_escaped(self, client, mock_repo_manager):
        import re
        markdown = "# Title\n\n```\n</script><script>alert(1)</script>\n```\n"
        mock_repo_manager.read_file_text.return_value = markdown
        resp = client.get("/test-repo/docs/breakout.md")
        assert resp.status_code == 200
        match = re.search(r'<script type="application/json" id="raw-markdown-json">(.*?)</script>', resp.text, re.S)
        assert match is not None
        payload = match.group(1)
        assert "<" not in payload
        assert json.loads(payload) == {"markdown": markdown}

    def test_encode_for_script_escapes_unsafe_chars(self):
        from app import _encode_for_script
        value = {"markdown": "a<b>c&d" + chr(0x2028) + chr(0x2029) + "한글"}
        encoded = _encode_for_script(value)
        for ch in ("<", ">", "&", chr(0x2028), chr(0x2029)):
            assert ch not in encoded
        assert json.loads(encoded) == value


class TestPathBreadcrumb:
    @staticmethod
    def _toolbar_breadcrumb(text):
        import re
        match = re.search(r'<div class="breadcrumb">(.*?)</div>', text, re.S)
        assert match is not None
        return match.group(1)

    def test_each_folder_links_to_repo_tree(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/sub/readme.md")
        assert resp.status_code == 200
        crumb = self._toolbar_breadcrumb(resp.text)
        assert '<a href="/view/test-repo?path=docs">docs</a>' in crumb
        assert '<a href="/view/test-repo?path=docs/sub">sub</a>' in crumb
        assert '<span class="current">readme.md</span>' in crumb
        assert "path=docs/sub/readme.md" not in crumb

    def test_path_info_is_merged_into_breadcrumb(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/sub/readme.md")
        assert 'class="path-info"' not in resp.text
        assert '<a href="/view/test-repo?path=docs/sub">sub</a>' in self._toolbar_breadcrumb(resp.text)

    def test_ref_is_preserved_in_folder_links(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/readme.md?ref=feat/x")
        assert resp.status_code == 200
        crumb = self._toolbar_breadcrumb(resp.text)
        assert '<a href="/view/test-repo?path=docs&amp;ref=feat%2Fx">docs</a>' in crumb

    def test_special_characters_are_escaped(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/%3Cb%3E%26x/a%22b.md")
        assert resp.status_code == 200
        crumb = self._toolbar_breadcrumb(resp.text)
        assert "<b>" not in crumb
        assert '<a href="/view/test-repo?path=docs/%3Cb%3E%26x">&lt;b&gt;&amp;x</a>' in crumb
        assert '<span class="current">a&quot;b.md</span>' in crumb

    def test_repo_page_expands_tree_to_path_param(self, client, mock_repo_manager):
        mock_repo_manager.list_tree.return_value = []
        mock_repo_manager.list_branches.return_value = ["main"]
        resp = client.get("/test-repo?path=docs/sub")
        assert resp.status_code == 200
        assert 'data-expand-path="docs/sub"' in resp.text


class TestDocumentSidebarTree:
    TREE = {
        "": [("docs", "dir"), ("src", "dir"), ("README.md", "file")],
        "docs": [("sub", "dir"), ("a.md", "file"), ("image.png", "file")],
        "docs/sub": [("readme.md", "file"), ("other.md", "file")],
    }

    @pytest.fixture
    def tree_manager(self, mock_repo_manager):
        from synctx_common.git_manager import TreeEntry

        def list_tree(path="", ref=None):
            prefix = f"{path}/" if path else ""
            return [TreeEntry(name=n, path=prefix + n, type=t) for n, t in self.TREE[path]]

        mock_repo_manager.read_file_text.return_value = "# Hello\n\n## Section"
        mock_repo_manager.list_tree.side_effect = list_tree
        return mock_repo_manager

    @staticmethod
    def _sidebar_tree(text):
        import re
        match = re.search(r'<div class="tree" id="file-tree">(.*?)</div>\s*' + re.escape('<section class="recent"'), text, re.S)
        assert match is not None
        return match.group(1)

    def test_tree_content_and_toc_order(self, client, tree_manager):
        text = client.get("/test-repo/docs/sub/readme.md").text
        assert text.index('id="file-tree"') < text.index('<main id="main" class="viewer-content"') < text.index('<aside class="shell-toc"')

    def test_folders_on_path_are_expanded_and_others_collapsed(self, client, tree_manager):
        tree = self._sidebar_tree(client.get("/test-repo/docs/sub/readme.md").text)
        assert '<div class="tree-children" data-path="docs" data-loaded="1">' in tree
        assert '<div class="tree-children" data-path="docs/sub" data-loaded="1">' in tree
        assert '<div class="tree-children collapsed" data-path="src"></div>' in tree
        assert tree.count('class="tree-toggle open"') == 2

    def test_only_folders_on_path_are_listed(self, client, tree_manager):
        client.get("/test-repo/docs/sub/readme.md")
        listed = [c.args[0] if c.args else c.kwargs.get("path", "") for c in tree_manager.list_tree.call_args_list]
        assert listed == ["", "docs", "docs/sub"]

    def test_current_document_is_selected(self, client, tree_manager):
        import re
        tree = self._sidebar_tree(client.get("/test-repo/docs/sub/readme.md").text)
        current = re.findall(r'<div class="tree-entry current">.*?</div>', tree, re.S)
        assert len(current) == 1
        assert 'href="/view/test-repo/docs/sub/readme.md"' in current[0]
        assert 'aria-current="page"' in current[0]
        assert tree.count("aria-current") == 1

    def test_other_documents_link_to_their_view(self, client, tree_manager):
        tree = self._sidebar_tree(client.get("/test-repo/docs/sub/readme.md").text)
        assert '<a href="/view/test-repo/docs/sub/other.md">other.md</a>' in tree
        assert '<a href="/view/test-repo/docs/a.md">a.md</a>' in tree
        assert '<a href="/view/test-repo/README.md">README.md</a>' in tree
        assert "image.png" in tree and "/view/test-repo/docs/image.png" not in tree

    def test_ref_is_kept_in_tree_links_and_listing(self, client, tree_manager):
        tree = self._sidebar_tree(client.get("/test-repo/docs/sub/readme.md?ref=feat/x").text)
        assert '<a href="/view/test-repo/docs/sub/other.md?ref=feat%2Fx">other.md</a>' in tree
        assert all(c.kwargs.get("ref") == "feat/x" for c in tree_manager.list_tree.call_args_list)

    def test_tree_scripts_are_available(self, client, tree_manager):
        text = client.get("/test-repo/docs/sub/readme.md").text
        assert '<script src="/view/static/viewer.js"></script>' in text
        assert 'var ACTIVE_REPO = "test-repo";' in text

    def test_folders_are_buttons_toggled_by_script(self, client, tree_manager):
        text = client.get("/test-repo/docs/sub/readme.md").text
        assert '<button type="button" class="tree-entry tree-dir" aria-expanded="true">' in text
        assert '<button type="button" class="tree-entry tree-dir" aria-expanded="false">' in text
        assert '<script src="/view/static/viewer.js"></script>' in text and "onclick=" not in text

    def test_ref_is_encoded_in_script(self, client, tree_manager):
        import re
        text = client.get("/test-repo/docs/sub/readme.md", params={"ref": "x';alert(1)//</script>"}).text
        script = re.search(r"<script>\s*var VIEW_PREFIX = .*?</script>", text, re.S).group(0)
        assert script.count("</script>") == 1
        assert 'var ACTIVE_REF = "x\';alert(1)//\\u003c/script\\u003e";' in script

    def test_tree_failure_keeps_document_view(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        mock_repo_manager.list_tree.side_effect = RuntimeError("boom")
        resp = client.get("/test-repo/docs/readme.md")
        assert resp.status_code == 200
        assert "<h1" in resp.text
        assert "파일 트리를 불러오지 못했습니다." in resp.text


class TestRewriteLinks:
    def test_markdown_link_points_to_file_view(self):
        from renderer import rewrite_links
        html = rewrite_links('<a href="sub/guide.md#s1">g</a>', "test-repo", "docs/readme.md")
        assert 'href="/view/test-repo/docs/sub/guide.md#s1"' in html

    def test_folder_link_points_to_repo_tree(self):
        from renderer import rewrite_links
        html = rewrite_links('<a href="sub/">s</a>', "test-repo", "docs/readme.md")
        assert 'href="/view/test-repo?path=docs/sub"' in html

    def test_parent_folder_link_is_resolved(self):
        from renderer import rewrite_links
        html = rewrite_links('<a href="../other/#top">o</a>', "test-repo", "docs/sub/readme.md")
        assert 'href="/view/test-repo?path=docs/other"' in html

    def test_percent_encoded_folder_link_is_not_double_encoded(self):
        from renderer import rewrite_links
        html = rewrite_links('<a href="9.%EA%B7%BC%EA%B1%B0/">r</a>', "test-repo", "10. docs/README.md")
        assert 'href="/view/test-repo?path=10.%20docs/9.%EA%B7%BC%EA%B1%B0"' in html

    def test_folder_link_in_rendered_page(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "[근거](evidence/)"
        resp = client.get("/test-repo/docs/readme.md")
        assert resp.status_code == 200
        assert 'href="/view/test-repo?path=docs/evidence"' in resp.text

    def test_image_src_keeps_ref(self):
        from renderer import rewrite_links
        html = rewrite_links('<img alt="a" src="img/a&amp;b.png">', "test-repo", "docs/readme.md", ref="feature/x")
        assert 'src="/raw/test-repo/docs/img/a&amp;b.png?ref=feature%2Fx"' in html
        html = rewrite_links('<img alt="a" src="img/a&amp;b.png">', "test-repo", "docs/readme.md")
        assert 'src="/raw/test-repo/docs/img/a&amp;b.png"' in html

    @pytest.mark.parametrize(
        ("src", "expected"),
        [
            ("img/icons.svg#warning", "/raw/test-repo/docs/img/icons.svg?ref=feature%2Fx#warning"),
            ("img/icons.svg?color=red#warning", "/raw/test-repo/docs/img/icons.svg?color=red&amp;ref=feature%2Fx#warning"),
        ],
    )
    def test_image_src_keeps_ref_before_fragment(self, src, expected):
        from renderer import rewrite_links
        html = rewrite_links(f'<img alt="i" src="{src}">', "test-repo", "docs/readme.md", ref="feature/x")
        assert f'src="{expected}"' in html

    def test_picture_source_srcset_points_to_raw(self):
        # README·도움말의 다크 모드 그림(<picture><source srcset>)도 /raw로 받아야 한다
        from renderer import rewrite_links
        html = rewrite_links('<picture><source media="(prefers-color-scheme: dark)" srcset="img/a-dark.svg">'
                             '<img src="img/a-light.svg"></picture>', "test-repo", "docs/readme.md", ref="feature/x")
        assert 'srcset="/raw/test-repo/docs/img/a-dark.svg?ref=feature%2Fx"' in html
        assert 'src="/raw/test-repo/docs/img/a-light.svg?ref=feature%2Fx"' in html

    def test_srcset_candidates_keep_descriptors_and_external_urls(self):
        from renderer import rewrite_links
        html = rewrite_links('<img src="a.png" srcset="a.png 1x, img/a@2x.png 2x, https://cdn.example/a.png 3x">',
                             "test-repo", "docs/readme.md")
        assert ('srcset="/raw/test-repo/docs/a.png 1x, /raw/test-repo/docs/img/a@2x.png 2x, '
                'https://cdn.example/a.png 3x"') in html

    @pytest.mark.parametrize("srcset", [
        "data:image/png;base64,AAAA,BBBB 1x",
        "https://cdn.example/a,b.png 1x, https://cdn.example/c.png 2x",
        "data:image/svg+xml,%3Csvg%3E%3C/svg%3E",
    ])
    def test_srcset_keeps_commas_inside_data_and_external_urls(self, srcset):
        from renderer import rewrite_links
        html = rewrite_links(f'<img src="https://x.example/a.png" srcset="{srcset}">', "test-repo", "docs/readme.md")
        assert f'srcset="{srcset}"' in html

    def test_srcset_rewrites_relative_url_next_to_data_url(self):
        from renderer import rewrite_links
        html = rewrite_links('<source srcset="data:image/png;base64,AAAA 1x,img/b.png 2x">', "test-repo", "docs/readme.md")
        assert 'srcset="data:image/png;base64,AAAA 1x, /raw/test-repo/docs/img/b.png 2x"' in html

    @pytest.mark.parametrize(("path", "media_type"), [
        ("docs/img/a.png", "image/png"), ("docs/img/a.JPG", "image/jpeg"), ("docs/img/a.gif", "image/gif"),
        ("docs/img/a.webp", "image/webp"), ("docs/img/a.svg", "image/svg+xml"), ("docs/a.bin", "application/octet-stream"),
    ])
    def test_raw_serves_images_with_their_media_type(self, client, mock_repo_manager, path, media_type):
        # 브라우저는 SVG를 image/svg+xml일 때만 그림으로 그린다(README·도움말의 움직이는 그림)
        mock_repo_manager.read_file.return_value = b"<svg/>"
        resp = client.get(f"/raw/test-repo/{path}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].split(";")[0] == media_type

    def test_raw_svg_cannot_run_scripts_when_opened_directly(self, client, mock_repo_manager):
        mock_repo_manager.read_file.return_value = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        resp = client.get("/raw/test-repo/docs/img/a.svg")
        csp = resp.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "sandbox" in csp and "script-src" not in csp
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_picture_in_rendered_page_uses_raw_for_dark_image(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = (
            '<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="img/hero-dark.svg">'
            '<img src="img/hero-light.svg" alt="hero"></picture></p>')
        text = client.get("/test-repo/docs/readme.md").text
        assert 'srcset="/raw/test-repo/docs/img/hero-dark.svg"' in text
        assert 'src="/raw/test-repo/docs/img/hero-light.svg"' in text

    def test_image_with_fragment_in_rendered_page_reads_ref(self, client, mock_repo_manager):
        import re
        from urllib.parse import parse_qs, urlsplit
        mock_repo_manager.read_file_text.return_value = "![icon](img/icons.svg#warning)"
        text = client.get("/test-repo/docs/readme.md?ref=feature/x").text
        src = re.search(r'<img[^>]*src="([^"]+)"', text).group(1).replace("&amp;", "&")
        parts = urlsplit(src)
        assert parts.fragment == "warning"
        assert parse_qs(parts.query) == {"ref": ["feature/x"]}
        mock_repo_manager.read_file.return_value = b"<svg/>"
        client.get(f"{parts.path}?{parts.query}")
        mock_repo_manager.read_file.assert_called_with("docs/img/icons.svg", ref="feature/x")

    def test_image_in_rendered_page_keeps_ref(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "![구성도](img/arch.png)"
        resp = client.get("/test-repo/docs/readme.md?ref=feature/x")
        assert resp.status_code == 200
        assert 'src="/raw/test-repo/docs/img/arch.png?ref=feature%2Fx"' in resp.text

    def test_markdown_link_keeps_ref_before_fragment(self):
        from renderer import rewrite_links
        html = rewrite_links('<a href="sub/guide.md#s1">g</a>', "test-repo", "docs/readme.md", ref="feature/x")
        assert 'href="/view/test-repo/docs/sub/guide.md?ref=feature%2Fx#s1"' in html

    def test_markdown_link_with_query_appends_ref(self):
        from renderer import rewrite_links
        html = rewrite_links('<a href="guide.md?mode=meeting">g</a>', "test-repo", "docs/readme.md", ref="feature/x")
        assert 'href="/view/test-repo/docs/guide.md?mode=meeting&amp;ref=feature%2Fx"' in html

    def test_markdown_link_with_ampersand_keeps_name(self):
        from renderer import rewrite_links
        html = rewrite_links('<a href="a&amp;b.md">f</a>', "test-repo", "docs/readme.md", ref="feature/x")
        assert 'href="/view/test-repo/docs/a&amp;b.md?ref=feature%2Fx"' in html
        html = rewrite_links('<a href="a&amp;b.md">f</a>', "test-repo", "docs/readme.md")
        assert 'href="/view/test-repo/docs/a&amp;b.md"' in html

    def test_markdown_link_in_rendered_page_keeps_ref(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "[가이드](guide.md#start)"
        resp = client.get("/test-repo/docs/readme.md?ref=feature/x")
        assert resp.status_code == 200
        assert 'href="/view/test-repo/docs/guide.md?ref=feature%2Fx#start"' in resp.text

    def test_folder_link_keeps_ref(self):
        from renderer import rewrite_links
        html = rewrite_links('<a href="sub/">s</a>', "test-repo", "docs/readme.md", ref="feature/x")
        assert 'href="/view/test-repo?path=docs/sub&amp;ref=feature%2Fx"' in html

    def test_folder_link_in_rendered_page_keeps_ref(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "[근거](evidence/)"
        resp = client.get("/test-repo/docs/readme.md?ref=feature/x")
        assert resp.status_code == 200
        assert 'href="/view/test-repo?path=docs/evidence&amp;ref=feature%2Fx"' in resp.text

    def test_folder_link_with_ampersand_in_rendered_page(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "[ab](a&b/)"
        resp = client.get("/test-repo/docs/readme.md")
        assert resp.status_code == 200
        assert 'href="/view/test-repo?path=docs/a%26b"' in resp.text

    def test_link_map_points_to_other_repo_and_keeps_other_attrs(self):
        from renderer import rewrite_links
        html = '<a href="../사내규정/x.md#a1" data-evidence="evidence-1">x</a><a href="../없는곳/y.md">y</a>'
        out = rewrite_links(html, "test-repo", "docs/m.md", ref="feat", link_map={"../사내규정/": "regs:규정/"})
        assert 'href="/view/regs/규정/x.md#a1" data-evidence="evidence-1"' in out
        assert "ref=feat" not in out.split("</a>")[0]
        assert 'href="/view/test-repo/없는곳/y.md?ref=feat"' in out  # 매핑에 없으면 기존 규칙 그대로
        same = rewrite_links('<a href="../근거/z.md#k">z</a>', "test-repo", "docs/m.md", ref="feat",
                             link_map={"../근거/": "test-repo:근거/"})
        assert 'href="/view/test-repo/근거/z.md?ref=feat#k"' in same  # 같은 저장소 매핑은 ref 유지
        kept = rewrite_links('<a href="../유령/y.md">g</a>', "test-repo", "docs/m.md", keep_prefixes=("../유령/",))
        assert 'href="../유령/y.md"' in kept

    def test_link_map_longest_key_folder_and_kept_prefix(self):
        from renderer import rewrite_links
        html = '<a href="../유령/깊이/z.md">z</a><a href="../유령/y.md">y</a><a href="../유령/깊이/폴더/">f</a>'
        out = rewrite_links(html, "test-repo", "docs/m.md", ref="feat",
                            link_map={"../유령/깊이/": "regs:근거/"}, keep_prefixes=("../유령/",))
        assert 'href="/view/regs/근거/z.md"' in out
        assert 'href="../유령/y.md"' in out
        assert 'href="/view/regs?path=%EA%B7%BC%EA%B1%B0/%ED%8F%B4%EB%8D%94"' in out

    def test_link_map_to_repo_root_or_folder_uses_folder_view(self):
        from renderer import rewrite_links
        root = rewrite_links('<a href="../규정/">r</a><a href="../규정/#x">h</a>', "test-repo", "docs/m.md",
                             ref="feat", link_map={"../규정/": "regs:"})
        assert root.count('href="/view/regs?path="') == 2  # 다른 저장소는 ref를 붙이지 않는다
        same = rewrite_links('<a href="../규정/">r</a>', "test-repo", "docs/m.md", ref="feat",
                             link_map={"../규정/": "test-repo:"})
        assert 'href="/view/test-repo?path=&amp;ref=feat"' in same
        sub = rewrite_links('<a href="../규정/">a</a><a href="../규정/세부/">b</a>', "test-repo", "docs/m.md",
                            link_map={"../규정/": "regs:근거"})
        assert 'href="/view/regs?path=%EA%B7%BC%EA%B1%B0"' in sub  # 원래 폴더 링크는 매핑 값에 /가 없어도 폴더
        assert 'href="/view/regs?path=%EA%B7%BC%EA%B1%B0%EC%84%B8%EB%B6%80"' in sub


class TestRefEscaping:
    MALICIOUS_REFS = ['x"><b>', "';alert(1)//</script>"]

    @staticmethod
    def _assert_not_raw(text, ref):
        assert ref not in text
        assert '"><b>' not in text
        assert "'';alert(1)" not in text

    @pytest.mark.parametrize("ref", MALICIOUS_REFS)
    def test_document_page_escapes_ref(self, client, mock_repo_manager, ref):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/readme.md", params={"ref": ref})
        assert resp.status_code == 200
        self._assert_not_raw(resp.text, ref)

    @pytest.mark.parametrize("ref", MALICIOUS_REFS)
    def test_repo_page_escapes_ref(self, client, mock_repo_manager, ref):
        mock_repo_manager.list_tree.return_value = []
        mock_repo_manager.list_branches.return_value = ["main", ref]
        resp = client.get("/test-repo", params={"ref": ref})
        assert resp.status_code == 200
        self._assert_not_raw(resp.text, ref)

    def test_repo_page_encodes_ref_for_script(self, client, mock_repo_manager):
        mock_repo_manager.list_tree.return_value = []
        mock_repo_manager.list_branches.return_value = ["main"]
        resp = client.get("/test-repo", params={"ref": "';alert(1)//</script>"})
        assert 'var ACTIVE_REF = "\';alert(1)//\\u003c/script\\u003e";' in resp.text
        assert 'var ACTIVE_REPO = "test-repo";' in resp.text

    def test_document_page_keeps_normal_ref(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/readme.md?ref=feat/x")
        assert resp.status_code == 200
        assert 'href="/view/test-repo?ref=feat%2Fx"' in resp.text
        assert 'href="/view/api/repos/test-repo/download?path=docs/readme.md&amp;ref=feat%2Fx"' in resp.text

    def test_repo_page_keeps_normal_ref(self, client, mock_repo_manager):
        entry = MagicMock(type="file", path="docs/readme.md")
        entry.name = "readme.md"
        mock_repo_manager.profile.name = "Test Repo"
        mock_repo_manager.list_tree.return_value = [entry]
        mock_repo_manager.list_branches.return_value = ["main", "feat/x"]
        resp = client.get("/test-repo?ref=feat/x")
        assert resp.status_code == 200
        assert 'var ACTIVE_REF = "feat/x";' in resp.text
        assert '<option value="feat/x" selected>feat/x</option>' in resp.text
        assert 'href="/view/test-repo/docs/readme.md?ref=feat%2Fx"' in resp.text
        assert "Test Repo (feat/x)" in resp.text


class TestPathEscaping:
    @staticmethod
    def _doc_config_raw(text):
        import re
        match = re.search(r"var SYNCTX_DOC = (\{.*?\});", text)
        assert match is not None
        return match.group(1)

    @staticmethod
    def _repo_page(client, mock_repo_manager, entries):
        mock_repo_manager.list_tree.return_value = entries
        mock_repo_manager.list_branches.return_value = ["main"]
        resp = client.get("/test-repo")
        assert resp.status_code == 200
        return resp.text

    def test_script_filename_is_json_literal(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/x%27%3Balert(1)%3B%27%3C%2Fscript%3E.md")
        assert resp.status_code == 200
        raw = self._doc_config_raw(resp.text)
        assert "<" not in raw
        assert json.loads(raw)["path"] == "docs/x';alert(1);'</script>.md"

    def test_script_filename_keeps_normal_path(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/readme.md")
        assert json.loads(self._doc_config_raw(resp.text))["path"] == "docs/readme.md"

    def test_tree_entries_are_escaped(self, client, mock_repo_manager):
        from types import SimpleNamespace
        text = self._repo_page(client, mock_repo_manager, [
            SimpleNamespace(type="dir", name="<img src=x onerror=alert(1)>", path='d"><img src=x onerror=alert(1)>'),
            SimpleNamespace(type="file", name='"><script>alert(1)</script>.md', path='"><script>alert(1)</script>.md'),
            SimpleNamespace(type="file", name="<b>bin</b>.png", path="<b>bin</b>.png"),
        ])
        assert "<img src=x" not in text
        assert "<script>alert(1)" not in text
        assert "<b>bin</b>" not in text
        assert '<span class="name">&lt;img src=x onerror=alert(1)&gt;/</span>' in text
        assert 'data-path="d&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"' in text
        assert '<a href="/view/test-repo/%22%3E%3Cscript%3Ealert%281%29%3C/script%3E.md">&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;.md</a>' in text
        assert '<span class="name">&lt;b&gt;bin&lt;/b&gt;.png</span>' in text

    def test_tree_keeps_normal_paths(self, client, mock_repo_manager):
        from types import SimpleNamespace
        text = self._repo_page(client, mock_repo_manager, [
            SimpleNamespace(type="dir", name="docs", path="docs"),
            SimpleNamespace(type="file", name="readme.md", path="docs/readme.md"),
        ])
        assert '<span class="name">docs/</span>' in text
        assert 'data-path="docs"' in text
        assert '<a href="/view/test-repo/docs/readme.md">readme.md</a>' in text

    def test_subtree_script_escapes_entries(self):
        js = (Path(__file__).resolve().parents[1] / "src" / "static" / "viewer.js").read_text(encoding="utf-8")
        assert "escapeHtml(d.name)" in js and "escapeHtml(d.path)" in js
        assert "encodePath(f.path)" in js and "escapeHtml(f.name)" in js
        assert "+ f.name +" not in js and "+ d.name +" not in js

    def test_download_link_encodes_path(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "print(1)"
        resp = client.get('/test-repo/src/a%26b%23c%22%3E.py')
        assert resp.status_code == 200
        assert 'href="/view/api/repos/test-repo/download?path=src/a%26b%23c%22%3E.py"' in resp.text


class TestShellPages:
    def test_index_uses_shell_and_korean_copy(self, client):
        text = client.get("/").text
        assert 'class="skip-link" href="#main"' in text
        assert '<h1 class="page-title">저장소</h1>' in text
        assert "기본 브랜치 main" in text
        assert '<li data-repo-id="test-repo"><a class="repo-card"' in text
        assert "https://git.test/repo.git" not in text
        assert "Select Repository" not in text and "Repositories" not in text
        assert '<script src="/view/static/viewer.js"></script>' in text
        assert 'class="shell-nav shell-nav-mobile-only"' in text and 'data-action="repo"' in text

    def test_repo_page_uses_shell_and_korean_copy(self, client, mock_repo_manager):
        from synctx_common.git_manager import CommitInfo
        mock_repo_manager.list_tree.return_value = []
        mock_repo_manager.list_branches.return_value = ["main"]
        mock_repo_manager.profile.name = "Test Repo"
        mock_repo_manager.last_commit.return_value = CommitInfo(sha="abcdef1234567", author="kim", authored_at="2026-10-16T00:00:00+00:00")
        text = client.get("/test-repo").text
        assert 'id="shell-nav"' in text and 'data-tree-filter' in text
        assert "폴더 0개 · 파일 0개" in text
        assert "abcdef1" in text and "kim" in text
        assert "Filter files" not in text and "folders" not in text
        assert '<option value="main" selected>main (기본)</option>' in text
        assert "위의 문서 검색을 쓰세요" in text and "파일 찾기" not in text

    def test_repo_page_offers_readme(self, client, mock_repo_manager):
        from types import SimpleNamespace
        mock_repo_manager.list_tree.return_value = [SimpleNamespace(type="file", name="README.md", path="README.md")]
        mock_repo_manager.list_branches.return_value = ["main"]
        assert 'href="/view/test-repo/README.md">README 열기</a>' in client.get("/test-repo").text

    def test_dirs_are_buttons_with_expanded_state(self, client, mock_repo_manager):
        from types import SimpleNamespace
        mock_repo_manager.list_tree.return_value = [SimpleNamespace(type="dir", name="docs", path="docs")]
        mock_repo_manager.list_branches.return_value = ["main"]
        assert '<button type="button" class="tree-entry tree-dir" aria-expanded="false">' in client.get("/test-repo").text

    def test_files_api_lists_viewable_paths(self, client, mock_repo_manager):
        mock_repo_manager.file_paths.return_value = {"b.md", "a.py", "img.png", "docs/c.md"}
        resp = client.get("/api/repos/test-repo/files")
        assert resp.status_code == 200
        assert resp.json() == {"files": ["a.py", "b.md", "docs/c.md"]}

    def test_files_api_hides_internal_errors(self, client, mock_repo_manager):
        mock_repo_manager.file_paths.side_effect = RuntimeError("Authorization: Basic SECRET")
        resp = client.get("/api/repos/test-repo/files")
        assert resp.status_code == 502
        assert "SECRET" not in resp.text


class TestDocumentPage:
    def test_doc_meta_shows_last_commit(self, client, mock_repo_manager):
        from synctx_common.git_manager import CommitInfo
        mock_repo_manager.read_file_text.return_value = "# Hello"
        mock_repo_manager.last_commit.return_value = CommitInfo(sha="1234567890abc", author="lee", authored_at="2026-10-16T00:00:00+00:00")
        text = client.get("/test-repo/docs/a.md").text
        assert "마지막 커밋 lee" in text and '<code class="sha">1234567</code>' in text
        mock_repo_manager.last_commit.assert_called_with("docs/a.md", ref=None)

    def test_layout_landmarks_and_toc(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# T\n\n## A\n\n## B"
        text = client.get("/test-repo/docs/a.md").text
        assert 'class="skip-link" href="#main"' in text
        assert '<nav id="shell-nav" class="shell-nav"' in text
        assert '<aside class="shell-toc" aria-label="이 문서의 목차">' in text
        assert 'data-action="open-toc"' in text
        assert '<article class="markdown-body">' in text
        assert 'data-theme-mode="query"' in text

    def test_download_menu_offers_formats(self, client, mock_repo_manager, monkeypatch):
        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        mock_repo_manager.read_file_text.return_value = "# Hello"
        text = client.get("/test-repo/docs/a.md").text
        assert '<summary class="btn">내려받기</summary>' in text
        assert 'download?path=docs/a.md&amp;format=html' in text
        assert 'download?path=docs/a.md&amp;format=pdf' in text

    def test_doc_config_defined_before_viewer_script(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        text = client.get("/test-repo/docs/a.md").text
        assert text.index("var SYNCTX_DOC = {") < text.index('<script src="/view/static/viewer.js"></script>')

    def test_binary_file_is_rejected_before_reading(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.side_effect = UnicodeDecodeError("utf-8", b"\x89", 0, 1, "invalid start byte")
        resp = client.get("/api/repos/test-repo/render", params={"path": "img/logo.png"})
        assert resp.status_code == 415
        mock_repo_manager.read_file_text.assert_not_called()

    def test_no_alert_calls_in_page(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "---\nmarp: true\n---\n# S"
        assert "alert(" not in client.get("/test-repo/docs/deck.md").text

    def test_default_branch_is_recorded_as_empty_ref(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        text = client.get("/test-repo/docs/a.md", params={"ref": mock_repo_manager.profile.default_ref}).text
        assert 'var ACTIVE_REF = "";' in text

    def test_other_branch_is_recorded_as_ref(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        assert 'var ACTIVE_REF = "feat/x";' in client.get("/test-repo/docs/a.md", params={"ref": "feat/x"}).text

    def test_document_render_does_not_look_up_remote_branches(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        mock_repo_manager.list_branches.side_effect = RuntimeError("offline")
        assert client.get("/test-repo/docs/a.md").status_code == 200
        mock_repo_manager.list_branches.assert_not_called()

    def test_document_branch_select_has_only_active_branch(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        text = client.get("/test-repo/docs/a.md").text
        assert text.count('<option value="main" selected>main (기본)</option>') == 2
        text = client.get("/test-repo/docs/a.md", params={"ref": "feat/x"}).text
        assert text.count('<option value="feat/x" selected>feat/x</option>') == 2
        assert '<option value="main"' not in text

    def test_repo_page_does_not_look_up_remote_branches(self, client, mock_repo_manager):
        mock_repo_manager.list_tree.return_value = []
        mock_repo_manager.list_branches.side_effect = RuntimeError("offline")
        text = client.get("/test-repo").text
        mock_repo_manager.list_branches.assert_not_called()
        assert text.count('<option value="main" selected>main (기본)</option>') == 2

    def test_error_page_does_not_look_up_remote_branches(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.side_effect = RuntimeError("boom")
        mock_repo_manager.file_paths.return_value = {"docs/a.md"}
        resp = client.get("/test-repo/docs/a.md", params={"ref": "feat/x"})
        assert resp.status_code == 500
        mock_repo_manager.list_branches.assert_not_called()
        assert resp.text.count('<option value="feat/x" selected>feat/x</option>') == 2


class TestDocumentTitle:
    @staticmethod
    def _title(text):
        import re
        return re.search(r"<title>(.*?)</title>", text, re.S).group(1)

    def test_h1_is_the_title(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# 안녕\n\n본문"
        text = client.get("/test-repo/docs/a.md").text
        assert "안녕" in self._title(text)

    def test_document_without_h1_uses_file_name(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "본문만 있는 문서\n\n## 소제목"
        text = client.get("/test-repo/docs/no-heading.md").text
        assert "Markdown Preview" not in text
        assert self._title(text).startswith("no-heading.md")
        assert '"title": "no-heading.md"' in text

    def test_code_file_uses_file_name(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "print(1)"
        text = client.get("/test-repo/src/tool.py").text
        assert "Markdown Preview" not in text

    def test_meeting_document_without_h1_uses_meeting_title(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = (
            "---\nsynctx:\n  type: meeting\n  meeting:\n    id: dx-21\n    title: DX 21번 검토\n"
            "    starts_at: '2026-10-08T14:00+09:00'\n    channel: dx-team\n---\n본문\n")
        text = client.get("/test-repo/docs/meet.md").text
        assert "Markdown Preview" not in text
        assert self._title(text).startswith("DX 21번 검토")
        assert '"title": "DX 21번 검토"' in text


class TestAPIRoutes:
    def test_repos_list(self, client):
        resp = client.get("/api/repos")
        assert resp.status_code == 200
        body = resp.json()
        repos = body["repositories"]
        assert any(r["id"] == "test-repo" for r in repos)

    def test_repo_not_found(self, client):
        resp = client.get("/nonexistent/docs/test.md")
        assert resp.status_code == 404


class TestSearchApi:
    def test_search_returns_repo_groups(self, client, mock_repo_manager):
        from synctx_common.git_manager import GrepMatch, GrepResult
        mock_repo_manager.grep.return_value = GrepResult("ok", [GrepMatch("docs/a.md", 2, "Hello Search")], False)
        data = client.get("/api/search", params={"q": " search "}).json()
        assert data["query"] == "search"
        assert data["repos"] == [{"id": "test-repo", "name": "Test Repo", "status": "ok", "truncated": False,
                                  "matches": [{"path": "docs/a.md", "line": 2, "before": "Hello ", "match": "Search", "after": ""}]}]
        kwargs = mock_repo_manager.grep.call_args.kwargs
        assert ":(glob,icase)**/*.md" in kwargs["pathspecs"] and kwargs["limit"] == 50 and 0 < kwargs["timeout"] <= 3.0

    def test_search_hides_unviewable_files(self, client, mock_repo_manager):
        from synctx_common.git_manager import GrepResult
        mock_repo_manager.grep.return_value = GrepResult("ok", [], False)
        client.get("/api/search", params={"q": "ab"})
        path_filter = mock_repo_manager.grep.call_args.kwargs["path_filter"]
        assert path_filter("docs/a.md") and not path_filter(".env") and not path_filter("x/.md")
        assert not path_filter("docs/a\nb.md") and not path_filter("docs/a\tb.md")
        assert not path_filter(".hidden.md") and not path_filter(".gitignore") and not path_filter(".github/x.md")

    @pytest.mark.parametrize("q", ["a", "x" * 101, "a\nb", ""])
    def test_search_rejects_bad_queries(self, client, mock_repo_manager, q):
        assert client.get("/api/search", params={"q": q}).status_code == 400
        mock_repo_manager.grep.assert_not_called()

    def test_search_unknown_repo_and_ref_without_repo(self, client):
        assert client.get("/api/search", params={"q": "ab", "repo": "nope"}).status_code == 404
        assert client.get("/api/search", params={"q": "ab", "ref": "feat"}).status_code == 400

    def test_search_passes_non_default_ref_only(self, client, mock_repo_manager):
        from synctx_common.git_manager import GrepResult
        mock_repo_manager.grep.return_value = GrepResult("ok", [], False)
        client.get("/api/search", params={"q": "ab", "repo": "test-repo", "ref": "main"})
        assert mock_repo_manager.grep.call_args.args[1] is None
        client.get("/api/search", params={"q": "ab", "repo": "test-repo", "ref": "feat/x"})
        assert mock_repo_manager.grep.call_args.args[1] == "feat/x"

    def test_search_empty_repo_and_ref_mean_all_repositories(self, client, mock_repo_manager):
        from synctx_common.git_manager import GrepResult
        mock_repo_manager.grep.return_value = GrepResult("ok", [], False)
        data = client.get("/api/search", params={"q": "ab", "repo": ""}).json()
        assert [repo["id"] for repo in data["repos"]] == ["test-repo"]
        mock_repo_manager.grep.assert_called_once()
        assert mock_repo_manager.grep.call_args.args[1] is None
        resp = client.get("/api/search", params={"q": "ab", "repo": "", "ref": ""})
        assert resp.status_code == 200 and [repo["id"] for repo in resp.json()["repos"]] == ["test-repo"]

    def test_search_failure_hides_exception_text(self, client, mock_repo_manager):
        mock_repo_manager.grep.side_effect = RuntimeError("Authorization: Basic SECRET")
        resp = client.get("/api/search", params={"q": "ab"})
        assert resp.status_code == 200 and resp.json()["repos"][0]["status"] == "failed"
        assert "SECRET" not in resp.text

    def test_search_with_no_repositories(self, client, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app.config, "repositories", [])
        resp = client.get("/api/search", params={"q": "ab"})
        assert resp.status_code == 200 and resp.json() == {"query": "ab", "repos": []}

    def test_search_requires_viewer_role(self, client, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app.config.authz, "enabled", True)
        monkeypatch.setattr(viewer_app.config.authz.role_mappings, "viewer", ["synctx-viewer"])
        assert client.get("/api/search", params={"q": "ab"}).status_code == 403
        ok = {"X-Forwarded-Groups": "synctx-viewer"}
        assert client.get("/api/search", params={"q": "a"}, headers=ok).status_code == 400
        assert client.get("/search", params={"q": "ab"}).status_code == 403
        assert client.get("/search", params={"q": "a"}, headers=ok).status_code == 200


class TestSearchPage:
    def test_search_page_renders_html(self, client, mock_repo_manager):
        from synctx_common.git_manager import GrepMatch, GrepResult
        mock_repo_manager.grep.return_value = GrepResult("ok", [GrepMatch("docs/a.md", 2, "Hello Search")], False)
        resp = client.get("/search", params={"q": "search"})
        assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]
        assert "<mark>Search</mark>" in resp.text and 'class="shell-nav shell-nav-mobile-only"' in resp.text

    def test_search_page_invalid_query_does_not_search(self, client, mock_repo_manager):
        resp = client.get("/search", params={"q": "a"})
        assert resp.status_code == 200 and "두 글자 이상" in resp.text
        mock_repo_manager.grep.assert_not_called()

    def test_search_page_with_no_repositories(self, client, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app.config, "repositories", [])
        resp = client.get("/search", params={"q": "ab"})
        assert resp.status_code == 200 and "찾은 결과가 없습니다" in resp.text

    def test_search_page_unknown_repo_is_html_404(self, client):
        resp = client.get("/search", params={"q": "ab", "repo": "nope"})
        assert resp.status_code == 404 and "이 저장소를 찾을 수 없습니다" in resp.text

    @pytest.mark.parametrize("params, expected_ref", [
        ({"repo": "test-repo", "ref": "feat/x", "ref_repo": "test-repo"}, "feat/x"),
        ({"repo": "test-repo", "ref": "feat/x", "ref_repo": "other"}, None),
        ({"ref": "feat/x", "ref_repo": "test-repo"}, None),
    ])
    def test_search_page_uses_ref_only_for_its_repo(self, client, mock_repo_manager, params, expected_ref):
        from synctx_common.git_manager import GrepResult
        mock_repo_manager.grep.return_value = GrepResult("ok", [], False)
        client.get("/search", params={"q": "ab", **params})
        assert mock_repo_manager.grep.call_args.args[1] == expected_ref


class TestErrorPages:
    def test_unknown_repo_page_is_html(self, client):
        resp = client.get("/nope")
        assert resp.status_code == 404
        assert "text/html" in resp.headers["content-type"]
        assert "이 저장소를 찾을 수 없습니다" in resp.text
        assert 'href="/view/">저장소 목록</a>' in resp.text
        assert 'class="shell-nav shell-nav-mobile-only"' in resp.text and 'data-action="toggle-nav"' in resp.text

    def test_missing_file_suggests_similar_names(self, client, mock_repo_manager):
        mock_repo_manager.file_size.side_effect = RuntimeError("Authorization: Basic SECRET")
        mock_repo_manager.read_file_text.side_effect = RuntimeError("Authorization: Basic SECRET")
        mock_repo_manager.file_paths.return_value = {"docs/readme.md", "docs/guide.md"}
        resp = client.get("/test-repo/docs/readm.md")
        assert resp.status_code == 404
        assert "이 파일을 찾을 수 없습니다" in resp.text
        assert 'href="/view/test-repo/docs/readme.md"' in resp.text
        assert "SECRET" not in resp.text and "Authorization" not in resp.text

    def test_existing_file_failure_is_generic(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.side_effect = RuntimeError("boom SECRET")
        mock_repo_manager.file_paths.return_value = {"docs/a.md"}
        resp = client.get("/test-repo/docs/a.md")
        assert resp.status_code == 500
        assert "문서를 불러오지 못했습니다" in resp.text and "SECRET" not in resp.text

    def test_lookup_failure_is_not_reported_as_missing(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.side_effect = RuntimeError("boom")
        mock_repo_manager.file_paths.side_effect = RuntimeError("Authorization: Basic SECRET")
        resp = client.get("/test-repo/docs/a.md")
        assert resp.status_code == 502
        assert "문서를 불러오지 못했습니다" in resp.text
        assert "이 파일을 찾을 수 없습니다" not in resp.text and "SECRET" not in resp.text

    def test_binary_file_page_is_unsupported(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.side_effect = UnicodeDecodeError("utf-8", b"\x89", 0, 1, "invalid start byte")
        resp = client.get("/test-repo/img/logo.png")
        assert resp.status_code == 415
        assert "화면에서 볼 수 없는 형식입니다" in resp.text

    def test_too_large_offers_download(self, client, mock_repo_manager):
        mock_repo_manager.file_size.return_value = 10_000
        resp = client.get("/test-repo/docs/big.md")
        assert resp.status_code == 413
        assert 'href="/view/api/repos/test-repo/download?path=docs/big.md">원본 내려받기</a>' in resp.text

    def test_unsupported_type_offers_download(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "x"
        resp = client.get("/test-repo/files/a.bin")
        assert resp.status_code == 415
        assert "화면에서 볼 수 없는 형식입니다" in resp.text
        assert 'href="/view/api/repos/test-repo/download?path=files/a.bin">원본 내려받기</a>' in resp.text

    def test_error_page_escapes_path(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.side_effect = RuntimeError("x")
        mock_repo_manager.file_paths.return_value = set()
        resp = client.get('/test-repo/%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E.md')
        assert "<script>alert(1)" not in resp.text

    def test_api_errors_stay_json(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.side_effect = RuntimeError("x")
        resp = client.get("/api/repos/test-repo/render", params={"path": "docs/a.md"})
        assert "application/json" in resp.headers["content-type"]


class TestDownloadFilename:
    @staticmethod
    def _disposition(resp):
        value = resp.headers["content-disposition"]
        value.encode("latin-1")
        assert value.startswith("attachment; ")
        params = dict(part.split("=", 1) for part in value.removeprefix("attachment; ").split("; "))
        return params["filename"], params["filename*"]

    @pytest.fixture
    def fake_marp(self, monkeypatch):
        import httpx
        import app as viewer_app

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json, headers=None):
                return MagicMock(status_code=200, content=b"%PDF-1.4")

        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(viewer_app, "_prepare_pdf_markdown", lambda *args: "# A")

    @pytest.fixture
    def fake_html(self, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app, "_render_download_html", lambda *args: "<html></html>")

    @pytest.mark.parametrize("fmt,expected", [
        ("raw", "회의록 1차.md"),
        ("html", "회의록 1차.html"),
        ("pdf", "회의록 1차.pdf"),
    ])
    def test_korean_name(self, client, mock_repo_manager, fake_marp, fake_html, fmt, expected):
        from urllib.parse import unquote
        mock_repo_manager.read_file.return_value = b"# A"
        resp = client.get("/api/repos/test-repo/download", params={"path": "docs/회의록 1차.md", "format": fmt})
        assert resp.status_code == 200
        fallback, encoded = self._disposition(resp)
        assert fallback == f'"___ 1_.{expected.rsplit(".", 1)[1]}"'
        assert encoded.startswith("UTF-8''")
        assert " " not in encoded
        assert unquote(encoded.removeprefix("UTF-8''")) == expected

    @pytest.mark.parametrize("fmt,suffix", [("raw", ".md"), ("html", ".html"), ("pdf", ".pdf")])
    def test_quote_and_backslash_are_escaped(self, client, mock_repo_manager, fake_marp, fake_html, fmt, suffix):
        mock_repo_manager.read_file.return_value = b"# A"
        resp = client.get("/api/repos/test-repo/download", params={"path": 'docs/a"b\\c.md', "format": fmt})
        assert resp.status_code == 200
        fallback, encoded = self._disposition(resp)
        assert fallback == f'"a\\"b\\\\c{suffix}"'
        assert encoded == f"UTF-8''a%22b%5Cc{suffix}"

    def test_control_characters_are_replaced(self, client, mock_repo_manager):
        mock_repo_manager.read_file.return_value = b"x"
        resp = client.get("/api/repos/test-repo/download", params={"path": "docs/a\r\nb.txt"})
        assert resp.status_code == 200
        fallback, encoded = self._disposition(resp)
        assert fallback == '"a__b.txt"'
        assert encoded == "UTF-8''a%0D%0Ab.txt"

    def test_ascii_name(self, client, mock_repo_manager):
        mock_repo_manager.read_file.return_value = b"x"
        resp = client.get("/api/repos/test-repo/download", params={"path": "docs/readme.md"})
        assert resp.status_code == 200
        assert resp.headers["content-disposition"] == "attachment; filename=\"readme.md\"; filename*=UTF-8''readme.md"

    def test_missing_file_is_404(self, client, mock_repo_manager):
        import subprocess
        mock_repo_manager.read_file.side_effect = subprocess.CalledProcessError(128, ["git", "show"])
        resp = client.get("/api/repos/test-repo/download", params={"path": "docs/none.md"})
        assert resp.status_code == 404

    def test_invalid_path_is_404(self, client, mock_repo_manager):
        mock_repo_manager.read_file.side_effect = ValueError("Invalid path: ../x")
        resp = client.get("/api/repos/test-repo/download", params={"path": "../x"})
        assert resp.status_code == 404

    def test_unexpected_error_is_500_and_logged(self, client, monkeypatch, caplog):
        import app as viewer_app

        def broken(*args):
            raise UnicodeEncodeError("latin-1", "회", 0, 1, "ordinal not in range(256)")

        monkeypatch.setattr(viewer_app, "_render_download_html", broken)
        with caplog.at_level("ERROR"):
            resp = client.get("/api/repos/test-repo/download", params={"path": "docs/a.md", "format": "html"})
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Download failed"
        assert any("download_failed" in r.getMessage() for r in caplog.records)

    def test_marp_unreachable_is_502(self, client, monkeypatch):
        import httpx
        import app as viewer_app

        class DownClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json, headers=None):
                raise httpx.ConnectError("connection refused")

        monkeypatch.delenv("SYNCTX_ENABLED_SERVICES", raising=False)
        monkeypatch.setattr(httpx, "AsyncClient", DownClient)
        monkeypatch.setattr(viewer_app, "_prepare_pdf_markdown", lambda *args: "# A")
        resp = client.get("/api/repos/test-repo/download", params={"path": "docs/a.md", "format": "pdf"})
        assert resp.status_code == 502
        assert resp.json()["detail"] == "PDF conversion failed"


class TestWebhookToken:
    def test_webhook_no_token_passes_when_unset(self, client, mock_repo_manager, monkeypatch):
        from unittest.mock import AsyncMock
        monkeypatch.delenv("SYNCTX_SERVICE_TOKEN", raising=False)
        mock_repo_manager.fetch = AsyncMock(return_value="sha123")
        resp = client.post("/api/repos/test-repo/webhook", json={"after": "abc"})
        assert resp.status_code == 200

    def test_webhook_rejects_missing_token(self, client, mock_repo_manager, monkeypatch):
        monkeypatch.setenv("SYNCTX_SERVICE_TOKEN", "secret-token-123")
        resp = client.post("/api/repos/test-repo/webhook", json={"after": "abc"})
        assert resp.status_code == 401

    def test_webhook_rejects_wrong_token(self, client, mock_repo_manager, monkeypatch):
        monkeypatch.setenv("SYNCTX_SERVICE_TOKEN", "secret-token-123")
        resp = client.post(
            "/api/repos/test-repo/webhook",
            json={"after": "abc"},
            headers={"X-Service-Token": "wrong-token"},
        )
        assert resp.status_code == 401

    def test_webhook_accepts_correct_token(self, client, mock_repo_manager, monkeypatch):
        from unittest.mock import AsyncMock
        monkeypatch.setenv("SYNCTX_SERVICE_TOKEN", "secret-token-123")
        mock_repo_manager.fetch = AsyncMock(return_value="sha456")
        resp = client.post(
            "/api/repos/test-repo/webhook",
            json={"after": "abc"},
            headers={"X-Service-Token": "secret-token-123"},
        )
        assert resp.status_code == 200
        assert resp.json()["head_sha"] == "sha456"


class TestBoardRoute:
    @pytest.fixture
    def board_repo(self, mock_repo_manager, monkeypatch):
        import app as viewer_app
        from types import SimpleNamespace

        def _setup(repo_id, version):
            monkeypatch.setattr(viewer_app, "repo_managers", {repo_id: mock_repo_manager})
            mock_repo_manager.list_tree.return_value = [
                SimpleNamespace(type="file", name="task.md", path="docs/it's task.md"),
            ]
            mock_repo_manager.read_file_text.return_value = (
                f"---\ntitle: Task\nsynctx:\n  type: task\n  version: {json.dumps(version)}\n---\nbody\n"
            )

        return _setup

    def test_board_not_shadowed_by_permalink(self, client, board_repo):
        board_repo("test-repo", "v1")
        resp = client.get("/api/repos/test-repo/board/v1")
        assert resp.status_code == 200
        assert "<title>Scrum Board - v1</title>" in resp.text
        assert "IN_PROGRESS (0)" in resp.text

    def test_malicious_version_is_escaped(self, client, board_repo):
        version = "<script>alert(1)<img src=x onerror=alert(1)>"
        board_repo("test-repo", version)
        resp = client.get("/api/repos/test-repo/board/%3Cscript%3Ealert(1)%3Cimg%20src=x%20onerror=alert(1)%3E")
        assert resp.status_code == 200
        assert "<script" not in resp.text
        assert "<img" not in resp.text
        assert "<title>Scrum Board - &lt;script&gt;alert(1)&lt;img src=x onerror=alert(1)&gt;</title>" in resp.text
        assert "<h2>Scrum Board: &lt;script&gt;alert(1)&lt;img src=x onerror=alert(1)&gt;</h2>" in resp.text

    def test_malicious_repo_id_is_escaped(self, client, board_repo):
        board_repo("<script>'x", "v1")
        resp = client.get("/api/repos/%3Cscript%3E%27x/board/v1")
        assert resp.status_code == 200
        assert "<script" not in resp.text
        assert "<p>Repository: &lt;script&gt;&#x27;x</p>" in resp.text
        assert (
            "onclick=\"window.location.href='/view/api/repos/%3Cscript%3E%27x/render?path=docs/it%27s%20task.md'\""
            in resp.text
        )


class TestErrorResponsesHideExceptionText:
    SECRET = "SECRET-TOKEN-9876"

    def _git_error(self):
        import subprocess
        return subprocess.CalledProcessError(
            128, ["git", "-c", f"http.extraHeader=Authorization: Basic {self.SECRET}", "show"],
            stderr=f"fatal: https://{self.SECRET}@git.test/r.git".encode(),
        )

    @pytest.mark.parametrize("method,attr,url,status,detail", [
        ("get", "list_branches", "/api/repos/test-repo/branches", 400, "Failed to list branches"),
        ("get", "list_tree", "/api/repos/test-repo/tree", 400, "Failed to list files"),
        ("get", "read_file", "/api/repos/test-repo/download?path=docs/a.md&format=raw", 404, "File not found"),
        ("get", "read_file", "/raw/test-repo/docs/a.md", 404, "File not found"),
        ("get", "read_file_text", "/api/repos/test-repo/render?path=docs/a.md", 404, "File not found"),
        ("get", "list_tree", "/api/repos/test-repo/board/v1", 400, "Failed to build board"),
    ])
    def test_git_failure_detail_is_fixed_text(self, client, mock_repo_manager, caplog, method, attr, url, status, detail):
        getattr(mock_repo_manager, attr).side_effect = self._git_error()
        with caplog.at_level("WARNING"):
            resp = getattr(client, method)(url)
        assert resp.status_code == status
        assert resp.json()["detail"] == detail
        assert self.SECRET not in resp.text and "extraHeader" not in resp.text
        assert self.SECRET not in caplog.text

    def test_webhook_refresh_failure_detail_is_fixed_text(self, client, mock_repo_manager, monkeypatch, caplog):
        from unittest.mock import AsyncMock
        monkeypatch.delenv("SYNCTX_SERVICE_TOKEN", raising=False)
        mock_repo_manager.fetch = AsyncMock(side_effect=self._git_error())
        with caplog.at_level("WARNING"):
            resp = client.post("/api/repos/test-repo/webhook", json={"after": "abc"})
        assert resp.status_code == 502
        assert resp.json()["detail"] == "Repository refresh failed"
        assert self.SECRET not in resp.text and self.SECRET not in caplog.text


RENDER_SHA = "a" * 40
RENDER_RESULTS = ("ok", "denied_token", "denied_dest", "disabled", "not_html", "too_large", "bundle_error")


@pytest.fixture
def html_render(client, mock_repo_manager, monkeypatch):
    from types import SimpleNamespace

    import app as viewer_app
    from synctx_common.git_manager import GitRepoManager
    from synctx_common.render_token import issue_render_token

    policy = viewer_app.config.repositories[0].html_embed
    policy.enabled = True
    policy.render_files = True
    policy.max_bundle_bytes = 4096
    monkeypatch.setattr(viewer_app, "render_key", b"k" * 32)
    monkeypatch.setattr(viewer_app, "render_previous_key", None)
    manager = mock_repo_manager
    manager.validate_path.side_effect = lambda path: GitRepoManager.validate_path(manager, path)
    manager.resolve_commit_sha.return_value = RENDER_SHA
    manager.blob_size_resolved.return_value = 100
    files = {
        "docs/index.html": b'<html><head><title>HTML</title></head><body><script src="app.js"></script><iframe name="pf" src="next.htm"></iframe><a href="next.htm" target="pf">next</a></body></html>',
        "docs/app.js": b"window.snapshot = 1;",
        "docs/next.htm": b"<html><body>next snapshot</body></html>",
    }
    manager.read_resolved.side_effect = lambda path, sha: files[path]
    manager.read_file_text.return_value = "<html><body>source</body></html>"
    token = issue_render_token("test-repo", "main", RENDER_SHA, key=viewer_app.render_key)
    return SimpleNamespace(client=client, manager=manager, app=viewer_app, policy=policy, files=files,
                           token=token, url=f"/render/{token}/test-repo/docs/index.html")


def _assert_render_headers(response):
    assert response.headers["Content-Security-Policy"] == "sandbox allow-scripts"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert "X-Frame-Options" not in response.headers


def _render_counts(repo_id="test-repo"):
    from synctx_common.observability import html_render_total

    return {result: html_render_total.labels(repo_id=repo_id, result=result)._value.get() for result in RENDER_RESULTS}


class TestHtmlRenderRoute:
    @pytest.mark.parametrize("dest", ["iframe", "frame"])
    def test_success_keeps_frames_and_uses_one_sha(self, html_render, dest):
        env = html_render
        before = _render_counts()
        response = env.client.get(env.url, headers={"Sec-Fetch-Dest": dest})
        assert response.status_code == 200
        _assert_render_headers(response)
        assert '<iframe name="pf" src="next.htm">' in response.text
        assert '<a href="next.htm" target="pf">' in response.text
        assert "frame-src 'self'" in response.text
        assert "window.snapshot = 1;" in response.text
        env.manager.ensure_commit_available.assert_called_once_with("main", RENDER_SHA)
        env.manager.blob_size_resolved.assert_called_once_with("docs/index.html", RENDER_SHA)
        assert [call.args for call in env.manager.read_resolved.call_args_list] == [
            ("docs/index.html", RENDER_SHA), ("docs/app.js", RENDER_SHA),
        ]
        env.manager.resolve_ref.assert_not_called()
        env.manager.resolve_commit_sha.assert_not_called()
        after = _render_counts()
        assert {r: after[r] - before[r] for r in RENDER_RESULTS} == {r: int(r == "ok") for r in RENDER_RESULTS}

    @pytest.mark.parametrize("case,status,result", [
        ("token", 403, "denied_token"),
        ("no_dest", 403, "denied_dest"),
        ("document", 403, "denied_dest"),
        ("image", 403, "denied_dest"),
        ("enabled", 403, "disabled"),
        ("render_files", 403, "disabled"),
        ("extension", 415, "not_html"),
        ("pre_size", 413, "too_large"),
        ("bundle_size", 413, "bundle_error"),
    ])
    def test_failures_and_result_counters(self, html_render, monkeypatch, case, status, result):
        from synctx_common.html_bundle import BundleError

        env = html_render
        url = env.url
        headers = {"Sec-Fetch-Dest": "iframe"}
        if case == "token":
            url = url.replace(env.token, "invalid")
        elif case == "no_dest":
            headers = {}
        elif case in ("document", "image"):
            headers["Sec-Fetch-Dest"] = case
        elif case in ("enabled", "render_files"):
            setattr(env.policy, case, False)
        elif case == "extension":
            url = url.replace("index.html", "index.txt")
        elif case == "pre_size":
            env.manager.blob_size_resolved.return_value = env.policy.max_bundle_bytes + 1
        elif case == "bundle_size":
            monkeypatch.setattr(env.app, "bundle", MagicMock(side_effect=BundleError("too_large", "大")))
        before = _render_counts()
        response = env.client.get(url, headers=headers)
        assert response.status_code == status
        _assert_render_headers(response)
        after = _render_counts()
        assert {r: after[r] - before[r] for r in RENDER_RESULTS} == {r: int(r == result) for r in RENDER_RESULTS}
        env.manager.read_resolved.assert_not_called()
        if case != "pre_size" and case != "bundle_size":
            env.manager.ensure_commit_available.assert_not_called()

    @pytest.mark.parametrize("case,status", [("authz", 403), ("sha", 404), ("file", 404), ("validation", 422), ("unexpected", 500)])
    def test_json_errors_have_render_headers_and_no_result_count(self, html_render, monkeypatch, case, status):
        from fastapi import Depends
        from fastapi.routing import APIRoute

        env = html_render
        if case == "authz":
            env.app.config.authz.enabled = True
        elif case == "sha":
            env.manager.ensure_commit_available.side_effect = FileNotFoundError("secret")
        elif case == "file":
            env.manager.blob_size_resolved.side_effect = FileNotFoundError("secret")
        elif case == "unexpected":
            env.manager.blob_size_resolved.side_effect = RuntimeError("secret")
        else:
            def validate(number: int):
                return number

            route = APIRoute("/render/{token}/{repo_id}/{path:path}", env.app.render_html,
                             dependencies=[Depends(validate)])
            monkeypatch.setattr(env.app.app.router, "routes", [route, *env.app.app.router.routes])
        before = _render_counts()
        response = env.client.get(env.url, headers={"Sec-Fetch-Dest": "iframe"})
        assert response.status_code == status
        assert response.headers["content-type"] == "application/json"
        _assert_render_headers(response)
        assert _render_counts() == before
        assert "secret" not in response.text
        if case == "sha":
            assert "새로 고침" in response.json()["detail"]

    def test_authz_headers_and_admin_role(self, html_render):
        env = html_render
        env.app.config.authz.enabled = True
        env.app.config.authz.role_mappings.viewer = ["viewer-role"]
        env.app.config.authz.role_mappings.platform_admin = ["admin-role"]
        for role in ("viewer-role", "admin-role"):
            response = env.client.get(env.url, headers={"Sec-Fetch-Dest": "iframe", "X-Forwarded-Groups": f"other, {role}"})
            assert response.status_code == 200

    def test_authz_failure_precedes_token_validation(self, html_render, monkeypatch):
        env = html_render
        env.app.config.authz.enabled = True
        verify = MagicMock(side_effect=AssertionError("authz must run first"))
        monkeypatch.setattr(env.app, "verify_render_token", verify)
        response = env.client.get(env.url.replace(env.token, "invalid"))
        assert response.status_code == 403
        _assert_render_headers(response)
        verify.assert_not_called()

    def test_validation_order(self, html_render, monkeypatch):
        from synctx_common.render_token import RenderTokenError

        env = html_render
        events = []

        def access(groups):
            events.append("authz")

        def token(*args, **kwargs):
            events.append("token")
            return "main", RENDER_SHA

        def policy(repo_id):
            events.append("policy")
            return env.policy

        def path(value):
            events.append("path")
            return value

        monkeypatch.setattr(env.app, "verify_viewer_access", access)
        monkeypatch.setattr(env.app, "verify_render_token", token)
        monkeypatch.setattr(env.app, "_html_embed_policy", policy)
        env.manager.validate_path.side_effect = path
        env.manager.ensure_commit_available.side_effect = lambda *args: events.append("sha")
        env.manager.blob_size_resolved.side_effect = lambda *args: events.append("size") or 100
        original_bundle = env.app.bundle

        def bundle(*args, **kwargs):
            events.append("bundle")
            return original_bundle(*args, **kwargs)

        monkeypatch.setattr(env.app, "bundle", bundle)
        assert env.client.get(env.url, headers={"Sec-Fetch-Dest": "iframe"}).status_code == 200
        assert events == ["authz", "token", "policy", "path", "sha", "size", "bundle"]
        events.clear()
        assert env.client.get(env.url).status_code == 403
        assert events == ["authz", "token"]
        events.clear()
        monkeypatch.setattr(env.app, "verify_render_token", MagicMock(side_effect=RenderTokenError("bad")))
        assert env.client.get(env.url).status_code == 403
        assert events == ["authz"]

    @pytest.mark.parametrize("reason,status", [("too_large", 413), ("missing_file", 404), ("unsupported", 422), ("outside_repo", 422)])
    def test_bundle_errors_escape_message(self, html_render, monkeypatch, reason, status):
        from synctx_common.html_bundle import BundleError

        env = html_render
        monkeypatch.setattr(env.app, "bundle", MagicMock(side_effect=BundleError(reason, '<img src=x onerror="bad"> & reason')))
        response = env.client.get(env.url, headers={"Sec-Fetch-Dest": "iframe"})
        assert response.status_code == status
        assert '<img' not in response.text
        assert '&lt;img src=x onerror=&quot;bad&quot;&gt; &amp; reason' in response.text
        _assert_render_headers(response)

    def test_actual_bundle_dependency_size_is_bundle_error(self, html_render):
        env = html_render
        env.files["docs/app.js"] = b"x" * env.policy.max_bundle_bytes
        before = _render_counts()
        response = env.client.get(env.url, headers={"Sec-Fetch-Dest": "iframe"})
        assert response.status_code == 413
        assert _render_counts()["bundle_error"] == before["bundle_error"] + 1
        assert _render_counts()["too_large"] == before["too_large"]

    def test_missing_bundle_dependency_returns_404(self, html_render):
        import subprocess

        env = html_render

        def read(path, sha):
            if path == "docs/app.js":
                raise subprocess.CalledProcessError(128, ["git", "show"])
            return env.files[path]

        env.manager.read_resolved.side_effect = read
        before = _render_counts()
        response = env.client.get(env.url, headers={"Sec-Fetch-Dest": "iframe"})
        assert response.status_code == 404
        assert "docs/app.js" in response.text
        _assert_render_headers(response)
        assert _render_counts()["bundle_error"] == before["bundle_error"] + 1

    @pytest.mark.parametrize("suffix", ["HTML", "HtM"])
    def test_extension_ignores_case(self, html_render, suffix):
        env = html_render
        path = f"docs/index.{suffix}"
        env.files[path] = b"<html><body>ok</body></html>"
        assert env.client.get(env.url.replace("docs/index.html", path), headers={"Sec-Fetch-Dest": "iframe"}).status_code == 200

    @pytest.mark.parametrize("url_repo", ["other", "test-repo"])
    def test_unknown_repo_is_denied_and_label_is_bounded(self, html_render, url_repo):
        from synctx_common.render_token import issue_render_token

        env = html_render
        token = issue_render_token(url_repo, "main", RENDER_SHA, key=env.app.render_key)
        before = _render_counts("unknown")
        response = env.client.get(f"/render/{token}/other/docs/index.html", headers={"Sec-Fetch-Dest": "iframe"})
        assert response.status_code == 403
        assert _render_counts("unknown")["denied_token"] == before["denied_token"] + 1
        env.manager.ensure_commit_available.assert_not_called()

    def test_root_escape_and_expiry_have_distinct_guidance(self, html_render):
        from synctx_common.render_token import issue_render_token

        env = html_render
        root_escape = env.client.get(f"/render/{env.token}/test-repo/../other/index.html", headers={"Sec-Fetch-Dest": "iframe"})
        expired_token = issue_render_token("test-repo", "main", RENDER_SHA, key=env.app.render_key, now=1, ttl_seconds=1)
        expired = env.client.get(env.url.replace(env.token, expired_token), headers={"Sec-Fetch-Dest": "iframe"})
        assert root_escape.status_code == expired.status_code == 403
        assert "문서 주소 또는 토큰이 유효하지 않습니다" in root_escape.text
        assert "유효 시간이 지났습니다" in expired.text
        assert "유효 시간이 지났습니다" not in root_escape.text
        assert "새로 고치세요" in expired.text

    @pytest.mark.parametrize("prefix", ["", "/view"])
    def test_mount_headers_and_http_metric_path(self, html_render, prefix):
        from synctx_common.observability import http_requests_total
        from standalone import create_app

        env = html_render
        app = env.app.app
        if prefix:
            app = create_app(viewer=env.app, refresh_interval=0)
        metric = http_requests_total.labels(service="viewer", method="GET", path="/render/{token}/{repo_id}/{path}", status=200)
        before = metric._value.get()
        response = TestClient(app).get(prefix + env.url, headers={"Sec-Fetch-Dest": "iframe"})
        assert response.status_code == 200
        _assert_render_headers(response)
        assert metric._value.get() == before + 1
        assert not any(env.token in sample.labels.get("path", "") for family in http_requests_total.collect() for sample in family.samples)

    @pytest.mark.parametrize("encoded", ["../secret.html", "%2e%2e/secret.html"])
    def test_path_rejection_with_direct_asgi_request(self, html_render, encoded):
        import asyncio
        from urllib.parse import unquote

        env = html_render
        raw_path = f"/render/{env.token}/test-repo/{encoded}"
        messages = []

        async def request():
            received = False

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": b""}
                await asyncio.Event().wait()

            async def send(message):
                messages.append(message)

            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "method": "GET",
                     "scheme": "http", "path": unquote(raw_path), "raw_path": raw_path.encode(), "query_string": b"",
                     "root_path": "", "headers": [(b"host", b"testserver"), (b"sec-fetch-dest", b"iframe")],
                     "server": ("testserver", 80), "client": ("testclient", 123), "http_version": "1.1"}
            await env.app.app(scope, receive, send)

        before = _render_counts()
        asyncio.run(request())
        start = next(m for m in messages if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body").decode()
        assert start["status"] == 400
        assert "secret" not in body and "Invalid path" not in body
        env.manager.validate_path.assert_called_once_with("../secret.html")
        env.manager.ensure_commit_available.assert_not_called()
        assert _render_counts() == before


class TestHtmlRenderScreen:
    @pytest.mark.parametrize("endpoint", ["/test-repo/docs/index.html", "/api/repos/test-repo/render?path=docs/index.html"])
    @pytest.mark.parametrize("layout", [None, "full"])
    def test_render_screen_token_dom_and_csp(self, html_render, monkeypatch, endpoint, layout):
        import html
        import re
        from urllib.parse import parse_qs, urlsplit
        from synctx_common.render_token import verify_render_token

        env = html_render
        issue = MagicMock(wraps=env.app.issue_render_token)
        monkeypatch.setattr(env.app, "issue_render_token", issue)
        monkeypatch.setattr(env.app, "load_render_keys", MagicMock(side_effect=AssertionError("request key reload")))
        before = _render_counts()
        params = {"mode": "render"}
        if layout:
            params["layout"] = layout
        if "?" in endpoint:
            params["path"] = "docs/index.html"
        response = env.client.get(endpoint, params=params)
        assert response.status_code == 200
        assert "frame-src 'self'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "X-Synctx-Html-Render" not in response.headers
        assert 'data-html-render data-render-layout="fit" data-render-aspect="16/9"' in response.text
        assert 'data-render-bars' in response.text and 'data-render-stage' in response.text
        assert 'data-action="render-fit" aria-pressed="true"' in response.text
        assert 'data-action="render-ratio" aria-pressed="false"' in response.text
        assert 'data-action="render-fullscreen"' in response.text
        assert "HTML과 스크립트를 실행합니다" in response.text and "새로 고치세요" in response.text
        frame = re.search(r'<iframe data-render-frame[^>]*src="([^"]+)"', response.text).group(1)
        assert 'sandbox="allow-scripts"' in response.text
        assert frame.startswith("/view/render/")
        token = frame.split("/")[3]
        assert verify_render_token(token, "test-repo", key=env.app.render_key) == ("main", RENDER_SHA)
        issue.assert_called_once_with("test-repo", "main", RENDER_SHA, key=env.app.render_key,
                                      ttl_seconds=env.app.config.viewer.render_token_ttl_seconds)
        env.manager.resolve_commit_sha.assert_called_once_with("main")
        env.manager.read_file_text.assert_not_called()
        env.manager.read_resolved.assert_not_called()
        assert _render_counts() == before
        link = re.search(r'data-action="render-new-tab" href="([^"]+)" target="_blank" rel="noopener noreferrer"', response.text).group(1)
        assert parse_qs(urlsplit(html.unescape(link)).query) == {"mode": ["render"], "layout": ["full"]}
        if layout == "full":
            assert 'data-render-full="true"' in response.text
            assert "page-render-full" in response.text
            assert 'id="shell-nav"' not in response.text
            assert '<header' not in response.text
        else:
            assert 'data-render-full="true"' not in response.text
            assert 'id="shell-nav"' in response.text
            assert '<header' in response.text

    @pytest.mark.parametrize("mode", [None, "source", "meeting"])
    def test_source_has_links_without_issuing_token_and_ignores_layout(self, html_render, monkeypatch, mode):
        env = html_render
        issue = MagicMock(side_effect=AssertionError("source must not issue"))
        monkeypatch.setattr(env.app, "issue_render_token", issue)
        params = {"layout": "unrecognized", "ref": "feat/a&b"}
        if mode:
            params["mode"] = mode
        response = env.client.get("/test-repo/docs/index.html", params=params)
        assert response.status_code == 200
        assert 'data-action="render-open" href="/view/test-repo/docs/index.html?mode=render&amp;ref=feat%2Fa%26b"' in response.text
        assert 'data-action="render-new-tab" href="/view/test-repo/docs/index.html?mode=render&amp;layout=full&amp;ref=feat%2Fa%26b" target="_blank" rel="noopener noreferrer"' in response.text
        assert 'data-render-frame' not in response.text
        assert '/view/render/' not in response.text
        assert "frame-src 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "&lt;html&gt;" in response.text
        issue.assert_not_called()
        env.manager.resolve_commit_sha.assert_not_called()

    @pytest.mark.parametrize("enabled,render_files,path", [
        (False, False, "index.html"), (True, False, "index.html"), (False, True, "index.htm"),
        (True, True, "script.js"), (True, True, "readme.md"),
    ])
    def test_non_renderable_mode_render_keeps_normal_csp_and_no_buttons(self, html_render, monkeypatch, enabled, render_files, path):
        env = html_render
        env.policy.enabled = enabled
        env.policy.render_files = render_files
        monkeypatch.setattr(env.app, "issue_render_token", MagicMock(side_effect=AssertionError("not enabled")))
        response = env.client.get(f"/test-repo/docs/{path}", params={"mode": "render", "layout": "full"})
        assert response.status_code == 200
        assert 'data-html-render' not in response.text
        assert 'data-action="render-open"' not in response.text
        assert 'data-action="render-new-tab"' not in response.text
        assert "frame-src 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Frame-Options"] == "DENY"

    @pytest.mark.parametrize("failure", ["resolve_commit_sha", "blob_size_resolved"])
    def test_error_screen_never_widens_csp(self, html_render, monkeypatch, failure):
        env = html_render
        getattr(env.manager, failure).side_effect = FileNotFoundError("missing")
        issue = MagicMock(side_effect=AssertionError("error screen must not issue"))
        monkeypatch.setattr(env.app, "issue_render_token", issue)
        response = env.client.get("/test-repo/docs/index.html?mode=render")
        assert response.status_code != 200
        assert "frame-src 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Frame-Options"] == "DENY"
        assert 'data-render-frame' not in response.text
        issue.assert_not_called()

    def test_invalid_render_layout_rejected(self, html_render):
        response = html_render.client.get("/api/repos/test-repo/render?path=docs/index.html&mode=render&layout=ratio")
        assert response.status_code == 422
        html_render.manager.resolve_commit_sha.assert_not_called()

    def test_selected_branch_token_pins_reads_after_branch_moves(self, html_render):
        import re
        from synctx_common.render_token import verify_render_token

        env = html_render
        env.app.config.viewer.render_token_ttl_seconds = 300
        response = env.client.get("/test-repo/docs/index.html", params={"mode": "render", "ref": "feature/a"})
        assert response.status_code == 200
        frame = re.search(r'<iframe data-render-frame[^>]*src="([^"]+)"', response.text).group(1)
        token = frame.split("/")[3]
        assert verify_render_token(token, "test-repo", key=env.app.render_key) == ("feature/a", RENDER_SHA)
        env.manager.resolve_commit_sha.assert_called_once_with("feature/a")
        env.manager.resolve_commit_sha.return_value = "b" * 40
        first = env.client.get(frame.removeprefix("/view"), headers={"Sec-Fetch-Dest": "iframe"})
        second = env.client.get(frame.removeprefix("/view").replace("index.html", "next.htm") + "?ref=elsewhere",
                                headers={"Sec-Fetch-Dest": "frame"})
        assert first.status_code == second.status_code == 200
        assert "next snapshot" in second.text
        assert all(call.args[1] == RENDER_SHA for call in env.manager.read_resolved.call_args_list)
        assert all(call.args == ("feature/a", RENDER_SHA) for call in env.manager.ensure_commit_available.call_args_list)
        env.manager.resolve_ref.assert_not_called()
        env.manager.resolve_commit_sha.assert_called_once()

    def test_standalone_screen_uses_working_public_frame_url(self, html_render):
        import re
        from standalone import create_app

        env = html_render
        client = TestClient(create_app(viewer=env.app, refresh_interval=0))
        screen = client.get("/view/test-repo/docs/index.html?mode=render&layout=full")
        assert screen.status_code == 200
        frame_url = re.search(r'<iframe data-render-frame[^>]*src="([^"]+)"', screen.text).group(1)
        assert frame_url.startswith("/view/render/")
        frame = client.get(frame_url, headers={"Sec-Fetch-Dest": "iframe"})
        assert frame.status_code == 200
        _assert_render_headers(frame)
        assert screen.headers["X-Frame-Options"] == "DENY"

    def test_render_path_text_in_source_does_not_trigger_header_exception(self, html_render):
        response = html_render.client.get("/test-repo/docs/render/index.html")
        assert response.status_code == 200
        assert "frame-src 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Frame-Options"] == "DENY"
