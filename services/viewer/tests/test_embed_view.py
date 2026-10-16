import subprocess
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

ORIGIN = "https://cdn.synctx.local"
MD = "# 재구성\n\n```synctx-html\nsrc: ./motion/index.html\ntitle: 모션\n```\n\n<iframe src=\"https://evil.example\"></iframe>\n"
FILES = {
    "docs/motion/index.html": '<!DOCTYPE html><html><head><title>t</title></head><body><script src="motion.js"></script></body></html>',
    "docs/motion/motion.js": "window.v = 1;",
}


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    from synctx_common.config import (
        AuthzConfig, HtmlEmbedConfig, PlatformHtmlEmbedConfig, RepoAuthConfig, RepoProfile,
        RuntimeConfig, SynctxConfig, ViewerConfig,
    )
    mock_config = SynctxConfig(
        authz=AuthzConfig(enabled=False),
        runtime=RuntimeConfig(public_base_url="https://synctx.synctx.local"),
        viewer=ViewerConfig(max_render_size_bytes=4096, cdn_base_url="/cdn"),
        html_embed=PlatformHtmlEmbedConfig(allowed_origins=[ORIGIN]),
        repositories=[
            RepoProfile(id="test-repo", name="T", url="https://git.test/t.git", auth=RepoAuthConfig(type="none"),
                        html_embed=HtmlEmbedConfig(enabled=True, allowed_origins=[ORIGIN])),
            RepoProfile(id="plain-repo", name="P", url="https://git.test/p.git", auth=RepoAuthConfig(type="none")),
        ],
    )
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "config", mock_config)


def _manager(md=MD, files=None):
    files = dict(FILES if files is None else files)
    manager = MagicMock()
    manager.profile = MagicMock()
    manager.profile.default_ref = "main"
    manager.file_size.return_value = 100
    manager.read_file_text.return_value = md

    def read_resolved(path, resolved):
        if path not in files:
            raise subprocess.CalledProcessError(128, ["git", "show", path])
        return files[path].encode("utf-8")

    manager.resolve_ref.side_effect = lambda ref=None: f"origin/{ref or 'main'}"
    manager.read_resolved.side_effect = read_resolved
    manager.read_file.side_effect = lambda path, ref=None: read_resolved(path, None)
    return manager


def _client(monkeypatch, **managers):
    import app as viewer_app
    monkeypatch.setattr(viewer_app, "repo_managers", managers)
    return TestClient(viewer_app.app, raise_server_exceptions=False)


def test_block_becomes_sandboxed_iframe(monkeypatch):
    client = _client(monkeypatch, **{"test-repo": _manager()})
    resp = client.get("/test-repo/docs/reconfig.md")
    assert resp.status_code == 200
    assert resp.text.count("<iframe") == 1
    assert '<iframe class="synctx-html" sandbox="allow-scripts" title="모션"' in resp.text
    assert '<iframe src="https://evil.example"' not in resp.text
    assert "synctx-html-slot" not in resp.text
    assert "window.v = 1;" in resp.text


def test_nul_in_src_shows_error_box(monkeypatch):
    md = MD.replace("./motion/index.html", "./a%00.html")
    manager = _manager(md=md)
    manager.read_resolved.side_effect = lambda path, resolved: (_ for _ in ()).throw(ValueError("embedded null byte"))
    client = _client(monkeypatch, **{"test-repo": manager})
    resp = client.get("/test-repo/docs/reconfig.md")
    assert resp.status_code == 200
    assert "인터랙티브 콘텐츠를 불러오지 못했습니다" in resp.text


def test_disabled_repo_shows_notice_with_source_link(monkeypatch):
    client = _client(monkeypatch, **{"plain-repo": _manager()})
    resp = client.get("/plain-repo/docs/reconfig.md")
    assert "<iframe" not in resp.text
    assert "이 저장소는 HTML 실행이 허용되지 않습니다." in resp.text
    assert 'href="/view/plain-repo/docs/motion/index.html"' in resp.text


