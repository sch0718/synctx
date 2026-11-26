"""Viewer 회의 모드 테스트."""

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest
import yaml
from fastapi.testclient import TestClient

from contrast import contrast, rgb, theme_vars

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


def meeting_doc(title="DX 21번 검토", starts_at="2026-10-08T14:00+09:00", body="# 회의 자료\n\n## 안건\n\n본문 내용\n",
                extra_meeting: dict | None = None):
    fm = {"synctx": {"type": "meeting", "meeting": {
        "id": "dx-21", "title": title, "starts_at": starts_at, "channel": "dx-team", **(extra_meeting or {})}}}
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
        assert "내려받기" in resp.text
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


class TestHighlightContrast:
    """형광펜 위 본문 글자는 모든 테마에서 WCAG AA 4.5:1 이상이어야 한다."""

    @staticmethod
    def _decl(block, prop):
        m = re.search(rf"(?:^|[;{{\s]){prop}\s*:\s*([^;}}]+)", block)
        return m.group(1).strip() if m else None

    def _theme_colors(self, theme_css):
        body = re.search(r"^body \{([^}]*)\}", theme_css, re.M).group(1)
        doc = re.search(r"^\.markdown-body \{([^}]*)\}", theme_css, re.M).group(1)
        text = rgb(self._decl(body, "color"))[0]
        doc_bg = self._decl(doc, "background")
        if not doc_bg or not re.match(r"#|rgb", doc_bg):
            doc_bg = self._decl(body, "background")
        background = rgb(doc_bg)[0]
        return text, background

    def _mark_style(self, css, theme, kind, nested=False):
        selectors = ["mark.meeting-hl"] + ([f"mark.meeting-hl--{kind}"] if kind != "note" else [])
        passes = [{sel for sel in selectors}, {f'[data-theme="{theme}"] {sel}' for sel in selectors}]
        if nested:
            passes.append({"mark.meeting-hl mark.meeting-hl"})
        style = {}
        for wanted in passes:
            for rule in re.finditer(r"^(\S[^{}]*)\{([^}]*)\}", css, re.M):
                if rule.group(1).strip() in wanted:
                    for prop in ("background", "color"):
                        value = self._decl(rule.group(2), prop)
                        if value:
                            style[prop] = value
        return style

    def _composite(self, style, behind):
        background = style["background"]
        if background == "transparent":
            return behind
        mark_rgb, alpha = rgb(background)
        return tuple(round(m * alpha + b * (1 - alpha)) for m, b in zip(mark_rgb, behind))

    def _text(self, styles, text):
        for style in styles:
            if style.get("color", "inherit") != "inherit":
                text = rgb(style["color"])[0]
        return text

    @pytest.mark.parametrize("kind", ["note", "decision", "action"])
    def test_all_themes_meet_aa(self, kind):
        import meeting
        from renderer import THEME_CSS
        css = (meeting.STATIC_DIR / "meeting.css").read_text(encoding="utf-8")
        failures = []
        for theme, theme_css in THEME_CSS.items():
            text, page = self._theme_colors(theme_css)
            style = self._mark_style(css, theme, kind)
            ratio = contrast(self._text([style], text), self._composite(style, page))
            if ratio < 4.5:
                failures.append(f"{theme}: {ratio:.2f}")
        assert not failures, failures

    def _meeting_color(self, css, theme, name, values):
        value = None
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        for rule in re.finditer(r"^(\S[^{}]*)\{([^}]*)\}", css, re.M):
            selectors = {sel.strip() for sel in rule.group(1).split(",")}
            if selectors & {":root", f'[data-theme="{theme}"]'}:
                value = self._decl(rule.group(2), name) or value
        ref = re.fullmatch(r"var\((--[\w-]+)(?:,\s*([^)]+))?\)", value)
        if ref:
            value = values.get(ref.group(1), ref.group(2))
        return rgb(value)[0]

    @pytest.mark.parametrize("name", ["--meeting-danger", "--meeting-muted", "--meeting-link"])
    def test_panel_text_meets_aa_on_panel_backgrounds(self, name):
        import meeting
        from renderer import THEME_CSS
        css = (meeting.STATIC_DIR / "meeting.css").read_text(encoding="utf-8")
        failures = []
        for theme in THEME_CSS:
            values = theme_vars(theme)
            color = self._meeting_color(css, theme, name, values)
            for var in ("--bg-default", "--bg-neutral"):
                ratio = contrast(color, rgb(values[var])[0])
                if ratio < 4.5:
                    failures.append(f"{theme} {var}: {ratio:.2f}")
        assert not failures, failures

    def test_panel_secondary_text_and_links_use_meeting_colors(self):
        import meeting
        css = (meeting.STATIC_DIR / "meeting.css").read_text(encoding="utf-8")
        assert not re.search(r"(?<![-\w])color:\s*var\(--text-secondary", css)
        assert not re.search(r"(?<![-\w])color:\s*var\(--primary", css)

    @pytest.mark.parametrize("outer", ["note", "decision", "action"])
    @pytest.mark.parametrize("inner", ["note", "decision", "action"])
    def test_overlapping_memos_do_not_stack_backgrounds(self, outer, inner):
        import meeting
        from renderer import THEME_CSS
        css = (meeting.STATIC_DIR / "meeting.css").read_text(encoding="utf-8")
        failures = []
        for theme, theme_css in THEME_CSS.items():
            text, page = self._theme_colors(theme_css)
            outer_style = self._mark_style(css, theme, outer)
            inner_style = self._mark_style(css, theme, inner, nested=True)
            behind = self._composite(inner_style, self._composite(outer_style, page))
            ratio = contrast(self._text([outer_style, inner_style], text), behind)
            if ratio < 4.5:
                failures.append(f"{theme}: {ratio:.2f}")
        assert not failures, failures


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

    def test_export_carries_theme_attribute(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body=RICH_BODY)
        resp = client.get("/api/repos/test-repo/meeting-export", params={"path": "docs/m.md", "theme": "hacker"})
        assert resp.status_code == 200
        assert '<html lang="ko" data-theme="hacker">' in resp.text

    def test_export_includes_theme_variables(self, client, manager):
        from renderer import THEME_VARS_CSS
        manager.read_file_text.return_value = meeting_doc(body=RICH_BODY)
        html = client.get("/api/repos/test-repo/meeting-export", params={"path": "docs/m.md", "theme": "hacker"}).text
        assert html.count(THEME_VARS_CSS) == 1
        assert html.index(THEME_VARS_CSS) < html.index("meeting-panel")

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

    def test_marker_comment_in_body_is_dropped(self, client, manager):
        # 본문 HTML 주석은 정리 단계에서 지워지므로 메모 자리표시와 겹치지 않는다
        manager.read_file_text.return_value = meeting_doc(body="# 회의\n\n<!--synctx-meeting-embed-->\n")
        resp = self._get(client)
        assert resp.status_code == 200
        assert resp.text.count("<!--synctx-meeting-embed-->") == 1


