"""정적 내보내기 테마: 외부 CSS 출력, 테마 표, 서버 테마 부분 교체."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from renderer import THEME_CSS, THEME_HLJS, THEME_MERMAID
from static_export import pages, themes
from viewer_ui import _THEME_BOOT_ATTR, _THEME_BOOT_QUERY

THEMES = ("github-light", "github-dark", "cayman", "minimal", "hacker", "modernist", "corporate")
MERMAID_DOC = "# 제목\n\n```mermaid\ngraph TD\n A-->B\n```\n\n본문\n"


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    from synctx_common.config import SynctxConfig, ViewerConfig, AuthzConfig, RepoProfile, RepoAuthConfig
    cfg = SynctxConfig(
        authz=AuthzConfig(enabled=False),
        viewer=ViewerConfig(max_render_size_bytes=100_000, asset_source="bundled"),
        repositories=[RepoProfile(id="r", name="T", url="https://git.test/r.git", default_ref="main",
                                  auth=RepoAuthConfig(type="none"))],
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


def doc_html(client, manager, text, path="a.md"):
    manager.read_file_text.return_value = text
    resp = client.get(f"/r/{path}")
    assert resp.status_code == 200
    return resp.text


def run_boot(html, stored, *, raises=False):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node가 없어 부트 스크립트를 실행해 보지 못했다")
    script = re.search(r"<script>(\(function\(\)\{var C=.*?)</script>", html, re.DOTALL).group(1)
    harness = f"""
