"""viewer_ui: 공통 틀·문구·오류 본문 테스트."""

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import viewer_ui as ui

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
REPOS = [SimpleNamespace(id="r1", name="회의 자료", default_ref="main"), SimpleNamespace(id="r2", name='a"<b>', default_ref="main")]


@pytest.mark.parametrize("delta,expected", [
    (timedelta(seconds=30), "방금"),
    (timedelta(minutes=5), "5분 전"),
    (timedelta(hours=2, minutes=59), "2시간 전"),
    (timedelta(days=3), "3일 전"),
    (timedelta(days=65), "2개월 전"),
    (timedelta(days=800), "2년 전"),
    (timedelta(seconds=-30), "방금"),
])
def test_relative_time_ko(delta, expected):
    assert ui.relative_time_ko((NOW - delta).isoformat(), NOW) == expected


def test_relative_time_ko_bad_input():
    assert ui.relative_time_ko("not-a-date", NOW) == ""
    assert ui.relative_time_ko(None, NOW) == ""


def test_theme_options_korean_labels_and_selection():
    html = ui.theme_options("github-dark")
    assert '<option value="github-dark" selected>GitHub 어둡게</option>' in html
    assert '<option value="github-light">GitHub 밝게</option>' in html


def test_repo_options_escape_and_placeholder():
    html = ui.repo_options(REPOS, "r1")
    assert '<option value="">저장소 선택</option>' in html
    assert '<option value="r1" selected>회의 자료</option>' in html
    assert "a&quot;&lt;b&gt;" in html and '<b>' not in html


def test_branch_options_marks_default():
    html = ui.branch_options(["main", "feat/x"], "feat/x", "main")
    assert '<option value="main">main (기본)</option>' in html
    assert '<option value="feat/x" selected>feat/x</option>' in html


def test_header_has_skip_link_finder_theme_and_status():
    html = ui.render_header(view_prefix="/view", repos=REPOS, theme="github-light", active_repo="r1",
                            branches=["main"], active_ref=None, default_ref="main", repo_status="cloning")
    assert html.index('class="skip-link" href="#main"') < html.index("<header")
    assert 'id="finder-input"' in html and 'role="combobox"' in html
    assert 'data-action="toggle-nav"' in html and 'aria-controls="shell-nav"' in html
    assert "저장소 받는 중" in html
    assert 'data-action="branch"' in html


def test_header_without_repo_has_no_branch_but_keeps_menu():
    html = ui.render_header(view_prefix="/view", repos=REPOS, theme="github-light")
    assert 'data-action="branch"' not in html
    assert 'data-action="toggle-nav"' in html
    assert "저장소 받는 중" not in html


def test_mobile_nav_holds_controls_and_close():
    html = ui.render_mobile_nav(controls='<div class="nav-controls"></div>', extra="<p>x</p>")
    assert html.startswith('<nav id="shell-nav" class="shell-nav shell-nav-mobile-only"')
    assert ui.NAV_CLOSE in html and '<div class="nav-controls"></div>' in html
    assert 'class="nav-backdrop" data-action="close-nav" hidden' in html


def test_ready_status_is_hidden():
    html = ui.render_header(view_prefix="/view", repos=REPOS, theme="github-light", active_repo="r1",
                            branches=["main"], default_ref="main", repo_status="ready")
    assert 'class="shell-status' not in html


def test_page_wraps_assets_and_encodes_script_values():
    html = ui.page(view_prefix="/view", title='제목 <x>', theme="github-light", header="<header></header>",
                   body='<main id="main"></main>', repo_id="r1", ref="';alert(1)//</script>")
    assert "<title>제목 &lt;x&gt; - synctx</title>" in html
    assert '<link rel="stylesheet" href="/view/static/viewer.css">' in html
    assert html.index('src="/view/static/viewer-core.js"') < html.index('src="/view/static/viewer.js"')
    assert 'var ACTIVE_REPO = "r1";' in html
    assert 'var ACTIVE_REF = "\';alert(1)//\\u003c/script\\u003e";' in html
    assert 'id="toast-region"' in html and 'id="shortcuts-dialog"' in html
    assert 'data-theme-mode="attr"' in html


