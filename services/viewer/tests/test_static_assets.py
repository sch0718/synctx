"""정적 자산 제공과 필수 동작 함수 존재 확인."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "src" / "static"


@pytest.fixture
def client():
    import app as viewer_app
    return TestClient(viewer_app.app)


@pytest.mark.parametrize("name,ctype", [("viewer.css", "text/css"), ("viewer.js", "javascript"), ("viewer-core.js", "javascript")])
def test_static_files_are_served(client, name, ctype):
    resp = client.get(f"/static/{name}")
    assert resp.status_code == 200
    assert ctype in resp.headers["content-type"]


def test_tree_functions_escape_entries():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    for needle in ("function toggleDir(", "function loadSubtree(", "function filterTree(", "function expandToPath(",
                   "escapeHtml(d.name)", "escapeHtml(d.path)", "encodePath(f.path)", "escapeHtml(f.name)"):
        assert needle in js
    assert "+ f.name +" not in js and "+ d.name +" not in js
    assert "alert(" not in js


def test_drawer_uses_inert_and_breakpoints():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    assert "matchMedia('(max-width: 767px)')" in js and "matchMedia('(min-width: 1100px)')" in js
    assert "nav.inert = !open;" in js and "blockOutside(nav, open);" in js
    assert "if (navOpen()) return;" in js


def test_nav_close_hidden_outside_drawer_and_drawer_above_meeting_ui():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    # .btn이 뒤에 선언돼도 데스크톱에서 "메뉴 닫기"가 보이지 않아야 한다
    assert ".btn.nav-close { display: none;" in css
    assert css.index(".btn.nav-close { display: none;") < css.index("@media (max-width: 767px)")
    assert "z-index: 1100;" in css.split("@media (max-width: 767px)")[1]


def test_storage_access_is_guarded():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    assert js.count("localStorage") == 2
    assert "function storageGet(" in js and "function storageSet(" in js


def test_css_uses_text_disabled_only_for_icons():
    import re
    lines = [l.strip() for l in (STATIC / "viewer.css").read_text(encoding="utf-8").splitlines()
             if re.search(r"(?<![-\w])color:\s*var\(--text-disabled\)", l)]
    assert all(re.match(r"\.(icon-|\S+ svg)", l) for l in lines), lines


def test_drawer_closes_on_toc_link_and_recent_title_falls_back():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    assert "e.target.closest('a[href^=\"#\"]')" in js and "anchor.closest('#shell-nav')" in js
    assert "escapeHtml(x.title || x.path)" in js


def test_mobile_header_keeps_only_menu_logo_and_finder():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    mobile = css.split("@media (max-width: 767px)")[1]
    for needle in (".shell-header .shell-spacer", ".shell-header .shell-status", '.shell-header [data-action="shortcuts"]'):
        assert needle in mobile


def _rule(css, selector):
    import re
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match is not None, selector
    return match.group(1)


def test_anchor_targets_clear_the_sticky_header():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    body = _rule(css, ".markdown-body [id], #main")
    assert "scroll-margin-top: calc(var(--shell-header-h) + 12px)" in body


def test_drawer_becomes_visible_immediately_when_opened():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    mobile = css.split("@media (max-width: 767px)")[1]
    assert "visibility 0.2s" in _rule(mobile, ".shell-nav, .shell-nav-mobile-only")
    assert "visibility 0s" in _rule(mobile, ".shell-nav.is-open")


def test_drawer_toc_link_closes_after_default_navigation():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    block = js.split("if (anchor && navOpen() && anchor.closest('#shell-nav')) {")[1].split("return;")[0]
    assert "window.setTimeout(" in block and "setNav(false)" in block


def _rule(css, selector):
    import re
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match is not None, selector
    return match.group(1)


def test_branch_list_is_filled_after_load_not_while_rendering():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    assert "'/branches'" in js and "core.branchChoices(" in js
    assert 'select[data-action="branch"]' in js
    assert "option.textContent = " in js
