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