def test_doc_config_precedes_viewer_script():
    html = ui.page(view_prefix="/view", title="t", theme="github-light", header="", body="",
                   doc_config={"path": "a</script>.md"})
    assert html.index("var SYNCTX_DOC = ") < html.index('src="/view/static/viewer.js"')
    assert "a\\u003c/script\\u003e.md" in html
    assert "var SYNCTX_DOC = null;" in ui.page(view_prefix="/view", title="t", theme="github-light", header="", body="")


def test_page_theme_via_query_mode():
    html = ui.page(view_prefix="/view", title="t", theme="github-light", header="", body="", theme_via_query=True)
    assert 'data-theme-mode="query"' in html
    assert "searchParams.has('theme')" in html


@pytest.mark.parametrize("kind", ["repo_missing", "file_missing", "too_large", "unsupported", "failed"])
def test_error_body_uses_copy_and_main_landmark(kind):
    html = ui.error_body(kind, view_prefix="/view", repo_id="r1", path="docs/a.md",
                         download_href="/view/api/repos/r1/download?path=docs/a.md")
    title, guide = ui.ERROR_COPY[kind]
    assert f"<h1>{title}</h1>" in html and guide in html
    assert 'id="main"' in html


def test_error_body_escapes_path_and_suggestions():
    html = ui.error_body("file_missing", view_prefix="/view", repo_id="r1", path='x"><script>a</script>',
                         suggestions=['docs/"q".md'])
    assert "<script>a</script>" not in html
    assert "x&quot;&gt;&lt;script&gt;" in html
    assert 'href="/view/r1/docs/%22q%22.md"' in html


def test_error_body_download_only_for_size_and_type():
    href = "/view/api/repos/r1/download?path=a.bin"
    assert "원본 내려받기" in ui.error_body("unsupported", view_prefix="/view", repo_id="r1", path="a.bin", download_href=href)
    assert "원본 내려받기" not in ui.error_body("file_missing", view_prefix="/view", repo_id="r1", path="a.bin", download_href=href)


def test_search_page_escapes_and_links():
    results = [{"id": "r1", "name": "One", "status": "ok", "truncated": True,
                "matches": [{"path": '회의 자료/a:"b".md', "line": 7, "before": "x ", "match": "<b>Hit</b>", "after": " y"}]}]
    html = ui.render_search_page(view_prefix="/view", query="<b>hit</b>", raw_query="<b>hit</b>",
                                 repos=REPOS, active_repo=None, ref=None, results=results)
    assert "<b>Hit</b>" not in html and "&lt;b&gt;hit&lt;/b&gt;" in html
    assert "x <mark>&lt;b&gt;Hit&lt;/b&gt;</mark> y" in html
    assert 'href="/view/r1/%ED%9A%8C%EC%9D%98%20%EC%9E%90%EB%A3%8C/a%3A%22b%22.md"' in html
    assert "7행" in html and ui.SEARCH_STATUS_COPY["truncated"] in html
    assert "1개 저장소에서 1건" in html


def test_search_page_statuses_empty_and_invalid():
    rs = [{"id": s, "name": s, "status": s, "truncated": False, "matches": []}
          for s in ("timeout", "unsupported", "unavailable", "failed", "ok")]
    html = ui.render_search_page(view_prefix="/view", query="ab", raw_query="ab", repos=REPOS,
                                 active_repo=None, ref=None, results=rs)
    for key in ("timeout", "unsupported", "unavailable", "failed"):
        assert ui.SEARCH_STATUS_COPY[key] in html
    assert "찾은 결과가 없습니다" in html
    cut = [{"id": "r1", "name": "One", "status": "ok", "truncated": True, "matches": []}]
    cut_html = ui.render_search_page(view_prefix="/view", query="ab", raw_query="ab", repos=REPOS,
                                     active_repo=None, ref=None, results=cut)
    assert ui.SEARCH_STATUS_COPY["truncated"] in cut_html and "찾은 결과가 없습니다" not in cut_html
    bad = ui.render_search_page(view_prefix="/view", query="", raw_query="a", repos=REPOS,
                                active_repo=None, ref=None, results=None)
    assert "두 글자 이상, 100글자 이하로 한 줄만 입력하세요" in bad and 'value="a"' in bad


