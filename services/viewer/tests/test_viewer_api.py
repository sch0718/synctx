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
        assert "too large" in resp.json()["detail"].lower()


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

    def test_sidebar_path_info_has_folder_links(self, client, mock_repo_manager):
        import re
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/sub/readme.md")
        match = re.search(r'<div class="path-info">(.*?)</div>', resp.text, re.S)
        assert match is not None
        assert '<a href="/view/test-repo?path=docs/sub">sub</a>' in match.group(1)

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
        assert "expandToPath(" in resp.text


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
        match = re.search(r'<div class="tree" id="file-tree">(.*)</div>\s*</div>\s*</div>\s*<div class="viewer-content">', text, re.S)
        assert match is not None
        return match.group(1)

    def test_tree_is_below_toc(self, client, tree_manager):
        resp = client.get("/test-repo/docs/sub/readme.md")
        assert resp.status_code == 200
        assert resp.text.index('<div class="toc">') < resp.text.index('id="file-tree"') < resp.text.index('<div class="viewer-content">')

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
        assert "function toggleDir(" in text
        assert 'var ACTIVE_REPO = "test-repo";' in text

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
        assert "Failed to load file tree." in resp.text


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
        assert '<a href="/view/test-repo?ref=feat%2Fx">test-repo</a>' in resp.text
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
    def _script_filename(text):
        import re
        match = re.search(r"var filename = (.*?)\.split\('/'\)", text)
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
        literal = self._script_filename(resp.text)
        assert "<" not in literal
        assert json.loads(literal) == "docs/x';alert(1);'</script>.md"

    def test_script_filename_keeps_normal_path(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "# Hello"
        resp = client.get("/test-repo/docs/readme.md")
        assert self._script_filename(resp.text) == '"docs/readme.md"'

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

    def test_subtree_script_escapes_entries(self, client, mock_repo_manager):
        text = self._repo_page(client, mock_repo_manager, [])
        assert "escapeHtml(d.name)" in text
        assert "escapeHtml(d.path)" in text
        assert "encodePath(f.path)" in text
        assert "escapeHtml(f.name)" in text
        assert "+ f.name +" not in text
        assert "+ d.name +" not in text

    def test_download_link_encodes_path(self, client, mock_repo_manager):
        mock_repo_manager.read_file_text.return_value = "print(1)"
        resp = client.get('/test-repo/src/a%26b%23c%22%3E.py')
        assert resp.status_code == 200
        assert 'href="/view/api/repos/test-repo/download?path=src/a%26b%23c%22%3E.py"' in resp.text


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
