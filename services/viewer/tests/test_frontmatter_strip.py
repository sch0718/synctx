"""front matter를 본문에서 떼어 내는지 확인하는 회귀 테스트."""

import json
import re
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


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


@pytest.fixture
def manager():
    from synctx_common.config import RepoProfile, RepoAuthConfig
    m = MagicMock()
    m.profile = RepoProfile(id="test-repo", name="T", url="https://git.test/r.git", auth=RepoAuthConfig(type="none"))
    m.file_size.return_value = 100
    return m


@pytest.fixture
def client(manager, monkeypatch):
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "repo_managers", {"test-repo": manager})
    return TestClient(viewer_app.app, raise_server_exceptions=False)


DOC = "---\ntitle: 숨길 제목\nsynctx:\n  type: doc\n---\n# 본문 제목\n\n본문"


def _article(html: str) -> str:
    return re.search(r'<article class="markdown-body">(.*?)</article>', html, re.DOTALL).group(1)


def test_front_matter_not_rendered(client, manager):
    manager.read_file_text.return_value = DOC
    resp = client.get("/test-repo/docs/a.md")
    assert resp.status_code == 200
    body = _article(resp.text)
    assert "숨길 제목" not in body
    assert "synctx:" not in body
    assert "본문 제목" in body


def test_raw_markdown_keeps_front_matter(client, manager):
    manager.read_file_text.return_value = DOC
    resp = client.get("/test-repo/docs/a.md")
    raw = re.search(r'<script type="application/json" id="raw-markdown-json">(.*?)</script>', resp.text, re.DOTALL).group(1)
    assert json.loads(raw)["markdown"] == DOC


def test_marp_toolbar_still_detected(client, manager):
    manager.read_file_text.return_value = "---\nmarp: true\n---\n# Slide"
    resp = client.get("/test-repo/docs/slides.md")
    assert "convertMarpPdf()" in resp.text
    assert "marp: true" not in _article(resp.text)


def test_file_read_once(client, manager):
    manager.read_file_text.return_value = DOC
    client.get("/test-repo/docs/a.md")
    assert manager.read_file_text.call_count == 1


def test_title_is_escaped(client, manager):
    manager.read_file_text.return_value = "# </title><script>alert(1)</script>\n\n본문"
    resp = client.get("/test-repo/docs/a.md")
    head = resp.text.split("</head>")[0]
    assert "<script>alert(1)</script>" not in head
    assert "&lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt; - synctx Viewer</title>" in head


def test_code_file_unaffected(client, manager):
    manager.read_file_text.return_value = "---\nkey: value\n---\nprint(1)\n"
    resp = client.get("/test-repo/docs/a.py")
    assert resp.status_code == 200
    assert "key: value" in _article(resp.text)