def test_search_page_does_not_claim_no_results_when_every_repo_failed():
    rs = [{"id": s, "name": s, "status": s, "truncated": False, "matches": []}
          for s in ("timeout", "unsupported", "unavailable", "failed")]
    html = ui.render_search_page(view_prefix="/view", query="ab", raw_query="ab", repos=REPOS,
                                 active_repo=None, ref=None, results=rs)
    assert "찾은 결과가 없습니다" not in html
    for key in ("timeout", "unsupported", "unavailable", "failed"):
        assert ui.SEARCH_STATUS_COPY[key] in html
    only_timeout = [{"id": "r1", "name": "One", "status": "timeout", "truncated": False, "matches": []}]
    timeout_html = ui.render_search_page(view_prefix="/view", query="ab", raw_query="ab", repos=REPOS,
                                         active_repo=None, ref=None, results=only_timeout)
    assert "찾은 결과가 없습니다" not in timeout_html and ui.SEARCH_STATUS_COPY["timeout"] in timeout_html


def test_search_page_keeps_ref_only_for_its_repo():
    rs = [{"id": "r1", "name": "One", "status": "ok", "truncated": False,
           "matches": [{"path": "a.md", "line": 1, "before": "", "match": "ab", "after": ""}]}]
    html = ui.render_search_page(view_prefix="/view", query="ab", raw_query="ab", repos=REPOS,
                                 active_repo="r1", ref="feat/x", results=rs)
    assert 'href="/view/r1/a.md?ref=feat%2Fx"' in html
    assert '<input type="hidden" name="ref" value="feat/x">' in html
    assert '<input type="hidden" name="ref_repo" value="r1">' in html


def test_search_page_drops_ref_for_other_repositories():
    rs = [{"id": rid, "name": rid, "status": "ok", "truncated": False,
           "matches": [{"path": "a.md", "line": 1, "before": "", "match": "ab", "after": ""}]} for rid in ("r1", "r2")]
    html = ui.render_search_page(view_prefix="/view", query="ab", raw_query="ab", repos=REPOS,
                                 active_repo="r1", ref="feat/x", results=rs)
    assert 'href="/view/r1/a.md?ref=feat%2Fx"' in html
    assert 'href="/view/r2/a.md"' in html


def test_search_page_has_no_ref_inputs_without_active_repo():
    html = ui.render_search_page(view_prefix="/view", query="ab", raw_query="ab", repos=REPOS,
                                 active_repo=None, ref=None, results=[])
    assert 'name="ref"' not in html and 'name="ref_repo"' not in html


def test_header_and_shortcuts_say_document_search():
    header = ui.render_header(view_prefix="/view", repos=REPOS, theme="github-light")
    assert 'placeholder="문서 검색"' in header and 'aria-label="문서 검색"' in header
    assert 'aria-label="검색 결과"' in header and "파일 찾기" not in header
    page = ui.page(view_prefix="/view", title="t", theme="github-light", header="", body="")
    assert "<dd>문서 검색</dd>" in page


def test_meeting_card_escapes_once():
    data = {"title": '<b>"회의"</b> & 정리', "time_label": "2026-10-15 (목) 15:00–15:30",
            "starts_at_iso": "2026-10-15T15:00:00+09:00", "duration_minutes": 30,
            "presenters": ["<i>리드</i>"], "channel": "dx&ops", "cancelled": False,
            "agenda": [("a", "A & <B>")]}
    html = ui.render_meeting_card(data)
    assert "<b>" not in html and "<i>" not in html
    assert "&lt;b&gt;&quot;회의&quot;&lt;/b&gt; &amp; 정리" in html
    assert "&amp;amp;" not in html
    assert 'href="#a">A &amp; &lt;B&gt;</a>' in html
    assert 'data-starts-at="2026-10-15T15:00:00+09:00"' in html and 'data-duration="30"' in html