def test_bundle_failure_shows_error_box(monkeypatch):
    client = _client(monkeypatch, **{"test-repo": _manager(files={"docs/motion/index.html": FILES["docs/motion/index.html"]})})
    resp = client.get("/test-repo/docs/reconfig.md")
    assert "<iframe" not in resp.text
    assert "인터랙티브 콘텐츠를 불러오지 못했습니다" in resp.text
    assert "docs/motion/motion.js" in resp.text


def test_nested_example_is_left_as_code(monkeypatch):
    md = "````markdown\n```synctx-html\nsrc: ./motion/index.html\n```\n````\n"
    client = _client(monkeypatch, **{"test-repo": _manager(md=md)})
    resp = client.get("/test-repo/docs/howto.md")
    assert "<iframe" not in resp.text
    assert "synctx-html" in resp.text


def test_csp_adds_data_fonts_and_platform_origins(monkeypatch):
    client = _client(monkeypatch, **{"test-repo": _manager()})
    csp = client.get("/test-repo/docs/reconfig.md").headers["Content-Security-Policy"]
    assert csp == (
        f"default-src 'self'; script-src 'self' 'unsafe-inline' {ORIGIN}; "
        f"style-src 'self' 'unsafe-inline' {ORIGIN}; img-src 'self' data: {ORIGIN}; "
        f"font-src 'self' data: {ORIGIN}; connect-src 'self' {ORIGIN}; frame-src 'none'; object-src 'none'"
    )


def test_download_html_adds_cdn_notice(monkeypatch):
    client = _client(monkeypatch, **{"test-repo": _manager()})
    resp = client.get("/api/repos/test-repo/download", params={"path": "docs/reconfig.md", "format": "html"})
    assert resp.status_code == 200
    assert '<iframe class="synctx-html" sandbox="allow-scripts"' in resp.text
    assert "사내 CDN 접근이 필요할 수 있는 콘텐츠입니다" in resp.text


def test_download_pdf_replaces_block(monkeypatch):
    import httpx

    sent = {}

    class FakeResponse:
        status_code = 200
        content = b"%PDF-1.4"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json, headers=None):
            sent.update(json)
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    client = _client(monkeypatch, **{"test-repo": _manager()})
    resp = client.get("/api/repos/test-repo/download", params={"path": "docs/reconfig.md", "format": "pdf"})
    assert resp.status_code == 200
    assert "```synctx-html" not in sent["markdown"]
    assert "> 인터랙티브 콘텐츠: [Viewer에서 보기](https://synctx.synctx.local/view/test-repo/docs/reconfig.md)" in sent["markdown"]


@pytest.mark.parametrize("fmt,helper", [("html", "_render_download_html"), ("pdf", "_prepare_pdf_markdown")])
def test_download_work_runs_off_event_loop(monkeypatch, fmt, helper):
    import threading

    import httpx
    import app as viewer_app

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json, headers=None):
            return type("R", (), {"status_code": 200, "content": b"%PDF"})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    seen = {}
    original = getattr(viewer_app, helper)

    def spy(*args, **kwargs):
        seen["thread"] = threading.current_thread().name
        return original(*args, **kwargs)

    monkeypatch.setattr(viewer_app, helper, spy)
    client = _client(monkeypatch, **{"test-repo": _manager()})
    resp = client.get("/api/repos/test-repo/download", params={"path": "docs/reconfig.md", "format": fmt})
    assert resp.status_code == 200
    assert seen["thread"].startswith("asyncio")


def test_metric_counts_results(monkeypatch):
    from synctx_common.observability import html_bundle_total
    before = html_bundle_total.labels(service="viewer", repo_id="test-repo", result="ok")._value.get()
    client = _client(monkeypatch, **{"test-repo": _manager()})
    client.get("/test-repo/docs/reconfig.md")
    after = html_bundle_total.labels(service="viewer", repo_id="test-repo", result="ok")._value.get()
    assert after == before + 1
