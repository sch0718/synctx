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


def test_meeting_guide_uses_guarded_storage_and_reopens_after_dialog():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    assert "MEETING_GUIDE_KEY = 'synctx-meeting-guide'" in js
    assert "core.meetingGuideAutoOpen(storageGet(MEETING_GUIDE_KEY))" in js
    assert "storageSet(MEETING_GUIDE_KEY, core.MEETING_GUIDE_HIDDEN)" in js
    assert "window.setTimeout(openMeetingGuide, 0)" in js
    for action in ("'meeting-guide-open'", "'meeting-guide-close'", "'meeting-guide-never'"):
        assert action in js
    body = js.split("function openMeetingGuide(")[1].split("\n  function ")[0]
    assert "storageSet" not in body and "preventScroll: true" in body
    assert "initMeetingGuide();" in js


def test_meeting_guide_css_keeps_hidden_working():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    assert "display" not in _rule(css, ".meeting-guide")
    assert "var(--primary)" in _rule(css, ".meeting-guide-open")
    assert "display: none" in _rule(css, ".shortcuts-extra .btn[hidden]")
    assert "padding: 4px 0" in _rule(css, ".meeting-guide-open")


def _present_js():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    return js.split("function initPresent(")[1].split("\n  }\n")[0]


def test_present_memo_button_mirrors_meeting_toggle_state():
    present = _present_js()
    assert "aria-expanded" in present and "present-memo" in present
    assert re.search(r"attributeFilter: \['aria-expanded'\]", present)


def test_present_flash_jump_does_not_depend_on_old_class():
    present = _present_js()
    assert "meeting-hl--flash" in present and "oldValue" not in present


def test_present_panel_sits_between_bands():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    rule = re.search(r"\.page-present \.meeting-panel\s*\{([^}]*)\}", css).group(1)
    assert "var(--present-top-h" in rule and "var(--present-nav-h" in rule and "max-width" in rule
    present = _present_js()
    assert "--present-top-h" in present and "--present-nav-h" in present


def _finder_js():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    return js.split("function initFinder(")[1].split("\n  }\n")[0]


def test_finder_groups_results_and_uses_core_helpers():
    finder = _finder_js()
    assert 'role="group"' in finder and "aria-labelledby" in finder
    assert "core.finderHref(" in finder and "core.finderFileStatus(" in finder
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    assert re.search(r'\.finder-results li\[role="group"\]\s*\{[^}]*padding:\s*0', css)


def test_evidence_js_uses_safe_dom_and_meeting_ui_marker():
    js = (STATIC / "meeting" / "evidence.js").read_text(encoding="utf-8")
    assert "data-meeting-ui" in js and "tooltip" in js and "aria-describedby" in js and "data-synctx-evidence" in js and "data-evidence-pop" in js
    assert "getElementById('evidence-data')" not in js and 'getElementById("evidence-data")' not in js
    assert "core.popupPosition(" in js and "core.opensInNewContext(" in js and "page-present" in js
    assert "location.hash =" not in js and "fetch(" not in js
    assert re.search(r"window\.addEventListener\('keydown',[\s\S]*?stopPropagation[\s\S]*?,\s*true\)", js)
    assert js.count(".innerHTML") == 1


def test_evidence_click_stops_propagation_only_when_handled():
    js = (STATIC / "meeting" / "evidence.js").read_text(encoding="utf-8")
    click = js.split("function onClick(e) {")[1].split("\n  }\n")[0]
    # 처리한 클릭(발표 화면 말풍선, 근거 모음 이동)만 메모 형광펜 등 다른 클릭 처리로 넘기지 않는다
    assert len(re.findall(r"e\.preventDefault\(\);\s*e\.stopPropagation\(\);", click)) == 2
    assert click.count("stopPropagation") == 2
    assert click.index("opensInNewContext") < click.index("stopPropagation")
    assert click.index("if (!shown(item)) return;") < click.rindex("stopPropagation")


def test_evidence_css_hides_list_in_present_and_popup_in_print():
    css = (STATIC / "meeting" / "evidence.css").read_text(encoding="utf-8")
    assert re.search(r"@media screen[^{]*\{\s*\.page-present \.evidence-list\s*\{[^}]*display:\s*none", css)
    assert re.search(r"@media print[^{]*\{[^}]*\.evidence-pop[^}]*display:\s*none", css)
    assert re.search(r"z-index:\s*1002", css)
    assert not re.search(r"var\(--[\w-]+\)", css)  # 모든 var()에 기본값


def test_evidence_css_marks_focus_without_color_alone_and_respects_reduced_motion():
    css = (STATIC / "meeting" / "evidence.css").read_text(encoding="utf-8")
    focus = _rule(css, ".evidence-entry:focus, .evidence-entry:target")
    assert "outline:" in focus and "box-shadow:" in focus
    assert re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*transition:\s*none", css)


def test_present_escape_leaves_open_evidence_popup_alone():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    present = js.split("function initPresent(")[1].split("\n  }\n")[0]
    assert "data-evidence-pop" in present


def _render_js():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    return js.split("function initRender(")[1].split("\n  }\n")[0]


def _embed_expand_js():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    return js.split("function initEmbedExpand(")[1].split("\n  }\n")[0]


