"""Viewer 회의 모드 테스트."""

import json
import re
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient

BACKSLASH = chr(92)


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    from synctx_common.config import SynctxConfig, ViewerConfig, AuthzConfig, RepoProfile, RepoAuthConfig
    mock_config = SynctxConfig(
        authz=AuthzConfig(enabled=False),
        viewer=ViewerConfig(max_render_size_bytes=100_000, cdn_base_url="/cdn"),
        repositories=[RepoProfile(id="test-repo", name="T", url="https://git.test/r.git", auth=RepoAuthConfig(type="none"))],
    )
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "config", mock_config)


def _profile(collab=True):
    from synctx_common.config import RepoProfile, RepoAuthConfig, CollabRepoConfig
    return RepoProfile(
        id="test-repo", name="T", url="https://git.test/r.git", default_ref="main",
        auth=RepoAuthConfig(type="none"), collab=CollabRepoConfig(enabled=collab),
    )


@pytest.fixture
def manager():
    m = MagicMock()
    m.profile = _profile()
    m.file_size.return_value = 100
    return m


@pytest.fixture
def client(manager, monkeypatch):
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "repo_managers", {"test-repo": manager})
    return TestClient(viewer_app.app, raise_server_exceptions=False)


def meeting_doc(title="DX 21번 검토", starts_at="2026-10-08T14:00+09:00", body="# 회의 자료\n\n## 안건\n\n본문 내용\n"):
    fm = {"synctx": {"type": "meeting", "meeting": {
        "id": "dx-21", "title": title, "starts_at": starts_at, "channel": "dx-team"}}}
    return "---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n" + body


def config_json(html: str) -> str:
    m = re.search(r'<script type="application/json" id="meeting-config">(.*?)</script>', html, re.DOTALL)
    return m.group(1) if m else None


def article(html: str) -> str:
    return re.search(r'<article class="markdown-body">(.*?)</article>', html, re.DOTALL).group(1)


class TestEncodeForScript:
    def test_escapes_and_roundtrip(self):
        import meeting
        value = {"t": "</script><script>alert(1)</script> <!-- --> &amp; " + chr(0x2028) + chr(0x2029)}
        encoded = meeting.encode_for_script(value)
        for ch in ("<", ">", "&", chr(0x2028), chr(0x2029)):
            assert ch not in encoded
        assert BACKSLASH + "u003c" in encoded
        assert BACKSLASH + "u003e" in encoded
        assert BACKSLASH + "u0026" in encoded
        assert BACKSLASH + "u2028" in encoded
        assert json.loads(encoded) == value

    def test_keeps_korean(self):
        import meeting
        assert "회의" in meeting.encode_for_script({"t": "회의"})


class TestResolveMeetingContext:
    def test_not_meeting_document(self):
        import meeting
        ctx = meeting.resolve_meeting_context(_profile(), "a.md", {"synctx": {"type": "doc"}}, None)
        assert (ctx.enabled, ctx.reason) == (False, None)

    def test_non_markdown_path(self):
        import meeting
        ctx = meeting.resolve_meeting_context(_profile(), "a.py", {"synctx": {"type": "meeting"}}, None)
        assert (ctx.enabled, ctx.reason) == (False, None)

    def test_invalid_meeting(self):
        import meeting
        fm = yaml.safe_load(meeting_doc(starts_at="2026-10-08T14:00").split("---\n")[1])
        ctx = meeting.resolve_meeting_context(_profile(), "a.md", fm, None)
        assert ctx.enabled is False
        assert ctx.reason == "회의 정보에 오류가 있습니다: starts_at에 타임존이 없습니다(예: 2026-10-08T14:00+09:00)"

    def test_collab_disabled(self):
        import meeting
        fm = yaml.safe_load(meeting_doc().split("---\n")[1])
        ctx = meeting.resolve_meeting_context(_profile(collab=False), "a.md", fm, None)
        assert ctx.reason == "이 저장소는 회의 연동 대상이 아닙니다"

    def test_non_default_ref(self):
        import meeting
        fm = yaml.safe_load(meeting_doc().split("---\n")[1])
        ctx = meeting.resolve_meeting_context(_profile(), "a.md", fm, "dev")
        assert ctx.reason == "회의 모드는 기본 브랜치에서만 사용할 수 있습니다"

    def test_default_ref_explicit(self):
        import meeting
        fm = yaml.safe_load(meeting_doc().split("---\n")[1])
        ctx = meeting.resolve_meeting_context(_profile(), "a.md", fm, "main")
        assert ctx.enabled is True
        assert ctx.meeting.id == "dx-21"