class TestRenderDownloadHtml:
    def test_html_filter_applies_to_body_only(self):
        from renderer import render_download_html
        html = render_download_html("# 제목\n\n```mermaid\nflowchart LR\n A-->B\n```\n", html_filter=lambda s: s.replace("제목", "필터됨"))
        assert "필터됨" in html
        assert "<script>" in html

    def test_html_carries_known_theme_only(self):
        from renderer import render_download_html
        assert '<html lang="ko" data-theme="hacker">' in render_download_html("본문", theme="hacker")
        unknown = render_download_html("본문", theme='"><script>x()</script>')
        assert '<html lang="ko" data-theme="github-light">' in unknown
        assert "x()" not in unknown

    def test_title_escaped(self):
        from renderer import render_download_html
        html = render_download_html("# </title><script>x()</script>\n")
        assert "<title>&lt;/title&gt;&lt;script&gt;x()&lt;/script&gt;</title>" in html

    def test_article_has_no_padding_whitespace(self):
        from renderer import render_download_html
        html = render_download_html("본문")
        assert '<article class="markdown-body"><p>' in html
        assert "</p></article>" in html


class TestMeetingCard:
    def test_meeting_doc_shows_card_before_meta(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        text = client.get("/test-repo/docs/m.md").text
        assert text.index('class="meeting-info"') < text.index('class="doc-meta"')
        assert "2026-10-08 (목) 14:00–15:00" in text and 'class="agenda"' in text

    def test_card_shown_even_when_meeting_mode_unavailable(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        assert 'class="meeting-info"' in client.get("/test-repo/docs/m.md?ref=feat").text

    def test_plain_doc_has_no_card(self, client, manager):
        manager.read_file_text.return_value = "# Hello"
        assert 'class="meeting-info"' not in client.get("/test-repo/docs/a.md").text

    def test_card_without_meeting_mode_offers_no_meeting_link(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        text = client.get("/test-repo/docs/m.md?ref=feat").text
        assert 'class="meeting-info"' in text and "mode=meeting" not in text

    def test_cancelled_meeting_shows_cancelled_status(self, client, manager):
        fm = {"synctx": {"type": "meeting", "meeting": {
            "id": "dx-21", "title": "DX 21번 검토", "starts_at": "2026-10-08T14:00+09:00",
            "channel": "dx-team", "cancelled": True}}}
        manager.read_file_text.return_value = "---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n# 본문\n"
        text = client.get("/test-repo/docs/m.md").text
        assert 'data-cancelled="true"' in text and "취소된 회의</p>" in text


class TestPresentMode:
    def test_present_mode_renders_bar_and_meeting_assets(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        text = client.get("/test-repo/docs/m.md?mode=present").text
        assert 'class="page-doc page-present"' in text and 'data-action="present-next"' in text
        assert '<article class="markdown-body">' in text
        assert 'src="/view/static/meeting/meeting.js"' in text and 'data-action="present-memo"' in text

    def test_present_without_meeting_mode_hides_memo(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        text = client.get("/test-repo/docs/m.md?mode=present&ref=feat").text
        assert 'data-action="present-next"' in text and 'data-action="present-memo"' not in text
        assert "meeting/meeting.js" not in text
        assert 'href="/view/test-repo/docs/m.md?ref=feat"' in text

    def test_present_on_plain_doc_shows_notice(self, client, manager):
        manager.read_file_text.return_value = "# Hello"
        text = client.get("/test-repo/docs/a.md?mode=present").text
        assert "발표 화면은 회의 문서에서만 쓸 수 있습니다" in text and "page-present" not in text

    def test_meeting_doc_offers_present_button(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        assert 'href="/view/test-repo/docs/m.md?mode=present">발표하기</a>' in client.get("/test-repo/docs/m.md").text

    def test_present_on_invalid_meeting_shows_validation_reason(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(starts_at="2026-10-08T14:00")
        text = client.get("/test-repo/docs/m.md?mode=present").text
        assert "회의 정보에 오류가 있습니다: starts_at에 타임존이 없습니다" in text
        assert "발표 화면은 회의 문서에서만 쓸 수 있습니다" not in text and "page-present" not in text

    def test_present_has_no_meeting_exit_link(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        text = client.get("/test-repo/docs/m.md?mode=present").text
        assert "회의 모드 나가기" not in text and "meeting-exit" not in text

    def test_present_button_keeps_non_default_ref(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        text = client.get("/test-repo/docs/m.md?ref=feat").text
        assert 'href="/view/test-repo/docs/m.md?mode=present&amp;ref=feat"' in text

    def test_present_bar_sits_between_doc_head_and_article(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        text = client.get("/test-repo/docs/m.md?mode=present").text
        assert text.index('class="doc-head"') < text.index('class="present-top"') < text.index('<article class="markdown-body">')

    def test_present_leaves_mermaid_rendering_to_slides(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body=MERMAID_BODY)
        present = client.get("/test-repo/docs/m.md?mode=present").text
        assert "mermaid.min.js" in present and "mermaid.initialize(" in present
        assert "mermaid.run(" not in present
        assert "mermaid.run();" in client.get("/test-repo/docs/m.md").text


class TestMeetingGuide:
    def test_meeting_mode_has_guide_in_doc_head(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        text = client.get("/test-repo/docs/m.md?mode=meeting").text
        assert 'id="meeting-guide"' in text and "회의 모드 안내 보기" in text
        assert text.index('class="meeting-exit"') < text.index('id="meeting-guide"') < text.index('class="doc-meta"')
        assert 'meeting-guide' not in article(text)

    def test_present_mode_has_no_guide(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        text = client.get("/test-repo/docs/m.md?mode=present").text
        assert "meeting/meeting.js" in text
        assert 'id="meeting-guide"' not in text and "회의 모드 안내 보기" not in text

    @pytest.mark.parametrize("doc,collab,query", [
        ("meeting", True, ""), ("meeting", False, "?mode=meeting"),
        ("meeting", True, "?mode=meeting&ref=dev"), ("plain", True, "?mode=meeting"),
    ])
    def test_no_guide_without_meeting_mode(self, client, manager, doc, collab, query):
        manager.profile = _profile(collab=collab)
        manager.read_file_text.return_value = meeting_doc() if doc == "meeting" else "# 일반 문서"
        text = client.get("/test-repo/docs/m.md" + query).text
        assert 'id="meeting-guide"' not in text and "회의 모드 안내 보기" not in text

    def test_export_has_no_guide(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        text = client.get("/api/repos/test-repo/meeting-export", params={"path": "docs/m.md"}).text
        assert "meeting-guide" not in text

    def test_guide_button_names_exist_in_meeting_js(self):
        import viewer_ui as ui
        js = (Path(ui.__file__).parent / "static" / "meeting" / "meeting.js").read_text(encoding="utf-8")
        copy = " ".join(ui.MEETING_GUIDE_ITEMS)
        for name in ("+ 메모", "메모", "회의 정리", ".md로 내보내기", "가져오기"):
            assert f"「{name}」" in copy
            assert f"['{name}']" in js, name


EVIDENCE_BODY = "# 회의 자료\n\n## 1. 점검\n\n규정 [제1조](../근거/a.md#c1)와 [A 전체](../근거/a.md)\n"
EVIDENCE_SOURCE = ('# 규정 A\n\n<a id="c1"></a>\n**제1조(목적)** <script>alert(1)</script>'
                   '<b onclick="x()">내용</b> <a href="javascript:alert(1)">위험</a> ![그림](http://ext/x.png)\n')


def _serve(manager, files):
    manager.file_size.return_value = 100
    manager.blob_size_resolved.return_value = 100
    manager.resolve_ref.side_effect = lambda ref=None: f"origin/{ref or 'main'}"
    def read_resolved(path, resolved):
        if path not in files:
            raise subprocess.CalledProcessError(128, ["git", "show"])
        return files[path].encode("utf-8")
    manager.read_resolved.side_effect = read_resolved


A_HREF = f"/view/test-repo/{quote('근거/a.md')}"  # 원문 열기 주소는 기존 doc_href처럼 quote한다


def _evidence_json(html):
    """data-synctx-evidence 속성이 붙은 JSON script 하나의 내용을 읽는다(id·속성 순서에 기대지 않는다)."""
    found = re.findall(r'<script\b(?=[^>]*\btype="application/json")(?=[^>]*\bdata-synctx-evidence\b)[^>]*>(.*?)</script>', html, re.S)
    assert len(found) == 1
    return json.loads(found[0])


def _evidence_section(html):
    """section#evidence-list의 안쪽 HTML만 깊이를 세어 잘라 낸다(중첩 section 포함)."""
    start = html.index('<section class="evidence-list"')
    depth, i = 0, start
    for m in re.finditer(r"<(/?)section\b", html[start:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[start:start + m.end()]
    raise AssertionError("evidence-list가 닫히지 않았다")


class TestEvidence:
    def test_meeting_doc_has_evidence_list_and_data(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body=EVIDENCE_BODY)
        _serve(manager, {"근거/a.md": EVIDENCE_SOURCE})
        text = client.get("/test-repo/docs/m.md").text
        assert 'data-evidence="evidence-1"' in text and 'data-evidence="evidence-2"' in text
        assert 'id="evidence-list"' in text and "근거 2건" in text and 'id="evidence-data"' in text
        assert "/static/meeting/evidence.js" in text and "/static/meeting/evidence.css" in text
        listing = _evidence_section(text)
        data = _evidence_json(text)
        for piece in [listing] + [e["html"] for e in data["entries"]]:
            assert "<script" not in piece and "onclick" not in piece and "javascript:" not in piece and "http://ext" not in piece
        assert text.index("</article>") < text.index('id="evidence-list"')
        assert f'href="{A_HREF}#c1">원문 열기</a>' in text

    def test_evidence_keeps_article_text(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body=EVIDENCE_BODY)
        _serve(manager, {})
        plain = article_text(client.get("/test-repo/docs/m.md").text)
        _serve(manager, {"근거/a.md": EVIDENCE_SOURCE})
        assert article_text(client.get("/test-repo/docs/m.md").text) == plain

    def test_export_article_text_matches_viewer_with_evidence(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body=EVIDENCE_BODY)
        _serve(manager, {"근거/a.md": EVIDENCE_SOURCE})
        viewer = article_text(client.get("/test-repo/docs/m.md").text)
        export = client.get("/api/repos/test-repo/meeting-export", params={"path": "docs/m.md"}).text
        assert article_text(export) == viewer

    def test_same_repo_mapping_keeps_ref(self, client, manager):
        doc = meeting_doc(body="# 회의\n\n[근거](../규정/a.md#c1)\n",
                          extra_meeting={"references": {"../규정/": "test-repo:근거/"}})
        manager.read_file_text.return_value = doc
        _serve(manager, {"근거/a.md": EVIDENCE_SOURCE})
        text = client.get("/test-repo/docs/m.md?ref=feat").text
        assert 'href="/view/test-repo/근거/a.md?ref=feat#c1"' in text and f'href="{A_HREF}?ref=feat#c1">원문 열기</a>' in text

    def test_evidence_reader_adapter(self, client, manager, monkeypatch):
        import app as viewer_app
        from synctx_common.config import RepoProfile, RepoAuthConfig
        regs = RepoProfile(id="regs", name="R", url="https://git.test/regs.git", auth=RepoAuthConfig(type="none"))
        other = MagicMock()
        other.profile = regs
        _serve(other, {"x.md": "다른 저장소"})
        monkeypatch.setattr(viewer_app, "repo_managers", {"test-repo": manager, "regs": other})
        monkeypatch.setattr(viewer_app.config, "repositories", [*viewer_app.config.repositories, regs])
        _serve(manager, {"ok.md": "가"})
        manager.read_resolved.side_effect = lambda path, resolved: {"ok.md": "가".encode(), "bad.md": b"\xff"}[path]
        read = viewer_app._evidence_reader("test-repo", manager, "feat")
        assert read("test-repo", "ok.md") == "가"
        assert manager.resolve_ref.call_args.args == ("feat",)  # repo_reader가 resolve_ref(ref)를 위치 인자로 부른다
        assert read("regs", "x.md") == "다른 저장소"
        assert other.resolve_ref.call_args.args == (None,)  # 다른 저장소는 기본 ref
        with pytest.raises(Exception):
            read("test-repo", "bad.md")
        assert manager.resolve_ref.call_count == 1  # 같은 저장소의 여러 파일은 ref를 한 번만 해석한다
        manager.read_resolved.reset_mock()
        manager.blob_size_resolved.return_value = viewer_app.config.viewer.max_render_size_bytes + 1
        with pytest.raises(Exception):
            read("test-repo", "ok.md")
        manager.read_resolved.assert_not_called()  # 크기가 넘으면 읽지 않는다

    def test_evidence_shown_without_meeting_mode(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body=EVIDENCE_BODY)
        _serve(manager, {"근거/a.md": EVIDENCE_SOURCE})
        text = client.get("/test-repo/docs/m.md?ref=feat").text
        assert 'id="evidence-list"' in text and f'href="{A_HREF}?ref=feat#c1">원문 열기</a>' in text

    def test_evidence_shown_when_collab_disabled(self, client, manager):
        manager.profile = _profile(collab=False)
        manager.read_file_text.return_value = meeting_doc(body=EVIDENCE_BODY)
        _serve(manager, {"근거/a.md": EVIDENCE_SOURCE})
        for url in ("/test-repo/docs/m.md", "/test-repo/docs/m.md?mode=present"):
            text = client.get(url).text
            assert 'class="evidence-list"' in text and "data-synctx-evidence" in text
            assert "/static/meeting/evidence.js" in text and 'data-evidence="evidence-1"' in text
            assert "/static/meeting/meeting.js" not in text  # 회의 모드 자산은 싣지 않는다

    def test_plain_doc_has_no_evidence(self, client, manager):
        manager.read_file_text.return_value = "# Hello\n\n[x](../근거/a.md)\n"
        _serve(manager, {"근거/a.md": EVIDENCE_SOURCE})
        text = client.get("/test-repo/docs/a.md").text
        assert "evidence-list" not in text and "data-evidence" not in text

    def test_cross_repo_mapping_and_missing_repo_notice(self, client, manager, monkeypatch):
        import app as viewer_app
        from synctx_common.config import RepoProfile, RepoAuthConfig
        regs = RepoProfile(id="regs", name="R", url="https://git.test/regs.git", auth=RepoAuthConfig(type="none"))
        other = MagicMock()
        other.profile = regs
        _serve(other, {"규정/x.md": "# X 규정\n\n## 범위\n본문"})
        monkeypatch.setattr(viewer_app, "repo_managers", {"test-repo": manager, "regs": other})
        monkeypatch.setattr(viewer_app.config, "repositories", [*viewer_app.config.repositories, regs])
        doc = meeting_doc(body="# 회의\n\n[규정](../사내규정/x.md#범위) [유령](../유령/y.md)\n",
                          extra_meeting={"references": {"../사내규정/": "regs:규정/", "../유령/": "ghost:"}})
        assert "references" in yaml.safe_load(doc.split("---\n")[1])["synctx"]["meeting"]
        manager.read_file_text.return_value = doc
        _serve(manager, {})
        text = client.get("/test-repo/docs/m.md").text
        assert 'href="/view/regs/규정/x.md#범위"' in text and "X 규정" in text
        assert "근거 저장소를 찾지 못했습니다: ghost" in text
        assert 'href="../유령/y.md"' in text  # 없는 저장소 매핑은 링크도 원문 그대로
        assert "유령/y.md" not in str(manager.read_resolved.call_args_list)  # 같은 저장소 경로로 읽지 않는다

    def test_export_shows_missing_repo_notice_and_keeps_href(self, client, manager):
        doc = meeting_doc(body="# 회의\n\n[유령](../유령/y.md)\n",
                          extra_meeting={"references": {"../유령/": "ghost:"}})
        manager.read_file_text.return_value = doc
        _serve(manager, {"유령/y.md": "# 같은 저장소 동명 파일"})
        text = client.get("/api/repos/test-repo/meeting-export", params={"path": "docs/m.md"}).text
        assert text.count("근거 저장소를 찾지 못했습니다: ghost") == 1
        assert text.index("근거 저장소를 찾지 못했습니다") < text.index('<article class="markdown-body">')
        assert 'href="../유령/y.md"' in text and "data-evidence" not in text and "evidence-list" not in text

    def test_body_heading_ids_do_not_collide_with_evidence_ids(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body=EVIDENCE_BODY + "\n## Evidence data\n\n## Evidence list\n")
        _serve(manager, {"근거/a.md": EVIDENCE_SOURCE})
        text = client.get("/test-repo/docs/m.md").text
        assert text.count('id="evidence-data"') == 1 and text.count('id="evidence-list"') == 1  # 둘 다 본문 제목 쪽
        assert text.count("data-synctx-evidence") == 1 and 'id="evidence-data-2" data-synctx-evidence' in text
        assert 'class="evidence-list" id="evidence-list-2"' in text

    def test_export_embeds_evidence_offline(self, client, manager):
        manager.read_file_text.return_value = meeting_doc(body=EVIDENCE_BODY)
        _serve(manager, {"근거/a.md": EVIDENCE_SOURCE})
        text = client.get("/api/repos/test-repo/meeting-export", params={"path": "docs/m.md"}).text
        assert 'id="evidence-list"' in text and 'id="evidence-data"' in text and 'data-evidence="evidence-1"' in text
        assert "원문 열기" not in text and not re.search(r'\b(?:src|href)="https?:', text)
        assert "/static/meeting/evidence.js" not in text and "SynctxEvidenceCore" in text
        assert text.count('id="evidence-data"') == 1
        listing = _evidence_section(text)
        data = _evidence_json(text)
        for piece in [listing] + [e["html"] for e in data["entries"]]:
            assert "javascript:" not in piece and "<script" not in piece and "onclick" not in piece  # 문서 전체가 아니라 근거 부분만 본다


class TestJitsiIntegration:
    @pytest.mark.parametrize('query', ['', '?mode=meeting', '?mode=present'])
    def test_jitsi_assets_and_configuration_in_all_meeting_views(self, client, manager, monkeypatch, query):
        from datetime import datetime, timezone
        from synctx_common.config import JitsiConfig
        import app
        monkeypatch.setattr(app.config.collab, 'jitsi', JitsiConfig(
            enabled=True, public_url='https://meet.synctx.local', app_id='synctx'))
        manager.read_file_text.return_value = meeting_doc(starts_at=datetime.now(timezone.utc).isoformat())
        response = client.get('/test-repo/docs/m.md' + query)
        assert response.status_code == 200
        markup = response.text
        raw = re.search(r'<script type="application/json" id="jitsi-config">(.*?)</script>', markup).group(1)
        cfg = json.loads(raw)
        assert cfg['enabled'] is True and cfg['doc'] == 'test-repo:docs/m.md'
        assert cfg['endpoint'] == '/view/api/meetings/test-repo/dx-21/jitsi'
        assert cfg['public_url'] == 'https://meet.synctx.local'
        assert markup.index('id="jitsi-config"') < markup.index('meeting/jitsi-core.js') < markup.index('meeting/jitsi.js')
        assert 'meeting/jitsi.css' in markup.split('</head>')[0]
        assert 'data-jitsi-join' in markup
        assert 'disabled>참여' not in markup
        assert 'JITSI_APP_SECRET' not in markup and 'room_secret' not in markup
        assert 'data-jitsi-join' not in article(markup)
        if query == '?mode=present':
            assert re.search(r'<header class="present-top">.*?data-jitsi-join', markup)

    @pytest.mark.parametrize('query,collab,reason', [('', True, '영상 회의가 꺼져'),
                                                    ('?mode=present&ref=dev', True, '기본 브랜치'),
                                                    ('', False, '회의 연동 대상')])
    def test_unavailable_buttons_are_disabled_with_text(self, client, manager, query, collab, reason):
        manager.profile = _profile(collab)
        manager.read_file_text.return_value = meeting_doc()
        markup = client.get('/test-repo/docs/m.md' + query).text
        assert 'disabled>참여' in markup and reason in markup

    def test_export_has_no_jitsi_scripts_or_token(self, client, manager):
        manager.read_file_text.return_value = meeting_doc()
        response = client.get('/api/repos/test-repo/meeting-export?path=docs/m.md')
        assert response.status_code == 200
        assert 'jitsi.js' not in response.text and 'jitsi-config' not in response.text