const attrs = {{}}; const hrefs = {{}};
const stored = {json.dumps(stored)};
globalThis.window = globalThis;
globalThis.localStorage = {{ getItem(k) {{ if ({json.dumps(raises)}) throw new Error('blocked'); return k === 'synctx-theme' ? stored : null; }} }};
globalThis.document = {{
  documentElement: {{ setAttribute(k, v) {{ attrs[k] = v; }} }},
  getElementById(id) {{ return (id === 'theme-css' || id === 'hljs-css') ? {{ setAttribute(k, v) {{ hrefs[id] = v; }} }} : null; }},
}};
{script}
console.log(JSON.stringify({{ attrs, hrefs, boot: globalThis.SYNCTX_STATIC && {{ theme: SYNCTX_STATIC.theme, mermaid: SYNCTX_STATIC.mermaid }} }}));
"""
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


class TestExport:
    def test_exports_all_seven_themes(self, tmp_path):
        written = themes.export_theme_assets(tmp_path)
        assert tuple(written) == THEMES
        for name, rel in written.items():
            assert rel == f"view/static/themes/{name}.css"
            assert (tmp_path / rel).read_text(encoding="utf-8") == THEME_CSS[name]
        assert sorted(p.name for p in (tmp_path / "view/static/themes").iterdir()) == sorted(f"{n}.css" for n in THEMES)

    def test_theme_table_matches_renderer_tables(self):
        assert tuple(themes.THEME_TABLE) == THEMES
        for name, entry in themes.THEME_TABLE.items():
            assert entry == {"css": f"view/static/themes/{name}.css", "hljs": THEME_HLJS[name], "mermaid": THEME_MERMAID[name]}

    def test_bundled_hljs_files_exist_for_every_theme(self):
        vendor = Path(__file__).resolve().parents[1] / "src" / "static" / "vendor" / "hljs"
        for name in THEMES:
            assert (vendor / f"{THEME_HLJS[name]}.min.css").is_file()


class TestFinalizeReplacesServerParts:
    def test_finalize_replaces_server_theme_parts(self, client, manager):
        html = doc_html(client, manager, MERMAID_DOC)
        assert _THEME_BOOT_QUERY in html and THEME_CSS["github-light"] in html
        out = pages.finalize_page(html, default_theme="corporate")
        assert _THEME_BOOT_QUERY not in out and _THEME_BOOT_ATTR not in out
        assert "window.location.replace" not in out
        assert THEME_CSS["github-light"] not in out
        assert "/cdn/highlight.js/11.9.0/styles/" not in out.split("</head>")[0]
        assert out.count('id="theme-css"') == 1 and out.count('id="hljs-css"') == 1
        assert re.search(r'<link id="hljs-css" rel="stylesheet" href="/view/static/vendor/hljs/github\.min\.css">', out)
        assert "theme: SYNCTX_STATIC.mermaid" in out
        assert not re.search(r"theme:\s*'[^']*'", out)
        assert "mermaid.run();" in out

    def test_replaced_parts_keep_order_before_viewer_css(self, client, manager):
        out = pages.finalize_page(doc_html(client, manager, MERMAID_DOC), default_theme="corporate")
        head = out.split("</head>")[0]
        assert head.index("<style>") < head.index('id="theme-css"') < head.index("/view/static/viewer.css")
        assert head.index('id="hljs-css"') < head.index("(function(){var C=")

    def test_mermaid_init_not_added_when_document_has_no_diagram(self, client, manager):
        out = pages.finalize_page(doc_html(client, manager, "# 제목\n\n본문\n"), default_theme="corporate")
        assert "mermaid.initialize" not in out
        assert "mermaid.min.js" not in out

    def test_markdown_text_that_mimics_server_parts_is_untouched(self, client, manager):
        text = ("# 제목\n\n`mermaid.initialize({ startOnLoad: false, theme: 'default' })`\n\n"
                "```html\n<style>x</style>\n```\n")
        html = doc_html(client, manager, text)
        out = pages.finalize_page(html, default_theme="corporate")
        raw = re.search(r'<script type="application/json" id="raw-markdown-json">.*?</script>', html, re.DOTALL).group(0)
        assert raw in out
        assert re.search(r'<article class="markdown-body">.*?</article>', html, re.DOTALL).group(0) in out

    def test_repo_page_gets_theme_link_and_boot_without_doc_parts(self, client):
        html = client.get("/r").text
        out = pages.finalize_page(html, default_theme="corporate")
        assert _THEME_BOOT_ATTR not in out
        assert out.count('id="theme-css"') == 1 and 'id="hljs-css"' not in out
        assert out.count("(function(){var C=") == 1

    def test_finalize_is_idempotent_with_theme_parts(self, client, manager):
        once = pages.finalize_page(doc_html(client, manager, MERMAID_DOC), default_theme="corporate")
        assert pages.finalize_page(once, default_theme="corporate") == once

    def test_default_theme_is_used_for_initial_hrefs(self, client, manager):
        out = pages.finalize_page(doc_html(client, manager, MERMAID_DOC), default_theme="hacker")
        assert 'id="theme-css" rel="stylesheet" href="/view/static/themes/hacker.css"' in out
        assert 'id="hljs-css" rel="stylesheet" href="/view/static/vendor/hljs/monokai.min.css"' in out


class TestBootScript:
    @pytest.fixture
    def out(self, client, manager):
        return pages.finalize_page(doc_html(client, manager, MERMAID_DOC), default_theme="corporate")

    def test_default_is_corporate_when_unset(self, out):
        result = run_boot(out, None)
        assert result["attrs"]["data-theme"] == "corporate"
        assert result["boot"] == {"theme": "corporate", "mermaid": "default"}
        assert result["hrefs"] == {"theme-css": "/view/static/themes/corporate.css", "hljs-css": "/view/static/vendor/hljs/github.min.css"}

    def test_unknown_stored_theme_falls_back_to_corporate(self, out):
        for bad in ("nope", "", "__proto__", "constructor", "toString", "github-light ", "CORPORATE"):
            assert run_boot(out, bad)["attrs"]["data-theme"] == "corporate", bad

    def test_blocked_local_storage_falls_back_to_corporate(self, out):
        assert run_boot(out, None, raises=True)["attrs"]["data-theme"] == "corporate"

    @pytest.mark.parametrize("name", THEMES)
    def test_each_stored_theme_maps_css_hljs_mermaid(self, out, name):
        result = run_boot(out, name)
        assert result["attrs"]["data-theme"] == name
        assert result["hrefs"] == {"theme-css": f"/view/static/themes/{name}.css",
                                   "hljs-css": f"/view/static/vendor/hljs/{THEME_HLJS[name]}.min.css"}
        assert result["boot"] == {"theme": name, "mermaid": THEME_MERMAID[name]}

    def test_boot_table_covers_all_themes_and_escapes_script_end(self, out):
        assert out.count("</script") == out.count("<script")
        table = json.loads(re.search(r"var C=(\{.*?\});var s=null", out, re.DOTALL).group(1))
        assert tuple(table["table"]) == THEMES and table["defaultTheme"] == "corporate"