def test_render_and_embed_initializers_preserve_iframe_nodes_and_sources():
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    assert "initRender();" in js and "initEmbedExpand();" in js
    for block in (_render_js(), _embed_expand_js()):
        assert not re.search(r"\bsrcdoc\b|\.src\s*=|setAttribute\(['\"]src['\"]", block)
        for forbidden in ("appendChild", "insertBefore", "cloneNode", "createElement", "innerHTML", "replaceChild", "replaceChildren"):
            assert forbidden not in block


def test_render_measures_bars_and_synchronizes_layout_controls():
    render = _render_js()
    for needle in ("[data-html-render]", "if (!root) return;", "[data-render-bars]", "getBoundingClientRect().height",
                   "--render-bars-height", "'resize', measureBars", "ResizeObserver(measureBars)", "observer.observe(bar)",
                   "data-render-layout", "'render-fit'", "'render-ratio'", "aria-pressed", "core.renderAspectRatio(", "--render-aspect"):
        assert needle in render
    assert "setLayout(root.getAttribute('data-render-layout'))" in render
    assert "syncFullscreen();" in render


def test_render_fullscreen_is_requested_by_click_and_failures_use_toast():
    render = _render_js()
    click = render.split("root.addEventListener('click', async function (e) {")[1]
    assert "await root.requestFullscreen()" in click and "await document.exitFullscreen()" in click
    assert "typeof root.requestFullscreen !== 'function'" in click
    assert "typeof document.exitFullscreen !== 'function'" in click
    assert re.search(r"catch\s*\([^)]*\)\s*\{\s*window\.synctxToast\(", click)
    failure = click.split("catch (err)")[1]
    assert "setLayout(" not in failure
    assert "'fullscreenchange', syncFullscreen" in render
    assert "fullscreen.setAttribute('aria-pressed', String(!!document.fullscreenElement))" in render


def test_embed_expand_restores_focus_and_scroll_and_traps_tab():
    embed = _embed_expand_js()
    for needle in ("[data-html-embed]", "'embed-expand'", "'embed-close'", "el.closest('[data-html-embed]')",
                   "close();", "opener = el", "scrollY = window.scrollY", "setAttribute('data-embed-expanded', 'true')",
                   "removeAttribute('data-embed-expanded')", "classList.add('embed-scroll-lock')", "classList.remove('embed-scroll-lock')",
                   "button.focus({ preventScroll: true })", "opener.focus({ preventScroll: true })", "window.scrollTo(0, scrollY)",
                   "e.key === 'Escape'", "page-present", "dialog[open]", "e.key === 'Tab'", "e.shiftKey",
                   "expanded.contains(document.activeElement)", "el.tabIndex >= 0", "el.getClientRects().length"):
        assert needle in embed
    assert re.search(r"document\.addEventListener\('keydown',[\s\S]*?stopImmediatePropagation[\s\S]*?,\s*true\)", embed)
    assert "document.addEventListener('focusin'" in embed and "expanded.contains(e.target)" in embed
    js = (STATIC / "viewer.js").read_text(encoding="utf-8")
    keys = js.split("function initKeys(")[1].split("\n  }\n")[0]
    assert "if ($('[data-embed-expanded=\"true\"]')) return;" in keys


def test_render_css_sizes_stage_and_fullscreen_without_hiding_bars():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    stage = _rule(css, "[data-html-render] [data-render-stage]")
    assert "width: 100%" in stage and "height: calc(100vh - var(--render-bars-height, 0px))" in stage
    ratio = _rule(css, '[data-html-render][data-render-layout="ratio"] [data-render-stage]')
    assert "aspect-ratio: var(--render-aspect, 1.7778)" in ratio and "max-height:" in ratio
    frame = _rule(css, "[data-html-render] [data-render-frame]")
    assert all(needle in frame for needle in ("width: 100%", "height: 100%", "border: 0", "background: #fff", "color: #1f2328"))
    full = _rule(css, '[data-html-render][data-render-full="true"], [data-html-render]:fullscreen')
    assert all(needle in full for needle in ("max-width: none", "margin: 0", "padding: 0"))
    assert "height: calc(100vh - var(--render-bars-height, 0px))" in _rule(css, "[data-html-render]:fullscreen [data-render-stage]")
    assert not re.search(r"\[data-render-bars\][^{]*\{[^}]*display:\s*none", css)


def test_embed_css_expands_above_drawer_and_hides_close_only_when_collapsed():
    css = (STATIC / "viewer.css").read_text(encoding="utf-8")
    expanded = _rule(css, '[data-html-embed][data-embed-expanded="true"]')
    assert all(needle in expanded for needle in ("position: fixed", "inset: 0", "z-index: 1200", "display: flex",
                                               "background: var(--bg-card)", "color: var(--text-primary)"))
    frame = _rule(css, '[data-html-embed][data-embed-expanded="true"] iframe')
    assert "flex: 1" in frame and "min-height: 0" in frame and "height: 100% !important" in frame and "aspect-ratio: auto !important" in frame
    assert "display: none" in _rule(css, '[data-html-embed]:not([data-embed-expanded="true"]) [data-action="embed-close"]')
    close = _rule(css, '[data-html-embed][data-embed-expanded="true"] [data-action="embed-close"]')
    assert all(needle in close for needle in ("position: absolute", "top:", "right:"))
    assert "overflow: hidden" in _rule(css, "body.embed-scroll-lock")