def test_meeting_card_omits_empty_rows_and_marks_cancelled():
    data = {"title": "회의", "time_label": "t", "starts_at_iso": "x", "duration_minutes": 60,
            "presenters": [], "channel": "dx", "cancelled": True, "agenda": []}
    html = ui.render_meeting_card(data)
    assert "발표자" not in html and 'class="agenda"' not in html
    assert "취소된 회의" in html and 'data-cancelled="true"' in html


def test_present_bar_controls_and_escaping():
    html = ui.render_present_bar(title='<b>회의</b>', exit_href="/view/r/m.md?ref=a&b", memo=True)
    assert "<b>" not in html and "&lt;b&gt;회의&lt;/b&gt;" in html
    assert 'href="/view/r/m.md?ref=a&amp;b"' in html
    for action in ("present-prev", "present-next", "present-memo"):
        assert f'data-action="{action}"' in html
    assert 'role="progressbar"' in html and "data-present-live" in html
    for label in ("이전(←)", "다음(→)", "메모 보기(N)", "발표 끝내기(Esc)"):
        assert label in html
    assert '<p class="present-empty" hidden>발표할 내용이 없습니다</p>' in html
    assert 'data-action="present-memo"' not in ui.render_present_bar(title="t", exit_href="/x", memo=False)


def test_meeting_exit_renders_hidden_guide_and_open_button():
    html = ui.render_meeting_exit('/view/r/a.md?ref=x"y')
    assert 'href="/view/r/a.md?ref=x&quot;y"' in html and "회의 모드 나가기" in html
    assert re.search(r'<button[^>]*data-action="meeting-guide-open"[^>]*aria-controls="meeting-guide"[^>]*hidden>사용법</button>', html)
    assert re.search(r'<section id="meeting-guide"[^>]*aria-labelledby="meeting-guide-title"[^>]*hidden>', html)
    assert '<h2 id="meeting-guide-title" class="meeting-guide-title" tabindex="-1">회의 모드 안내</h2>' in html
    assert len(ui.MEETING_GUIDE_ITEMS) == 4 and all(f"<li>{t}</li>" in html for t in ui.MEETING_GUIDE_ITEMS)
    assert ui.MEETING_GUIDE_ITEMS[0] == "메모는 이 브라우저에만 저장되고 다른 사람과 공유되지 않습니다."
    assert '>다시 보지 않기</button>' in html and 'data-action="meeting-guide-close">닫기</button>' in html


def test_shortcuts_dialog_offers_meeting_guide_only_when_asked():
    plain = ui.page(view_prefix="/view", title="t", theme="github-light", header="", body="")
    guided = ui.page(view_prefix="/view", title="t", theme="github-light", header="", body="", meeting_guide=True)
    assert "회의 모드 안내 보기" not in plain and "<dd>문서 검색</dd>" in plain
    dialog = guided.split('<dialog id="shortcuts-dialog"')[1].split("</dialog>")[0]
    assert re.search(r'data-action="meeting-guide-open" hidden>회의 모드 안내 보기</button>', dialog)
    assert dialog.index("</dl>") < dialog.index("회의 모드 안내 보기") < dialog.index('method="dialog"')


def test_present_bar_memo_button_exposes_panel_state():
    html = ui.render_present_bar(title="t", exit_href="/x", memo=True)
    button = re.search(r'<button[^>]*data-action="present-memo"[^>]*>', html).group(0)
    assert 'aria-controls="meeting-panel"' in button and 'aria-expanded="false"' in button


def test_present_progress_has_no_empty_aria_values():
    html = ui.render_present_bar(title="t", exit_href="/x", memo=False)
    progress = re.search(r'<div class="present-progress"[^>]*>', html).group(0)
    assert not re.search(r"aria-value(max|now)(?!=)", progress)


def test_search_page_without_query_is_not_an_alert():
    html = ui.render_search_page(view_prefix="/view", query="", raw_query="", repos=REPOS,
                                 active_repo=None, ref=None, results=None)
    assert 'role="alert"' not in html and "search-note-invalid" not in html
    assert ui.SEARCH_QUERY_HINT in html
    blank = ui.render_search_page(view_prefix="/view", query="", raw_query="   ", repos=REPOS,
                                  active_repo=None, ref=None, results=None)
    assert 'role="alert"' not in blank
