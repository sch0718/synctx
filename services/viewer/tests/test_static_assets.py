"""정적 자산 제공과 필수 동작 함수 존재 확인."""

import re
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


def test_tree_filter_shows_focus():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    assert "border-color: var(--primary)" in _rule(css, ".tree-filter:focus-within")


def test_wide_layout_always_shows_file_tree_and_hides_drawer_toc():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    wide = css.split("@media (min-width: 1100px)")[1]
    assert "display: flex" in _rule(wide, '.shell-nav [data-nav-panel="files"][hidden]')
    assert "display: none" in _rule(wide, '.shell-nav [data-nav-panel="toc"]')


def test_toasts_have_close_button_and_success_times_out():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    toast = js.split("function synctxToast(")[1].split("window.synctxToast")[0]
    assert "toast-close" in toast and "'닫기'" in toast and "setAttribute('aria-label', '닫기')" in toast
    assert "opts.kind === 'success'" in toast
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    assert ".toast-close" in css


def test_marp_forbidden_is_not_reported_as_service_down():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    assert "권한이 없습니다" in js and "슬라이드를 만들거나 출판할 권한이 필요합니다. 관리자에게 요청하세요." in js
    assert "e.message === '403'" in js


def test_branch_list_is_filled_after_load_not_while_rendering():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    assert "'/branches'" in js and "core.branchChoices(" in js
    assert 'select[data-action="branch"]' in js
    assert "option.textContent = " in js


def test_subtree_marks_current_document():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    assert 'aria-current="page"' in js.split("async function loadSubtree(")[1].split("function filterTree")[0]


def test_finder_enter_needs_visible_results_and_skips_ime_composition():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    finder = js.split("function initFinder(")[1].split("function focusFinder")[0]
    assert "e.isComposing || e.keyCode === 229" in finder
    assert "if (!input.value.trim()) { clearResults(); return; }" in finder
    assert "if (active >= 0 && !list.hidden)" in finder


def test_finder_late_file_list_does_not_reopen_dismissed_results():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    finder = js.split("function initFinder(")[1].split("function focusFinder")[0]
    assert "if (dismissed || document.activeElement !== input || !input.value.trim()) return;" in finder
    assert "dismissed = true;" in finder and "dismissed = false;" in finder


def test_finder_scrolls_active_result_into_view():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    paint = js.split("function paint()")[1].split("function render()")[0]
    assert "scrollIntoView({ block: 'nearest' })" in paint


def test_finder_uses_search_scheduler():
    finder = (STATIC / "viewer.js").read_text(encoding="utf-8").split("function initFinder(")[1].split("function focusFinder")[0]
    assert "core.createSearchScheduler(" in finder and "core.finderOptions(" in finder
    assert "'/api/search?q='" in finder


def test_meeting_card_status_and_agenda_tracking():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    assert "core.meetingStatus(" in js
    spy = js.split("function initTocSpy(")[1].split("\n  }\n")[0]
    assert ".agenda a[href^=" in spy and "core.sectionOwners(" in spy


def test_present_mode_hides_without_moving_nodes():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    present = js.split("function initPresent(")[1].split("\n  }\n")[0]
    assert "core.buildSlides(" in present and "core.presentKeyAction(" in present
    assert ".hidden = " in present and "appendChild" not in present and "insertBefore" not in present
    assert "history.replaceState" in present
    assert "core.presentTimeLabel(" in present
    assert "MutationObserver" in present and "meeting-hl--flash" in present and "data-memo-id" in present
    assert "present-empty" in present
    assert re.search(r"window\.addEventListener\('keydown',[\s\S]*?,\s*true\)", present)
    assert "querySelector('.meeting-toggle')" in present or 'querySelector(".meeting-toggle")' in present


def test_present_keeps_author_hidden_blocks_hidden():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    present = js.split("function initPresent(")[1].split("\n  }\n")[0]
    blocks = present.split("var blocks = ")[1].split(";\n")[0]
    assert "!el.hidden" in blocks


def test_present_renders_mermaid_per_slide():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    present = js.split("function initPresent(")[1].split("\n  }\n")[0]
    assert "window.mermaid.run({ nodes:" in present and "data-processed" in present
    assert "if (!window.mermaid) return;" in present
    assert "DOMContentLoaded" in present


def test_present_follows_hash_changes():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    present = js.split("function initPresent(")[1].split("\n  }\n")[0]
    assert "window.addEventListener('hashchange'" in present
    assert "decodeURIComponent(" in present and "scrollIntoView(" in present


def test_meeting_status_writes_only_changed_text():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    status = js.split("function initMeetingStatus(")[1].split("\n  }\n")[0]
    assert "label.textContent !== status.label" in status
    present = js.split("function initPresent(")[1].split("\n  }\n")[0]
    assert "time.textContent !== text" in present


def test_shell_shortcuts_stop_in_present_mode():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    handler = js.split("else if (e.key === 't'")[0].rsplit("document.addEventListener('keydown'", 1)[1]
    assert "page-present" in handler


def test_present_css_hides_slides_and_uses_one_column():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    assert re.search(r"\.page-present [^{]*\[hidden\][^{]*\{[^}]*display:\s*none\s*!important", css)
    assert re.search(r"\.page-present \.layout-doc[^{]*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)", css)
    assert ".page-present .meeting-card" not in css