class TestRenderModes:
    def test_normal_view_has_meeting_button_and_download(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        resp = client.get("/test-repo/docs/m.md")
        assert resp.status_code == 200
        assert 'href="/view/test-repo/docs/m.md?mode=meeting"' in resp.text
        assert "다운로드" in resp.text
        assert config_json(resp.text) is None

    def test_meeting_mode_injects_assets(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        resp = client.get("/test-repo/docs/m.md?mode=meeting")
        assert resp.status_code == 200
        html = resp.text
        cfg = json.loads(config_json(html))
        assert cfg == {
            "schema": 1,
            "repo_id": "test-repo",
            "meeting": {"id": "dx-21", "title": "DX 21번 검토", "starts_at": "2026-10-08T14:00:00+09:00"},
            "path": "docs/m.md",
            "storage_key": "synctx-meeting:test-repo:dx-21:v1",
            "root_selector": "article.markdown-body",
            "export_url": "/view/api/repos/test-repo/meeting-export?path=docs/m.md",
            "readonly": False,
        }
        assert '<link rel="stylesheet" href="/view/static/meeting/meeting.css">' in html.split("</head>")[0]
        core = html.index('src="/view/static/meeting/memo-core.js"')
        ui = html.index('src="/view/static/meeting/meeting.js"')
        assert html.index('id="meeting-config"') < core < ui
        assert "?mode=meeting" not in html

    def test_api_render_route_accepts_mode(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        resp = client.get("/api/repos/test-repo/render", params={"path": "docs/m.md", "mode": "meeting"})
        assert config_json(resp.text) is not None

    @pytest.mark.parametrize("doc,profile,query,expected", [
        ("invalid", True, "?mode=meeting", "회의 정보에 오류가 있습니다: starts_at에 타임존이 없습니다"),
        ("valid", False, "?mode=meeting", "이 저장소는 회의 연동 대상이 아닙니다"),
        ("valid", True, "?mode=meeting&ref=dev", "회의 모드는 기본 브랜치에서만 사용할 수 있습니다"),
    ])
    def test_unavailable_shows_notice_outside_article(self, client, manager, doc, profile, query, expected):
        manager.profile = _profile(collab=profile)
        manager.read_file_text.return_value = meeting_doc(starts_at="2026-10-08T14:00") if doc == "invalid" else meeting_doc()
        resp = client.get("/test-repo/docs/m.md" + query)
        assert resp.status_code == 200
        assert expected in resp.text
        assert expected not in article(resp.text)
        assert config_json(resp.text) is None
        assert 'class="meeting-notice"' in resp.text

    def test_non_meeting_doc_with_mode_has_no_notice(self, client, manager):
        manager.read_file_text.return_value = "# 일반 문서"
        resp = client.get("/test-repo/docs/a.md?mode=meeting")
        assert 'class="meeting-notice"' not in resp.text
        assert config_json(resp.text) is None

    def test_invalid_meeting_has_no_meeting_button(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(starts_at="2026-10-08T14:00")
        resp = client.get("/test-repo/docs/m.md")
        assert "?mode=meeting" not in resp.text

    def test_malicious_title_is_encoded(self, client, manager):
        title = "</script><script>alert(1)</script> <!-- --> &amp; " + chr(0x2028) + " 끝"
        manager.read_file_text.return_value = meeting_doc(title=title)
        resp = client.get("/test-repo/docs/m.md?mode=meeting")
        raw = config_json(resp.text)
        for ch in ("<", ">", "&", chr(0x2028)):
            assert ch not in raw
        assert json.loads(raw)["meeting"]["title"] == title


class TestStaticAssets:
    @pytest.mark.parametrize("name", ["meeting.css", "memo-core.js", "meeting.js"])
    def test_served(self, client, name):
        resp = client.get(f"/static/meeting/{name}")
        assert resp.status_code == 200

    def test_meeting_js_has_no_literal_markers(self):
        import meeting
        source = (meeting.STATIC_DIR / "meeting.js").read_text(encoding="utf-8")
        assert "synctx-meeting-embed" not in source
        assert "</script" not in source.lower()
        assert BACKSLASH + "u" not in source

    @pytest.mark.parametrize("name", ["memo-core.js", "meeting.js"])
    def test_inlined_sources_have_no_script_state_literals(self, name):
        import meeting
        source = (meeting.STATIC_DIR / name).read_text(encoding="utf-8").lower()
        assert "<!--" not in source
        assert "<script" not in source
        assert "</script" not in source


from html.parser import HTMLParser


class _TextCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def article_text(html: str) -> str:
    collector = _TextCollector()
    collector.feed(article(html))
    return "".join(collector.parts)


RICH_BODY = (
    "# 회의 자료\n\n"
    "## 안건\n\n"
    "백업 결과를 월 1회 보고한다. [다른 문서](other.md)\n\n"
    "| 항목 | 담당 |\n|---|---|\n| 백업 | IT |\n\n"
    "```python\nprint('x')\n```\n\n"
    "<script>alert('본문 스크립트')</script>\n"
)

MERMAID_BODY = "# 다이어그램\n\n```mermaid\nflowchart LR\n  A --> B\n```\n\n뒤의 문장\n"


class TestMeetingExport:
    def _get(self, client, path="docs/m.md"):
        return client.get("/api/repos/test-repo/meeting-export", params={"path": path})

    def test_404_when_not_meeting(self, client, manager):
        manager.read_file_text.return_value = "# 일반 문서"
        assert self._get(client, "docs/a.md").status_code == 404

    def test_404_when_collab_disabled(self, client, manager):
        manager.profile = _profile(collab=False)
        manager.read_file_text.return_value = meeting_doc()
        assert self._get(client).status_code == 404

    def test_413_when_too_large(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        manager.file_size.return_value = 10_000_000
        assert self._get(client).status_code == 413

    def test_assembled_document(self, client, manager):
        import meeting
        manager.read_file_text.return_value = meeting_doc(body=RICH_BODY)
        resp = self._get(client)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        html = resp.text
        cfg = json.loads(config_json(html))
        assert cfg["readonly"] is True
        assert cfg["export_url"] is None
        assert cfg["storage_key"] == "synctx-meeting:test-repo:dx-21:v1"
        assert html.count(meeting.EMBED_MARKER) == 1
        css = (meeting.STATIC_DIR / "meeting.css").read_text(encoding="utf-8")
        assert html.count(css) == 1
        assert html.count("root.SynctxMemoCore = {") == 1
        assert html.count("synctx 회의 모드 화면") == 1
        marker_at = html.index(meeting.EMBED_MARKER)
        assert marker_at < html.index('id="meeting-config"') < html.index("root.SynctxMemoCore = {")
        assert html.rstrip().endswith("</body>\n</html>")
        assert 'src="http' not in html and "href=\"http" not in html
        assert "본문 스크립트" not in article(html)

    def test_csp_meta_in_head(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body=RICH_BODY)
        html = self._get(client).text
        csp = (
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:\">"
        )
        assert html.count(csp) == 1
        assert html.index("<head>") < html.index(csp) < html.index("</head>")

    def test_article_text_matches_viewer(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body=RICH_BODY)
        viewer_html = client.get("/test-repo/docs/m.md?mode=meeting").text
        export_html = self._get(client).text
        assert article_text(export_html) == article_text(viewer_html)

    def test_mermaid_document_exports(self, client, manager):
        import meeting
        manager.read_file_text.return_value = meeting_doc(body=MERMAID_BODY)
        resp = self._get(client)
        assert resp.status_code == 200
        assert resp.text.count(meeting.EMBED_MARKER) == 1
        viewer_html = client.get("/test-repo/docs/m.md?mode=meeting").text
        assert article_text(resp.text) == article_text(viewer_html)

    def test_malicious_title_encoded_in_export(self, client, manager):
        # validate_meeting이 제목 앞뒤 공백(U+2028 포함)을 strip하므로 U+2028은 안쪽에 둔다
        title = "</script><script>alert(1)</script> <!-- --> &amp; " + chr(0x2028) + " 끝"
        manager.read_file_text.return_value = meeting_doc(title=title)
        raw = config_json(self._get(client).text)
        for ch in ("<", ">", "&", chr(0x2028)):
            assert ch not in raw
        assert json.loads(raw)["meeting"]["title"] == title

    def test_malicious_heading_title_escaped(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body="# </title><script>alert(1)</script>\n\n본문\n")
        html = self._get(client).text
        head = html.split("</head>")[0]
        assert "<script>alert(1)</script>" not in head
        assert "<title>&lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;</title>" in head

    def test_marker_in_body_fails_assembly(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body="# 회의\n\n<!--synctx-meeting-embed-->\n")
        assert self._get(client).status_code == 500


class TestRenderDownloadHtml:
    def test_html_filter_applies_to_body_only(self):
        from renderer import render_download_html
        html = render_download_html("# 제목\n\n```mermaid\nflowchart LR\n A-->B\n```\n", html_filter=lambda s: s.replace("제목", "필터됨"))
        assert "필터됨" in html
        assert "<script>" in html

    def test_title_escaped(self):
        from renderer import render_download_html
        html = render_download_html("# </title><script>x()</script>\n")
        assert "<title>&lt;/title&gt;&lt;script&gt;x()&lt;/script&gt;</title>" in html

    def test_article_has_no_padding_whitespace(self):
        from renderer import render_download_html
        html = render_download_html("본문")
        assert '<article class="markdown-body"><p>' in html
        assert "</p></article>" in html
